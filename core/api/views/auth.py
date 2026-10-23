"""Authentication, onboarding invites, and device sessions."""
from __future__ import annotations

import uuid

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils import timezone

from core.api.auth import (
    enforce_token_limit,
    feature_flags,
    role_value,
)
from core.api.base import (
    APIError,
    Conflict,
    Forbidden,
    NotFound,
    api_endpoint,
    error,
    no_content,
    parse_json,
    success,
)
from core.api.serializers import school_payload, staff_payload, user_payload
from core.models import (
    APIAccessToken,
    AuditEvent,
    HouseMaster,
    PushDevice,
    StaffInvite,
    StaffRole,
    UserProfile,
    get_user_profile,
)
from core.throttling import client_ip, is_rate_limited

User = get_user_model()

ONBOARDING_HOUSE_ROLES = (StaffRole.HOUSEMASTER, StaffRole.HOUSE_TUTOR)
SELF_SERVICE_ROLES = tuple(
    role for role in StaffRole.values if role != StaffRole.UNASSIGNED
)


def _device_metadata(payload):
    device = payload.get("device") or {}
    if not isinstance(device, dict):
        raise APIError("invalid_device", "device must be an object.")
    platform = (device.get("platform") or "other").strip().lower()
    valid = {choice for choice, _ in APIAccessToken.Platform.choices}
    if platform not in valid:
        platform = APIAccessToken.Platform.OTHER
    return {
        "device_id": str(device.get("device_id") or "")[:128],
        "device_name": str(device.get("device_name") or "")[:150],
        "platform": platform,
        "app_version": str(device.get("app_version") or "")[:30],
    }


def _session_payload(request, token, raw_key, user):
    return {
        "token": raw_key,
        "token_type": "Bearer",
        "expires_at": token.expires_at.isoformat(),
        "canonical_origin": request.build_absolute_uri("/")[:-1],
        "user": user_payload(user),
        "school": school_payload(),
        "features": feature_flags(),
    }


def _unread_counts(user):
    from core.models import RollCallNotification, StaffNotification

    return {
        "notifications": StaffNotification.objects.filter(
            user=user, read_at__isnull=True
        ).count(),
        "roll_call_notifications": RollCallNotification.objects.filter(
            user=user, read_at__isnull=True
        ).count(),
    }


@api_endpoint(["POST"], auth=False)
def login(request):
    payload = parse_json(request)
    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    if not username or not password:
        raise APIError(
            "validation_error",
            "Username and password are required.",
            fields={
                "username": ["This field is required."] if not username else [],
                "password": ["This field is required."] if not password else [],
            },
        )

    identity = f"{client_ip(request)}:{username.lower()}"
    window = getattr(settings, "API_LOGIN_RATE_WINDOW", 300)
    if is_rate_limited(
        "api-login",
        identity,
        getattr(settings, "API_LOGIN_RATE_LIMIT", 10),
        window,
    ):
        raise APIError(
            "rate_limited",
            "Too many sign-in attempts. Try again later.",
            status=429,
            headers={"Retry-After": str(window)},
        )

    user = authenticate(request, username=username, password=password)
    if user is None:
        raise APIError(
            "invalid_credentials",
            "Invalid username or password.",
            status=401,
        )
    if not user.is_active:
        raise APIError(
            "account_inactive",
            "This staff account is inactive. Contact your school administrator.",
            status=403,
        )

    profile = get_user_profile(user)
    if profile is None or profile.role == StaffRole.UNASSIGNED:
        raise Forbidden(
            "This account has not been assigned a staff role yet. "
            "Contact your school administrator.",
            code="account_not_ready",
        )

    requested_role = (payload.get("role") or "").strip().upper()
    if requested_role and requested_role != role_value(user):
        raise Forbidden(
            f"This account is registered as {profile.get_role_display()}, "
            f"not {dict(StaffRole.choices).get(requested_role, requested_role)}.",
            code="role_mismatch",
        )

    metadata = _device_metadata(payload)
    if metadata["device_id"]:
        APIAccessToken.objects.filter(
            user=user,
            device_id=metadata["device_id"],
            revoked_at__isnull=True,
        ).update(revoked_at=timezone.now(), revoked_reason="device_replaced")

    token, raw_key = APIAccessToken.issue(user, **metadata)
    enforce_token_limit(user, keep=token)
    return success(request, _session_payload(request, token, raw_key, user))


