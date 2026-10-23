"""Explicit field allowlists for every API payload.

Nothing here uses model introspection or wildcard serialization: adding a
column to a model must never silently expose it through the API.
"""
from __future__ import annotations

from django.urls import reverse

from core.api.auth import capabilities
from core.api.base import iso
from core.models import SchoolConfiguration


def school_payload():
    configuration = SchoolConfiguration.get_solo()
    return {
        "name": configuration.name,
        "short_name": configuration.short_name,
        "address": configuration.address,
        "contact_number": configuration.contact_number,
        "logo_url": configuration.logo.url if configuration.logo else None,
    }


def user_payload(user):
    profile = getattr(user, "profile", None)
    return {
        "id": str(user.pk),
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.get_full_name() or user.username,
        "email": user.email,
        "phone_number": getattr(profile, "phone_number", "") or "",
        "role": getattr(profile, "role", ""),
        "role_display": profile.get_role_display() if profile else "",
        "is_active": user.is_active,
        "capabilities": capabilities(user),
    }


def term_payload(term):
    return {
        "id": str(term.pk),
        "academic_year_id": str(term.academic_year_id),
        "academic_year": term.academic_year.name,
        "term": term.term,
        "term_display": term.get_term_display(),
        "level": term.level,
        "level_display": term.get_level_display(),
        "start_date": iso(term.start_date),
        "end_date": iso(term.end_date),
        "is_current": term.is_current,
    }


def year_payload(year):
    return {
        "id": str(year.pk),
        "name": year.name,
        "start_date": iso(year.start_date),
        "end_date": iso(year.end_date),
        "is_current": year.is_current,
    }


def house_payload(house):
    return {
        "id": str(house.pk),
        "name": house.name,
        "motto": house.motto or "",
        "display_order": house.display_order,
    }


def dormitory_payload(dormitory):
    return {
        "id": str(dormitory.pk),
        "house_id": str(dormitory.house_id),
        "name": dormitory.name,
        "gender": dormitory.gender,
        "gender_display": dormitory.get_gender_display(),
        "capacity": dormitory.capacity,
    }


def room_payload(room):
    return {
        "id": str(room.pk),
        "dormitory_id": str(room.dormitory_id),
        "name": room.name,
        "capacity": room.capacity,
    }


def programme_payload(programme):
    return {
        "id": str(programme.pk),
        "name": programme.name,
        "code": programme.code or "",
    }


def class_group_payload(class_group):
    return {
        "id": str(class_group.pk),
        "name": class_group.name,
        "programme_id": str(class_group.programme_id) if class_group.programme_id else None,
    }


def staff_payload(user):
    profile = getattr(user, "profile", None)
    housemaster = getattr(user, "housemaster", None)
    house = getattr(housemaster, "house", None)
    return {
        "id": str(user.pk),
        "full_name": user.get_full_name() or user.username,
        "role": getattr(profile, "role", ""),
        "role_display": profile.get_role_display() if profile else "",
        "house_id": str(house.pk) if house else None,
        "house_name": house.name if house else None,
    }


def photo_url(request, student):
    if not student.photo:
        return None
    return request.build_absolute_uri(
        reverse("core_api:student_photo", args=[student.pk])
    )


def public_qr_url(request, student):
    return request.build_absolute_uri(
        reverse("core:public_student_exeat", args=[student.public_token])
    )


def student_summary(request, student):
    has_active_exeat = getattr(student, "_has_active_exeat", None)
    if has_active_exeat is None:
        has_active_exeat = student.exeats.filter(is_active=True).exists()
    return {
        "id": str(student.pk),
        "admission_no": student.admission_no,
        "index_number": student.index_number,
        "full_name": student.full_name,
        "gender": student.gender,
        "residency": student.residency,
        "status": student.status,
        "house": house_payload(student.house) if student.house else None,
        "dormitory": (
            {"id": str(student.dormitory.pk), "name": student.dormitory.name}
            if student.dormitory
            else None
        ),
        "class_group": (
            {"id": str(student.class_group.pk), "name": student.class_group.name}
            if student.class_group
            else None
        ),
        "photo_url": photo_url(request, student),
        "is_active_exeat": has_active_exeat,
    }


