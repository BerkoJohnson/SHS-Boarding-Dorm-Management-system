"""
Context processor supplying top command bar notifications and authenticated user metadata.
"""
from typing import Dict, Any, List
from django.utils import timezone
from django.db.models import Q
from core.models import (
    Exeat, ExeatReconciliation, DisciplinaryRecord, SchoolConfiguration, StaffRole,
    get_user_profile, get_approvable_pending_for_user,
)
from core.permissions import (
    ADMIN_ROLES,
    STUDENT_MANAGEMENT_ROLES,
    STUDENT_RECORD_ROLES,
    ROLL_CALL_ROLES,
    GATE_ROLES,
    EXEAT_ROLES,
    EXEAT_APPROVAL_ROLES,
    EXEAT_CREATE_ROLES,
    CONDUCT_ROLES,
    REPORT_ROLES,
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
    school_configuration = SchoolConfiguration.get_solo()
    if not hasattr(request, "user") or not request.user.is_authenticated:
        return {
            "school_configuration": school_configuration,
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
            "can_view_exeats": False,
            "can_view_reports": False,
            "can_view_config": False,
            "can_manage_students": False,
            "can_view_student_records": False,
            "can_create_exeat": False,
            "can_approve_exeat": False,
            "can_access_gate": False,
            "can_access_roll_call": False,
        }

    now = timezone.now()
    user = request.user
    profile = get_user_profile(user)
    role = profile.role if profile else None
    housemaster = getattr(user, 'housemaster', None)
    is_school_wide = user.is_superuser or role in ADMIN_ROLES
    assigned_house = housemaster.house if housemaster else None
    house_scope_id = (
        None if is_school_wide else housemaster.house_id if housemaster else None
    )
    has_house_scope = is_school_wide or house_scope_id is not None

    # 1. Overdue Exeats (Students departed through gate but past expected return)
    overdue_exeats = Exeat.objects.filter(
        status=Exeat.Status.APPROVED,
        expected_return__lte=now,
    ).filter(
        Q(movement__actual_departure__isnull=False, movement__actual_return__isnull=True)
        | Q(
            reconciliation__outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
            reconciliation__actual_return__isnull=True,
        )
    ).select_related("student__house", "student").order_by("expected_return")
    if not is_school_wide:
        overdue_exeats = (
            overdue_exeats.filter(student__house_id=house_scope_id)
            if house_scope_id is not None
            else overdue_exeats.none()
        )

    overdue_count = overdue_exeats.count()

    unresolved_exeats = Exeat.objects.filter(
        status=Exeat.Status.APPROVED,
        expected_return__lte=now,
        movement__actual_departure__isnull=True,
        movement__actual_return__isnull=True,
        reconciliation__isnull=True,
    ).select_related("student__house", "student").order_by("expected_return")
    if not is_school_wide:
        unresolved_exeats = (
            unresolved_exeats.filter(student__house_id=house_scope_id)
            if house_scope_id is not None
            else unresolved_exeats.none()
        )
    unresolved_count = unresolved_exeats.count()

    # 2. Pending Exeats requiring approval — SCOPED to user jurisdiction
    #    Admins and Senior Housemasters see all pending requests; regular
    #    Housemasters only see requests for students in their assigned House.
    pending_exeats = get_approvable_pending_for_user(request.user)
    pending_count = pending_exeats.count()

    # 3. Critical Infractions
    critical_records = DisciplinaryRecord.objects.filter(
        severity=DisciplinaryRecord.Severity.CRITICAL,
        corrected_at__isnull=True,
    ).select_related("student", "student__house").order_by("-created_at")
    if role not in CONDUCT_ROLES and role not in ADMIN_ROLES:
        critical_records = critical_records.none()
    if not is_school_wide:
        critical_records = (
            critical_records.filter(student__house_id=house_scope_id)
            if house_scope_id is not None
            else critical_records.none()
        )

    critical_count = critical_records.count()

    # Build structured list of notification items for dropdown presentation
    notifications: List[Dict[str, Any]] = []

    for exeat in overdue_exeats[:4]:
        student = exeat.student
        hours_overdue = 1
        if exeat.expected_return:
            diff = now - exeat.expected_return
            hours_overdue = max(1, int(diff.total_seconds() // 3600))

        house_name = student.house.name if student.house else "Boarding House"
        notifications.append({
            "category": "overdue",
            "category_label": "Overdue Gate Alert",
            "title": f"{student.full_name} is Overdue",
            "subtitle": f"{hours_overdue}h overdue • {house_name}",
            "time_ago": f"{hours_overdue}h late",
            "url": f"/exeats/{exeat.id}/",
            "badge_color": "rose",
            "icon": "alert-triangle",
        })

    for exeat in unresolved_exeats[:3]:
        student = exeat.student
        house_name = student.house.name if student.house else "Boarding House"
        notifications.append({
            "category": "unresolved",
            "category_label": "Pass Movement Follow-up",
            "title": f"{student.full_name}: pass unresolved",
            "subtitle": f"No movement record • {house_name}",
            "time_ago": "Needs follow-up",
            "url": f"/exeats/{exeat.id}/",
            "badge_color": "amber",
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

    total_notifications = overdue_count + unresolved_count + pending_count + critical_count

    # 4. User identity and Housemaster profile resolution
    user_initials = get_user_initials(user)
    if user.is_superuser or role == StaffRole.ADMIN:
        role_display = "System Administrator"
    elif role == StaffRole.SENIOR_HOUSEMASTER:
        role_display = "Senior Housemaster"
    elif role == StaffRole.GATE_OFFICER:
        role_display = "Security / Gate Officer"
    elif role == StaffRole.HOUSE_TUTOR:
        role_display = "House Tutor"
    elif housemaster:
        role_display = (
            f"Housemaster ({assigned_house.name})"
            if assigned_house
            else "Housemaster"
        )
    elif user.is_staff:
        role_display = "Staff Officer"
    else:
        role_display = "House Tutor"

    staff_id = housemaster.staff_id if housemaster else getattr(user, 'username', '')
    assigned_dormitory = housemaster.assigned_dormitory if housemaster else None
    phone_number = housemaster.phone_number if housemaster else (profile.phone_number if profile else '')

    if is_school_wide:
        jurisdiction_scope = "Full Institutional Authority"
    elif role == StaffRole.SENIOR_HOUSEMASTER:
        jurisdiction_scope = "School-Wide Boarding Oversight"
    elif assigned_house:
        jurisdiction_scope = f"{assigned_house.name} Oversight"
    elif role == StaffRole.GATE_OFFICER:
        jurisdiction_scope = "School-Wide Gate Operations"
    elif role == StaffRole.HOUSE_TUTOR:
        jurisdiction_scope = "House Tutorial & Incident Logging"
    else:
        jurisdiction_scope = "Standard Staff Jurisdiction"

    is_super = user.is_superuser
    can_view_houses = is_super or role in ADMIN_ROLES
    can_view_conduct = is_super or (has_house_scope and role in CONDUCT_ROLES)
    can_view_exeats = is_super or (has_house_scope and role in EXEAT_ROLES)
    can_view_reports = is_super or (has_house_scope and role in REPORT_ROLES)
    can_view_config = is_super or role in ADMIN_ROLES
    can_manage_students = is_super or role in STUDENT_MANAGEMENT_ROLES
    can_view_student_records = is_super or (
        has_house_scope and role in STUDENT_RECORD_ROLES
    )
    can_create_exeat = is_super or (has_house_scope and role in EXEAT_CREATE_ROLES)
    can_approve_exeat = is_super or (has_house_scope and role in EXEAT_APPROVAL_ROLES)
    can_access_gate = is_super or role in GATE_ROLES
    can_access_roll_call = is_super or (has_house_scope and role in ROLL_CALL_ROLES)

    return {
        "school_configuration": school_configuration,
        "notifications": notifications,
        "notification_count": total_notifications,
        "overdue_count": overdue_count,
        "pending_exeat_count": pending_count,
        "critical_incident_count": critical_count,
        "user_initials": user_initials,
        "user_role_display": role_display,
        "user_assigned_house": assigned_house,
        "has_house_scope": has_house_scope,
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
        "can_view_exeats": can_view_exeats,
        "can_view_reports": can_view_reports,
        "can_view_config": can_view_config,
        "can_manage_students": can_manage_students,
        "can_view_student_records": can_view_student_records,
        "can_create_exeat": can_create_exeat,
        "can_approve_exeat": can_approve_exeat,
        "can_access_gate": can_access_gate,
        "can_access_roll_call": can_access_roll_call,
    }