@api_endpoint(["POST"])
def refresh(request):
    old_token = request.api_token
    user = request.api_user

    profile = get_user_profile(user)
    if profile is None or profile.role == StaffRole.UNASSIGNED:
        raise Forbidden(
            "This account no longer has a staff role.",
            code="account_not_ready",
        )

    old_token.revoke("refreshed")
    token, raw_key = APIAccessToken.issue(
        user,
        device_id=old_token.device_id,
        device_name=old_token.device_name,
        platform=old_token.platform,
        app_version=old_token.app_version,
    )
    enforce_token_limit(user, keep=token)
    return success(request, _session_payload(request, token, raw_key, user))


@api_endpoint(["POST"])
def logout(request):
    request.api_token.revoke("logout")
    PushDevice.objects.filter(
        user=request.api_user,
        device_id=request.api_token.device_id or "__none__",
    ).update(is_active=False)
    return no_content(request)


@api_endpoint(["POST"])
def logout_all(request):
    revoked = APIAccessToken.objects.filter(
        user=request.api_user, revoked_at__isnull=True
    ).update(revoked_at=timezone.now(), revoked_reason="logout_all")
    PushDevice.objects.filter(user=request.api_user).update(is_active=False)
    return success(request, {"revoked_tokens": revoked})


@api_endpoint(["GET"])
def me(request):
    user = request.api_user
    return success(
        request,
        {
            "user": user_payload(user),
            "school": school_payload(),
            "features": feature_flags(),
            "unread": _unread_counts(user),
        },
    )


@api_endpoint(["POST"])
def change_password(request):
    payload = parse_json(request)
    current_password = str(payload.get("current_password") or "")
    new_password = str(payload.get("new_password") or "")
    user = request.api_user

    if not user.check_password(current_password):
        raise APIError(
            "invalid_password",
            "The current password is incorrect.",
            status=400,
            fields={"current_password": ["The current password is incorrect."]},
        )
    if not new_password:
        raise APIError(
            "validation_error",
            "A new password is required.",
            fields={"new_password": ["This field is required."]},
        )
    try:
        validate_password(new_password, user=user)
    except DjangoValidationError as exc:
        raise APIError(
            "validation_error",
            "The new password does not meet the school's password policy.",
            fields={"new_password": list(exc.messages)},
        )
    user.set_password(new_password)
    user.save(update_fields=["password"])

    revoked = (
        APIAccessToken.objects.filter(user=user, revoked_at__isnull=True)
        .exclude(pk=request.api_token.pk)
        .update(revoked_at=timezone.now(), revoked_reason="password_changed")
    )
    return success(request, {"revoked_tokens": revoked})


@api_endpoint(["GET"])
def devices(request):
    tokens = APIAccessToken.objects.filter(
        user=request.api_user,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).order_by("-created_at")
    current = request.api_token.pk
    data = [
        {
            "id": str(token.pk),
            "prefix": token.key_prefix,
            "device_id": token.device_id,
            "device_name": token.device_name,
            "platform": token.platform,
            "app_version": token.app_version,
            "created_at": token.created_at.isoformat(),
            "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
            "expires_at": token.expires_at.isoformat(),
            "current": token.pk == current,
        }
        for token in tokens
    ]
    return success(request, data)


@api_endpoint(["DELETE"])
def revoke_device(request, token_id):
    try:
        token = APIAccessToken.objects.get(
            pk=token_id,
            user=request.api_user,
            revoked_at__isnull=True,
        )
    except (APIAccessToken.DoesNotExist, ValueError, DjangoValidationError):
        raise NotFound("That device session was not found.")
    token.revoke("revoked_by_user")
    AuditEvent.record(
        actor=request.api_user,
        action=AuditEvent.Action.API_TOKEN_REVOKED,
        target_type="APIAccessToken",
        target_id=token.pk,
        summary=f"Revoked API session {token.key_prefix}…",
    )
    return no_content(request)


# ---------------------------------------------------------------------------
# Onboarding: public invite validation and redemption
# ---------------------------------------------------------------------------
def _enforce_invite_rate_limit(request):
    """Throttle invite probing before touching the invite table."""
    window = getattr(settings, "API_LOGIN_RATE_WINDOW", 300)
    if is_rate_limited(
        "api-invite",
        client_ip(request),
        getattr(settings, "API_LOGIN_RATE_LIMIT", 10),
        window,
    ):
        raise APIError(
            "rate_limited",
            "Too many attempts. Try again later.",
            status=429,
            headers={"Retry-After": str(window)},
        )


