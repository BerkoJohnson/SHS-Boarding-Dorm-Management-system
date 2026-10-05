"""
Context processor supplying top command bar notifications and authenticated user metadata.
"""
from typing import Dict, Any, List
from django.utils import timezone
from django.db.models import Q
from core.models import Exeat, ExeatMovement, DisciplinaryRecord, HouseMaster, StaffRole, get_user_profile
from core.permissions import (
    ADMIN_ROLES,
    ROLL_CALL_ROLES,
    GATE_ROLES,
    OPERATIONS_ROLES,
    EXEAT_ROLES,
    EXEAT_APPROVAL_ROLES,
    EXEAT_CREATE_ROLES,
    CONDUCT_ROLES,
    HOUSE_ROLES,
)


def get_user_initials(user) -> str:
    """
    Computes a clean 2-character monogram / initials string from a User instance.

    Examples:
        - Kwame Mensah -> 'KM'
        - admin -> 'AD'
        - John -> 'JO'
    """
    if not user or not user.is_authenticated:
        return "AD"

    first = (user.first_name or "").strip()
    last = (user.last_name or "").strip()

    if first and last:
        return f"{first[0].upper()}{last[0].upper()}"
    elif first:
        return first[:2].upper()
    elif user.username:
        clean_user = user.username.strip()
        if len(clean_user) >= 2:
            return clean_user[:2].upper()
        return clean_user.upper()
    return "AD"


