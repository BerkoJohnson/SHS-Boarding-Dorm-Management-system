"""Exeat lifecycle endpoints: request, review, reschedule, cancel, reconcile."""
from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.api.base import (
    APIError,
    Conflict,
    Forbidden,
    NotFound,
    api_endpoint,
    apply_ordering,
    idempotent,
    paginate,
    parse_json,
    success,
)
from core.api.serializers import (
    exeat_detail,
    exeat_summary,
    review_payload,
    schedule_change_payload,
)
from core.models import (
    AcademicTerm,
    Exeat,
    ExeatMovement,
    ExeatReconciliation,
    ExeatReview,
    ExeatScheduleChange,
    SMSDelivery,
    StaffRole,
    Student,
    get_approvable_pending_for_user,
    get_user_profile,
)
from core.permissions import (
    EXEAT_APPROVAL_ROLES,
    EXEAT_CREATE_ROLES,
    EXEAT_ROLES,
    can_approve_exeat_check,
    filter_exeats_for_user,
    filter_queryset_by_house,
)
from core.services.exeat_workflow import (
    approve_with_override,
    cancel_exeat,
    decide_exeat_review,
    reschedule_exeat,
)
from core.services.late_returns import open_late_return_incident
from core.services.sms import enqueue_exeat_event_sms

ORDERING_FIELDS = {
    "created_at",
    "expected_departure",
    "expected_return",
    "status",
    "exeat_type",
    "student__full_name",
}

EXEAT_QUERYSET = (
    "student__house",
    "student__dormitory",
    "student__class_group",
    "student__guardian",
    "academic_term",
    "approved_by",
    "approved_by_user",
    "movement",
    "reconciliation",
)


def _base_queryset():
    return Exeat.objects.select_related(*EXEAT_QUERYSET)


def _scoped_exeats(user):
    return filter_exeats_for_user(user, _base_queryset())


def _get_exeat(user, exeat_id):
    exeat = _scoped_exeats(user).filter(pk=exeat_id).first()
    if exeat is None:
        raise NotFound("That exeat pass was not found.")
    return exeat


def _parse_datetime(payload, key, *, required=False):
    raw = payload.get(key)
    if raw in (None, ""):
        if required:
            raise APIError(
                "validation_error",
                f"{key} is required.",
                fields={key: ["This field is required."]},
            )
        return None
    parsed = parse_datetime(str(raw).strip())
    if parsed is None:
        raise APIError(
            "validation_error",
            f"{key} must be an ISO 8601 date-time.",
            fields={key: ["Use an ISO 8601 date-time, e.g. 2026-10-24T08:00:00Z."]},
        )
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _apply_filters(request, queryset):
    status = (request.GET.get("status") or "").strip()
    if status:
        if status not in Exeat.Status.values:
            raise APIError("invalid_filter", "Unknown exeat status.")
        queryset = queryset.filter(status=status)

    exeat_type = (request.GET.get("exeat_type") or "").strip()
    if exeat_type:
        if exeat_type not in Exeat.ExeatType.values:
            raise APIError("invalid_filter", "Unknown exeat type.")
        queryset = queryset.filter(exeat_type=exeat_type)

    for param, field in (
        ("student_id", "student_id"),
        ("house_id", "student__house_id"),
        ("academic_term_id", "academic_term_id"),
    ):
        raw = (request.GET.get(param) or "").strip()
        if not raw:
            continue
        try:
            uuid.UUID(raw)
        except ValueError:
            raise APIError("invalid_filter", f"{param} must be a UUID.")
        queryset = queryset.filter(**{field: raw})

    query = (request.GET.get("q") or "").strip()
    if query:
        from django.db.models import Q

        queryset = queryset.filter(
            Q(exeat_number__icontains=query)
            | Q(student__full_name__icontains=query)
            | Q(student__admission_no__icontains=query)
        )

    active = (request.GET.get("active") or "").strip().lower()
    if active in {"1", "true", "yes"}:
        queryset = queryset.filter(is_active=True)
    elif active in {"0", "false", "no"}:
        queryset = queryset.filter(is_active=False)

    start = _parse_datetime(request.GET, "from")
    end = _parse_datetime(request.GET, "to")
    if start:
        queryset = queryset.filter(expected_departure__gte=start)
    if end:
        queryset = queryset.filter(expected_departure__lte=end)

    bucket = (request.GET.get("bucket") or "").strip()
    if bucket == "pending_approvals":
        queryset = get_approvable_pending_for_user(request.api_user).filter(
            pk__in=queryset.values("pk")
        )
    elif bucket == "active":
        queryset = queryset.filter(is_active=True)
    elif bucket == "overdue":
        now = timezone.now()
        queryset = queryset.filter(
            status=Exeat.Status.APPROVED,
            expected_return__lt=now,
            is_active=True,
        )
    return queryset