def student_detail(request, student):
    payload = student_summary(request, student)
    payload.update(
        {
            "date_of_birth": iso(student.date_of_birth),
            "programme": programme_payload(student.programme),
            "room": (
                {"id": str(student.room.pk), "name": student.room.name}
                if student.room
                else None
            ),
            "guardian": (
                {
                    "id": str(student.guardian.pk),
                    "full_name": student.guardian.full_name,
                    "phone_number": student.guardian.phone_number,
                    "alt_phone_number": student.guardian.alt_phone_number or "",
                }
                if student.guardian
                else None
            ),
            "completed_on": iso(student.completed_on),
            "public_token": str(student.public_token),
            "public_token_expires_at": iso(student.public_token_expires_at),
            "public_qr_url": public_qr_url(request, student),
            "created_at": iso(student.created_at),
            "updated_at": iso(student.updated_at),
        }
    )
    return payload


def movement_payload(exeat):
    movement = getattr(exeat, "movement", None)
    if movement is None:
        return None
    return {
        "actual_departure": iso(movement.actual_departure),
        "actual_return": iso(movement.actual_return),
        "departure_recorded_by": movement.departure_recorded_by or "",
        "return_recorded_by": movement.return_recorded_by or "",
    }


def exeat_summary(exeat):
    return {
        "id": str(exeat.pk),
        "exeat_number": exeat.exeat_number,
        "status": exeat.status,
        "status_display": exeat.get_status_display(),
        "exeat_type": exeat.exeat_type,
        "exeat_type_display": exeat.get_exeat_type_display(),
        "expected_departure": iso(exeat.expected_departure),
        "expected_return": iso(exeat.expected_return),
        "is_active": exeat.is_active,
        "is_emergency_override": exeat.is_emergency_override,
        "student": {
            "id": str(exeat.student_id),
            "full_name": exeat.student.full_name,
            "admission_no": exeat.student.admission_no,
            "house_id": str(exeat.student.house_id) if exeat.student.house_id else None,
        },
        "movement": movement_payload(exeat),
    }


def exeat_detail(exeat):
    payload = exeat_summary(exeat)
    approver = None
    if exeat.approved_by_id:
        approver = exeat.approved_by.full_name
    elif exeat.approved_by_user_id:
        approver = (
            exeat.approved_by_user.get_full_name()
            or exeat.approved_by_user.get_username()
        )
    payload.update(
        {
            "reason": exeat.reason,
            "destination": exeat.destination,
            "approval_remarks": exeat.approval_remarks or "",
            "approved_by_name": approver,
            "decided_at": iso(exeat.decided_at),
            "created_at": iso(exeat.created_at),
            "updated_at": iso(exeat.updated_at),
            "academic_term_id": (
                str(exeat.academic_term_id) if exeat.academic_term_id else None
            ),
            "reconciliation": reconciliation_payload(exeat),
        }
    )
    return payload


def review_payload(review):
    return {
        "id": str(review.pk),
        "review_type": review.review_type,
        "review_type_display": review.get_review_type_display(),
        "status": review.status,
        "status_display": review.get_status_display(),
        "outcome": review.outcome,
        "outcome_display": review.get_outcome_display() if review.outcome else "",
        "reason": review.reason,
        "review_notes": review.review_notes,
        "due_at": iso(review.due_at),
        "reviewed_at": iso(review.reviewed_at),
    }


def schedule_change_payload(change):
    return {
        "id": str(change.pk),
        "change_type": change.change_type,
        "change_type_display": change.get_change_type_display(),
        "previous_departure": iso(change.previous_departure),
        "previous_return": iso(change.previous_return),
        "new_departure": iso(change.new_departure),
        "new_return": iso(change.new_return),
        "reason": change.reason,
        "created_at": iso(change.created_at),
    }


