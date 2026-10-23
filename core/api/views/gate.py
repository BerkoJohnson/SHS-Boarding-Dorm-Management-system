"""Gate-side pass verification driven by a scanned card or typed identifier."""
from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from core.api.base import (
    APIError,
    Conflict,
    NotFound,
    api_endpoint,
    idempotent,
    iso,
    parse_json,
    success,
)
from core.api.serializers import (
    exeat_detail,
    exeat_summary,
    movement_payload,
    student_summary,
)
from core.api.views.students import _uuid_from_code
from core.models import (
    AuditEvent,
    Exeat,
    ExeatReconciliation,
    SMSDelivery,
    Student,
)
from core.permissions import GATE_ROLES
from core.services.gate_movements import record_gate_departure, record_gate_return
from core.services.late_returns import open_late_return_incident
from core.services.sms import enqueue_exeat_event_sms


def _exeat_queryset():
    return Exeat.objects.select_related(
        "student",
        "student__house",
        "student__dormitory",
        "movement",
        "reconciliation",
        "approved_by",
        "approved_by_user",
    )


def _reconciliation(exeat):
    return getattr(exeat, "reconciliation", None)


def _movement_state(exeat):
    if exeat is None:
        return "none"
    movement = getattr(exeat, "movement", None)
    reconciliation = _reconciliation(exeat)
    if reconciliation and reconciliation.outcome == "student_out":
        return "out"
    if movement and movement.actual_return:
        return "returned"
    if movement and movement.actual_departure:
        return "out"
    return "awaiting_departure"


def _gate_flags(exeat):
    now = timezone.now()
    movement = getattr(exeat, "movement", None)
    reconciliation = _reconciliation(exeat)
    can_depart = bool(
        exeat
        and exeat.status == Exeat.Status.APPROVED
        and not reconciliation
        and not (movement and movement.actual_departure)
        and now >= exeat.expected_departure
    )
    can_return = bool(
        exeat
        and exeat.status == Exeat.Status.APPROVED
        and movement
        and movement.actual_departure
        and not movement.actual_return
    )
    warnings = []
    if exeat:
        if exeat.status != Exeat.Status.APPROVED:
            warnings.append(
                f"This pass is {exeat.get_status_display().lower()} and cannot be used at the gate."
            )
        if reconciliation:
            warnings.append("This pass has already been reconciled.")
        if movement and movement.actual_return:
            warnings.append("The student has already returned from this pass.")
        if not reconciliation and now < exeat.expected_departure:
            warnings.append(
                "Departure opens at "
                + timezone.localtime(exeat.expected_departure).strftime(
                    "%d %b %Y, %H:%M"
                )
                + "."
            )
        if exeat.expected_return and now > exeat.expected_return and not (
            movement and movement.actual_return
        ):
            warnings.append("This pass is overdue.")
    return can_depart, can_return, warnings


def _payload_for_exeat(request, exeat, match_type):
    can_depart, can_return, warnings = _gate_flags(exeat)
    return {
        "match": match_type,
        "student": student_summary(request, exeat.student),
        "exeat": exeat_detail(exeat),
        "movement_state": _movement_state(exeat),
        "can_depart": can_depart,
        "can_return": can_return,
        "warnings": warnings,
    }


def _audit_scan(request, student, *, match_type, source):
    AuditEvent.record(
        actor=request.api_user,
        action=AuditEvent.Action.STUDENT_SCAN,
        target_type="Student",
        target_id=student.pk,
        summary=f"Scanned {student.full_name} ({student.admission_no})",
        metadata={"source": source, "match_type": match_type},
    )


def _active_exeat(student):
    now = timezone.now()
    return (
        _exeat_queryset()
        .filter(student=student, status=Exeat.Status.APPROVED)
        .filter(
            Q(expected_departure__lte=now, expected_return__gt=now)
            | Q(movement__actual_departure__isnull=False)
        )
        .filter(Q(movement__isnull=True) | Q(movement__actual_return__isnull=True))
        .order_by("-expected_departure", "-created_at")
        .first()
    )


