"""Student registry, lookup, photos, and ID-card metadata."""
from __future__ import annotations

import mimetypes
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from django.http import FileResponse
from django.utils import timezone

from core.api.base import (
    APIError,
    NotFound,
    api_endpoint,
    apply_ordering,
    idempotent,
    paginate,
    parse_json,
    success,
)
from core.api.serializers import (
    class_group_payload,
    dormitory_payload,
    exeat_summary,
    house_payload,
    programme_payload,
    public_qr_url,
    room_payload,
    student_detail,
    student_summary,
)
from core.models import (
    AuditEvent,
    ClassGroup,
    Dormitory,
    DormitoryRoom,
    Exeat,
    Guardian,
    House,
    Programme,
    Student,
)
from core.permissions import STUDENT_MANAGEMENT_ROLES, STUDENT_RECORD_ROLES

STUDENT_FILTER_ORDERING = {
    "full_name",
    "admission_no",
    "created_at",
    "updated_at",
    "class_group__name",
    "house__name",
}


def _student_queryset():
    return Student.objects.select_related(
        "programme", "house", "dormitory", "room", "class_group"
    ).annotate(
        _has_active_exeat=Exists(
            Exeat.objects.filter(student=OuterRef("pk"), is_active=True)
        )
    )


def _scoped_students(user):
    from core.permissions import filter_queryset_by_house

    return filter_queryset_by_house(user, _student_queryset(), "house_id")


def _apply_filters(request, queryset):
    query = (request.GET.get("q") or "").strip()
    if query:
        queryset = queryset.filter(
            Q(full_name__icontains=query)
            | Q(admission_no__icontains=query)
            | Q(index_number__icontains=query)
        )
    for param, field in (
        ("house_id", "house_id"),
        ("dormitory_id", "dormitory_id"),
        ("class_group_id", "class_group_id"),
        ("programme_id", "programme_id"),
        ("status", "status"),
        ("residency", "residency"),
        ("gender", "gender"),
    ):
        value = (request.GET.get(param) or "").strip()
        if not value:
            continue
        if param.endswith("_id"):
            try:
                uuid.UUID(value)
            except ValueError:
                raise APIError(
                    "invalid_filter",
                    f"{param} must be a UUID.",
                )
        queryset = queryset.filter(**{field: value})

    has_active = (request.GET.get("has_active_exeat") or "").strip().lower()
    if has_active in {"1", "true", "yes"}:
        queryset = queryset.filter(_has_active_exeat=True)
    elif has_active in {"0", "false", "no"}:
        queryset = queryset.filter(_has_active_exeat=False)
    return queryset


@api_endpoint(["GET"], roles=STUDENT_RECORD_ROLES)
def students(request):
    queryset = _apply_filters(request, _scoped_students(request.api_user))
    queryset = apply_ordering(
        request,
        queryset,
        STUDENT_FILTER_ORDERING,
        default="full_name",
    )
    items, pagination = paginate(
        request, queryset, lambda student: student_summary(request, student)
    )
    return success(request, items, pagination=pagination)


@api_endpoint(["GET"], roles=STUDENT_RECORD_ROLES)
def student_detail_view(request, student_id):
    from core.permissions import filter_queryset_by_house

    student = (
        filter_queryset_by_house(
            request.api_user,
            Student.objects.select_related(
                "programme",
                "house",
                "dormitory",
                "room",
                "class_group",
                "guardian",
            ).annotate(
                _has_active_exeat=Exists(
                    Exeat.objects.filter(student=OuterRef("pk"), is_active=True)
                )
            ),
            "house_id",
        )
        .filter(pk=student_id)
        .first()
    )
    if student is None:
        raise NotFound("That student was not found.")

    recent = list(
        Exeat.objects.select_related("student", "student__house", "movement")
        .filter(student=student)
        .order_by("-created_at")[:5]
    )
    data = student_detail(request, student)
    data["recent_exeats"] = [exeat_summary(exeat) for exeat in recent]
    return success(request, data)