def header_context(request) -> Dict[str, Any]:
    """
    Provides real-time operational alerts (overdue gate logs, pending approvals,
    critical incidents) and authenticated user state to templates inheriting base.html.
    """
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "notifications": [],
            "notification_count": 0,
            "overdue_count": 0,
            "pending_exeat_count": 0,
            "critical_incident_count": 0,
            "user_initials": "AD",
            "user_role_display": "Guest",
            "user_assigned_house": None,
            "can_view_houses": False,
            "can_view_conduct": False,
            "can_view_reports": False,
            "can_view_config": False,
            "can_create_exeat": False,
            "can_approve_exeat": False,
            "can_access_gate": False,
            "can_access_roll_call": False,
        }

    now = timezone.now()
    user = request.user

    # 1. Overdue Exeats (Students departed through gate but past expected return)
    overdue_movements = ExeatMovement.objects.filter(
        actual_departure__isnull=False,
        actual_return__isnull=True,
        exeat__expected_return__lt=now,
    ).select_related("exeat__student__house", "exeat__student").order_by("exeat__expected_return")

    overdue_count = overdue_movements.count()

    # 2. Pending Exeats requiring approval
    pending_exeats = Exeat.objects.filter(
        status=Exeat.Status.PENDING
    ).select_related("student__house", "student").order_by("-created_at")

    pending_count = pending_exeats.count()

    # 3. Critical Infractions
    critical_records = DisciplinaryRecord.objects.filter(
        severity=DisciplinaryRecord.Severity.CRITICAL
    ).select_related("student", "student__house").order_by("-created_at")

    critical_count = critical_records.count()

    # Build structured list of notification items for dropdown presentation
    notifications: List[Dict[str, Any]] = []

    for movement in overdue_movements[:4]:
        student = movement.exeat.student
        hours_overdue = 1
        if movement.exeat.expected_return:
            diff = now - movement.exeat.expected_return
            hours_overdue = max(1, int(diff.total_seconds() // 3600))

        house_name = student.house.name if student.house else "Boarding House"
        notifications.append({
            "category": "overdue",
            "category_label": "Overdue Gate Alert",
            "title": f"{student.full_name} is Overdue",
            "subtitle": f"{hours_overdue}h overdue • {house_name}",
            "time_ago": f"{hours_overdue}h late",
            "url": f"/exeats/{movement.exeat_id}/",
            "badge_color": "rose",
            "icon": "alert-triangle",
        })

    for exeat in pending_exeats[:3]:
        student = exeat.student
        house_name = student.house.name if student.house else "Boarding House"
        notifications.append({
            "category": "pending",
            "category_label": "Pending Sign-off",
            "title": f"{exeat.get_exeat_type_display()} Leave Request",
            "subtitle": f"{student.full_name} ({student.admission_no}) • {house_name}",
            "time_ago": "Needs approval",
            "url": f"/exeats/{exeat.id}/",
            "badge_color": "amber",
            "icon": "clock",
        })

    for record in critical_records[:3]:
        student = record.student
        house_name = student.house.name if student.house else "School House"
        notifications.append({
            "category": "critical",
            "category_label": "Disciplinary Infraction",
            "title": f"Critical: {record.offense}",
            "subtitle": f"{student.full_name} • {record.action_taken}",
            "time_ago": "Urgent review",
            "url": "/conduct/?severity=Critical",
            "badge_color": "red",
            "icon": "shield-alert",
        })

    total_notifications = overdue_count + pending_count + critical_count

    # 4. User identity and Housemaster profile resolution
    user_initials = get_user_initials(user)
    profile = get_user_profile(user)

    # First check direct OneToOne relationship on user
    housemaster = getattr(user, 'housemaster', None)
    if not housemaster:
        # Fallback to staff_id or name lookup for backwards compatibility
        housemaster = HouseMaster.objects.filter(
            Q(staff_id__iexact=user.username) | Q(full_name__icontains=user.username)
        ).select_related("house").first()

    if housemaster:
        if housemaster.is_senior_housemaster:
            role_display = "Senior Housemaster"
        else:
            role_display = f"Housemaster ({housemaster.house.name if housemaster.house else 'General'})"
        assigned_house = housemaster.house
    elif user.is_superuser or (profile and profile.role == StaffRole.ADMIN):
        role_display = "System Administrator"
        assigned_house = None
    elif profile and profile.role == StaffRole.GATE_OFFICER:
        role_display = "Security / Gate Officer"
        assigned_house = None
    elif profile and profile.role == StaffRole.HOUSE_TUTOR:
        role_display = "House Tutor"
        assigned_house = None
    elif user.is_staff:
        role_display = "Staff Officer"
        assigned_house = None
    else:
        role_display = "House Tutor"
        assigned_house = None

    staff_id = housemaster.staff_id if housemaster else getattr(user, 'username', '')
    assigned_dormitory = housemaster.assigned_dormitory if housemaster else None
    phone_number = housemaster.phone_number if housemaster else (profile.phone_number if profile else '')

    if user.is_superuser or (profile and profile.role == StaffRole.ADMIN):
        jurisdiction_scope = "Full Institutional Authority"
    elif housemaster and housemaster.is_senior_housemaster:
        jurisdiction_scope = "School-Wide Boarding Oversight"
    elif assigned_house:
        jurisdiction_scope = f"{assigned_house.name} Oversight"
    elif profile and profile.role == StaffRole.GATE_OFFICER:
        jurisdiction_scope = "Security Checkpoint Operations"
    elif profile and profile.role == StaffRole.HOUSE_TUTOR:
        jurisdiction_scope = "House Tutorial & Incident Logging"
    else:
        jurisdiction_scope = "Standard Staff Jurisdiction"

    role = profile.role if profile else None
    is_super = user.is_superuser
    can_view_houses = is_super or role in HOUSE_ROLES
    can_view_conduct = is_super or role in CONDUCT_ROLES
    can_view_reports = is_super or role in ADMIN_ROLES
    can_view_config = is_super or role in ADMIN_ROLES
    can_create_exeat = is_super or role in EXEAT_CREATE_ROLES
    can_approve_exeat = is_super or role in EXEAT_APPROVAL_ROLES
    can_access_gate = is_super or role in GATE_ROLES
    can_access_roll_call = is_super or role in ROLL_CALL_ROLES

    return {
        "notifications": notifications,
        "notification_count": total_notifications,
        "overdue_count": overdue_count,
        "pending_exeat_count": pending_count,
        "critical_incident_count": critical_count,
        "user_initials": user_initials,
        "user_role_display": role_display,
        "user_assigned_house": assigned_house,
        "user_assigned_dormitory": assigned_dormitory,
        "user_staff_id": staff_id,
        "user_phone": phone_number,
        "user_jurisdiction_scope": jurisdiction_scope,
        "user_profile": profile,
        "user_housemaster": housemaster,
        "is_senior_housemaster": bool(profile and profile.is_senior_housemaster),
        "is_gate_officer": bool(profile and profile.is_gate_officer),
        "can_view_houses": can_view_houses,
        "can_view_conduct": can_view_conduct,
        "can_view_reports": can_view_reports,
        "can_view_config": can_view_config,
        "can_create_exeat": can_create_exeat,
        "can_approve_exeat": can_approve_exeat,
        "can_access_gate": can_access_gate,
        "can_access_roll_call": can_access_roll_call,
    }