@api_endpoint(["POST"], roles=GATE_ROLES, features=("exeat_gate",))
def verify(request):
    payload = parse_json(request)
    code = str(payload.get("code") or "").strip()
    if not code:
        raise APIError(
            "validation_error",
            "Scan a card or enter an exeat number, admission number, or index number.",
            fields={"code": ["This field is required."]},
        )
    if len(code) < 2:
        raise APIError(
            "query_too_short",
            "Enter at least two characters.",
            status=400,
        )

    # 1. Student ID QR (a UUID, optionally embedded in a public verification URL).
    token = _uuid_from_code(code)
    if token is not None:
        student = Student.objects.filter(public_token=token).first()
        if student is not None:
            if student.public_token_expired:
                raise APIError(
                    "qr_expired",
                    "This student's ID QR code has expired. Ask the school office to reissue it.",
                    status=410,
                )
            exeat = _active_exeat(student)
            recent = list(
                _exeat_queryset().filter(student=student).order_by("-created_at")[:5]
            )
            can_depart, can_return, warnings = _gate_flags(exeat)
            _audit_scan(request, student, match_type="student_qr", source="api_gate_verify")
            return success(
                request,
                {
                    "match": "student",
                    "student": student_summary(request, student),
                    "exeat": exeat_detail(exeat) if exeat else None,
                    "exeats": [exeat_summary(row) for row in recent],
                    "movement_state": _movement_state(exeat),
                    "can_depart": can_depart,
                    "can_return": can_return,
                    "warnings": warnings,
                },
            )

    # 2. Exact pass number, regardless of status, so the officer can see why a
    #    pending or cancelled pass cannot be used.
    exeat = _exeat_queryset().filter(exeat_number__iexact=code).first()
    if exeat is not None:
        return success(request, _payload_for_exeat(request, exeat, "exeat_number"))

    # 3. Admission or index number.
    student = (
        Student.objects.select_related("programme", "house", "dormitory", "class_group")
        .filter(Q(admission_no__iexact=code) | Q(index_number__iexact=code))
        .first()
    )
    if student is not None:
        exeat = _active_exeat(student)
        can_depart, can_return, warnings = _gate_flags(exeat)
        _audit_scan(
            request, student, match_type="admission_number", source="api_gate_verify"
        )
        return success(
            request,
            {
                "match": "student",
                "student": student_summary(request, student),
                "exeat": exeat_detail(exeat) if exeat else None,
                "exeats": [],
                "movement_state": _movement_state(exeat),
                "can_depart": can_depart,
                "can_return": can_return,
                "warnings": warnings,
            },
        )

    # 4. Partial pass number: return a ranked disambiguation list, never pick
    #    a pass on the officer's behalf.
    now = timezone.now()
    candidates = list(
        _exeat_queryset()
        .filter(
            exeat_number__icontains=code,
            status=Exeat.Status.APPROVED,
        )
        .filter(Q(movement__isnull=True) | Q(movement__actual_return__isnull=True))
    )
    if candidates:
        candidates.sort(key=lambda row: _gate_sort_key(row, now))
        return success(
            request,
            {
                "match": "ambiguous",
                "student": None,
                "exeat": None,
                "candidates": [exeat_summary(row) for row in candidates[:20]],
                "warnings": [
                    "Several passes match. Scan the student card or choose the pass explicitly."
                ],
            },
        )

    return success(
        request,
        {
            "match": "none",
            "student": None,
            "exeat": None,
            "warnings": ["No student or pass matches that code."],
        },
    )


def _gate_sort_key(exeat, now):
    movement = getattr(exeat, "movement", None)
    if movement and movement.actual_departure and not movement.actual_return:
        return (0, -movement.actual_departure.timestamp())
    if exeat.expected_departure <= now <= exeat.expected_return:
        return (1, exeat.expected_return.timestamp())
    if now < exeat.expected_departure:
        return (2, exeat.expected_departure.timestamp())
    return (3, -exeat.expected_return.timestamp())


def _gate_exeat(payload):
    exeat_id = payload.get("exeat_id") or payload.get("exeat")
    if not exeat_id:
        raise APIError(
            "validation_error",
            "An exeat id is required.",
            fields={"exeat_id": ["This field is required."]},
        )
    exeat = _exeat_queryset().filter(pk=exeat_id).first()
    if exeat is None:
        raise NotFound("That exeat pass was not found.")
    return exeat


def _movement_data(movement):
    return {
        "actual_departure": iso(getattr(movement, "actual_departure", None)),
        "actual_return": iso(getattr(movement, "actual_return", None)),
        "departure_recorded_by": getattr(movement, "departure_recorded_by", "") or "",
        "return_recorded_by": getattr(movement, "return_recorded_by", "") or "",
    }


@api_endpoint(["POST"], roles=GATE_ROLES, features=("exeat_gate",))
@idempotent
def depart(request):
    payload = parse_json(request)
    exeat = _gate_exeat(payload)
    actor = request.api_user
    recorded_by = actor.get_full_name() or actor.get_username()
    try:
        movement, recorded, reconciled = record_gate_departure(
            exeat, actor=actor, recorded_by=recorded_by
        )
    except ValueError as error:
        raise Conflict(str(error), code="gate_state_error")
    if not recorded:
        raise Conflict(
            "Gate departure is already recorded for this pass.",
            code="movement_recorded",
        )
    enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.DEPARTURE)
    exeat.refresh_from_db()
    return success(
        request,
        {
            "recorded": True,
            "exeat": exeat_detail(exeat),
            "movement": _movement_data(movement),
            "roll_call_reconciled": reconciled,
        },
    )


@api_endpoint(["POST"], roles=GATE_ROLES, features=("exeat_gate",))
@idempotent
def record_return(request):
    payload = parse_json(request)
    exeat = _gate_exeat(payload)
    actor = request.api_user
    recorded_by = actor.get_full_name() or actor.get_username()
    try:
        movement, recorded = record_gate_return(
            exeat, actor=actor, recorded_by=recorded_by
        )
    except ValueError as error:
        raise Conflict(str(error), code="gate_state_error")
    if movement is None:
        raise Conflict(
            "A return cannot be recorded without a confirmed departure. "
            "Use the reconciliation workflow if the departure was missed.",
            code="return_without_departure",
        )
    if not recorded:
        raise Conflict(
            "Gate return is already recorded for this pass.",
            code="movement_recorded",
        )
    source = (
        "gate reconciliation"
        if isinstance(movement, ExeatReconciliation)
        else "gate movement"
    )
    open_late_return_incident(
        exeat, actual_return=getattr(movement, "actual_return", None), source=source
    )
    enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.RETURN)
    exeat.refresh_from_db()
    return success(
        request,
        {
            "recorded": True,
            "exeat": exeat_detail(exeat),
            "movement": _movement_data(movement),
        },
    )