def _detail_payload(exeat):
    reviews = request_reviews(exeat)
    changes = list(
        ExeatScheduleChange.objects.filter(exeat=exeat).order_by("-created_at")[:20]
    )
    data = exeat_detail(exeat)
    data["reviews"] = [review_payload(review) for review in reviews]
    data["schedule_changes"] = [
        schedule_change_payload(change) for change in changes
    ]
    data["is_overdue"] = bool(
        exeat.status == Exeat.Status.APPROVED
        and exeat.is_active
        and exeat.expected_return
        and timezone.now() > exeat.expected_return
    )
    return data


def request_reviews(exeat):
    return list(
        ExeatReview.objects.filter(exeat=exeat)
        .select_related("authorized_by", "reviewed_by")
        .order_by("-created_at")
    )


@api_endpoint(["GET", "POST"], roles=EXEAT_ROLES, features=("exeat_gate",))
def exeats(request):
    if request.method == "POST":
        # Creation is restricted to EXEAT_CREATE_ROLES; the list is readable by
        # the wider EXEAT_ROLES group (including MEDICAL).
        if not request.api_user.is_superuser:
            profile = get_user_profile(request.api_user)
            if profile is None or profile.role not in EXEAT_CREATE_ROLES:
                raise Forbidden(
                    "Your staff role cannot create exeat requests.",
                    code="role_forbidden",
                )
        return _create_exeat(request)

    queryset = _apply_filters(request, _scoped_exeats(request.api_user))
    queryset = apply_ordering(
        request, queryset, ORDERING_FIELDS, default="-created_at"
    )
    items, pagination = paginate(request, queryset, exeat_summary)
    return success(request, items, pagination=pagination)


@api_endpoint(["GET"], roles=EXEAT_ROLES, features=("exeat_gate",))
def exeat_detail_view(request, exeat_id):
    exeat = _get_exeat(request.api_user, exeat_id)
    return success(request, _detail_payload(exeat))


@idempotent
def _create_exeat(request):
    payload = parse_json(request)
    student_id = payload.get("student_id") or payload.get("student")
    if not student_id:
        raise APIError(
            "validation_error",
            "Choose a student for this exeat.",
            fields={"student_id": ["This field is required."]},
        )
    try:
        uuid.UUID(str(student_id))
    except ValueError:
        raise APIError(
            "validation_error",
            "student_id must be a UUID.",
            fields={"student_id": ["Enter a valid student id."]},
        )

    student = (
        filter_queryset_by_house(
            request.api_user,
            Student.objects.select_related("house", "dormitory", "guardian"),
            "house_id",
        )
        .filter(pk=student_id)
        .first()
    )
    if student is None:
        raise NotFound("That student was not found.")

    exeat_type = str(payload.get("exeat_type") or "").strip()
    if exeat_type not in Exeat.ExeatType.values:
        raise APIError(
            "validation_error",
            "Choose a valid exeat type.",
            fields={"exeat_type": ["Choose a valid exeat type."]},
        )

    departure = _parse_datetime(payload, "expected_departure", required=True)
    return_at = _parse_datetime(payload, "expected_return", required=True)

    destination = str(payload.get("destination") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    field_errors = {}
    if not destination:
        field_errors["destination"] = ["This field is required."]
    if not reason:
        field_errors["reason"] = ["This field is required."]
    if field_errors:
        raise APIError(
            "validation_error",
            "The request could not be processed.",
            fields=field_errors,
        )

    term = None
    term_id = payload.get("academic_term_id")
    if term_id:
        term = AcademicTerm.objects.filter(pk=term_id).first()
        if term is None:
            raise APIError(
                "validation_error",
                "The selected academic term was not found.",
                fields={"academic_term_id": ["Unknown academic term."]},
            )

    exeat = Exeat(
        student=student,
        exeat_type=exeat_type,
        expected_departure=departure,
        expected_return=return_at,
        destination=destination,
        reason=reason,
        academic_term=term,
        status=Exeat.Status.PENDING,
        created_by=request.api_user,
    )
    try:
        exeat.full_clean()
        exeat.save()
    except DjangoValidationError:
        raise
    enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.REQUESTED)
    return success(request, _detail_payload(exeat), status=201)