@api_endpoint(["POST"], roles=STUDENT_MANAGEMENT_ROLES)
@idempotent
def update_allocation(request, student_id):
    """Move a boarding student to a room without changing their house."""
    payload = parse_json(request)
    if "dormitory_id" not in payload or "room_id" not in payload:
        raise APIError(
            "validation_error",
            "Both dormitory_id and room_id are required.",
            fields={
                key: ["This field is required."]
                for key in ("dormitory_id", "room_id")
                if key not in payload
            },
        )
    try:
        dormitory_id = uuid.UUID(str(payload["dormitory_id"]))
        room_id = uuid.UUID(str(payload["room_id"]))
    except (TypeError, ValueError, AttributeError):
        raise APIError(
            "validation_error",
            "dormitory_id and room_id must be valid UUIDs.",
            fields={
                "dormitory_id": ["Use a valid UUID."],
                "room_id": ["Use a valid UUID."],
            },
        )

    with transaction.atomic():
        student = (
            _scoped_students(request.api_user)
            .select_for_update(of=("self",))
            .filter(pk=student_id)
            .first()
        )
        if student is None:
            raise NotFound("That student was not found.")
        if student.residency != Student.ResidencyStatus.BOARDING:
            raise APIError(
                "invalid_allocation",
                "Only boarding students can be assigned to a dormitory room.",
                status=409,
            )
        dormitory = Dormitory.objects.filter(pk=dormitory_id).first()
        room = (
            DormitoryRoom.objects.select_related("dormitory")
            .filter(pk=room_id)
            .first()
        )
        if dormitory is None or room is None:
            raise NotFound("The selected dormitory or room was not found.")
        if room.dormitory_id != dormitory.pk:
            raise APIError(
                "invalid_allocation",
                "The selected room does not belong to the selected dormitory.",
                fields={"room_id": ["Choose a room in the selected dormitory."]},
            )

        before = {
            "dormitory_id": str(student.dormitory_id) if student.dormitory_id else None,
            "room_id": str(student.room_id) if student.room_id else None,
        }
        student.dormitory = dormitory
        student.room = room
        try:
            student.save(update_fields=("dormitory", "room", "updated_at"))
        except ValidationError as error:
            raise APIError(
                "invalid_allocation",
                "The selected room cannot be assigned to this student.",
                fields=getattr(
                    error,
                    "message_dict",
                    {"placement": error.messages},
                ),
            )
        AuditEvent.record(
            actor=request.api_user,
            action=AuditEvent.Action.ADMIN_EDIT,
            summary=f"Changed dormitory room for {student.full_name}.",
            target_type="Student",
            target_id=student.pk,
            before=before,
            after={"dormitory_id": str(dormitory.pk), "room_id": str(room.pk)},
            metadata={"source": "mobile_student_allocation"},
        )
        updated = _student_queryset().filter(pk=student.pk).first()
    return success(request, student_detail(request, updated))


@api_endpoint(["GET"], roles=STUDENT_RECORD_ROLES)
def student_search(request):
    query = (request.GET.get("q") or "").strip()
    if len(query) < 2:
        return success(request, [])
    queryset = _scoped_students(request.api_user).filter(
        Q(full_name__icontains=query)
        | Q(admission_no__icontains=query)
        | Q(index_number__icontains=query)
    )[:20]
    return success(
        request,
        [student_summary(request, student) for student in queryset],
    )


@api_endpoint(["GET"], roles=STUDENT_RECORD_ROLES)
def student_lookup(request):
    """Resolve one student from a scanned UUID or a typed identifier."""
    raw = (request.GET.get("q") or "").strip()
    if not raw:
        raise APIError(
            "validation_error",
            "Provide a student QR token, admission number, or index number.",
            fields={"q": ["This field is required."]},
        )
    student = _resolve_student(raw, request.api_user)
    if student is None:
        raise NotFound("No student matches that code.")
    if _uuid_from_code(raw) is not None:
        from core.models import AuditEvent

        AuditEvent.record(
            actor=request.api_user,
            action=AuditEvent.Action.STUDENT_SCAN,
            target_type="Student",
            target_id=student.pk,
            summary=f"Scanned {student.full_name} ({student.admission_no})",
            metadata={"source": "api_student_lookup", "match_type": "student_qr"},
        )
    return success(request, student_detail(request, student))


def _resolve_student(raw, user):
    from core.permissions import filter_queryset_by_house

    queryset = filter_queryset_by_house(user, _student_queryset(), "house_id")
    candidate = _uuid_from_code(raw)
    if candidate is not None:
        student = queryset.filter(public_token=candidate).first()
        if student is not None:
            if student.public_token_expired:
                raise APIError(
                    "qr_expired",
                    "This student's ID QR code has expired. Ask the school office to reissue it.",
                    status=410,
                )
            return student
    return (
        queryset.filter(Q(admission_no__iexact=raw) | Q(index_number__iexact=raw))
        .order_by("full_name")
        .first()
    )


