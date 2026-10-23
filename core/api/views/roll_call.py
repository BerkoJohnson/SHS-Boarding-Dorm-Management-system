"""Roll-call muster endpoints for house staff."""
from __future__ import annotations

from datetime import date

from django.db.models import Count, Q

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
    roll_call_entry_payload,
    roll_call_session_payload,
)
from core.models import (
    Dormitory,
    Exeat,
    House,
    RollCallEntry,
    RollCallSession,
)
from core.permissions import ROLL_CALL_ROLES, get_house_scope
from core.services.roll_call import (
    complete_roll_call_session,
    create_roll_call_session,
    mark_all_present,
    update_entry_status,
)

ORDERING_FIELDS = {
    "date",
    "created_at",
    "updated_at",
    "status",
    "session_type",
    "house__name",
}


def _parse_date(value):
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise APIError(
            "validation_error",
            "inspection_date must be an ISO date (YYYY-MM-DD).",
            fields={"inspection_date": ["Use YYYY-MM-DD."]},
        )


def _count_annotations():
    return {
        "roll_call_total_students": Count("entries"),
        "roll_call_present_count": Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.PRESENT)
        ),
        "roll_call_absent_count": Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.ABSENT)
        ),
        "roll_call_on_exeat_count": Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.ON_EXEAT)
        ),
        "roll_call_infirmary_count": Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.INFIRMARY)
        ),
        "roll_call_unmarked_count": Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.UNMARKED)
        ),
    }


def _session_queryset():
    return (
        RollCallSession.objects.select_related(
            "house",
            "dormitory",
            "academic_term",
            "academic_term__academic_year",
            "conducted_by",
            "conducted_by__user",
            "created_by",
        )
        .annotate(**_count_annotations())
        .all()
    )


def _apply_house_scope(user, queryset, lookup="house_id"):
    house = get_house_scope(user)
    if house is None:
        return queryset
    return queryset.filter(**{lookup: house.pk})


def _get_session(user, session_id):
    session = _session_queryset().filter(pk=session_id).first()
    if session is None:
        raise NotFound("That roll-call session was not found.")
    house = get_house_scope(user)
    if house is not None and session.house_id != house.pk:
        # Do not confirm the existence of another house's muster.
        raise NotFound("That roll-call session was not found.")
    return session


def _entries_for(session):
    entries = list(
        session.entries.select_related(
            "student",
            "student__house",
            "student__dormitory",
            "student__class_group",
        ).order_by("student__full_name")
    )
    student_ids = [entry.student_id for entry in entries]
    active_ids = set(
        Exeat.objects.filter(student_id__in=student_ids, is_active=True).values_list(
            "student_id", flat=True
        )
    )
    for entry in entries:
        entry.student._has_active_exeat = entry.student_id in active_ids
    return entries


def _detail_payload(request, session):
    payload = roll_call_session_payload(session)
    payload["entries"] = [
        roll_call_entry_payload(request, entry) for entry in _entries_for(session)
    ]
    return payload


def _resolve_house(user, payload):
    house_scope = get_house_scope(user)
    requested = payload.get("house_id")
    if house_scope is not None:
        if requested and str(requested) != str(house_scope.pk):
            raise Forbidden(
                "You can only run roll call for your assigned house.",
                code="house_scope_denied",
            )
        return house_scope
    if not requested:
        raise APIError(
            "validation_error",
            "A house_id is required for school-wide roll call.",
            fields={"house_id": ["This field is required."]},
        )
    house = House.objects.filter(pk=requested).first()
    if house is None:
        raise NotFound("That house was not found.")
    return house


def _validate_session_type(value):
    if value in (None, ""):
        return RollCallSession.SessionType.EVENING
    if value not in RollCallSession.SessionType.values:
        raise APIError(
            "validation_error",
            "Unknown roll-call session type.",
            fields={
                "session_type": [
                    f"Use one of: {', '.join(RollCallSession.SessionType.values)}."
                ]
            },
        )
    return value


@api_endpoint(
    ["GET", "POST"], roles=ROLL_CALL_ROLES, features=("roll_call",)
)
@idempotent
def sessions(request):
    user = request.api_user
    if request.method == "POST":
        return _start_session(request)

    queryset = _session_queryset()
    queryset = _apply_house_scope(user, queryset)

    status = (request.GET.get("status") or "").strip()
    if status:
        if status not in RollCallSession.Status.values:
            raise APIError(
                "validation_error",
                "Unknown roll-call status.",
                fields={
                    "status": [
                        f"Use one of: {', '.join(RollCallSession.Status.values)}."
                    ]
                },
            )
        queryset = queryset.filter(status=status)

    session_type = (request.GET.get("session_type") or "").strip()
    if session_type:
        if session_type not in RollCallSession.SessionType.values:
            raise APIError(
                "validation_error",
                "Unknown roll-call session type.",
                fields={
                    "session_type": [
                        f"Use one of: {', '.join(RollCallSession.SessionType.values)}."
                    ]
                },
            )
        queryset = queryset.filter(session_type=session_type)

    inspection_date = _parse_date(request.GET.get("date"))
    if inspection_date is not None:
        queryset = queryset.filter(date=inspection_date)

    house_id = (request.GET.get("house_id") or "").strip()
    if house_id:
        house = get_house_scope(user)
        if house is not None and str(house.pk) != house_id:
            raise Forbidden(
                "You can only view roll call for your assigned house.",
                code="house_scope_denied",
            )
        queryset = queryset.filter(house_id=house_id)

    queryset = apply_ordering(
        request, queryset, ORDERING_FIELDS, default="-date,-created_at"
    )
    items, pagination = paginate(request, queryset, roll_call_session_payload)
    return success(request, items, pagination=pagination)


