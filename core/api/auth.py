"""Bearer-token authentication and capability reporting for the mobile API."""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from core.models import APIAccessToken, StaffRole, get_user_profile
from core.permissions import (
    ADMIN_ROLES,
    CONDUCT_ROLES,
    EXEAT_APPROVAL_ROLES,
    EXEAT_CREATE_ROLES,
    EXEAT_ROLES,
    GATE_ROLES,
    REPORT_ROLES,
    ROLL_CALL_ROLES,
    STUDENT_MANAGEMENT_ROLES,
    STUDENT_RECORD_ROLES,
)

FEATURE_KEYS = (
    "roll_call",
    "exeat_gate",
    "student_id_cards",
    "sms_dispatch",
    "roster_import",
    "advanced_reports",
    "priority_support",
)


def _bearer_value(request):
    header = (request.META.get("HTTP_AUTHORIZATION") or "").strip()
    if not header:
        return ""
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def authenticate_bearer(request):
    """Resolve the bearer token, or ``None`` when it is missing or unusable.

    Session cookies are deliberately ignored: a logged-in browser must not be
    able to call the API without a token, which keeps CSRF out of the API
    threat model entirely.
    """
    raw_key = _bearer_value(request)
    if not raw_key:
        return None
    token = APIAccessToken.resolve(raw_key)
    if token is None:
        return None

    interval = getattr(settings, "API_TOKEN_TOUCH_INTERVAL", 300)
    now = timezone.now()
    if token.last_used_at is None or (now - token.last_used_at) >= timedelta(
        seconds=max(0, interval)
    ):
        APIAccessToken.objects.filter(pk=token.pk).update(last_used_at=now)
        token.last_used_at = now
    return token


def user_has_any_role(user, roles):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = get_user_profile(user)
    return bool(profile and profile.role in roles)


def role_value(user):
    if user.is_superuser:
        return StaffRole.ADMIN
    profile = get_user_profile(user)
    return profile.role if profile else StaffRole.UNASSIGNED


def house_for_user(user):
    """Return the user's assigned house, or ``None`` for school-wide staff."""
    housemaster = getattr(user, "housemaster", None)
    return getattr(housemaster, "house", None)


def capabilities(user):
    """Return the role-derived permission map consumed by the mobile client."""
    profile = get_user_profile(user)
    role = role_value(user)
    is_admin = bool(user.is_superuser or (profile and profile.is_admin))
    house = house_for_user(user)

    return {
        "role": role,
        "is_admin": is_admin,
        "is_senior_housemaster": bool(profile and profile.is_senior_housemaster),
        "is_housemaster": bool(profile and profile.is_housemaster),
        "is_gate_officer": bool(profile and profile.is_gate_officer),
        "is_medical": bool(profile and profile.is_medical),
        "house_id": str(house.pk) if house else None,
        "house_name": house.name if house else None,
        "students_view": is_admin or role in STUDENT_RECORD_ROLES,
        "students_manage": is_admin or role in STUDENT_MANAGEMENT_ROLES,
        "exeats_view": is_admin or role in EXEAT_ROLES,
        "exeats_create": is_admin or role in EXEAT_CREATE_ROLES,
        "exeats_approve": is_admin or role in EXEAT_APPROVAL_ROLES,
        "gate_operate": is_admin or role in GATE_ROLES,
        "roll_call": is_admin or role in ROLL_CALL_ROLES,
        "conduct": is_admin or role in CONDUCT_ROLES,
        "reports": is_admin or role in REPORT_ROLES,
        "configuration": is_admin or role in ADMIN_ROLES,
        "public_directory": True,
    }


def feature_flags():
    try:
        from tenancy.entitlements import feature_enabled
    except Exception:  # pragma: no cover - tenancy always importable
        return {key: True for key in FEATURE_KEYS}
    return {key: bool(feature_enabled(key)) for key in FEATURE_KEYS}


def scope_house_id(user):
    """Return the house UUID a non-privileged user is limited to.

    ``None`` means "no house filter". A ``PermissionDenied`` for a staff member
    with no assigned house is surfaced to the caller so the endpoint can return
    a clear ``403`` instead of silently returning school-wide data.
    """
    try:
        from core.permissions import get_house_scope

        house = get_house_scope(user)
    except PermissionDenied:
        raise
    return house.pk if house else None


def enforce_token_limit(user, *, keep=None, max_tokens=None):
    """Revoke oldest tokens so a user stays within the device limit."""
    max_tokens = max_tokens or getattr(settings, "API_TOKEN_MAX_PER_USER", 8)
    if not max_tokens or max_tokens < 1:
        return 0
    active = APIAccessToken.objects.filter(
        user=user,
        revoked_at__isnull=True,
        expires_at__gt=timezone.now(),
    ).order_by("-created_at")
    if keep is not None:
        active = active.exclude(pk=keep.pk)
    # ``keep`` is not part of ``active``, so allow one fewer other session.
    others_allowed = max(0, max_tokens - (1 if keep is not None else 0))
    stale = list(active.values_list("pk", flat=True)[others_allowed:])
    if not stale:
        return 0
    return APIAccessToken.objects.filter(pk__in=stale).update(
        revoked_at=timezone.now(),
        revoked_reason="device_limit",
    )