@api_endpoint(["POST"], roles=EXEAT_APPROVAL_ROLES, features=("exeat_gate",))
@idempotent
def approve_exeat(request, exeat_id):
    exeat = _get_exeat(request.api_user, exeat_id)
    payload = parse_json(request)
    if exeat.status != Exeat.Status.PENDING:
        raise Conflict(
            f"Only pending requests can be approved; this pass is "
            f"{exeat.get_status_display().lower()}."
        )
    if exeat.exeat_type == Exeat.ExeatType.EMERGENCY:
        raise Conflict(
            "General emergency passes must be approved through the emergency "
            "override endpoint so the 24-hour review is recorded.",
            code="override_required",
        )
    if not can_approve_exeat_check(request.api_user, exeat):
        raise Forbidden(
            "Only the student's Housemaster, the Senior Housemaster, or an "
            "administrator can approve this pass.",
            code="approval_forbidden",
        )

    remarks = str(payload.get("approval_remarks") or "").strip()
    exeat.approved_by_user = request.api_user
    current_hm = getattr(request.api_user, "housemaster", None)
    if current_hm is not None and (
        current_hm.is_senior_housemaster
        or current_hm.house_id == exeat.student.house_id
    ):
        exeat.approved_by = current_hm
    exeat.status = Exeat.Status.APPROVED
    if remarks:
        exeat.approval_remarks = remarks

    try:
        exeat.save()
        ExeatMovement.objects.get_or_create(exeat=exeat)
    except DjangoValidationError as error:
        raise APIError(
            "validation_error",
            " ".join(error.messages),
            fields=getattr(error, "message_dict", None),
            status=400,
        )
    except IntegrityError:
        raise Conflict(
            "This student already has an active approved pass. Record the "
            "return or cancel that pass before approving another.",
            code="active_pass_exists",
        )

    enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.APPROVED)
    return success(request, _detail_payload(exeat))


@api_endpoint(["POST"], roles=EXEAT_APPROVAL_ROLES, features=("exeat_gate",))
@idempotent
def reject_exeat(request, exeat_id):
    exeat = _get_exeat(request.api_user, exeat_id)
    payload = parse_json(request)
    if exeat.status != Exeat.Status.PENDING:
        raise Conflict(
            f"Only pending requests can be rejected; this pass is "
            f"{exeat.get_status_display().lower()}."
        )
    if not can_approve_exeat_check(request.api_user, exeat):
        raise Forbidden(
            "Only the student's Housemaster, the Senior Housemaster, or an "
            "administrator can reject this pass.",
            code="approval_forbidden",
        )
    remarks = str(
        payload.get("approval_remarks") or payload.get("reason") or ""
    ).strip()
    if not remarks:
        raise APIError(
            "validation_error",
            "A rejection reason is required so the student and guardian "
            "understand the decision.",
            fields={"approval_remarks": ["This field is required."]},
        )
    exeat.approval_remarks = remarks
    exeat.approved_by_user = request.api_user
    exeat.status = Exeat.Status.REJECTED
    exeat.save()
    enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.REJECTED)
    return success(request, _detail_payload(exeat))


@api_endpoint(["POST"], roles=EXEAT_APPROVAL_ROLES, features=("exeat_gate",))
@idempotent
def override_exeat(request, exeat_id):
    exeat = _get_exeat(request.api_user, exeat_id)
    payload = parse_json(request)
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise APIError(
            "validation_error",
            "An immediate reason is required for an emergency override.",
            fields={"reason": ["This field is required."]},
        )
    try:
        exeat, review = approve_with_override(
            exeat, actor=request.api_user, reason=reason
        )
    except ValueError as error:
        raise Conflict(str(error), code="override_rejected")
    data = _detail_payload(exeat)
    data["review"] = review_payload(review)
    return success(request, data)


@api_endpoint(["POST"], roles=EXEAT_APPROVAL_ROLES, features=("exeat_gate",))
@idempotent
def cancel_exeat_view(request, exeat_id):
    exeat = _get_exeat(request.api_user, exeat_id)
    if not can_approve_exeat_check(request.api_user, exeat):
        raise Forbidden(
            "Only the student's Housemaster, the Senior Housemaster, or an "
            "administrator can cancel this pass.",
            code="approval_forbidden",
        )
    try:
        exeat = cancel_exeat(exeat, actor=request.api_user)
    except ValueError as error:
        raise Conflict(str(error), code="invalid_state")
    return success(request, _detail_payload(exeat))