def _start_session(request):
    user = request.api_user
    payload = parse_json(request)
    house = _resolve_house(user, payload)
    session_type = _validate_session_type(payload.get("session_type"))
    inspection_date = _parse_date(payload.get("inspection_date"))
    notes = str(payload.get("notes") or "").strip()

    dormitory = None
    dormitory_id = payload.get("dormitory_id")
    if dormitory_id:
        dormitory = Dormitory.objects.filter(
            pk=dormitory_id, house=house
        ).first()
        if dormitory is None:
            raise APIError(
                "validation_error",
                "That dormitory does not belong to the selected house.",
                fields={"dormitory_id": ["Select a dormitory in this house."]},
            )

    conducted_by = None
    housemaster = getattr(user, "housemaster", None)
    if housemaster is not None and housemaster.house_id == house.pk:
        conducted_by = housemaster

    session = create_roll_call_session(
        house=house,
        session_type=session_type,
        inspection_date=inspection_date,
        conducted_by=conducted_by,
        created_by=user,
        dormitory=dormitory,
        notes=notes,
    )
    reused = bool(getattr(session, "reused_existing", False))
    session = _get_session(user, session.pk)
    return success(
        request,
        _detail_payload(request, session),
        status=200 if reused else 201,
        meta={"reused_existing": reused},
    )


@api_endpoint(["GET"], roles=ROLL_CALL_ROLES, features=("roll_call",))
def session_detail(request, session_id):
    session = _get_session(request.api_user, session_id)
    return success(request, _detail_payload(request, session))


@api_endpoint(
    ["POST"], roles=ROLL_CALL_ROLES, features=("roll_call",)
)
@idempotent
def update_entry(request, session_id, entry_id):
    session = _get_session(request.api_user, session_id)
    entry = (
        RollCallEntry.objects.select_related(
            "student",
            "student__house",
            "student__dormitory",
            "student__class_group",
            "session",
        )
        .filter(pk=entry_id, session=session)
        .first()
    )
    if entry is None:
        raise NotFound("That roll-call entry was not found in this session.")

    payload = parse_json(request)
    status = payload.get("status")
    if status not in RollCallEntry.Status.values:
        raise APIError(
            "validation_error",
            "Unknown attendance status.",
            fields={
                "status": [
                    f"Use one of: {', '.join(RollCallEntry.Status.values)}."
                ]
            },
        )
    remarks = str(payload.get("remarks") or "").strip()
    if len(remarks) > 200:
        raise APIError(
            "validation_error",
            "Remarks must be 200 characters or fewer.",
            fields={"remarks": ["Keep remarks under 200 characters."]},
        )

    try:
        entry = update_entry_status(
            entry, status, remarks=remarks, updated_by=request.api_user
        )
    except ValueError as error:
        raise Conflict(str(error), code="roll_call_state_error")

    session = _get_session(request.api_user, session_id)
    entry = (
        RollCallEntry.objects.select_related(
            "student",
            "student__house",
            "student__dormitory",
            "student__class_group",
        )
        .filter(pk=entry.pk)
        .first()
    )
    active = Exeat.objects.filter(
        student_id=entry.student_id, is_active=True
    ).exists()
    entry.student._has_active_exeat = active
    return success(
        request,
        {
            "entry": roll_call_entry_payload(request, entry),
            "session": roll_call_session_payload(session),
        },
    )


@api_endpoint(
    ["POST"], roles=ROLL_CALL_ROLES, features=("roll_call",)
)
@idempotent
def mark_session_present(request, session_id):
    session = _get_session(request.api_user, session_id)
    try:
        updated = mark_all_present(session, corrected_by=request.api_user)
    except ValueError as error:
        raise Conflict(str(error), code="roll_call_state_error")
    session = _get_session(request.api_user, session_id)
    return success(
        request,
        {
            "updated": updated,
            "session": roll_call_session_payload(session),
        },
    )


@api_endpoint(
    ["POST"], roles=ROLL_CALL_ROLES, features=("roll_call",)
)
@idempotent
def complete_session(request, session_id):
    session = _get_session(request.api_user, session_id)
    already_completed = session.status == RollCallSession.Status.COMPLETED
    try:
        complete_roll_call_session(session)
    except ValueError as error:
        raise Conflict(str(error), code="roll_call_state_error")
    session = _get_session(request.api_user, session_id)
    return success(
        request,
        {
            "completed": True,
            "already_completed": already_completed,
            "session": roll_call_session_payload(session),
        },
    )