def _uuid_from_code(raw):
    """Extract the first UUID from a scanned value or URL."""
    import re

    match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        raw or "",
    )
    if not match:
        return None
    try:
        return uuid.UUID(match.group(0))
    except ValueError:
        return None


@api_endpoint(["GET"], roles=STUDENT_RECORD_ROLES)
def student_photo(request, student_id):
    from core.permissions import filter_queryset_by_house

    student = (
        filter_queryset_by_house(
            request.api_user, Student.objects.all(), "house_id"
        )
        .filter(pk=student_id)
        .first()
    )
    if student is None:
        raise NotFound("That student was not found.")
    if not student.photo:
        raise NotFound("No photo is available for this student.")
    response = FileResponse(
        student.photo.open("rb"),
        content_type=mimetypes.guess_type(student.photo.name)[0]
        or "application/octet-stream",
    )
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "private, max-age=300"
    return response


@api_endpoint(["GET"], roles=STUDENT_MANAGEMENT_ROLES, features=("student_id_cards",))
def student_id_card(request, student_id):
    student = Student.objects.select_related(
        "programme", "house", "dormitory", "class_group"
    ).filter(pk=student_id).first()
    if student is None:
        raise NotFound("That student was not found.")
    return success(
        request,
        {
            "student": student_summary(request, student),
            "public_token": str(student.public_token),
            "public_token_expires_at": (
                student.public_token_expires_at.isoformat()
                if student.public_token_expires_at
                else None
            ),
            "public_qr_url": public_qr_url(request, student),
            "layouts": ["classic", "crest", "modern"],
        },
    )


@api_endpoint(["POST"], roles=STUDENT_MANAGEMENT_ROLES, features=("student_id_cards",))
def rotate_public_token(request, student_id):
    student = Student.objects.filter(pk=student_id).first()
    if student is None:
        raise NotFound("That student was not found.")
    ttl_days = getattr(settings, "STUDENT_ID_PUBLIC_TOKEN_TTL_DAYS", 0)
    student.public_token = uuid.uuid4()
    student.public_token_expires_at = (
        timezone.now() + timezone.timedelta(days=ttl_days) if ttl_days > 0 else None
    )
    student.save(update_fields=["public_token", "public_token_expires_at", "updated_at"])
    return success(
        request,
        {
            "public_token": str(student.public_token),
            "public_token_expires_at": (
                student.public_token_expires_at.isoformat()
                if student.public_token_expires_at
                else None
            ),
            "public_qr_url": public_qr_url(request, student),
        },
    )


@api_endpoint(["GET"], roles=STUDENT_MANAGEMENT_ROLES)
def student_options(request):
    return success(
        request,
        {
            "programmes": [
                programme_payload(row) for row in Programme.objects.order_by("name")
            ],
            "houses": [house_payload(row) for row in House.objects.order_by("name")],
            "dormitories": [
                dormitory_payload(row)
                for row in Dormitory.objects.select_related("house").order_by("name")
            ],
            "rooms": [
                room_payload(row)
                for row in DormitoryRoom.objects.select_related("dormitory").order_by(
                    "dormitory__name", "name"
                )
            ],
            "class_groups": [
                class_group_payload(row)
                for row in ClassGroup.objects.select_related("programme").order_by(
                    "name"
                )
            ],
            "guardians": [
                {
                    "id": str(row.pk),
                    "full_name": row.full_name,
                    "phone_number": row.phone_number,
                }
                for row in Guardian.objects.order_by("full_name")
            ],
            "residency_statuses": [
                {"value": value, "label": label}
                for value, label in Student.ResidencyStatus.choices
            ],
            "enrollment_statuses": [
                {"value": value, "label": label}
                for value, label in Student.EnrollmentStatus.choices
            ],
            "genders": [
                {"value": value, "label": label}
                for value, label in Student.Gender.choices
            ],
        },
    )


@api_endpoint(["GET"], roles=STUDENT_MANAGEMENT_ROLES)
def allocation_options(request):
    """Return only dormitory rooms needed by the mobile allocation workflow."""
    rooms = DormitoryRoom.objects.select_related("dormitory").order_by(
        "dormitory__name", "name"
    )
    return success(request, {"rooms": [room_payload(room) for room in rooms]})
