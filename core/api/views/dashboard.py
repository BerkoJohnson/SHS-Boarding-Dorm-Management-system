"""Role-aware home summary for the staff app."""
from __future__ import annotations

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.utils import timezone

from core.api.base import api_endpoint, iso, success
from core.api.serializers import (
    exeat_summary,
    house_payload,
    roll_call_session_payload,
    user_payload,
)
from core.api.views.notifications import recent_notifications, unread_counts
from core.models import (
    Exeat,
    ExeatMovement,
    RollCallEntry,
    RollCallSession,
    Student,
    get_approvable_pending_for_user,
    get_user_profile,
)
from core.permissions import (
    ADMIN_ROLES,
    EXEAT_APPROVAL_ROLES,
    EXEAT_ROLES,
    GATE_ROLES,
    ROLL_CALL_ROLES,
    STUDENT_RECORD_ROLES,
    filter_exeats_for_user,
    get_house_scope,
)
from tenancy.entitlements import feature_enabled


def _exeat_scope(user, role):
    if user.is_superuser or role in GATE_ROLES:
        return Exeat.objects.all()
    if role in EXEAT_ROLES:
        try:
            return filter_exeats_for_user(user, Exeat.objects.all())
        except DjangoPermissionDenied:
            return None
    return None


@api_endpoint(["GET"])
def dashboard(request):
    user = request.api_user
    profile = get_user_profile(user)
    role = profile.role if profile else ""
    now = timezone.now()
    today = timezone.localdate()

    data = {
        "generated_at": iso(now),
        "user": user_payload(user),
        "notifications": {
            "unread": unread_counts(user),
            "recent": recent_notifications(user, limit=5),
        },
    }

    # House scope is only meaningful for roles that operate on a single house.
    scoped_roles = set(STUDENT_RECORD_ROLES) | set(ROLL_CALL_ROLES)
    is_school_wide = user.is_superuser or role in ADMIN_ROLES
    house = None
    if not is_school_wide and role in scoped_roles:
        try:
            house = get_house_scope(user)
        except DjangoPermissionDenied:
            house = None
    data["scope"] = {
        "school_wide": is_school_wide,
        "house": house_payload(house) if house else None,
    }

    exeats = _exeat_scope(user, role)
    if exeats is not None and feature_enabled("exeat_gate"):
        out_on_pass = ExeatMovement.objects.filter(
            exeat__in=exeats,
            actual_departure__isnull=False,
            actual_return__isnull=True,
        )
        data["exeats"] = {
            "active": exeats.filter(is_active=True).count(),
            "on_pass": out_on_pass.count(),
            "overdue": out_on_pass.filter(exeat__expected_return__lt=now).count(),
            "today_departures": exeats.filter(
                expected_departure__date=today
            ).count(),
            "today_returns": exeats.filter(expected_return__date=today).count(),
            "upcoming": exeats.filter(
                status=Exeat.Status.APPROVED, expected_departure__gte=now
            ).count(),
        }
        if role in EXEAT_APPROVAL_ROLES or user.is_superuser:
            pending = get_approvable_pending_for_user(user)
            data["exeats"]["pending_approvals"] = pending.count()
            data["exeats"]["latest_pending"] = [
                exeat_summary(row) for row in pending[:5]
            ]
        if role in GATE_ROLES or user.is_superuser:
            data["gate"] = {
                "on_pass": out_on_pass.count(),
                "overdue": out_on_pass.filter(
                    exeat__expected_return__lt=now
                ).count(),
            }

    if (is_school_wide or house is not None) and (
        role in STUDENT_RECORD_ROLES or user.is_superuser
    ):
        students = Student.objects.all()
        if house is not None:
            students = students.filter(house=house)
        data["students"] = {
            "total": students.count(),
            "enrolled": students.filter(
                status=Student.EnrollmentStatus.ENROLLED
            ).count(),
            "boarding": students.filter(
                residency=Student.ResidencyStatus.BOARDING
            ).exclude(status=Student.EnrollmentStatus.COMPLETED).count(),
            "on_exeat": students.filter(exeats__is_active=True)
            .distinct()
            .count(),
        }

    if (
        (is_school_wide or house is not None)
        and (role in ROLL_CALL_ROLES or user.is_superuser)
        and feature_enabled("roll_call")
    ):
        sessions = RollCallSession.objects.all()
        if house is not None:
            sessions = sessions.filter(house=house)
        in_progress = sessions.filter(status=RollCallSession.Status.IN_PROGRESS)
        latest = sessions.order_by("-date", "-created_at").first()
        data["roll_call"] = {
            "in_progress": in_progress.count(),
            "unmarked": RollCallEntry.objects.filter(
                session__in=in_progress,
                status=RollCallEntry.Status.UNMARKED,
            ).count(),
            "latest_session": (
                roll_call_session_payload(latest) if latest else None
            ),
        }

    return success(request, data)
