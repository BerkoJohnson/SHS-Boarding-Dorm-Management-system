"""
Role-based authorization and permission decorators for Eden High School staff.

Defines role groupings, the @role_required decorator, and helper checks
for exeat approval and gate logging. All views must use @role_required
to enforce backend access control in addition to template-level UI hiding.
"""
from functools import wraps
from django.core.exceptions import PermissionDenied
from django.contrib import messages
from django.shortcuts import redirect
from core.models import StaffRole, get_user_profile


# ---------------------------------------------------------------------------
# Role Groupings — Single source of truth for access tiers
# ---------------------------------------------------------------------------
# These constants define which StaffRole values are allowed for each
# functional area. Templates and views both reference these via the
# context processor (user_profile.role) so changes here cascade everywhere.

# Administration: System config, reports, academic term management
ADMIN_ROLES = (StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER)

# Roll Call: Conducting dormitory attendance sessions
ROLL_CALL_ROLES = (
    StaffRole.ADMIN,
    StaffRole.SENIOR_HOUSEMASTER,
    StaffRole.HOUSEMASTER,
)

# Gate Operations: Logging departures/returns at the security checkpoint
GATE_ROLES = (
    StaffRole.ADMIN,
    StaffRole.GATE_OFFICER,
)

# Exeat Management: Creating and viewing exeat requests
EXEAT_ROLES = (
    StaffRole.ADMIN,
    StaffRole.SENIOR_HOUSEMASTER,
    StaffRole.HOUSEMASTER,
    StaffRole.HOUSE_TUTOR,
    StaffRole.GATE_OFFICER,
)

# Exeat Approval: Only senior staff can approve/reject
EXEAT_APPROVAL_ROLES = (
    StaffRole.ADMIN,
    StaffRole.SENIOR_HOUSEMASTER,
    StaffRole.HOUSEMASTER,
)

# Exeat Creation: Tutors, Housemasters and Admins (Gate Officers cannot create exeats)
EXEAT_CREATE_ROLES = (
    StaffRole.ADMIN,
    StaffRole.SENIOR_HOUSEMASTER,
    StaffRole.HOUSEMASTER,
    StaffRole.HOUSE_TUTOR,
)

# Conduct & Discipline: Recording infractions
CONDUCT_ROLES = (
    StaffRole.ADMIN,
    StaffRole.SENIOR_HOUSEMASTER,
    StaffRole.HOUSEMASTER,
    StaffRole.HOUSE_TUTOR,
)

# Reports are restricted to administrators, the Senior Housemaster, and
# assigned Housemasters. Housemasters see only their assigned house.
REPORT_ROLES = (
    StaffRole.ADMIN,
    StaffRole.SENIOR_HOUSEMASTER,
    StaffRole.HOUSEMASTER,
)


def get_house_scope(user):
    """Return the user's house, or None for school-wide administrators."""
    if not user or not user.is_authenticated:
        raise PermissionDenied("An authenticated staff account is required.")

    profile = get_user_profile(user)
    if user.is_superuser or (profile and profile.role in ADMIN_ROLES):
        return None

    housemaster = getattr(user, "housemaster", None)
    if not housemaster or not housemaster.house_id:
        raise PermissionDenied(
            "This account must be assigned to a house before accessing house operations."
        )
    return housemaster.house


def filter_queryset_by_house(user, queryset, house_lookup):
    """Restrict a queryset by its house relation for non-privileged staff."""
    house = get_house_scope(user)
    if house is None:
        return queryset
    return queryset.filter(**{house_lookup: house.pk})


def role_required(*allowed_roles):
    """
    Decorator enforcing that the authenticated user possesses one of the allowed institutional roles.
    Superusers bypass all role checks.

    Usage:
        @role_required(StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER)
        def my_view(request): ...
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('core:login')
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            profile = get_user_profile(request.user)
            if profile and profile.role in allowed_roles:
                return view_func(request, *args, **kwargs)

            messages.error(
                request,
                "Access denied: You do not have permission to access this area."
            )
            return redirect('core:dashboard')
        return _wrapped_view
    return decorator


def can_approve_exeat_check(user, exeat) -> bool:
    """
    Checks if a user has jurisdictional authority to approve or reject a specific exeat.
    
    Rules:
      - Superusers and Senior Housemasters can approve ANY student's exeat.
      - Regular Housemasters can ONLY approve exeats for students residing in their assigned House.
      - Tutors and Gate Officers cannot approve exeats.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    profile = get_user_profile(user)
    if not profile:
        return False
    if profile.role not in EXEAT_APPROVAL_ROLES:
        return False
    if profile.role == StaffRole.HOUSEMASTER:
        housemaster = getattr(user, "housemaster", None)
        if (
            not housemaster
            or not housemaster.house_id
            or not exeat.student_id
            or exeat.student.house_id != housemaster.house_id
        ):
            return False
    return profile.can_approve_exeat(exeat)


def can_log_gate_check(user) -> bool:
    """
    Checks if user is authorized to record student departures and arrivals at the gate.
    Allowed for: Admins, Senior Housemasters, Housemasters, Gate Officers.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    profile = get_user_profile(user)
    if not profile:
        return False
    return profile.can_log_gate_movement()