@api_endpoint(["POST"], auth=False)
def validate_invite(request):
    _enforce_invite_rate_limit(request)
    payload = parse_json(request)
    raw_code = str(payload.get("code") or "")
    invite = StaffInvite.resolve(raw_code)
    if invite is None:
        raise APIError(
            "invalid_invite",
            "That invite code is invalid, expired, or already used.",
            status=404,
        )
    return success(
        request,
        {
            "role": invite.role,
            "role_display": invite.get_role_display(),
            "first_name": invite.first_name,
            "last_name": invite.last_name,
            "email": invite.email,
            "house": (
                {"id": str(invite.house_id), "name": invite.house.name}
                if invite.house_id
                else None
            ),
            "expires_at": invite.expires_at.isoformat(),
            "school": school_payload(),
        },
    )


@api_endpoint(["POST"], auth=False)
def redeem_invite(request):
    _enforce_invite_rate_limit(request)
    payload = parse_json(request)
    raw_code = str(payload.get("code") or "")
    invite = StaffInvite.resolve(raw_code)
    if invite is None:
        raise APIError(
            "invalid_invite",
            "That invite code is invalid, expired, or already used.",
            status=404,
        )

    username = str(payload.get("username") or "").strip()
    password = str(payload.get("password") or "")
    first_name = str(payload.get("first_name") or invite.first_name).strip()
    last_name = str(payload.get("last_name") or invite.last_name).strip()
    phone_number = str(payload.get("phone_number") or invite.phone_number).strip()

    fields = {}
    if not username:
        fields["username"] = ["This field is required."]
    elif len(username) > 150:
        fields["username"] = ["Usernames must be 150 characters or fewer."]
    elif User.objects.filter(username__iexact=username).exists():
        fields["username"] = ["That username is already taken."]
    if not password:
        fields["password"] = ["This field is required."]
    if fields:
        raise APIError(
            "validation_error",
            "The request could not be processed.",
            fields=fields,
        )

    if invite.role in ONBOARDING_HOUSE_ROLES and not invite.house_id:
        raise APIError(
            "invite_incomplete",
            "This invite is missing a house assignment. Contact your administrator.",
            status=409,
        )
    if (
        invite.role in ONBOARDING_HOUSE_ROLES
        or invite.role == StaffRole.SENIOR_HOUSEMASTER
    ) and not phone_number:
        raise APIError(
            "validation_error",
            "A phone number is required for house staff.",
            fields={"phone_number": ["This field is required."]},
        )

    probe_user = User(username=username)
    try:
        validate_password(password, user=probe_user)
    except DjangoValidationError as exc:
        raise APIError(
            "validation_error",
            "The password does not meet the school's password policy.",
            fields={"password": list(exc.messages)},
        )

    metadata = _device_metadata(payload)
    with transaction.atomic():
        invite = StaffInvite.objects.select_for_update().get(pk=invite.pk)
        if not invite.is_open:
            raise Conflict("This invite has already been used or has expired.")

        # ``is_staff=True`` is what makes this a staff account. The User
        # pre_save signal enforces the tenant's max_staff quota under the same
        # advisory lock used by the web admin, so onboarding cannot bypass it.
        user = User.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
            email=str(payload.get("email") or invite.email).strip(),
            is_staff=True,
        )
        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = invite.role
        profile.phone_number = phone_number[:17]
        profile.save()

        if invite.role in ONBOARDING_HOUSE_ROLES or invite.role == StaffRole.SENIOR_HOUSEMASTER:
            staff_id = (invite.staff_id or f"STF-{uuid.uuid4().hex[:8].upper()}")[:30]
            HouseMaster.objects.create(
                user=user,
                full_name=f"{first_name} {last_name}".strip() or username,
                staff_id=staff_id,
                phone_number=phone_number[:17],
                house=invite.house if invite.role != StaffRole.SENIOR_HOUSEMASTER else None,
                is_senior_housemaster=invite.role == StaffRole.SENIOR_HOUSEMASTER,
            )

        invite.accept(user)
        AuditEvent.record(
            actor=user,
            action=AuditEvent.Action.STAFF_INVITE_REDEEMED,
            target_type="StaffInvite",
            target_id=invite.pk,
            summary=f"Staff invite redeemed for {username} ({invite.get_role_display()})",
        )

        token, raw_key = APIAccessToken.issue(user, **metadata)
        enforce_token_limit(user, keep=token)

    return success(
        request,
        _session_payload(request, token, raw_key, user),
        status=201,
    )


# ---------------------------------------------------------------------------
# Admin: invite management
# ---------------------------------------------------------------------------
def _invite_status(invite):
    if invite.revoked_at:
        return "revoked"
    if invite.accepted_at:
        return "accepted"
    if timezone.now() >= invite.expires_at:
        return "expired"
    return "open"