@api_endpoint(["POST"], roles=EXEAT_APPROVAL_ROLES, features=("exeat_gate",))
@idempotent
def reschedule_exeat_view(request, exeat_id):
    exeat = _get_exeat(request.api_user, exeat_id)
    if not can_approve_exeat_check(request.api_user, exeat):
        raise Forbidden(
            "Only the student's Housemaster, the Senior Housemaster, or an "
            "administrator can reschedule this pass.",
            code="approval_forbidden",
        )
    payload = parse_json(request)
    departure = _parse_datetime(payload, "expected_departure", required=True)
    return_at = _parse_datetime(payload, "expected_return", required=True)
    reason = str(payload.get("reason") or "").strip()
    try:
        reschedule_exeat(
            exeat,
            new_departure=departure,
            new_return=return_at,
            reason=reason,
            actor=request.api_user,
        )
    except ValueError as error:
        raise Conflict(str(error), code="invalid_state")
    exeat.refresh_from_db()
    return success(request, _detail_payload(exeat))


@api_endpoint(["POST"], roles=EXEAT_APPROVAL_ROLES, features=("exeat_gate",))
@idempotent
def reconcile_exeat(request, exeat_id):
    exeat = _get_exeat(request.api_user, exeat_id)
    if not can_approve_exeat_check(request.api_user, exeat):
        raise Forbidden(
            "Only the responsible Housemaster or Senior Housemaster can "
            "reconcile this pass.",
            code="approval_forbidden",
        )
    if exeat.status != Exeat.Status.APPROVED:
        raise Conflict("Only approved exeat passes can be reconciled.")
    if timezone.now() < exeat.expected_departure:
        raise Conflict(
            "A pass can only be reconciled after its scheduled departure time."
        )
    if ExeatReconciliation.objects.filter(exeat=exeat).exists():
        raise Conflict("This pass has already been reconciled.")
    movement = ExeatMovement.objects.filter(exeat=exeat).first()
    if movement and (movement.actual_departure or movement.actual_return):
        raise Conflict(
            "This pass already has gate movement records and cannot be reconciled."
        )

    payload = parse_json(request)
    outcome = str(payload.get("outcome") or "").strip()
    if outcome not in ExeatReconciliation.Outcome.values:
        raise APIError(
            "validation_error",
            "Choose a valid reconciliation outcome.",
            fields={"outcome": ["Choose a valid outcome."]},
        )
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise APIError(
            "validation_error",
            "Record the evidence or explanation for this reconciliation.",
            fields={"reason": ["This field is required."]},
        )
    actual_departure = _parse_datetime(payload, "actual_departure")
    actual_return = _parse_datetime(payload, "actual_return")

    reconciliation = ExeatReconciliation(
        exeat=exeat,
        outcome=outcome,
        actual_departure=actual_departure,
        actual_return=actual_return,
        reason=reason,
        reconciled_by=request.api_user,
    )
    try:
        reconciliation.full_clean()
        reconciliation.save()
    except DjangoValidationError:
        raise

    if outcome == ExeatReconciliation.Outcome.STUDENT_OUT:
        open_late_return_incident(
            exeat, detected_at=timezone.now(), source="reconciliation"
        )
        enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.DEPARTURE)
    elif outcome == ExeatReconciliation.Outcome.RETURNED:
        open_late_return_incident(
            exeat, actual_return=reconciliation.actual_return, source="reconciliation"
        )
        enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.RETURN)
    exeat.refresh_from_db()
    return success(request, _detail_payload(exeat))


@api_endpoint(
    ["POST"],
    roles=(StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER),
    features=("exeat_gate",),
)
@idempotent
def decide_review(request, exeat_id):
    exeat = _get_exeat(request.api_user, exeat_id)
    review = (
        ExeatReview.objects.filter(
            exeat=exeat, status=ExeatReview.Status.PENDING
        )
        .order_by("-created_at")
        .first()
    )
    if review is None:
        raise NotFound("This pass has no pending post-hoc review.")
    payload = parse_json(request)
    outcome = str(payload.get("outcome") or "").strip()
    notes = str(payload.get("notes") or "").strip()
    try:
        review = decide_exeat_review(
            review, reviewer=request.api_user, outcome=outcome, notes=notes
        )
    except ValueError as error:
        raise APIError(
            "validation_error",
            str(error),
            fields={"outcome": [str(error)]},
        )
    return success(request, review_payload(review))