def reconciliation_payload(exeat):
    reconciliation = getattr(exeat, "reconciliation", None)
    if reconciliation is None:
        return None
    return {
        "outcome": reconciliation.outcome,
        "outcome_display": reconciliation.get_outcome_display(),
        "actual_departure": iso(reconciliation.actual_departure),
        "actual_return": iso(reconciliation.actual_return),
        "reason": reconciliation.reason,
        "reconciled_at": iso(reconciliation.created_at),
    }


def reference_payload(*, terms, years, houses, dormitories, rooms, programmes, classes):
    return {
        "academic_terms": [term_payload(term) for term in terms],
        "academic_years": [year_payload(year) for year in years],
        "houses": [house_payload(house) for house in houses],
        "dormitories": [dormitory_payload(dormitory) for dormitory in dormitories],
        "rooms": [room_payload(room) for room in rooms],
        "programmes": [programme_payload(programme) for programme in programmes],
        "class_groups": [class_group_payload(group) for group in classes],
    }


def roll_call_entry_payload(request, entry):
    return {
        "id": str(entry.pk),
        "student": student_summary(request, entry.student),
        "status": entry.status,
        "status_display": entry.get_status_display(),
        "status_source": entry.status_source,
        "remarks": entry.remarks or "",
        "gate_conflict": (
            {
                "noted_at": iso(entry.gate_conflict_at),
                "note": entry.gate_conflict_note or "",
                "reviewed_at": iso(entry.gate_conflict_reviewed_at),
            }
            if entry.gate_conflict_at
            else None
        ),
        "updated_at": iso(entry.updated_at),
    }


def roll_call_session_payload(session):
    conducted_by = getattr(session, "conducted_by", None)
    created_by = getattr(session, "created_by", None)
    return {
        "id": str(session.pk),
        "house": house_payload(session.house),
        "dormitory": (
            dormitory_payload(session.dormitory) if session.dormitory else None
        ),
        "academic_term": (
            term_payload(session.academic_term) if session.academic_term else None
        ),
        "session_type": session.session_type,
        "session_type_display": session.get_session_type_display(),
        "date": iso(session.date),
        "status": session.status,
        "status_display": session.get_status_display(),
        "conducted_by": (
            staff_payload(conducted_by.user) if conducted_by else None
        ),
        "created_by": staff_payload(created_by) if created_by else None,
        "notes": session.notes or "",
        "counts": {
            "total_students": session.total_students,
            "present": session.present_count,
            "absent": session.absent_count,
            "on_exeat": session.on_exeat_count,
            "infirmary": session.infirmary_count,
            "unmarked": session.unmarked_count,
            "accounted": session.accounted_count,
        },
        "attendance_rate": session.attendance_rate,
        "expires_at": iso(session.expires_at),
        "is_expired": session.is_expired,
        "created_at": iso(session.created_at),
        "updated_at": iso(session.updated_at),
    }


def staff_notification_payload(notification):
    return {
        "id": f"staff:{notification.pk}",
        "source": "staff",
        "kind": notification.kind,
        "kind_display": notification.get_kind_display(),
        "title": notification.title,
        "message": notification.message,
        "url": notification.url or "",
        "read_at": iso(notification.read_at),
        "is_read": notification.read_at is not None,
        "created_at": iso(notification.created_at),
    }


def roll_call_notification_payload(notification):
    return {
        "id": f"roll_call:{notification.pk}",
        "source": "roll_call",
        "kind": "roll_call",
        "kind_display": "Roll call",
        "title": notification.title,
        "message": notification.message,
        "url": "",
        "read_at": iso(notification.read_at),
        "is_read": notification.read_at is not None,
        "created_at": iso(notification.created_at),
    }