def _invite_payload(invite):
    return {
        "id": str(invite.pk),
        "code_prefix": invite.code_prefix,
        "role": invite.role,
        "role_display": invite.get_role_display(),
        "first_name": invite.first_name,
        "last_name": invite.last_name,
        "email": invite.email,
        "phone_number": invite.phone_number,
        "staff_id": invite.staff_id,
        "house": (
            {"id": str(invite.house_id), "name": invite.house.name}
            if invite.house_id
            else None
        ),
        "note": invite.note,
        "status": _invite_status(invite),
        "created_at": invite.created_at.isoformat(),
        "expires_at": invite.expires_at.isoformat(),
        "accepted_at": invite.accepted_at.isoformat() if invite.accepted_at else None,
        "accepted_by": (
            staff_payload(invite.accepted_by) if invite.accepted_by_id else None
        ),
    }


@api_endpoint(["GET", "POST"], roles=(StaffRole.ADMIN,))
def staff_invites(request):
    if request.method == "GET":
        from core.api.base import paginate

        queryset = StaffInvite.objects.select_related("house", "accepted_by").order_by(
            "-created_at"
        )
        status_filter = (request.GET.get("status") or "").strip()
        now = timezone.now()
        if status_filter == "open":
            queryset = queryset.filter(
                revoked_at__isnull=True,
                accepted_at__isnull=True,
                expires_at__gt=now,
            )
        elif status_filter == "accepted":
            queryset = queryset.filter(accepted_at__isnull=False)
        elif status_filter == "revoked":
            queryset = queryset.filter(revoked_at__isnull=False)
        elif status_filter == "expired":
            queryset = queryset.filter(
                accepted_at__isnull=True, expires_at__lte=now
            )
        items, pagination = paginate(request, queryset, _invite_payload)
        return success(request, items, pagination=pagination)

    payload = parse_json(request)
    role = str(payload.get("role") or "").strip().upper()
    if role not in SELF_SERVICE_ROLES:
        raise APIError(
            "validation_error",
            "Choose a valid staff role.",
            fields={"role": ["Choose a valid staff role."]},
        )
    house_id = payload.get("house_id")
    house = None
    if house_id:
        from core.models import House

        house = House.objects.filter(pk=house_id).first()
        if house is None:
            raise APIError(
                "validation_error",
                "The selected house was not found.",
                fields={"house_id": ["The selected house was not found."]},
            )
    if role in ONBOARDING_HOUSE_ROLES and house is None:
        raise APIError(
            "validation_error",
            "Housemaster and House Tutor invites require a house.",
            fields={"house_id": ["This field is required for the selected role."]},
        )

    phone_number = str(payload.get("phone_number") or "").strip()[:17]
    if (
        role in ONBOARDING_HOUSE_ROLES or role == StaffRole.SENIOR_HOUSEMASTER
    ) and not phone_number:
        raise APIError(
            "validation_error",
            "A phone number is required for house staff.",
            fields={"phone_number": ["This field is required."]},
        )

    invite, raw_code = StaffInvite.issue(
        role=role,
        created_by=request.api_user,
        house=house,
        first_name=str(payload.get("first_name") or "").strip()[:150],
        last_name=str(payload.get("last_name") or "").strip()[:150],
        email=str(payload.get("email") or "").strip(),
        phone_number=phone_number,
        staff_id=str(payload.get("staff_id") or "").strip()[:30],
        note=str(payload.get("note") or "").strip()[:200],
    )
    AuditEvent.record(
        actor=request.api_user,
        action=AuditEvent.Action.STAFF_INVITE_CREATED,
        target_type="StaffInvite",
        target_id=invite.pk,
        summary=f"Created {invite.get_role_display()} invite {invite.code_prefix}…",
        after={"role": role, "house_id": str(house.pk) if house else None},
    )
    data = _invite_payload(invite)
    data["code"] = raw_code
    return success(request, data, status=201)


@api_endpoint(["DELETE"], roles=(StaffRole.ADMIN,))
def revoke_invite(request, invite_id):
    try:
        invite = StaffInvite.objects.get(pk=invite_id)
    except (StaffInvite.DoesNotExist, ValueError, DjangoValidationError):
        raise NotFound("That invite was not found.")
    if invite.accepted_at:
        raise Conflict("An accepted invite cannot be revoked.")
    if invite.revoked_at is None:
        invite.revoked_at = timezone.now()
        invite.save(update_fields=["revoked_at"])
    return no_content(request)
