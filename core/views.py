from django.views.decorators.http import require_POST, require_http_methods
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.db.models import Count, Q, Sum
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth import logout
from django.contrib import messages
from .models import (
    Student,
    Exeat,
    ExeatMovement,
    House,
    Dormitory,
    DisciplinaryRecord,
    AcademicTerm,
    HouseMaster,
    StaffRole,
    UserProfile,
    get_user_profile,
    RollCallSession,
    RollCallEntry,
)
from .forms import ExeatCreateForm, DisciplinaryRecordForm
from .permissions import (
    role_required,
    can_approve_exeat_check,
    can_log_gate_check,
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
from .services.roll_call import (
    create_roll_call_session,
    update_entry_status,
    mark_all_present,
    convert_absence_to_disciplinary_record,
)


@login_required
def dashboard_view(request):
    """
    Main Executive Dashboard for Senior Housemasters and School Administrators.
    
    Provides:
      - 10-Metric Workspace Grid (Boarders, Leaves, Pending, Overdue, Occupancy %, Day Students,
        Medical, Town, Weekend, Disciplinary Records).
      - Live Student Exeat & Movement Register with instant HTMX search and status filtering.
    """
    now = timezone.now()
    active_terms = AcademicTerm.objects.filter(is_current=True)

    # 1. Core Boarding & Cohort Metrics
    total_boarders = Student.objects.filter(
        residency=Student.ResidencyStatus.BOARDING
    ).count()
    day_students_count = Student.objects.filter(
        residency=Student.ResidencyStatus.DAY
    ).count()
    total_students = total_boarders + day_students_count

    total_capacity = Dormitory.objects.aggregate(total=Sum('capacity'))['total'] or 0
    occupancy_rate = (
        round((total_boarders / total_capacity * 100), 1)
        if total_capacity > 0
        else 0
    )

    # 2. Exeat & Real-Time Gate Movement Metrics
    pending_exeats = Exeat.objects.filter(status=Exeat.Status.PENDING).count()

    active_out = ExeatMovement.objects.filter(
        actual_departure__isnull=False,
        actual_return__isnull=True
    ).count()

    overdue_movements = ExeatMovement.objects.filter(
        actual_departure__isnull=False,
        actual_return__isnull=True,
        exeat__expected_return__lt=now
    ).select_related('exeat__student')
    overdue_count = overdue_movements.count()

    # 3. Categorical Exeat Metrics
    medical_exeats_count = Exeat.objects.filter(
        exeat_type=Exeat.ExeatType.MEDICAL
    ).count()
    town_exeats_count = Exeat.objects.filter(
        exeat_type=Exeat.ExeatType.TOWN
    ).count()
    weekend_exeats_count = Exeat.objects.filter(
        exeat_type=Exeat.ExeatType.WEEKEND
    ).count()

    # 4. Conduct & Discipline
    active_disciplinary_count = DisciplinaryRecord.objects.count()

    # 5. Master Student Exeat & Movement Register
    query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip().lower()

    exeats_qs = Exeat.objects.select_related(
        'student',
        'student__house',
        'student__dormitory',
        'student__class_group',
        'academic_term',
        'movement',
        'approved_by'
    ).order_by('-created_at')

    if query:
        exeats_qs = exeats_qs.filter(
            Q(exeat_number__icontains=query) |
            Q(student__full_name__icontains=query) |
            Q(student__admission_no__icontains=query) |
            Q(student__house__name__icontains=query) |
            Q(destination__icontains=query)
        )

    if status_filter == 'active':
        exeats_qs = exeats_qs.filter(
            movement__actual_departure__isnull=False,
            movement__actual_return__isnull=True
        )
    elif status_filter == 'pending':
        exeats_qs = exeats_qs.filter(status=Exeat.Status.PENDING)
    elif status_filter == 'overdue':
        exeats_qs = exeats_qs.filter(
            movement__actual_departure__isnull=False,
            movement__actual_return__isnull=True,
            expected_return__lt=now
        )
    elif status_filter == 'returned':
        exeats_qs = exeats_qs.filter(movement__actual_return__isnull=False)

    recent_exeats = exeats_qs[:30]
    total_matching_exeats = exeats_qs.count()

    context = {
        # Academic & Terms
        'active_terms': active_terms,
        # 10 Metric Workspace Grid
        'total_boarders': total_boarders,
        'day_students_count': day_students_count,
        'total_students': total_students,
        'total_capacity': total_capacity,
        'occupancy_rate': occupancy_rate,
        'pending_exeats': pending_exeats,
        'active_out': active_out,
        'overdue_count': overdue_count,
        'overdue_students': overdue_movements[:5],
        'medical_exeats_count': medical_exeats_count,
        'town_exeats_count': town_exeats_count,
        'weekend_exeats_count': weekend_exeats_count,
        'active_disciplinary_count': active_disciplinary_count,
        # Master Register Table
        'recent_exeats': recent_exeats,
        'total_matching_exeats': total_matching_exeats,
        'search_query': query,
        'current_status_filter': status_filter,
    }

    # HTMX Partial Rendering for Table Updates
    if request.htmx and request.GET.get('table_only'):
        return render(request, 'core/partials/dashboard_table.html', context)

    return render(request, 'core/index.html', context)

@login_required
@role_required(*EXEAT_ROLES)
def exeat_list_view(request):
    """Lists all exeats with multi-parameter HTMX search and filtering."""
    query = request.GET.get('q', '').strip()
    exeat_type = request.GET.get('type', '').strip()
    status_filter = request.GET.get('status', '').strip()
    house_id = request.GET.get('house', '').strip()
    now = timezone.now()

    exeats = Exeat.objects.select_related(
        'student',
        'student__house',
        'student__dormitory',
        'student__class_group',
        'academic_term',
        'movement',
        'approved_by'
    ).all().order_by('-created_at')

    # 1. Text Search Filter
    if query:
        exeats = exeats.filter(
            Q(exeat_number__icontains=query) |
            Q(student__full_name__icontains=query) |
            Q(student__admission_no__icontains=query) |
            Q(destination__icontains=query)
        )

    # 2. Exeat Type / Category Filter
    if exeat_type:
        exeats = exeats.filter(exeat_type=exeat_type)

    # 3. House Filter
    if house_id:
        exeats = exeats.filter(student__house_id=house_id)

    # 4. Status & Real-time Movement Filter
    if status_filter == 'active_out':
        exeats = exeats.filter(
            movement__actual_departure__isnull=False,
            movement__actual_return__isnull=True
        )
    elif status_filter == 'overdue':
        exeats = exeats.filter(
            movement__actual_departure__isnull=False,
            movement__actual_return__isnull=True,
            expected_return__lt=now
        )
    elif status_filter == 'returned':
        exeats = exeats.filter(movement__actual_return__isnull=False)
    elif status_filter:
        exeats = exeats.filter(status=status_filter)

    houses = House.objects.all().order_by('number')
    active_filters_count = sum(1 for v in [exeat_type, status_filter, house_id] if v)

    context = {
        'exeats': exeats[:100],
        'total_count': exeats.count(),
        'houses': houses,
        'selected_type': exeat_type,
        'selected_status': status_filter,
        'selected_house': house_id,
        'active_filters_count': active_filters_count,
        'query': query,
    }

    # Return partial if request is from HTMX search / filter
    if request.htmx:
        return render(request, 'core/exeats/partials/exeat_table.html', context)
    
    return render(request, 'core/exeats/list.html', context)

@login_required
@role_required(*HOUSE_ROLES)
def house_list_view(request):
    """Displays houses and dormitories with capacity metrics."""
    houses = House.objects.prefetch_related('dormitories', 'housemasters').annotate(
        total_students=Count('students')
    )
    return render(request, 'core/houses/list.html', {'houses': houses})

@login_required
@role_required(*CONDUCT_ROLES)
def conduct_list_view(request):
    """
    Displays disciplinary records with search filtering, severity breakdowns,
    and HTMX live partial reload.
    """
    query = request.GET.get('q', '').strip()
    severity = request.GET.get('severity', '').strip()
    house_id = request.GET.get('house', '').strip()

    records_qs = DisciplinaryRecord.objects.select_related(
        'student',
        'student__house',
        'student__dormitory',
        'student__class_group',
        'academic_term',
        'logged_by'
    ).all().order_by('-created_at')

    # 1. Text Search Filter
    if query:
        records_qs = records_qs.filter(
            Q(student__full_name__icontains=query) |
            Q(student__admission_no__icontains=query) |
            Q(offense__icontains=query) |
            Q(action_taken__icontains=query) |
            Q(description__icontains=query)
        )

    # 2. Severity Filter
    if severity:
        records_qs = records_qs.filter(severity=severity)

    # 3. House Filter
    if house_id:
        records_qs = records_qs.filter(student__house_id=house_id)

    # Top stats aggregations
    all_records = DisciplinaryRecord.objects.all()
    total_incidents = all_records.count()
    critical_count = all_records.filter(severity=DisciplinaryRecord.Severity.CRITICAL).count()
    major_count = all_records.filter(severity=DisciplinaryRecord.Severity.MAJOR).count()
    minor_count = all_records.filter(severity=DisciplinaryRecord.Severity.MINOR).count()

    houses = House.objects.all().order_by('number')
    active_filters_count = sum(1 for v in [severity, house_id] if v)

    context = {
        'records': records_qs[:100],
        'total_matching': records_qs.count(),
        'total_incidents': total_incidents,
        'critical_count': critical_count,
        'major_count': major_count,
        'minor_count': minor_count,
        'houses': houses,
        'selected_severity': severity,
        'selected_house': house_id,
        'active_filters_count': active_filters_count,
        'query': query,
    }

    if request.htmx:
        return render(request, 'core/conduct/partials/conduct_table.html', context)

    return render(request, 'core/conduct/list.html', context)


@login_required
@role_required(*CONDUCT_ROLES)
def incident_create_view(request):
    """
    Records a student disciplinary incident with severity level and sanction actions.
    """
    selected_student_id = request.GET.get('student_id') or request.POST.get('student')
    selected_student = None

    if selected_student_id:
        try:
            selected_student = Student.objects.select_related(
                'house', 'dormitory', 'class_group', 'guardian'
            ).get(pk=selected_student_id)
        except (Student.DoesNotExist, ValueError):
            selected_student = None

    if request.method == 'POST':
        form = DisciplinaryRecordForm(request.POST)
        if form.is_valid():
            record = form.save(commit=False)
            current_term = AcademicTerm.objects.filter(is_current=True).first()
            if current_term:
                record.academic_term = current_term
            if not record.logged_by_id:
                record.logged_by = HouseMaster.objects.filter(house=record.student.house).first() or HouseMaster.objects.first()
            record.save()

            messages.success(
                request,
                f"Incident '{record.offense}' successfully recorded for {record.student.full_name}."
            )
            return redirect('core:conduct_list')
        else:
            messages.error(request, "Please correct the errors in the incident form below.")
    else:
        initial_data = {}
        if selected_student:
            initial_data['student'] = selected_student.id
        form = DisciplinaryRecordForm(initial=initial_data)

    common_offenses = [
        "Overstayed Exeat",
        "Sneaking Out of School Compound",
        "Missing Evening Roll Call",
        "Lateness to Morning Assembly / House Duty",
        "Bullying / Harassment of Junior Student",
        "Fighting / Physical Altercation",
        "Possession of Unauthorized Electronics",
        "Improper Dressing / Uniform Infraction",
    ]

    context = {
        'form': form,
        'selected_student': selected_student,
        'common_offenses': common_offenses,
        'housemasters': HouseMaster.objects.select_related('house').all(),
    }
    return render(request, 'core/conduct/create.html', context)



@login_required
@role_required(*EXEAT_ROLES)
def student_search_view(request):
    """
    HTMX live autocomplete endpoint for searching students when issuing an exeat pass.
    
    Filters by name, admission number, or index number and renders student cards
    with boarding residency badges.
    """
    query = request.GET.get('q', '').strip()
    students = []
    if len(query) >= 2:
        students = Student.objects.select_related('house', 'dormitory', 'class_group').filter(
            Q(full_name__icontains=query) |
            Q(admission_no__icontains=query) |
            Q(index_number__icontains=query)
        )[:8]

    return render(request, 'core/exeats/partials/student_search_results.html', {
        'students': students,
        'query': query,
    })


@login_required
@role_required(*EXEAT_CREATE_ROLES)
def exeat_create_view(request):
    """
    Issues a new official student Exeat / Leave Pass.
    
    Supports:
      - Interactive student lookup with live house & dormitory badges.
      - Default date intervals and leave category selection.
      - Immediate Housemaster authorization and gate log creation.
    """
    selected_student_id = request.GET.get('student_id') or request.POST.get('student')
    selected_student = None

    if selected_student_id:
        try:
            selected_student = Student.objects.select_related(
                'house', 'dormitory', 'class_group', 'guardian'
            ).get(pk=selected_student_id)
        except (Student.DoesNotExist, ValueError):
            selected_student = None

    if request.method == 'POST':
        form = ExeatCreateForm(request.POST)
        if form.is_valid():
            exeat = form.save(commit=False)
            exeat.save()

            # Automatically create a Gate Movement log for approved exeats
            if exeat.status == Exeat.Status.APPROVED:
                ExeatMovement.objects.get_or_create(exeat=exeat)

            messages.success(
                request,
                f"Exeat Pass {exeat.exeat_number} successfully issued for {exeat.student.full_name}."
            )
            return redirect('core:dashboard')
        else:
            messages.error(request, "Please correct the errors in the exeat form below.")
    else:
        initial_data = {}
        if selected_student:
            initial_data['student'] = selected_student.id

        form = ExeatCreateForm(initial=initial_data)

    housemasters = HouseMaster.objects.select_related('house').all()
    active_terms = AcademicTerm.objects.filter(is_current=True)

    context = {
        'form': form,
        'selected_student': selected_student,
        'housemasters': housemasters,
        'active_terms': active_terms,
    }
    return render(request, 'core/exeats/create.html', context)


@login_required
@role_required(*EXEAT_ROLES)
def exeat_detail_view(request, pk):
    """
    Renders the official details and printable exeat pass slip for a student.
    """
    exeat = get_object_or_404(
        Exeat.objects.select_related(
            'student__house',
            'student__dormitory',
            'student__class_group',
            'student__guardian',
            'academic_term',
            'movement',
            'approved_by'
        ),
        pk=pk
    )
    return render(request, 'core/exeats/detail.html', {'exeat': exeat})


@login_required
@role_required(*EXEAT_ROLES)
def exeat_action_view(request, pk, action):
    """
    Processes quick workflow actions from the table 3-dots context menu:
      - 'departure': Log real-time exit through school gate (requires gate authority)
      - 'return': Log student return through gate (requires gate authority)
      - 'approve': Housemaster approvals (scoped to assigned House or Senior Housemaster)
      - 'reject': Reject request (scoped to assigned House or Senior Housemaster)
      - 'cancel': Void/cancel pass (scoped to assigned House or Senior Housemaster)
    """
    exeat = get_object_or_404(Exeat.objects.select_related('student__house', 'movement'), pk=pk)
    user_name = request.user.get_full_name() or request.user.username
    now = timezone.now()

    if request.method == 'POST':
        # 1. Gate operations
        if action in ('departure', 'return'):
            if not can_log_gate_check(request.user):
                messages.error(request, "Permission denied: You are not authorized to log gate checkpoint movements.")
            elif action == 'departure':
                if exeat.status != Exeat.Status.APPROVED:
                    messages.error(request, "Only approved exeats can have gate departure logged.")
                else:
                    movement, _ = ExeatMovement.objects.get_or_create(exeat=exeat)
                    movement.actual_departure = now
                    movement.departure_recorded_by = user_name
                    movement.save()
                    messages.success(request, f"Gate departure recorded for {exeat.student.full_name}.")
            elif action == 'return':
                if exeat.status != Exeat.Status.APPROVED:
                    messages.error(request, "Cannot record gate return for an unapproved exeat.")
                else:
                    movement, _ = ExeatMovement.objects.get_or_create(exeat=exeat)
                    if not movement.actual_departure:
                        movement.actual_departure = now
                    movement.actual_return = now
                    movement.return_recorded_by = user_name
                    movement.save()
                    messages.success(request, f"Gate return recorded for {exeat.student.full_name}.")

        # 2. Authorization workflow (approve / reject / cancel)
        elif action in ('approve', 'reject', 'cancel'):
            if not can_approve_exeat_check(request.user, exeat):
                house_label = exeat.student.house.name if (exeat.student and exeat.student.house) else "this House"
                messages.error(
                    request,
                    f"Permission denied: Only the Housemaster for {house_label} "
                    f"or the Senior Housemaster can {action} this exeat."
                )
            elif action == 'approve':
                exeat.status = Exeat.Status.APPROVED
                if not exeat.approved_by:
                    # Link to actual authenticated Housemaster if available
                    if hasattr(request.user, 'housemaster') and request.user.housemaster:
                        exeat.approved_by = request.user.housemaster
                    else:
                        housemaster = HouseMaster.objects.filter(house=exeat.student.house).first() or HouseMaster.objects.first()
                        exeat.approved_by = housemaster
                exeat.save()
                ExeatMovement.objects.get_or_create(exeat=exeat)
                messages.success(request, f"Exeat pass {exeat.exeat_number} marked as Approved.")
            elif action == 'reject':
                exeat.status = Exeat.Status.REJECTED
                exeat.save()
                messages.info(request, f"Exeat request {exeat.exeat_number} was rejected.")
            elif action == 'cancel':
                exeat.status = Exeat.Status.CANCELLED
                exeat.save()
                messages.info(request, f"Exeat pass {exeat.exeat_number} was cancelled.")

    referer = request.META.get('HTTP_REFERER')
    if referer:
        return redirect(referer)
    return redirect('core:dashboard')


def logout_view(request):
    """
    Safely terminates user session and redirects to login with flash feedback.
    Supports both POST and GET requests.
    """
    if request.user.is_authenticated:
        username = request.user.get_full_name() or request.user.username
        logout(request)
        messages.info(request, f"You have been signed out successfully. Goodbye, {username}!")
    return redirect('core:dashboard')


@login_required
def global_search_view(request):
    """
    Omni-search / Command Palette live endpoint.
    
    Accepts:
        GET parameter `q`: search query string
        
    Performs cross-model search across:
        - Students (full_name, admission_no, index_number)
        - Exeats (exeat_number, destination, student full_name, student admission_no)
        - Houses & Dormitories (House.name, Dormitory.name)
        - Disciplinary Records (offense, student full_name, action_taken)
        - Quick Actions (navigational shortcuts & command triggers)
        
    Returns:
        Rendered partial 'components/search_results.html'
    """
    query = request.GET.get('q', '').strip()
    
    if len(query) < 2:
        return render(request, 'components/search_results.html', {
            'query': query,
            'is_short': True,
            'students': [],
            'exeats': [],
            'houses': [],
            'dormitories': [],
            'conduct_records': [],
            'quick_actions': [],
            'total_results': 0,
        })
    
    # 1. Search Students
    students = list(
        Student.objects.filter(
            Q(full_name__icontains=query) |
            Q(admission_no__icontains=query) |
            Q(index_number__icontains=query)
        ).select_related('house', 'dormitory', 'programme')[:5]
    )
    
    # 2. Search Exeat Passes
    exeats = list(
        Exeat.objects.filter(
            Q(exeat_number__icontains=query) |
            Q(destination__icontains=query) |
            Q(student__full_name__icontains=query) |
            Q(student__admission_no__icontains=query)
        ).select_related('student', 'student__house', 'movement')[:5]
    )
    
    profile = get_user_profile(request.user)
    role = profile.role if profile else None
    is_super = request.user.is_superuser

    # 3. Search Houses & Dormitories (Restricted to housing roles)
    houses = []
    dormitories = []
    if is_super or role in HOUSE_ROLES:
        houses = list(House.objects.filter(name__icontains=query)[:4])
        dormitories = list(
            Dormitory.objects.filter(
                Q(name__icontains=query) | Q(house__name__icontains=query)
            ).select_related('house')[:4]
        )
    
    # 4. Search Disciplinary Records (Restricted to conduct roles)
    conduct_records = []
    if is_super or role in CONDUCT_ROLES:
        conduct_records = list(
            DisciplinaryRecord.objects.filter(
                Q(offense__icontains=query) |
                Q(student__full_name__icontains=query) |
                Q(action_taken__icontains=query)
            ).select_related('student', 'student__house')[:5]
        )
    
    # 5. Quick Actions matching (Filtered by role permissions)
    q_lower = query.lower()
    system_quick_actions = [
        {
            'title': 'Executive Dashboard',
            'description': 'Return to the main high school management metrics and active passes',
            'url': reverse('core:dashboard'),
            'badge': 'System',
            'badge_color': 'bg-slate-100 text-slate-700 border-slate-200',
            'icon': 'home',
            'keywords': ['dashboard', 'home', 'metrics', 'stats', 'analytics', 'overview'],
        },
    ]

    if is_super or role in EXEAT_CREATE_ROLES:
        system_quick_actions.append({
            'title': 'New Exeat Request',
            'description': 'Issue or register a new exeat pass for a student',
            'url': reverse('core:exeat_create'),
            'badge': 'Action',
            'badge_color': 'bg-emerald-50 text-emerald-700 border-emerald-200',
            'icon': 'ticket',
            'keywords': ['exeat', 'leave', 'pass', 'permission', 'out', 'new', 'create', 'issue'],
        })

    if is_super or role in GATE_ROLES:
        system_quick_actions.append({
            'title': 'Gate Security Terminal',
            'description': 'Record student gate check-out departures and check-in arrivals',
            'url': f"{reverse('core:roll_call_gate_hub')}?tab=gate",
            'badge': 'Gate Log',
            'badge_color': 'bg-blue-50 text-blue-700 border-blue-200',
            'icon': 'gate',
            'keywords': ['gate', 'security', 'movement', 'departure', 'arrival', 'return', 'check', 'out', 'in'],
        })

    if is_super or role in ROLL_CALL_ROLES:
        system_quick_actions.append({
            'title': 'Roll Call Sessions',
            'description': 'Dormitory roll call inspection register and attendance roster',
            'url': f"{reverse('core:roll_call_gate_hub')}?tab=roll_call",
            'badge': 'Roll Call',
            'badge_color': 'bg-indigo-50 text-indigo-700 border-indigo-200',
            'icon': 'clipboard',
            'keywords': ['roll call', 'muster', 'attendance', 'roster', 'dormitory', 'house'],
        })

    if is_super or role in CONDUCT_ROLES:
        system_quick_actions.append({
            'title': 'Record Conduct Incident',
            'description': 'Log student infraction, disciplinary offense, or warning',
            'url': reverse('core:incident_create'),
            'badge': 'Conduct',
            'badge_color': 'bg-amber-50 text-amber-700 border-amber-200',
            'icon': 'alert',
            'keywords': ['conduct', 'incident', 'discipline', 'offense', 'punish', 'warning', 'suspension', 'log', 'new'],
        })

    if is_super or role in HOUSE_ROLES:
        system_quick_actions.append({
            'title': 'Houses & Dormitories Overview',
            'description': 'View boarding houses, dorm capacity, and bed allocations',
            'url': reverse('core:house_list'),
            'badge': 'Housing',
            'badge_color': 'bg-purple-50 text-purple-700 border-purple-200',
            'icon': 'building',
            'keywords': ['house', 'dorm', 'dormitory', 'bed', 'capacity', 'occupancy', 'hostel'],
        })

    if is_super or role in ADMIN_ROLES:
        system_quick_actions.append({
            'title': 'Reports & Stats Dashboard',
            'description': 'Aggregated operational metrics and boarding analytics',
            'url': reverse('core:reports'),
            'badge': 'Reports',
            'badge_color': 'bg-sky-50 text-sky-700 border-sky-200',
            'icon': 'chart',
            'keywords': ['reports', 'stats', 'analytics', 'metrics', 'graphs'],
        })
        system_quick_actions.append({
            'title': 'System Configuration Hub',
            'description': 'Academic terms and school operational settings',
            'url': reverse('core:configuration'),
            'badge': 'Settings',
            'badge_color': 'bg-amber-50 text-amber-700 border-amber-200',
            'icon': 'settings',
            'keywords': ['configuration', 'terms', 'academic term', 'settings'],
        })

    if is_super:
        system_quick_actions.append({
            'title': 'Django Administration',
            'description': 'Access core administrative models, users, and system permissions',
            'url': reverse('admin:index'),
            'badge': 'Admin',
            'badge_color': 'bg-rose-50 text-rose-700 border-rose-200',
            'icon': 'shield',
            'keywords': ['admin', 'django', 'users', 'superuser', 'settings', 'config'],
        })
        
    matched_quick_actions = [
        action for action in system_quick_actions
        if q_lower in action['title'].lower()
        or q_lower in action['description'].lower()
        or any(q_lower in kw or kw in q_lower for kw in action['keywords'])
    ][:3]
    
    total_results = (
        len(students) +
        len(exeats) +
        len(houses) +
        len(dormitories) +
        len(conduct_records) +
        len(matched_quick_actions)
    )
    
    context = {
        'query': query,
        'is_short': False,
        'students': students,
        'exeats': exeats,
        'houses': houses,
        'dormitories': dormitories,
        'conduct_records': conduct_records,
        'quick_actions': matched_quick_actions,
        'total_results': total_results,
    }
    return render(request, 'components/search_results.html', context)


@login_required
@ensure_csrf_cookie
@role_required(*OPERATIONS_ROLES)
def roll_call_gate_hub_view(request):
    """
    Central Operational Control Hub for:
      - Dormitory / House Roll Call Muster Sessions (Housemasters / Tutors)
      - Campus Main Gate Departure and Return Control (Security / Gate Officers)
    """
    profile = get_user_profile(request.user)
    is_gate_only = not request.user.is_superuser and profile and profile.role == StaffRole.GATE_OFFICER
    is_tutor_only = not request.user.is_superuser and profile and profile.role == StaffRole.HOUSE_TUTOR

    if is_gate_only:
        active_tab = 'gate'
    elif is_tutor_only:
        active_tab = 'roll_call'
    else:
        active_tab = request.GET.get('tab', 'roll_call')

    now = timezone.now()

    # 1. Roll Call Context
    houses = House.objects.all().order_by('number').prefetch_related('dormitories')
    
    # Filter sessions
    selected_house_id = request.GET.get('house', '').strip()
    selected_status = request.GET.get('session_status', '').strip()
    
    sessions_qs = RollCallSession.objects.select_related(
        'house', 'dormitory', 'conducted_by', 'academic_term'
    ).prefetch_related('entries').order_by('-date', '-created_at')
    
    if selected_house_id:
        sessions_qs = sessions_qs.filter(house_id=selected_house_id)
    if selected_status:
        sessions_qs = sessions_qs.filter(status=selected_status)
        
    recent_sessions = list(sessions_qs[:15])

    # Current selected or active session
    selected_session_id = request.GET.get('session_id')
    current_session = None
    session_entries = []
    
    if selected_session_id:
        current_session = RollCallSession.objects.filter(pk=selected_session_id).select_related(
            'house', 'dormitory', 'conducted_by'
        ).first()
    elif recent_sessions:
        # Use the unsliced queryset for filter lookups (Django prohibits filtering a sliced qs)
        in_progress = sessions_qs.filter(status=RollCallSession.Status.IN_PROGRESS).first()
        current_session = in_progress or recent_sessions[0]

    if current_session:
        session_entries = current_session.entries.select_related(
            'student', 'student__house', 'student__dormitory', 'student__class_group'
        ).order_by('student__full_name')

    # 2. Gate Security Context
    active_departures = ExeatMovement.objects.filter(
        actual_departure__isnull=False,
        actual_return__isnull=True
    ).select_related(
        'exeat', 'exeat__student', 'exeat__student__house', 'exeat__student__dormitory'
    ).order_by('actual_departure')

    total_boarders = Student.objects.filter(
        residency=Student.ResidencyStatus.BOARDING
    ).count()
    
    currently_out_count = active_departures.count()
    overdue_count = sum(1 for m in active_departures if m.is_overdue)
    in_campus_count = max(0, total_boarders - currently_out_count)

    recent_movements = ExeatMovement.objects.filter(
        actual_departure__isnull=False
    ).select_related(
        'exeat', 'exeat__student', 'exeat__student__house'
    ).order_by('-updated_at')[:15]

    # Approved exeats awaiting departure
    awaiting_departures = Exeat.objects.filter(
        status=Exeat.Status.APPROVED,
        movement__actual_departure__isnull=True
    ).select_related(
        'student', 'student__house', 'student__dormitory', 'movement'
    ).order_by('-expected_departure')[:10]

    context = {
        'active_tab': active_tab,
        'houses': houses,
        'recent_sessions': recent_sessions,
        'current_session': current_session,
        'session_entries': session_entries,
        'selected_house': selected_house_id,
        'selected_status': selected_status,
        
        # Gate Terminal data
        'active_departures': active_departures,
        'overdue_count': overdue_count,
        'currently_out_count': currently_out_count,
        'in_campus_count': in_campus_count,
        'total_boarders': total_boarders,
        'recent_movements': recent_movements,
        'awaiting_departures': awaiting_departures,
    }

    if request.htmx:
        target = request.headers.get('HX-Target', '')
        if target == 'roll-call-roster':
            return render(request, 'core/operations/partials/roll_call_roster.html', context)
        elif target == 'gate-terminal-body':
            return render(request, 'core/operations/partials/gate_terminal_body.html', context)

    return render(request, 'core/operations/hub.html', context)


@login_required
@role_required(*ROLL_CALL_ROLES)
def roll_call_create_session_view(request):
    """Starts a new Roll Call muster session for a House and populates boarders."""
    if request.method == 'POST':
        house_id = request.POST.get('house')
        dormitory_id = request.POST.get('dormitory') or None
        session_type = request.POST.get('session_type', RollCallSession.SessionType.EVENING)
        notes = request.POST.get('notes', '').strip()
        
        house = get_object_or_404(House, pk=house_id)
        dormitory = Dormitory.objects.filter(pk=dormitory_id).first() if dormitory_id else None
        
        # Determine conducting staff
        conducted_by = None
        if hasattr(request.user, 'housemaster') and request.user.housemaster:
            conducted_by = request.user.housemaster
        else:
            conducted_by = HouseMaster.objects.filter(house=house).first() or HouseMaster.objects.first()
            
        session = create_roll_call_session(
            house=house,
            session_type=session_type,
            conducted_by=conducted_by,
            dormitory=dormitory,
            notes=notes,
        )
        
        messages.success(
            request,
            f"Muster started for {session.house.name}: {session.total_students} boarders loaded into roster."
        )
        return redirect(f"{reverse('core:roll_call_gate_hub')}?tab=roll_call&session_id={session.id}")
        
    return redirect('core:roll_call_gate_hub')


@login_required
@role_required(*ROLL_CALL_ROLES)
def roll_call_complete_session_view(request, session_pk):
    """Marks a roll call session as completed."""
    session = get_object_or_404(RollCallSession, pk=session_pk)
    
    if session.status == RollCallSession.Status.COMPLETED:
        messages.info(request, "This roll call session has already been finalized.")
        return redirect(f"{reverse('core:roll_call_gate_hub')}?tab=roll_call&session_id={session.id}")
        
    session.status = RollCallSession.Status.COMPLETED
    session.save(update_fields=['status', 'updated_at'])
    messages.success(
        request,
        f"Roll call session for {session.house.name} finalized: {session.present_count}/{session.total_students} boarders accounted for."
    )
    return redirect(f"{reverse('core:roll_call_gate_hub')}?tab=roll_call&session_id={session.id}")


@login_required
@role_required(*ROLL_CALL_ROLES)
def roll_call_mark_all_present_view(request, session_pk):
    """Bulk action marking all unexcused students as PRESENT."""
    session = get_object_or_404(RollCallSession, pk=session_pk)
    
    if session.status == RollCallSession.Status.COMPLETED:
        messages.error(request, "Cannot modify a finalized roll call session.")
        return redirect(f"{reverse('core:roll_call_gate_hub')}?tab=roll_call&session_id={session.id}")
    count = mark_all_present(session)
    messages.success(request, f"Marked {count} students present.")
    return redirect(f"{reverse('core:roll_call_gate_hub')}?tab=roll_call&session_id={session.id}")


@login_required
@role_required(*ROLL_CALL_ROLES)
def roll_call_update_entry_view(request, entry_pk):
    """HTMX inline status update for an individual student attendance entry."""
    entry = get_object_or_404(
        RollCallEntry.objects.select_related('session', 'student', 'student__house', 'student__dormitory'),
        pk=entry_pk
    )
    if request.method == 'POST':
        if entry.session.status == RollCallSession.Status.COMPLETED:
            messages.error(request, "Cannot modify an entry in a finalized session.")
            if request.htmx:
                return render(request, 'core/operations/partials/entry_row.html', {'entry': entry, 'session': entry.session})
            return redirect(f"{reverse('core:roll_call_gate_hub')}?tab=roll_call&session_id={entry.session.id}")
        new_status = request.POST.get('status')
        remarks = request.POST.get('remarks')
        if new_status in RollCallEntry.Status.values:
            update_entry_status(entry, new_status, remarks)
            
            # Auto-convert absent to disciplinary if requested
            if request.POST.get('flag_infraction') == 'true' and new_status == RollCallEntry.Status.ABSENT:
                staff = getattr(request.user, 'housemaster', None)
                convert_absence_to_disciplinary_record(entry, logged_by=staff)
                messages.warning(request, f"Conduct infraction logged for {entry.student.full_name}.")
                
        if request.htmx:
            return render(request, 'core/operations/partials/entry_row.html', {
                'entry': entry,
                'session': entry.session,
            })
            
    return redirect(f"{reverse('core:roll_call_gate_hub')}?tab=roll_call&session_id={entry.session.id}")


@login_required
@role_required(*GATE_ROLES)
def gate_terminal_action_view(request, exeat_pk, action):
    """
    Rapid Gate Security terminal action to record departures and returns.
    Enforces can_log_gate_check authorization.
    """
    if not can_log_gate_check(request.user):
        messages.error(request, "Permission denied: Only authorized Gate Officers and Housemasters can log gate movements.")
        return redirect(f"{reverse('core:roll_call_gate_hub')}?tab=gate")

    exeat = get_object_or_404(
        Exeat.objects.select_related('student', 'movement'),
        pk=exeat_pk
    )
    movement, _ = ExeatMovement.objects.get_or_create(exeat=exeat)
    officer_name = request.user.get_full_name() or request.user.username

    if action == 'departure':
        movement.actual_departure = timezone.now()
        movement.departure_recorded_by = officer_name
        movement.save()
        messages.success(request, f"Gate EXIT logged for {exeat.student.full_name} ({exeat.exeat_number}).")
    elif action == 'return':
        movement.actual_return = timezone.now()
        movement.return_recorded_by = officer_name
        movement.save()
        messages.success(request, f"Gate RETURN recorded for {exeat.student.full_name} ({exeat.exeat_number}).")

    return redirect(f"{reverse('core:roll_call_gate_hub')}?tab=gate")


@login_required
@role_required(*GATE_ROLES)
def gate_verify_pass_view(request):
    """
    Real-time lookup for Gate Officers scanning or typing student passes.
    Matches by exeat_number, student admission_no, or index_number.
    """
    q = request.GET.get('q', '').strip()
    exeat = None
    if q:
        exeat = Exeat.objects.filter(
            Q(exeat_number__iexact=q) |
            Q(student__admission_no__iexact=q) |
            Q(student__index_number__iexact=q) |
            Q(exeat_number__icontains=q)
        ).select_related(
            'student', 'student__house', 'student__dormitory', 'student__programme', 'movement'
        ).first()

    return render(request, 'core/operations/partials/gate_verification_card.html', {
        'exeat': exeat,
        'query': q,
    })





# -----------------------------------------------------------------------------
# REPORTS & STATS
# -----------------------------------------------------------------------------
@login_required
@role_required(*ADMIN_ROLES)
def reports_dashboard_view(request):
    """
    High-level dashboard aggregating operational metrics.
    Restricted to superusers and housemasters.
    """
    from core.services.reports import get_dashboard_metrics
    
    metrics = get_dashboard_metrics()
    
    # Get recent infractions for the table
    recent_infractions = DisciplinaryRecord.objects.select_related(
        'student', 'student__house'
    ).order_by('-created_at')[:10]
    
    context = {
        'metrics': metrics,
        'recent_infractions': recent_infractions,
        'active_tab': 'reports'
    }
    return render(request, 'core/reports/dashboard.html', context)


# -----------------------------------------------------------------------------
# SYSTEM CONFIGURATION
# -----------------------------------------------------------------------------
@login_required
@role_required(*ADMIN_ROLES)
def configuration_hub_view(request):
    """
    System configuration Control Center hub.
    Provides quick links to Django admin and inline toggle for Academic Terms.
    """
    if not (request.user.is_superuser or getattr(request.user, 'role', '') == 'Senior Housemaster'):
        messages.error(request, "You do not have permission to access system configurations.")
        return redirect('core:dashboard')
        
    academic_terms = AcademicTerm.objects.select_related('academic_year').order_by('-academic_year__start_date', '-term')
    
    context = {
        'academic_terms': academic_terms,
        'active_tab': 'configuration'
    }
    return render(request, 'core/config/hub.html', context)


@login_required
@role_required(*ADMIN_ROLES)
def set_active_term_view(request, term_id):
    """
    HTMX endpoint to toggle the active academic term.
    """
    from core.services.configuration import set_active_term
    
    if not (request.user.is_superuser or getattr(request.user, 'role', '') == 'Senior Housemaster'):
        return HttpResponseForbidden("Unauthorized")
        
    try:
        set_active_term(term_id)
        messages.success(request, "Active academic term successfully updated.")
    except AcademicTerm.DoesNotExist:
        messages.error(request, "Academic term not found.")
        
    response = HttpResponse(status=204)
    response['HX-Redirect'] = reverse('core:configuration')
    return response

