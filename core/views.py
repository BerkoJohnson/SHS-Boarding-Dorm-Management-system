import json
import uuid
from pathlib import Path

from django.views.decorators.http import require_POST, require_http_methods
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseBadRequest, FileResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.db import IntegrityError, transaction
from django.db.models import BooleanField, Count, F, Prefetch, Q, Sum, Value
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.contrib.auth import views as auth_views
from django.views.decorators.csrf import ensure_csrf_cookie
from django.contrib.auth import logout
from django.contrib import messages
from django.views.decorators.cache import never_cache
from .models import (
    Student,
    Exeat,
    ExeatMovement,
    ExeatReconciliation,
    SMSConfiguration,
    SMSDelivery,
    House,
    Dormitory,
    DisciplinaryRecord,
    AcademicTerm,
    HouseMaster,
    StaffRole,
    UserProfile,
    get_user_profile,
    get_approvable_pending_for_user,
    get_exeat_queue_stats,
    RollCallSession,
    RollCallEntry,
    RollCallAbsenceFollowUp,
    SchoolConfiguration,
    StudentIDExport,
)
from .forms import (
    ExeatCreateForm,
    ExeatApprovalForm,
    ExeatReconciliationForm,
    DisciplinaryRecordForm,
    ReportFilterForm,
    StudentRegistrationForm,
    StudentRosterUploadForm,
)
from .permissions import (
    role_required,
    can_approve_exeat_check,
    can_log_gate_check,
    ADMIN_ROLES,
    ROLL_CALL_ROLES,
    GATE_ROLES,
    EXEAT_ROLES,
    EXEAT_APPROVAL_ROLES,
    EXEAT_CREATE_ROLES,
    CONDUCT_ROLES,
    REPORT_ROLES,
    STUDENT_MANAGEMENT_ROLES,
    STUDENT_RECORD_ROLES,
    filter_queryset_by_house,
    get_house_scope,
    get_gate_house_scope,
)
from .services.roll_call import (
    create_roll_call_session,
    update_entry_status,
    complete_roll_call_session,
    mark_all_present,
    resolve_absence_follow_up,
    reconcile_gate_departure_with_roll_calls,
)
from .services.sms import enqueue_exeat_event_sms
from .services.student_import import import_student_roster
from .services.student_id import make_qr_data_uri
from .tasks import generate_student_id_export


STUDENT_ID_LAYOUTS = {
    "classic": "Classic",
    "crest": "Crest",
    "modern": "Modern",
}


class RememberMeLoginView(auth_views.LoginView):
    template_name = "registration/login.html"
    redirect_authenticated_user = True
    remember_me_age = 14 * 24 * 60 * 60

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.request.POST.get("remember_me") == "on":
            self.request.session.set_expiry(self.remember_me_age)
        else:
            self.request.session.set_expiry(0)
        return response


def paginate_queryset(queryset, request, page_parameter='page', per_page=10):
    """Paginate a queryset while retaining the request's other query parameters."""
    page_obj = Paginator(queryset, per_page).get_page(
        request.GET.get(page_parameter)
    )
    query_parameters = request.GET.copy()
    query_parameters.pop(page_parameter, None)
    return page_obj, query_parameters.urlencode()



def confirmed_out_exeat_filter():
    return (
        Q(movement__actual_departure__isnull=False, movement__actual_return__isnull=True)
        | Q(
            reconciliation__outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
            reconciliation__actual_return__isnull=True,
        )
    )


@login_required
def dashboard_view(request):
    """
    Main Executive Dashboard for Senior Housemasters and School Administrators.
    
    Provides:
      - Grouped student population, occupancy, movement, and care metrics.
      - Latest five records from the Exeat Register with live search and status filtering.
    """
    profile = get_user_profile(request.user)
    if profile and profile.role == StaffRole.GATE_OFFICER:
        return redirect('core:gate_security')

    now = timezone.now()
    house_scope = get_house_scope(request.user)
    active_terms = AcademicTerm.objects.filter(is_current=True)

    # 1. Core Boarding & Cohort Metrics
    students = Student.objects.all()
    dormitories = Dormitory.objects.all()
    if house_scope is not None:
        students = students.filter(house_id=house_scope.pk)
        dormitories = dormitories.filter(house_id=house_scope.pk)

    total_boarders = students.filter(residency=Student.ResidencyStatus.BOARDING).count()
    day_students_count = students.filter(residency=Student.ResidencyStatus.DAY).count()
    total_students = total_boarders + day_students_count

    total_capacity = dormitories.aggregate(total=Sum('capacity'))['total'] or 0
    occupancy_rate = (
        round((total_boarders / total_capacity * 100), 1)
        if total_capacity > 0
        else 0
    )

    # 2. Exeat & Real-Time Gate Movement Metrics
    scoped_exeats = Exeat.objects.all()
    if house_scope is not None:
        scoped_exeats = scoped_exeats.filter(student__house_id=house_scope.pk)
    pending_exeats = scoped_exeats.filter(status=Exeat.Status.PENDING).count()

    confirmed_out = scoped_exeats.filter(
        status=Exeat.Status.APPROVED,
    ).filter(confirmed_out_exeat_filter())
    active_out = confirmed_out.count()

    overdue_exeats = confirmed_out.filter(expected_return__lte=now).select_related('student__house')
    overdue_count = overdue_exeats.count()

    # 3. Categorical Exeat Metrics
    medical_exeats_count = scoped_exeats.filter(
        exeat_type=Exeat.ExeatType.MEDICAL
    ).count()
    town_exeats_count = scoped_exeats.filter(
        exeat_type=Exeat.ExeatType.TOWN
    ).count()
    weekend_exeats_count = scoped_exeats.filter(
        exeat_type=Exeat.ExeatType.WEEKEND
    ).count()

    # 4. Conduct & Discipline
    disciplinary_records = DisciplinaryRecord.objects.filter(
        corrected_at__isnull=True
    )
    if house_scope is not None:
        disciplinary_records = disciplinary_records.filter(student__house_id=house_scope.pk)
    active_disciplinary_count = disciplinary_records.count()

    # 5. Master Student Exeat & Movement Register
    query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'all').strip().lower()

    exeats_qs = scoped_exeats.select_related(
        'student',
        'student__house',
        'student__dormitory',
        'student__class_group',
        'academic_term',
        'movement',
        'reconciliation',
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
        exeats_qs = exeats_qs.filter(status=Exeat.Status.APPROVED).filter(confirmed_out_exeat_filter())
    elif status_filter == 'pending':
        exeats_qs = exeats_qs.filter(status=Exeat.Status.PENDING)
    elif status_filter == 'overdue':
        exeats_qs = exeats_qs.filter(
            status=Exeat.Status.APPROVED,
            expected_return__lte=now,
        ).filter(confirmed_out_exeat_filter())
    elif status_filter == 'unresolved':
        exeats_qs = exeats_qs.filter(
            status=Exeat.Status.APPROVED,
            expected_return__lte=now,
            movement__actual_departure__isnull=True,
            movement__actual_return__isnull=True,
            reconciliation__isnull=True,
        )
    elif status_filter == 'returned':
        exeats_qs = exeats_qs.filter(
            Q(movement__actual_return__isnull=False)
            | Q(
                reconciliation__outcome=ExeatReconciliation.Outcome.RETURNED,
                reconciliation__actual_return__isnull=False,
            )
        )

    recent_exeats = list(exeats_qs[:5])
    total_matching_exeats = max(exeats_qs.count(), len(recent_exeats))

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
        'overdue_students': overdue_exeats[:5],
        'medical_exeats_count': medical_exeats_count,
        'town_exeats_count': town_exeats_count,
        'weekend_exeats_count': weekend_exeats_count,
        'active_disciplinary_count': active_disciplinary_count,
        # Master Register Table
        'recent_exeats': recent_exeats,
        'exeats': recent_exeats,
        'total_matching_exeats': total_matching_exeats,
        'total_count': total_matching_exeats,
        'search_query': query,
        'query': query,
        'dashboard_preview': True,
        'selected_type': '',
        'selected_status': status_filter,
        'selected_house': '',
        'user_house_scope': house_scope,
        'current_status_filter': status_filter,
        'current_time': now,
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
        'reconciliation',
        'approved_by',
        'created_by',
    ).all().order_by('-created_at')
    exeats = filter_queryset_by_house(request.user, exeats, 'student__house_id')

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
    if status_filter == 'approvals':
        # Two-step Step 2 view: PENDING requests *I* am authorised to review & approve.
        # Helper returns already-scoped PENDING qs; add select_related for card display.
        exeats = get_approvable_pending_for_user(request.user).select_related(
            'student', 'student__house', 'student__dormitory', 'student__class_group',
            'academic_term', 'movement', 'reconciliation', 'approved_by', 'created_by'
        ).order_by('-created_at')
    elif status_filter == 'mine':
        # Two-step Step 1 view: Requests *I* drafted / submitted.
        exeats = exeats.filter(created_by=request.user)
    elif status_filter == 'active_out':
        exeats = exeats.filter(status=Exeat.Status.APPROVED).filter(confirmed_out_exeat_filter())
    elif status_filter == 'overdue':
        exeats = exeats.filter(
            status=Exeat.Status.APPROVED,
            expected_return__lte=now,
        ).filter(confirmed_out_exeat_filter())
    elif status_filter == 'unresolved':
        exeats = exeats.filter(
            status=Exeat.Status.APPROVED,
            expected_return__lte=now,
            movement__actual_departure__isnull=True,
            movement__actual_return__isnull=True,
            reconciliation__isnull=True,
        )
    elif status_filter == 'returned':
        exeats = exeats.filter(
            Q(movement__actual_return__isnull=False)
            | Q(
                reconciliation__outcome=ExeatReconciliation.Outcome.RETURNED,
                reconciliation__actual_return__isnull=False,
            )
        )
    elif status_filter:
        exeats = exeats.filter(status=status_filter)

    houses = House.objects.all().order_by('display_order', 'name')
    house_scope = get_house_scope(request.user)
    if house_scope is not None:
        houses = houses.filter(pk=house_scope.pk)
    active_filters_count = sum(1 for v in [exeat_type, status_filter, house_id] if v)

    # Quick chip counts (two-step workflow chips in list header). Uses base counts
    # (before text/type/house filters apply) so chips always reflect stable totals.
    base_qs = filter_queryset_by_house(request.user, Exeat.objects.all(), 'student__house_id')
    count_pending_approvals = get_approvable_pending_for_user(request.user).count()
    count_my_drafts = base_qs.filter(created_by=request.user).count() if request.user.is_authenticated else 0
    count_pending_global = base_qs.filter(status=Exeat.Status.PENDING).count()
    count_active_out = base_qs.filter(status=Exeat.Status.APPROVED).filter(
        confirmed_out_exeat_filter()
    ).count()
    count_overdue = base_qs.filter(
        status=Exeat.Status.APPROVED,
        expected_return__lte=now,
    ).filter(confirmed_out_exeat_filter()).count()
    count_unresolved = base_qs.filter(
        status=Exeat.Status.APPROVED,
        expected_return__lte=now,
        movement__actual_departure__isnull=True,
        movement__actual_return__isnull=True,
        reconciliation__isnull=True,
    ).count()

    exeats_page, pagination_query = paginate_queryset(exeats, request)

    context = {
        'exeats': exeats_page,
        'exeats_page': exeats_page,
        'pagination_query': pagination_query,
        'pagination_parameter': 'page',
        'total_count': exeats_page.paginator.count,
        'houses': houses,
        'selected_type': exeat_type,
        'selected_status': status_filter,
        'selected_house': house_id,
        'active_filters_count': active_filters_count,
        'query': query,
        'count_pending_approvals': count_pending_approvals,
        'count_my_drafts': count_my_drafts,
        'count_pending_global': count_pending_global,
        'count_active_out': count_active_out,
        'count_overdue': count_overdue,
        'count_unresolved': count_unresolved,
        'current_time': now,
    }

    # Return partial if request is from HTMX search / filter
    if request.htmx:
        return render(request, 'core/exeats/partials/exeat_table.html', context)
    
    return render(request, 'core/exeats/list.html', context)


@login_required
@role_required(*EXEAT_APPROVAL_ROLES)
def exeat_approvals_hub_view(request):
    """
    Phase 4.1: Dedicated PENDING APPROVALS HUB view (optimized workflow surface
    for Housemasters/Senior HM doing desk sign-offs, NOT a generic list).

    URL:   /exeats/pending-approvals/   (name: core:exeat_approvals)
    Roles: Admin, Senior Housemaster, Housemaster only.

    UX Differences from the generic list filter:
      - Header stat cards (queue size, oldest pending, avg age, today's throughput,
        overdue returns on the radar) — encourages SLA-aware desk work.
      - Per-house queue chips to jump between houses (useful for Senior HM who
        has school-wide authority but works one house at a time).
      - Approval-OPTIMIZED card grid, not a table: each card shows the full
        student profile inline + one-click Approve / Reject (with inline
        remarks) / Cancel — all POST, no detail-page navigation required for
        routine desk work.
      - Cards are SLA-sorted by oldest -> newest (opposite of the default
        register sort) so the approver tackles overdue queue entries first.

    Optional query params:
      - q         : free text search across student/number/destination
      - house_id  : scope view to one house (driven by per-house chip bar)
    """
    query = request.GET.get('q', '').strip()
    house_id = request.GET.get('house', '').strip()
    house_scope = get_house_scope(request.user)

    # Step 1: Start from jurisdiction-scoped PENDING queue (single source of truth)
    exeats = get_approvable_pending_for_user(request.user).select_related(
        'student', 'student__house', 'student__dormitory', 'student__class_group',
        'student__guardian', 'academic_term', 'created_by'
    )
    if house_scope is not None:
        exeats = exeats.filter(student__house_id=house_scope.pk)

    # Step 2: Optional house chip filter (Senior HM often works one house at a time)
    if house_id:
        try:
            exeats = exeats.filter(student__house_id=int(house_id))
        except (ValueError, TypeError):
            pass

    # Step 3: Optional free-text search (mirrors the register)
    if query and len(query) >= 2:
        exeats = exeats.filter(
            Q(student__full_name__icontains=query)
            | Q(exeat_number__icontains=query)
            | Q(student__admission_no__icontains=query)
            | Q(student__index_number__icontains=query)
            | Q(destination__icontains=query)
        )

    # Step 4: SLA-sort — OLDEST first (opposite of the register: approvers should
    # tackle the longest-waiting queue items first.)
    exeats = exeats.order_by('created_at')

    # Step 5: Hub header stats (run aggregations against filtered queue so
    # the KPI cards reflect the current subset the user is looking at)
    stats = get_exeat_queue_stats(exeats, request.user)

    # Also pass current user's HM record so the "Signing off as" banner is accurate
    current_hm = getattr(request.user, 'housemaster', None)
    if current_hm is None and request.user.is_authenticated:
        current_hm = HouseMaster.objects.filter(
            Q(staff_id__iexact=request.user.username)
            | Q(full_name__icontains=request.user.username)
        ).first()
    houses = House.objects.all().order_by('display_order', 'name')
    if house_scope is not None:
        houses = houses.filter(pk=house_scope.pk)

    active_filters_count = sum(1 for v in [query, house_id] if v)

    context = {
        'exeats': exeats,
        'total_count': exeats.count(),
        'query': query,
        'selected_house': house_id,
        'active_filters_count': active_filters_count,
        'stats': stats,
        'current_hm': current_hm,
        'houses': houses,
        # Breadcrumb context
        'page_subtitle': 'Step 2 desk sign-offs for requests in your jurisdiction.',
    }
    return render(request, 'core/exeats/approvals_hub.html', context)


@login_required
@role_required(*ADMIN_ROLES)
def house_list_view(request):
    """Show school-wide residential occupancy and placement oversight to admins."""
    boarding = Student.ResidencyStatus.BOARDING
    house_scope = get_house_scope(request.user)
    dormitories = Dormitory.objects.annotate(
        boarder_count=Count('boarders', filter=Q(boarders__residency=boarding))
    )
    houses_qs = (
        House.objects.annotate(
            boarder_count=Count(
                'students',
                filter=Q(students__residency=boarding),
                distinct=True,
            )
        )
        .prefetch_related(
            Prefetch('dormitories', queryset=dormitories),
            'housemasters',
        )
        .order_by('display_order', 'name')
    )
    if house_scope is not None:
        houses_qs = houses_qs.filter(pk=house_scope.pk)
    houses = list(houses_qs)

    for house in houses:
        house_dormitories = list(house.dormitories.all())
        house_capacity = sum(dorm.capacity for dorm in house_dormitories)
        housed_boarders = 0
        over_capacity_dormitories = []
        near_capacity_dormitories = []

        for dorm in house_dormitories:
            dorm.occupancy_rate = (
                round(dorm.boarder_count / dorm.capacity * 100, 1)
                if dorm.capacity
                else 0
            )
            dorm.remaining_capacity = max(dorm.capacity - dorm.boarder_count, 0)
            dorm.over_capacity_count = max(dorm.boarder_count - dorm.capacity, 0)
            dorm.is_over_capacity = dorm.boarder_count > dorm.capacity
            housed_boarders += dorm.boarder_count
            if dorm.is_over_capacity:
                over_capacity_dormitories.append(dorm.name)
            elif dorm.capacity and dorm.occupancy_rate >= 90:
                near_capacity_dormitories.append(dorm.name)

        house.capacity = house_capacity
        house.housed_boarders = housed_boarders
        house.unplaced_boarders = max(house.boarder_count - housed_boarders, 0)
        house.occupancy_rate = (
            round(housed_boarders / house_capacity * 100, 1)
            if house_capacity
            else 0
        )
        house.has_housemaster = bool(house.housemasters.all())
        house.attention_items = []

        if not house.has_housemaster:
            house.attention_items.append('No housemaster assigned')
        if not house_dormitories:
            house.attention_items.append('No dormitories configured')
        if house.unplaced_boarders:
            house.attention_items.append(
                f'{house.unplaced_boarders} boarder(s) without a matching dormitory placement'
            )
        if over_capacity_dormitories:
            house.attention_items.append(
                f'Over capacity: {", ".join(over_capacity_dormitories)}'
            )
        if near_capacity_dormitories:
            house.attention_items.append(
                f'Near capacity: {", ".join(near_capacity_dormitories)}'
            )

        house.is_critical = bool(
            over_capacity_dormitories or not house.has_housemaster
        )
        house.health_label = (
            'Critical'
            if house.is_critical
            else 'Needs attention'
            if house.attention_items
            else 'Healthy'
        )

    boarders = Student.objects.filter(residency=boarding)
    capacity_qs = Dormitory.objects.all()
    if house_scope is not None:
        boarders = boarders.filter(house_id=house_scope.pk)
        unassigned_boarders = boarders.filter(dormitory__isnull=True).count()
        capacity_qs = capacity_qs.filter(house_id=house_scope.pk)
    else:
        unassigned_boarders = boarders.filter(
            Q(house__isnull=True) | Q(dormitory__isnull=True)
        ).count()
    total_boarders = boarders.count()
    total_capacity = capacity_qs.aggregate(total=Sum('capacity'))['total'] or 0
    housed_boarders = max(total_boarders - unassigned_boarders, 0)
    overall_occupancy_rate = (
        round(housed_boarders / total_capacity * 100, 1)
        if total_capacity
        else 0
    )
    attention_houses = sum(bool(house.attention_items) for house in houses)

    context = {
        'houses': houses,
        'total_boarders': total_boarders,
        'unassigned_boarders': unassigned_boarders,
        'total_capacity': total_capacity,
        'overall_occupancy_rate': overall_occupancy_rate,
        'attention_houses': attention_houses,
    }
    return render(request, 'core/houses/list.html', context)


@login_required
@role_required(*STUDENT_MANAGEMENT_ROLES)
def student_registry_view(request):
    query = request.GET.get("q", "").strip()
    residency = request.GET.get("residency", "").strip()
    students = Student.objects.select_related(
        "programme", "house", "dormitory", "class_group", "guardian"
    )
    if query:
        students = students.filter(
            Q(admission_no__icontains=query)
            | Q(index_number__icontains=query)
            | Q(full_name__icontains=query)
            | Q(programme__name__icontains=query)
            | Q(house__name__icontains=query)
        )
    if residency in Student.ResidencyStatus.values:
        students = students.filter(residency=residency)

    students_page, pagination_query = paginate_queryset(
        students.order_by("full_name", "admission_no"),
        request,
        per_page=50,
    )
    return render(
        request,
        "core/students/registry.html",
        {
            "students_page": students_page,
            "pagination_query": pagination_query,
            "query": query,
            "selected_residency": residency,
        },
    )


@login_required
@role_required(*STUDENT_MANAGEMENT_ROLES)
def student_registration_view(request, student_pk=None):
    student = (
        get_object_or_404(
            Student.objects.select_related("guardian"), pk=student_pk
        )
        if student_pk
        else None
    )
    form = StudentRegistrationForm(
        request.POST or None,
        request.FILES or None,
        instance=student,
    )
    if request.method == "POST" and form.is_valid():
        try:
            saved_student = form.save()
        except IntegrityError:
            form.add_error(
                None,
                "A student identifier was just used by another record. "
                "Refresh the registry and check the admission and index numbers.",
            )
        else:
            messages.success(
                request,
                f"Student record saved for {saved_student.full_name}.",
            )
            return redirect("core:student_registry")
    return render(
        request,
        "core/students/registration_form.html",
        {
            "form": form,
            "student": student,
            "houses": House.objects.order_by("display_order", "name"),
            "dormitories": Dormitory.objects.select_related("house").order_by(
                "house__display_order", "house__name", "name"
            ),
        },
    )


@login_required
@role_required(*STUDENT_MANAGEMENT_ROLES)
def student_roster_import_view(request):
    form = StudentRosterUploadForm(request.POST or None, request.FILES or None)
    errors = []
    error_count = 0
    imported_count = 0
    if request.method == "POST" and form.is_valid():
        imported_count, errors = import_student_roster(
            form.cleaned_data["roster_file"]
        )
        error_count = len(errors)
        if imported_count:
            messages.success(
                request,
                f"Imported {imported_count} student record(s) successfully.",
            )
            return redirect("core:student_registry")
    return render(
        request,
        "core/students/import.html",
        {
            "form": form,
            "errors": errors[:200],
            "error_count": error_count,
            "omitted_error_count": max(error_count - 200, 0),
            "imported_count": imported_count,
        },
    )


@login_required
@role_required(*STUDENT_MANAGEMENT_ROLES)
def student_import_template_view(request):
    response = HttpResponse(
        "Admission No.,Index Number,Full Name,Gender,Date of Birth,Programme,"
        "Class,Residency,House,Dormitory,Status,Guardian Name,Guardian Contact,"
        "Guardian Alt Contact\r\n",
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = (
        'attachment; filename="student-import-template.csv"'
    )
    return response


@login_required
@role_required(*STUDENT_RECORD_ROLES)
def student_record_view(request, student_pk):
    students = Student.objects.select_related(
        "programme",
        "house",
        "dormitory",
        "class_group",
        "guardian",
    )
    students = filter_queryset_by_house(request.user, students, "house_id")
    student = get_object_or_404(students, pk=student_pk)

    exeats = student.exeats.select_related(
        "academic_term",
        "approved_by",
        "created_by",
        "movement",
        "reconciliation",
    ).order_by("-expected_departure", "-created_at")
    disciplinary_records = student.disciplinary_records.select_related(
        "academic_term",
        "logged_by",
        "corrected_by",
        "roll_call_entry",
        "roll_call_follow_up",
    ).order_by("-created_at")
    roll_call_entries = student.roll_call_entries.select_related(
        "session",
        "session__house",
        "session__dormitory",
        "session__conducted_by",
        "gate_conflict_reviewed_by",
    ).prefetch_related(
        "absence_follow_ups__reported_by",
        "absence_follow_ups__resolved_by",
    ).order_by("-session__date", "-created_at")

    context = {
        "student": student,
        "exeats": exeats,
        "disciplinary_records": disciplinary_records,
        "roll_call_entries": roll_call_entries,
        "exeat_count": exeats.count(),
        "disciplinary_record_count": disciplinary_records.count(),
        "roll_call_entry_count": roll_call_entries.count(),
        "pending_absence_follow_up_count": RollCallAbsenceFollowUp.objects.filter(
            entry__student=student,
            status=RollCallAbsenceFollowUp.Status.PENDING,
        ).count(),
    }
    return render(request, "core/students/record.html", context)


@login_required
@role_required(*STUDENT_MANAGEMENT_ROLES)
def student_id_card_view(request, student_pk):
    layout = request.GET.get("layout", "classic")
    if layout not in STUDENT_ID_LAYOUTS:
        return HttpResponseBadRequest("Choose a supported student ID layout.")

    student = get_object_or_404(
        Student.objects.select_related(
            "programme", "house", "dormitory", "class_group"
        ),
        pk=student_pk,
    )
    public_url = request.build_absolute_uri(
        reverse("core:public_student_exeat", args=[student.public_token])
    )
    return render(
        request,
        "core/students/id_card.html",
        {
            "student": student,
            "school_configuration": SchoolConfiguration.get_solo(),
            "public_url": public_url,
            "qr_code": make_qr_data_uri(public_url),
            "layouts": STUDENT_ID_LAYOUTS.items(),
            "selected_layout": layout,
        },
    )


@login_required
@role_required(*STUDENT_MANAGEMENT_ROLES)
@require_POST
def student_rotate_public_token_view(request, student_pk):
    student = get_object_or_404(Student, pk=student_pk)
    student.public_token = uuid.uuid4()
    student.save(update_fields=["public_token", "updated_at"])
    messages.success(
        request,
        "The student's QR link was replaced. Previously printed QR codes no longer work.",
    )
    return redirect("core:student_id_card", student_pk=student.pk)


@never_cache
def public_student_exeat_view(request, token):
    student = get_object_or_404(
        Student.objects.select_related("programme", "house", "class_group"),
        public_token=token,
    )
    now = timezone.now()
    active_exeat = (
        student.exeats.filter(
            status=Exeat.Status.APPROVED,
        )
        .filter(
            Q(
                expected_departure__lte=now,
                expected_return__gt=now,
            )
            | Q(
                movement__actual_departure__isnull=False,
                movement__actual_return__isnull=True,
            )
            | Q(
                reconciliation__outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
            )
        )
        .exclude(movement__actual_return__isnull=False)
        .exclude(
            reconciliation__outcome__in=(
                ExeatReconciliation.Outcome.UNUSED,
                ExeatReconciliation.Outcome.RETURNED,
            )
        )
        .order_by("-expected_departure", "-created_at", "-pk")
        .first()
    )
    exeat = active_exeat or student.exeats.order_by("-created_at", "-pk").first()
    movement = (
        ExeatMovement.objects.filter(exeat=exeat).first()
        if exeat and exeat.status == Exeat.Status.APPROVED
        else None
    )
    response = render(
        request,
        "core/students/public_exeat_check.html",
        {
            "student": student,
            "exeat": exeat,
            "movement": movement,
            "school_configuration": SchoolConfiguration.get_solo(),
        },
    )
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


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
    house_scope = get_house_scope(request.user)

    records_qs = DisciplinaryRecord.objects.select_related(
        'student',
        'student__house',
        'student__dormitory',
        'student__class_group',
        'academic_term',
        'logged_by',
        'corrected_by',
        'roll_call_entry',
    ).all().order_by('-created_at')
    records_qs = filter_queryset_by_house(
        request.user, records_qs, 'student__house_id'
    )

    # 1. Text Search Filter
    if query:
        records_qs = records_qs.filter(
            Q(student__full_name__icontains=query) |
            Q(student__admission_no__icontains=query) |
            Q(offense__icontains=query) |
            Q(action_taken__icontains=query) |
            Q(description__icontains=query) |
            Q(correction_note__icontains=query)
        )

    # 2. Severity Filter
    if severity:
        records_qs = records_qs.filter(severity=severity)

    # 3. House Filter
    if house_id:
        records_qs = records_qs.filter(student__house_id=house_id)

    # Top stats aggregations
    all_records = filter_queryset_by_house(
        request.user,
        DisciplinaryRecord.objects.filter(corrected_at__isnull=True),
        'student__house_id'
    )
    total_incidents = all_records.count()
    critical_count = all_records.filter(severity=DisciplinaryRecord.Severity.CRITICAL).count()
    major_count = all_records.filter(severity=DisciplinaryRecord.Severity.MAJOR).count()
    minor_count = all_records.filter(severity=DisciplinaryRecord.Severity.MINOR).count()

    houses = House.objects.all().order_by('display_order', 'name')
    if house_scope is not None:
        houses = houses.filter(pk=house_scope.pk)
    active_filters_count = sum(1 for v in [severity, house_id] if v)

    records_page, pagination_query = paginate_queryset(records_qs, request)

    context = {
        'records': records_page,
        'records_page': records_page,
        'pagination_query': pagination_query,
        'pagination_parameter': 'page',
        'total_matching': records_page.paginator.count,
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
    get_house_scope(request.user)
    selected_student_id = request.GET.get('student_id') or request.POST.get('student')
    selected_student = None

    if selected_student_id:
        selected_student = filter_queryset_by_house(
            request.user,
            Student.objects.select_related(
                'house', 'dormitory', 'class_group', 'guardian'
            ),
            'house_id',
        ).filter(pk=selected_student_id).first()

    if request.method == 'POST':
        form = DisciplinaryRecordForm(request.POST)
        form.fields['student'].queryset = filter_queryset_by_house(
            request.user, form.fields['student'].queryset, 'house_id'
        )
        if form.is_valid():
            record = form.save(commit=False)
            current_term = AcademicTerm.objects.filter(is_current=True).first()
            if current_term:
                record.academic_term = current_term
            record.logged_by = HouseMaster.objects.filter(user=request.user).first()
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
        form.fields['student'].queryset = filter_queryset_by_house(
            request.user, form.fields['student'].queryset, 'house_id'
        )

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
        'housemasters': filter_queryset_by_house(
            request.user,
            HouseMaster.objects.select_related('house').all(),
            'house_id',
        ),
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
        students_qs = Student.objects.select_related('house', 'dormitory', 'class_group').filter(
            Q(full_name__icontains=query) |
            Q(admission_no__icontains=query) |
            Q(index_number__icontains=query)
        )
        students = filter_queryset_by_house(
            request.user, students_qs, 'house_id'
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
    house_scope = get_house_scope(request.user)
    selected_student_id = request.GET.get('student_id') or request.POST.get('student')
    selected_student = None

    if selected_student_id:
        selected_student = filter_queryset_by_house(
            request.user,
            Student.objects.select_related(
                'house', 'dormitory', 'class_group', 'guardian'
            ),
            'house_id',
        ).filter(pk=selected_student_id).first()

    if request.method == 'POST':
        form = ExeatCreateForm(request.POST)
        form.fields['student'].queryset = filter_queryset_by_house(
            request.user, form.fields['student'].queryset, 'house_id'
        )
        if form.is_valid():
            exeat = form.save(commit=False)
            # Audit trail (Phase 1): record WHO drafted / submitted this leave request.
            exeat.created_by = request.user
            exeat.save()
            enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.REQUESTED)

            # INTENTIONALLY NOT creating ExeatMovement HERE.
            # Movement is created ONLY during Step 2 (PENDING -> APPROVED transition),
            # handled inside exeat_action_view. No gate log = no valid departure.

            # Build a transparent routing note for the success flash.
            house_hm_name = None
            senior_hm_name = None
            if exeat.student and exeat.student.house_id:
                house_hm = HouseMaster.objects.filter(
                    house_id=exeat.student.house_id
                ).order_by('-is_senior_housemaster').first()
                house_hm_name = house_hm.full_name if house_hm else None
            senior_hm = HouseMaster.objects.filter(is_senior_housemaster=True).first()
            senior_hm_name = senior_hm.full_name if senior_hm else None
            routing_bits = []
            if exeat.student and exeat.student.house and house_hm_name:
                routing_bits.append(f"{exeat.student.house.name} Housemaster ({house_hm_name})")
            if senior_hm_name:
                routing_bits.append(f"Senior Housemaster ({senior_hm_name})")
            if routing_bits:
                routing_note = f" Routed to: {'; '.join(routing_bits)}. Status: PENDING. No gate log until approved."
            else:
                routing_note = " Status: PENDING Housemaster review."

            messages.success(
                request,
                f"Exeat Request {exeat.exeat_number} submitted for {exeat.student.full_name}.{routing_note}"
            )
            # Redirect to list so submitter sees their freshly-submitted PENDING entry,
            # alongside the rest of the register (not the dashboard).
            return redirect('core:exeat_list')
        else:
            messages.error(request, "Please correct the errors in the exeat request form below.")
    else:
        initial_data = {}
        if selected_student:
            initial_data['student'] = selected_student.id

        form = ExeatCreateForm(initial=initial_data)
        form.fields['student'].queryset = filter_queryset_by_house(
            request.user, form.fields['student'].queryset, 'house_id'
        )

    housemasters = HouseMaster.objects.select_related('house').all()
    if house_scope is not None:
        housemasters = housemasters.filter(
            Q(house_id=house_scope.pk) | Q(is_senior_housemaster=True)
        )
    active_terms = AcademicTerm.objects.filter(is_current=True)

    # Step-1 routing preview (used by right-column banner 'Will be routed to …')
    routing_target = None
    if selected_student is not None and selected_student.house_id is not None:
        house_housemaster = (
            HouseMaster.objects.filter(house_id=selected_student.house_id)
            .order_by('-is_senior_housemaster', 'full_name').first()
        )
        senior_housemaster = HouseMaster.objects.filter(is_senior_housemaster=True).first()
        if house_housemaster or senior_housemaster:
            routing_target = {
                'house': selected_student.house,
                'house_housemaster': house_housemaster,
                'senior_housemaster': senior_housemaster,
            }

    context = {
        'form': form,
        'selected_student': selected_student,
        'housemasters': housemasters,
        'active_terms': active_terms,
        'workflow_mode': 'request',
        'routing_target': routing_target,
        'user_house_scope': house_scope,
    }
    return render(request, 'core/exeats/create.html', context)


@login_required
@role_required(*EXEAT_ROLES)
def exeat_detail_view(request, pk):
    """
    Renders the official details and printable exeat pass slip for a student.
    """
    exeat = get_object_or_404(
        filter_queryset_by_house(
            request.user,
            Exeat.objects.select_related(
            'student__house',
            'student__dormitory',
            'student__class_group',
            'student__guardian',
            'academic_term',
            'movement',
            'reconciliation',
            'approved_by',
            'created_by',
            ),
            'student__house_id',
        ),
        pk=pk
    )
    can_approve_exeat = can_approve_exeat_check(request.user, exeat)

    return render(request, 'core/exeats/detail.html', {
        'exeat': exeat,
        'can_approve_exeat': can_approve_exeat,
        'current_time': timezone.now(),
    })


@login_required
@role_required(*EXEAT_APPROVAL_ROLES)
def exeat_approval_review_view(request, pk):
    """
    Phase 4.2: Dedicated Step-2 REVIEW PAGE.

    URL:  /exeats/<uuid:pk>/review/   (name: core:exeat_review)

    This is the long-form, form-based counterpart to the quick POST-only
    approve/reject/cancel actions. Where the 3-dot menu + detail ribbon +
    hub cards only let you "fire and forget" a decision, this page surfaces
    the full ExeatApprovalForm so complex cases can have:

      * Explicit Approve vs Reject dropdown (choice is sticky on save errors)
      * Scoped `approved_by` dropdown (student's own HM + Senior HM only)
        — auto-prefilled to current user if they are a HM record with jurisdiction
      * `approval_remarks` (conditions if Approving, REQUIRED if Rejecting)
      * Full READ-ONLY request + student summary above the form so the
        approver has the full context on the same page (no tab switching)

    Access rules:
      * Only EXEAT_APPROVAL_ROLES (Admin, Senior HM, Housemaster)
      * User must pass `can_approve_exeat_check` (belongs to student's house
        OR Senior HM OR Admin)  — hard 403 if violated, regardless of decorator
      * Exeat MUST currently be status = PENDING — if already approved or
        rejected we redirect to detail with a friendly info flash.
    """
    exeat = get_object_or_404(
        filter_queryset_by_house(
            request.user,
            Exeat.objects.select_related(
                'student',
                'student__house',
                'student__dormitory',
                'student__class_group',
                'student__guardian',
                'academic_term',
                'created_by',
            ),
            'student__house_id',
        ),
        pk=pk
    )

    # 1. Double-gate jurisdiction — decorator narrows role; this guard narrows SCOPE.
    if not can_approve_exeat_check(request.user, exeat):
        house_label = (
            exeat.student.house.name
            if (exeat.student and exeat.student.house)
            else "this House"
        )
        messages.error(
            request,
            f"Review access denied. Only the Housemaster for {house_label} "
            f"or the Senior Housemaster can review this pending request."
        )
        return redirect('core:exeat_detail', pk=pk)

    # 2. State-machine guard: only PENDING requests can enter Step-2 review.
    if exeat.status != Exeat.Status.PENDING:
        messages.info(
            request,
            f"Exeat {exeat.exeat_number} is already '{exeat.get_status_display()}'; "
            f"review page is only available for PENDING requests."
        )
        return redirect('core:exeat_detail', pk=pk)

    # 3. Bind / instantiate the Step-2 form.
    if request.method == 'POST':
        form = ExeatApprovalForm(
            request.POST,
            instance=exeat,
            student=exeat.student,
            reviewing_user=request.user,
        )
        if form.is_valid():
            reviewed = form.save(commit=False)

            # Safety net: regardless of the dropdown values submitted, if
            # the current user is a HM with jurisdiction and no authorizer
            # was explicitly chosen, stamp them as the signatory.
            if reviewed.status == Exeat.Status.APPROVED and not reviewed.approved_by_id:
                hm = getattr(request.user, 'housemaster', None)
                if hm is None:
                    hm = HouseMaster.objects.filter(
                        Q(staff_id__iexact=request.user.username)
                        | Q(full_name__icontains=request.user.username)
                    ).first()
                if hm is not None and (
                    hm.is_senior_housemaster
                    or (
                        exeat.student.house_id is not None
                        and hm.house_id == exeat.student.house_id
                    )
                ):
                    reviewed.approved_by = hm

            reviewed.save()
            if reviewed.status == Exeat.Status.APPROVED:
                enqueue_exeat_event_sms(reviewed, SMSDelivery.EventType.APPROVED)
            elif reviewed.status == Exeat.Status.REJECTED:
                enqueue_exeat_event_sms(reviewed, SMSDelivery.EventType.REJECTED)

            # Step-2 transition: create gate movement log ONLY on APPROVAL.
            movement_created = False
            if reviewed.status == Exeat.Status.APPROVED:
                _mv, movement_created = ExeatMovement.objects.get_or_create(exeat=reviewed)

            # Human-friendly success flash with different text per decision.
            signatory = reviewed.approved_by.full_name if reviewed.approved_by else "Senior Housemaster"
            if reviewed.status == Exeat.Status.APPROVED:
                movement_msg = (
                    " Gate movement log entry created — student may now depart through the main gate."
                    if movement_created else " Gate movement log entry confirmed."
                )
                messages.success(
                    request,
                    f"Exeat {reviewed.exeat_number} APPROVED — signed off by {signatory}.{movement_msg}"
                )
            elif reviewed.status == Exeat.Status.REJECTED:
                reason_note = " (with reason recorded)" if reviewed.approval_remarks else ""
                messages.success(
                    request,
                    f"Exeat {reviewed.exeat_number} REJECTED{reason_note}. Request closed and logged."
                )

            # Redirect to detail page so the approver can review the
            # decision-stamped pass (and print if Approved).
            return redirect('core:exeat_detail', pk=pk)
        else:
            # Form-level validation errors (e.g. required remarks on Reject).
            messages.error(request, "Please correct the review-form errors below before submitting.")
    else:
        form = ExeatApprovalForm(
            instance=exeat,
            student=exeat.student,
            reviewing_user=request.user,
        )

    # --- Context prep -------------------------------------------------------
    now = timezone.now()
    queue_age_hours = None
    if exeat.created_at:
        queue_age_hours = round((now - exeat.created_at).total_seconds() / 3600, 1)

    # Identify current user's HM record so "Signing off as" banner is clear.
    current_hm = getattr(request.user, 'housemaster', None)
    if current_hm is None and request.user.is_authenticated:
        current_hm = HouseMaster.objects.filter(
            Q(staff_id__iexact=request.user.username)
            | Q(full_name__icontains=request.user.username)
        ).first()

    context = {
        'exeat': exeat,
        'form': form,
        'current_hm': current_hm,
        'queue_age_hours': queue_age_hours,
        'page_subtitle': (
            'Step 2 — Review pending request. Approve (creates gate log) or '
            'Reject (reason required). All context shown above the form.'
        ),
    }
    return render(request, 'core/exeats/review.html', context)


@login_required
@role_required(*EXEAT_ROLES)
@require_http_methods(["GET", "POST"])
def exeat_reconcile_view(request, pk):
    exeat = get_object_or_404(
        filter_queryset_by_house(
            request.user,
            Exeat.objects.select_related("student__house", "movement", "reconciliation"),
            'student__house_id',
        ),
        pk=pk,
    )
    if not can_approve_exeat_check(request.user, exeat):
        return HttpResponseForbidden("Only the responsible Housemaster or Senior Housemaster can reconcile this pass.")
    if exeat.status != Exeat.Status.APPROVED:
        messages.error(request, "Only approved exeat passes can be reconciled.")
        return redirect("core:exeat_detail", pk=exeat.pk)
    if timezone.now() < exeat.expected_departure:
        messages.error(request, "A pass can only be reconciled after its scheduled departure time.")
        return redirect("core:exeat_detail", pk=exeat.pk)
    if ExeatReconciliation.objects.filter(exeat=exeat).exists():
        messages.error(request, "This pass has already been reconciled.")
        return redirect("core:exeat_detail", pk=exeat.pk)

    movement = ExeatMovement.objects.filter(exeat=exeat).first()
    if movement and (movement.actual_departure or movement.actual_return):
        messages.error(request, "This pass already has gate movement records and cannot be reconciled.")
        return redirect("core:exeat_detail", pk=exeat.pk)

    form = ExeatReconciliationForm(request.POST or None)
    form.instance.exeat = exeat
    form.instance.reconciled_by = request.user
    if request.method == "POST" and form.is_valid():
        reconciliation = form.save(commit=False)
        reconciliation.exeat = exeat
        reconciliation.reconciled_by = request.user
        reconciliation.save()
        if reconciliation.outcome == ExeatReconciliation.Outcome.STUDENT_OUT:
            enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.DEPARTURE)
        elif reconciliation.outcome == ExeatReconciliation.Outcome.RETURNED:
            enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.RETURN)
        messages.success(request, f"Movement records reconciled for {exeat.exeat_number}.")
        return redirect("core:exeat_detail", pk=exeat.pk)

    return render(
        request,
        "core/exeats/reconcile.html",
        {"exeat": exeat, "form": form},
    )


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
    exeat = get_object_or_404(
        filter_queryset_by_house(
            request.user,
            Exeat.objects.select_related('student__house', 'movement'),
            'student__house_id',
        ),
        pk=pk,
    )
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
                elif ExeatReconciliation.objects.filter(exeat=exeat).exists():
                    messages.error(request, "This pass has been reconciled; a new gate departure cannot be recorded.")
                elif now < exeat.expected_departure:
                    messages.error(
                        request,
                        f"Gate departure for {exeat.exeat_number} is available at "
                        f"{timezone.localtime(exeat.expected_departure).strftime('%d %b %Y, %H:%M')}."
                    )
                elif ExeatMovement.objects.filter(exeat=exeat, actual_departure__isnull=False).exists():
                    messages.error(request, f"Gate departure is already recorded for {exeat.exeat_number}.")
                else:
                    movement, _ = ExeatMovement.objects.get_or_create(exeat=exeat)
                    movement.actual_departure = now
                    movement.departure_recorded_by = user_name
                    movement.save()
                    enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.DEPARTURE)
                    messages.success(request, f"Gate departure recorded for {exeat.student.full_name}.")
            elif action == 'return':
                if exeat.status != Exeat.Status.APPROVED:
                    messages.error(request, "Cannot record gate return for an unapproved exeat.")
                else:
                    reconciliation = ExeatReconciliation.objects.filter(
                        exeat=exeat,
                        outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
                        actual_return__isnull=True,
                    ).first()
                    if reconciliation:
                        reconciliation.actual_return = now
                        reconciliation.outcome = ExeatReconciliation.Outcome.RETURNED
                        reconciliation.return_recorded_by = request.user
                        reconciliation.save()
                        enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.RETURN)
                        messages.success(request, f"Gate return recorded for {exeat.student.full_name}.")
                    else:
                        movement = ExeatMovement.objects.filter(exeat=exeat).first()
                        if not movement or not movement.actual_departure:
                            messages.error(
                                request,
                                "A return cannot be recorded without a confirmed departure. "
                                "Use Reconcile Pass if the departure was missed.",
                            )
                        elif movement.actual_return:
                            messages.error(request, f"Gate return is already recorded for {exeat.exeat_number}.")
                        else:
                            movement.actual_return = now
                            movement.return_recorded_by = user_name
                            movement.save()
                            enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.RETURN)
                            messages.success(request, f"Gate return recorded for {exeat.student.full_name}.")

        # 2. Authorization workflow (approve / reject / cancel)
        elif action in ('approve', 'reject', 'cancel'):
            # Role guard (belt + suspenders — permission decorator already gates view,
            # but we additionally narrow to EXEAT_APPROVAL_ROLES for these three actions).
            if not can_approve_exeat_check(request.user, exeat):
                house_label = exeat.student.house.name if (exeat.student and exeat.student.house) else "this House"
                messages.error(
                    request,
                    f"Permission denied: Only the Housemaster for {house_label} "
                    f"or the Senior Housemaster can {action} this exeat."
                )

            # STATE-MACHINE GUARD — two-step rule: Approve/Reject may only be run on PENDING requests.
            # Already-approved/rejected passes can only be Cancelled (or edited by Admin separately).
            elif action in ('approve', 'reject') and exeat.status != Exeat.Status.PENDING:
                messages.error(
                    request,
                    f"Cannot {action} exeat {exeat.exeat_number}: its status is '{exeat.get_status_display()}'. "
                    f"Approve and Reject actions are only allowed on requests currently marked as PENDING. "
                    f"Use Cancel to withdraw an Approved pass instead."
                )

            # REJECT — capture a one-off rejection reason from POST if provided, store in approval_remarks
            elif action == 'reject':
                rejection_reason = (request.POST.get('approval_remarks') or '').strip()
                if not rejection_reason and not exeat.approval_remarks:
                    messages.warning(
                        request,
                        f"Exeat {exeat.exeat_number} was REJECTED. Note: no rejection reason was captured. "
                        f"Consider adding a reason in approval remarks for the student's records and audit trail."
                    )
                if rejection_reason:
                    exeat.approval_remarks = rejection_reason
                exeat.status = Exeat.Status.REJECTED
                exeat.save()
                enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.REJECTED)
                messages.info(request, f"Exeat request {exeat.exeat_number} was rejected.")

            elif action == 'approve':
                # --- Approved-by attribution ---
                # Auto-link to the currently-authenticated user when: (a) user has a
                # HouseMaster profile AND (b) that profile is actually the student's own
                # Housemaster OR the Senior Housemaster. Otherwise keep the existing one.
                if not exeat.approved_by or not (
                    exeat.approved_by.house_id == (exeat.student.house_id if exeat.student else None)
                    or exeat.approved_by.is_senior_housemaster
                ):
                    current_hm = getattr(request.user, 'housemaster', None)
                    if current_hm is None:
                        current_hm = HouseMaster.objects.filter(
                            models.Q(staff_id__iexact=request.user.username)
                            | models.Q(full_name__icontains=request.user.username)
                        ).first()

                    student_house_id = exeat.student.house_id if exeat.student else None
                    if current_hm is not None and (
                        current_hm.is_senior_housemaster or current_hm.house_id == student_house_id
                    ):
                        # Jurisdiction OK — stamp current user as the signatory
                        exeat.approved_by = current_hm

                exeat.status = Exeat.Status.APPROVED
                # Note: Exeat.clean() + Exeat.save() both run full_clean so
                # approved_by-is-set and jurisdiction rules from Phase 1 are enforced here.
                exeat.save()
                enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.APPROVED)

                # Step 2 transition: gate movement placeholder is ONLY created when the
                # request moves from PENDING -> APPROVED during Housemaster review.
                # (Previously this happened at creation time; it now happens on approval.)
                ExeatMovement.objects.get_or_create(exeat=exeat)

                messages.success(
                    request,
                    f"Exeat pass {exeat.exeat_number} marked as Approved. "
                    f"Gate movement log entry created — student may now depart through the main gate."
                )

            elif action == 'cancel':
                movement = ExeatMovement.objects.filter(exeat=exeat).first()
                if exeat.status not in (Exeat.Status.PENDING, Exeat.Status.APPROVED):
                    messages.error(
                        request,
                        f"Exeat pass {exeat.exeat_number} cannot be cancelled because it is "
                        f"{exeat.get_status_display().lower()}."
                    )
                elif ExeatReconciliation.objects.filter(exeat=exeat).exists():
                    messages.error(request, "A reconciled exeat pass cannot be cancelled.")
                elif movement and movement.actual_departure:
                    messages.error(
                        request,
                        f"Exeat pass {exeat.exeat_number} cannot be cancelled after gate departure has been recorded."
                    )
                else:
                    # Pending and approved passes may be cancelled before departure.
                    exeat.status = Exeat.Status.CANCELLED
                    exeat.save()
                    messages.info(request, f"Exeat pass {exeat.exeat_number} was cancelled and voided.")

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
    house_scope = get_house_scope(request.user)

    # 1. Search Students
    student_results = Student.objects.filter(
            Q(full_name__icontains=query) |
            Q(admission_no__icontains=query) |
            Q(index_number__icontains=query)
        ).select_related('house', 'dormitory', 'programme')
    student_results = filter_queryset_by_house(
        request.user, student_results, 'house_id'
    )
    students = list(student_results[:5])
    
    # 2. Search Exeat Passes
    exeat_results = Exeat.objects.filter(
            Q(exeat_number__icontains=query) |
            Q(destination__icontains=query) |
            Q(student__full_name__icontains=query) |
            Q(student__admission_no__icontains=query)
        ).select_related('student', 'student__house', 'movement')
    exeat_results = filter_queryset_by_house(
        request.user, exeat_results, 'student__house_id'
    )
    exeats = list(exeat_results[:5])
    
    profile = get_user_profile(request.user)
    role = profile.role if profile else None
    is_super = request.user.is_superuser

    # 3. Search Houses & Dormitories (Restricted to administration roles)
    houses = []
    dormitories = []
    if is_super or role in ADMIN_ROLES:
        house_results = House.objects.filter(name__icontains=query)
        house_results = filter_queryset_by_house(
            request.user, house_results, 'pk'
        )
        houses = list(house_results[:4])
        dormitory_results = Dormitory.objects.filter(
                Q(name__icontains=query) | Q(house__name__icontains=query)
            ).select_related('house')
        dormitory_results = filter_queryset_by_house(
            request.user, dormitory_results, 'house_id'
        )
        dormitories = list(dormitory_results[:4])
    
    # 4. Search Disciplinary Records (Restricted to conduct roles)
    conduct_records = []
    if is_super or role in CONDUCT_ROLES:
        conduct_results = DisciplinaryRecord.objects.filter(
                Q(offense__icontains=query) |
                Q(student__full_name__icontains=query) |
                Q(action_taken__icontains=query)
            ).select_related('student', 'student__house')
        conduct_results = filter_queryset_by_house(
            request.user, conduct_results, 'student__house_id'
        )
        conduct_records = list(conduct_results[:5])
    
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
            'url': reverse('core:gate_security'),
            'badge': 'Gate Log',
            'badge_color': 'bg-blue-50 text-blue-700 border-blue-200',
            'icon': 'gate',
            'keywords': ['gate', 'security', 'movement', 'departure', 'arrival', 'return', 'check', 'out', 'in'],
        })

    if is_super or role in ADMIN_ROLES or (
        house_scope is not None and role in ROLL_CALL_ROLES
    ):
        system_quick_actions.append({
            'title': 'Roll Call Sessions',
            'description': 'Dormitory roll call inspection register and attendance roster',
            'url': reverse('core:roll_call_sessions'),
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

    if is_super or role in ADMIN_ROLES:
        system_quick_actions.append({
            'title': 'Houses & Dormitories Overview',
            'description': 'View boarding houses, dorm capacity, and bed allocations',
            'url': reverse('core:house_list'),
            'badge': 'Housing',
            'badge_color': 'bg-purple-50 text-purple-700 border-purple-200',
            'icon': 'building',
            'keywords': ['house', 'dorm', 'dormitory', 'bed', 'capacity', 'occupancy', 'hostel'],
        })

    if is_super or role in REPORT_ROLES:
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
@role_required(*ROLL_CALL_ROLES)
def roll_call_sessions_view(request):
    """Render house-scoped roll-call sessions without gate-security data."""
    house_scope = get_house_scope(request.user)
    houses = House.objects.all().order_by('display_order', 'name').prefetch_related('dormitories')
    if house_scope is not None:
        houses = houses.filter(pk=house_scope.pk)

    selected_house_id = request.GET.get('house', '').strip()
    selected_status = request.GET.get('session_status', '').strip()
    sessions_qs = RollCallSession.objects.select_related(
        'house', 'dormitory', 'conducted_by', 'academic_term'
    ).prefetch_related('entries').order_by('-date', '-created_at')

    if house_scope is not None:
        selected_house_id = str(house_scope.pk)
        sessions_qs = sessions_qs.filter(house_id=house_scope.pk)
    elif selected_house_id:
        sessions_qs = sessions_qs.filter(house_id=selected_house_id)
    if selected_status:
        sessions_qs = sessions_qs.filter(status=selected_status)

    pending_follow_ups_qs = RollCallAbsenceFollowUp.objects.filter(
        status=RollCallAbsenceFollowUp.Status.PENDING
    ).select_related(
        'entry__student',
        'entry__session',
        'entry__session__house',
    ).order_by('created_at')
    pending_follow_ups_qs = filter_queryset_by_house(
        request.user,
        pending_follow_ups_qs,
        'entry__session__house_id',
    )
    if selected_house_id:
        pending_follow_ups_qs = pending_follow_ups_qs.filter(
            entry__session__house_id=selected_house_id
        )
    pending_follow_up_count = pending_follow_ups_qs.count()
    pending_follow_ups_page, pending_follow_up_pagination_query = paginate_queryset(
        pending_follow_ups_qs,
        request,
        page_parameter='follow_up_page',
    )

    recent_sessions = list(sessions_qs[:15])
    selected_session_id = request.GET.get('session_id')
    current_session = None
    session_entries = []
    session_entries_page = None

    if selected_session_id:
        current_session = sessions_qs.filter(pk=selected_session_id).select_related(
            'house', 'dormitory', 'conducted_by'
        ).first()
    elif recent_sessions:
        in_progress = sessions_qs.filter(status=RollCallSession.Status.IN_PROGRESS).first()
        current_session = in_progress or recent_sessions[0]

    if current_session:
        session_entries_qs = current_session.entries.select_related(
            'student', 'student__house', 'student__dormitory', 'student__class_group'
        ).prefetch_related(
            Prefetch(
                'absence_follow_ups',
                queryset=RollCallAbsenceFollowUp.objects.filter(
                    status=RollCallAbsenceFollowUp.Status.PENDING
                ).order_by('-created_at'),
                to_attr='pending_absence_follow_ups',
            ),
            Prefetch(
                'absence_follow_ups',
                queryset=RollCallAbsenceFollowUp.objects.order_by('-created_at')[:1],
                to_attr='latest_absence_follow_ups',
            )
        ).order_by('student__full_name')
        session_entries_page, pagination_query = paginate_queryset(
            session_entries_qs, request, page_parameter='entry_page'
        )
        session_entries = session_entries_page
    else:
        pagination_query = request.GET.copy()
        pagination_query.pop('entry_page', None)
        pagination_query = pagination_query.urlencode()

    context = {
        'houses': houses,
        'recent_sessions': recent_sessions,
        'current_session': current_session,
        'pending_absence_count': (
            current_session.entries.filter(
                absence_follow_ups__status=RollCallAbsenceFollowUp.Status.PENDING
            ).distinct().count()
            if current_session else 0
        ),
        'pending_follow_ups': pending_follow_ups_page,
        'pending_follow_up_count': pending_follow_up_count,
        'pending_follow_up_pagination_query': pending_follow_up_pagination_query,
        'session_entries': session_entries,
        'session_entries_page': session_entries_page,
        'pagination_query': pagination_query,
        'pagination_parameter': 'entry_page',
        'selected_house': selected_house_id,
        'selected_status': selected_status,
        'can_view_all_houses': house_scope is None,
        'user_house_scope': house_scope,
    }

    if request.htmx:
        return render(request, 'core/operations/partials/roll_call_roster.html', context)
    return render(request, 'core/operations/roll_call.html', context)


@login_required
@role_required(*GATE_ROLES)
def gate_security_view(request):
    """Render school-wide gate operations without roll-call data."""
    house_scope = get_gate_house_scope(request.user)
    now = timezone.now()

    active_departures = ExeatMovement.objects.filter(
        actual_departure__isnull=False,
        actual_return__isnull=True
    ).select_related(
        'exeat', 'exeat__student', 'exeat__student__house', 'exeat__student__dormitory'
    ).order_by('actual_departure')

    active_reconciliations = ExeatReconciliation.objects.filter(
        outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
        actual_return__isnull=True,
    ).select_related(
        'exeat', 'exeat__student', 'exeat__student__house'
    ).order_by('actual_departure')
    if house_scope is not None:
        active_departures = active_departures.filter(exeat__student__house_id=house_scope.pk)
        active_reconciliations = active_reconciliations.filter(
            exeat__student__house_id=house_scope.pk
        )

    boarders = Student.objects.filter(
        residency=Student.ResidencyStatus.BOARDING
    )
    if house_scope is not None:
        boarders = boarders.filter(house_id=house_scope.pk)
    total_boarders = boarders.count()
    
    currently_out_count = active_departures.count() + active_reconciliations.count()
    overdue_count = active_departures.filter(
        exeat__expected_return__lte=now
    ).count() + active_reconciliations.filter(
        exeat__expected_return__lte=now
    ).count()

    movement_rows = active_departures.order_by().values(
        'exeat_id',
        'actual_departure',
        student_name=F('exeat__student__full_name'),
        exeat_number=F('exeat__exeat_number'),
        expected_return=F('exeat__expected_return'),
        reconciled=Value(False, output_field=BooleanField()),
    )
    reconciliation_rows = active_reconciliations.order_by().values(
        'exeat_id',
        'actual_departure',
        student_name=F('exeat__student__full_name'),
        exeat_number=F('exeat__exeat_number'),
        expected_return=F('exeat__expected_return'),
        reconciled=Value(True, output_field=BooleanField()),
    )
    active_out_page, active_out_query = paginate_queryset(
        movement_rows.union(reconciliation_rows).order_by('actual_departure'),
        request,
        page_parameter='out_page',
    )
    in_campus_count = max(0, total_boarders - currently_out_count)

    recent_movements = ExeatMovement.objects.filter(
        actual_departure__isnull=False
    ).select_related(
        'exeat', 'exeat__student', 'exeat__student__house'
    ).order_by('-updated_at')
    if house_scope is not None:
        recent_movements = recent_movements.filter(exeat__student__house_id=house_scope.pk)
    recent_movements_page, recent_movements_query = paginate_queryset(
        recent_movements, request, page_parameter='movement_page'
    )

    # Approved exeats awaiting departure
    awaiting_departures = Exeat.objects.filter(
        status=Exeat.Status.APPROVED,
        movement__actual_departure__isnull=True,
        reconciliation__isnull=True,
    ).select_related(
        'student', 'student__house', 'student__dormitory', 'movement'
    ).order_by('-expected_departure')
    if house_scope is not None:
        awaiting_departures = awaiting_departures.filter(student__house_id=house_scope.pk)
    awaiting_departure_count = awaiting_departures.count()
    awaiting_departures_page, awaiting_departures_query = paginate_queryset(
        awaiting_departures, request, page_parameter='awaiting_page'
    )

    context = {
        'user_house_scope': house_scope,
        'active_out': active_out_page,
        'active_out_page': active_out_page,
        'active_out_query': active_out_query,
        'overdue_count': overdue_count,
        'currently_out_count': currently_out_count,
        'in_campus_count': in_campus_count,
        'total_boarders': total_boarders,
        'recent_movements': recent_movements_page,
        'recent_movements_page': recent_movements_page,
        'recent_movements_query': recent_movements_query,
        'awaiting_departures': awaiting_departures_page,
        'awaiting_departures_page': awaiting_departures_page,
        'awaiting_departure_count': awaiting_departure_count,
        'awaiting_departures_query': awaiting_departures_query,
        'current_time': now,
    }

    if request.htmx:
        return render(request, 'core/operations/partials/gate_terminal_body.html', context)
    return render(request, 'core/operations/gate.html', context)


@login_required
@role_required(*ROLL_CALL_ROLES)
def roll_call_create_session_view(request):
    """Starts a new Roll Call muster session for a House and populates boarders."""
    if request.method == 'POST':
        house_id = request.POST.get('house')
        dormitory_id = request.POST.get('dormitory') or None
        session_type = request.POST.get('session_type', RollCallSession.SessionType.EVENING)
        notes = request.POST.get('notes', '').strip()
        
        house = get_object_or_404(
            filter_queryset_by_house(request.user, House.objects.all(), 'pk'),
            pk=house_id,
        )
        dormitory = (
            get_object_or_404(Dormitory, pk=dormitory_id, house=house)
            if dormitory_id
            else None
        )
        
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
        return redirect(f"{reverse('core:roll_call_sessions')}?session_id={session.id}")
        
    return redirect('core:roll_call_sessions')


@login_required
@require_POST
@role_required(*ROLL_CALL_ROLES)
def roll_call_complete_session_view(request, session_pk):
    """Marks a roll call session as completed."""
    session = get_object_or_404(
        filter_queryset_by_house(request.user, RollCallSession.objects.all(), 'house_id'),
        pk=session_pk,
    )
    
    if session.status == RollCallSession.Status.COMPLETED:
        messages.info(request, "This roll call session has already been finalized.")
        return redirect(f"{reverse('core:roll_call_sessions')}?session_id={session.id}")
        
    try:
        complete_roll_call_session(session)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect(f"{reverse('core:roll_call_sessions')}?session_id={session.id}")

    session.refresh_from_db()
    messages.success(
        request,
        f"Roll call session for {session.house.name} finalized: "
        f"{session.present_count} present, {session.absent_count} absent, "
        f"{session.on_exeat_count} on exeat, and {session.infirmary_count} in infirmary."
    )
    return redirect(f"{reverse('core:roll_call_sessions')}?session_id={session.id}")


@login_required
@require_POST
@role_required(*ROLL_CALL_ROLES)
def roll_call_mark_all_present_view(request, session_pk):
    """Bulk action corrects explicitly absent students to PRESENT."""
    session = get_object_or_404(
        filter_queryset_by_house(request.user, RollCallSession.objects.all(), 'house_id'),
        pk=session_pk,
    )
    
    if session.status == RollCallSession.Status.COMPLETED:
        messages.error(request, "Cannot modify a finalized roll call session.")
        return redirect(f"{reverse('core:roll_call_sessions')}?session_id={session.id}")
    count = mark_all_present(session, request.user)
    messages.success(
        request,
        f"Marked {count} eligible absent students present. Unmarked students, excused "
        "statuses, and unresolved gate discrepancies were left unchanged; any linked "
        "conduct records were retained and marked corrected.",
    )
    return redirect(f"{reverse('core:roll_call_sessions')}?session_id={session.id}")


@login_required
@require_POST
@role_required(*ROLL_CALL_ROLES)
def roll_call_update_entry_view(request, entry_pk):
    """HTMX inline status update for an individual student attendance entry."""
    entry = get_object_or_404(
        filter_queryset_by_house(
            request.user,
            RollCallEntry.objects.select_related(
                'session', 'student', 'student__house', 'student__dormitory'
            ).prefetch_related(
                Prefetch(
                    'absence_follow_ups',
                    queryset=RollCallAbsenceFollowUp.objects.filter(
                        status=RollCallAbsenceFollowUp.Status.PENDING
                    ).order_by('-created_at'),
                    to_attr='pending_absence_follow_ups',
                ),
                Prefetch(
                    'absence_follow_ups',
                    queryset=RollCallAbsenceFollowUp.objects.order_by('-created_at')[:1],
                    to_attr='latest_absence_follow_ups',
                ),
            ),
            'session__house_id',
        ),
        pk=entry_pk
    )
    def render_update(toast_message=None, toast_level="success"):
        entry.pending_absence_follow_ups = list(
            entry.absence_follow_ups.filter(
                status=RollCallAbsenceFollowUp.Status.PENDING
            ).order_by('-created_at')
        )
        entry.latest_absence_follow_ups = list(
            entry.absence_follow_ups.order_by('-created_at')[:1]
        )
        response = render(
            request,
            'core/operations/partials/roll_call_entry_update.html',
            {
                'entry': entry,
                'session': entry.session,
                'current_session': entry.session,
                'pending_absence_count': entry.session.entries.filter(
                    absence_follow_ups__status=RollCallAbsenceFollowUp.Status.PENDING
                ).distinct().count(),
            },
        )
        if toast_message and request.htmx:
            response["HX-Trigger"] = json.dumps({
                "showToast": {
                    "message": toast_message,
                    "level": toast_level,
                }
            })
        return response

    if entry.session.status == RollCallSession.Status.COMPLETED:
        error_message = "Cannot modify an entry in a finalized session."
        if request.htmx:
            return render_update(error_message, "error")
        messages.error(request, error_message)
        return redirect(f"{reverse('core:roll_call_sessions')}?session_id={entry.session.id}")

    new_status = request.POST.get('status')
    remarks = request.POST.get('remarks')
    if new_status not in RollCallEntry.Status.values:
        error_message = "Choose a valid roll-call status."
        if request.htmx:
            return render_update(error_message, "error")
        messages.error(request, error_message)
        return redirect(f"{reverse('core:roll_call_sessions')}?session_id={entry.session.id}")

    try:
        previous_status = entry.status
        had_pending_follow_up = entry.absence_follow_ups.filter(
            status=RollCallAbsenceFollowUp.Status.PENDING
        ).exists()
        had_active_conduct_record = entry.disciplinary_records.filter(
            corrected_at__isnull=True
        ).exists()
        update_entry_status(
            entry,
            new_status,
            remarks,
            updated_by=request.user,
        )
    except ValueError as error:
        entry.refresh_from_db()
        if request.htmx:
            return render_update(str(error), "error")
        messages.error(request, str(error))
        return redirect(f"{reverse('core:roll_call_sessions')}?session_id={entry.session.id}")

    toast_message = None
    toast_level = "success"
    if new_status == RollCallEntry.Status.ABSENT:
        if previous_status != RollCallEntry.Status.ABSENT:
            toast_message = (
                "Absence flagged for staff follow-up. It is not a conduct "
                "incident unless confirmed after review."
            )
        else:
            toast_message = "This absence is already recorded for follow-up."
        toast_level = "info"
    elif previous_status == RollCallEntry.Status.ABSENT and had_pending_follow_up:
        if new_status in (
            RollCallEntry.Status.ON_EXEAT,
            RollCallEntry.Status.INFIRMARY,
            RollCallEntry.Status.OFFICIAL_ACTIVITY,
        ):
            toast_message = (
                "Absence reviewed as an authorized exception and recorded for audit."
            )
        else:
            toast_message = (
                f"Attendance updated to {new_status}; the pending absence was "
                "retained as a reviewed roll-call correction."
            )
        toast_level = "info"
    elif previous_status == RollCallEntry.Status.ABSENT and had_active_conduct_record:
        toast_message = (
            f"Attendance updated to {new_status}; the confirmed conduct "
            "record was retained and marked corrected."
        )
        toast_level = "info"
    elif new_status in (
        RollCallEntry.Status.ON_EXEAT,
        RollCallEntry.Status.INFIRMARY,
        RollCallEntry.Status.OFFICIAL_ACTIVITY,
    ):
        toast_message = (
            f"{new_status} status recorded."
        )

    if toast_message and not request.htmx:
        messages.success(request, toast_message)
    if request.htmx:
        return render_update(toast_message, toast_level)
    return redirect(f"{reverse('core:roll_call_sessions')}?session_id={entry.session.id}")


@login_required
@require_POST
@role_required(*ROLL_CALL_ROLES)
def roll_call_resolve_absence_follow_up_view(request, follow_up_pk):
    """Resolve a pending absence without treating it as misconduct by default."""
    follow_up = get_object_or_404(
        filter_queryset_by_house(
            request.user,
            RollCallAbsenceFollowUp.objects.select_related(
                'entry__session',
                'entry__student',
                'entry__session__house',
            ),
            'entry__session__house_id',
        ),
        pk=follow_up_pk,
    )
    resolution = request.POST.get('resolution', '')
    resulting_status = request.POST.get('resulting_status', '')
    resolution_note = request.POST.get('resolution_note', '')
    try:
        resolve_absence_follow_up(
            follow_up,
            resolution,
            request.user,
            resolution_note,
            resulting_status,
            logged_by=getattr(request.user, 'housemaster', None),
        )
    except ValueError as error:
        if request.htmx:
            entry = follow_up.entry
            entry.pending_absence_follow_ups = [follow_up]
            entry.latest_absence_follow_ups = [follow_up]
            response = render(
                request,
                'core/operations/partials/roll_call_entry_update.html',
                {
                    'entry': entry,
                    'session': entry.session,
                    'current_session': entry.session,
                    'pending_absence_count': entry.session.entries.filter(
                        absence_follow_ups__status=RollCallAbsenceFollowUp.Status.PENDING
                    ).distinct().count(),
                },
            )
            response["HX-Trigger"] = json.dumps({
                "showToast": {"message": str(error), "level": "error"}
            })
            return response
        messages.error(request, str(error))
        return redirect(
            f"{reverse('core:roll_call_sessions')}?session_id={follow_up.entry.session_id}"
        )

    if resolution == RollCallAbsenceFollowUp.Status.CONFIRMED:
        message = (
            f"Unexcused absence confirmed for {follow_up.entry.student.full_name}; "
            "a conduct incident was recorded."
        )
    elif resolution == RollCallAbsenceFollowUp.Status.EXCUSED:
        message = "Absence marked authorized; no conduct incident was created."
    else:
        message = "Roll-call entry corrected; the review decision was retained for audit."

    if request.htmx:
        entry = follow_up.entry
        entry.refresh_from_db()
        entry.pending_absence_follow_ups = list(
            entry.absence_follow_ups.filter(
                status=RollCallAbsenceFollowUp.Status.PENDING
            ).order_by('-created_at')
        )
        entry.latest_absence_follow_ups = list(
            entry.absence_follow_ups.order_by('-created_at')[:1]
        )
        response = render(
            request,
            'core/operations/partials/roll_call_entry_update.html',
            {
                'entry': entry,
                'session': entry.session,
                'current_session': entry.session,
                'pending_absence_count': entry.session.entries.filter(
                    absence_follow_ups__status=RollCallAbsenceFollowUp.Status.PENDING
                ).distinct().count(),
            },
        )
        response["HX-Trigger"] = json.dumps({
            "showToast": {
                "message": message,
                "level": (
                    "success"
                    if resolution == RollCallAbsenceFollowUp.Status.CONFIRMED
                    else "info"
                ),
            }
        })
        return response

    messages.success(request, message)
    return redirect(
        f"{reverse('core:roll_call_sessions')}?session_id={follow_up.entry.session_id}"
    )


@login_required
@role_required(*GATE_ROLES)
@require_POST
def gate_terminal_action_view(request, exeat_pk, action):
    """
    Rapid Gate Security terminal action to record departures and returns.
    Enforces can_log_gate_check authorization.
    """
    if not can_log_gate_check(request.user):
        messages.error(request, "Permission denied: Only authorized Gate Officers and administrators can log gate movements.")
        return redirect('core:gate_security')

    get_gate_house_scope(request.user)
    exeat = get_object_or_404(
        Exeat.objects.select_related('student', 'movement'),
        pk=exeat_pk
    )
    officer_name = request.user.get_full_name() or request.user.username

    if action == 'departure':
        if exeat.status != Exeat.Status.APPROVED:
            messages.error(request, "Only approved exeats can have gate departure logged.")
        elif ExeatReconciliation.objects.filter(exeat=exeat).exists():
            messages.error(request, "This pass has already been reconciled.")
        elif timezone.now() < exeat.expected_departure:
            messages.error(request, "Gate departure cannot be logged before the scheduled departure time.")
        else:
            movement, _ = ExeatMovement.objects.get_or_create(exeat=exeat)
            if movement.actual_departure:
                messages.error(request, "Gate departure is already recorded.")
            else:
                movement.actual_departure = timezone.now()
                movement.departure_recorded_by = officer_name
                with transaction.atomic():
                    movement.save()
                    reconciled_count = reconcile_gate_departure_with_roll_calls(
                        exeat,
                        movement,
                    )
                enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.DEPARTURE)
                message = (
                    f"Gate EXIT logged for {exeat.student.full_name} "
                    f"({exeat.exeat_number})."
                )
                if reconciled_count:
                    message += (
                        f" {reconciled_count} unmarked roll-call entry/entries "
                        "were automatically marked On Exeat."
                    )
                messages.success(request, message)
    elif action == 'return':
        if exeat.status != Exeat.Status.APPROVED:
            messages.error(request, "Only approved exeats can have gate return logged.")
        else:
            reconciliation = ExeatReconciliation.objects.filter(
                exeat=exeat,
                outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
                actual_return__isnull=True,
            ).first()
            if reconciliation:
                reconciliation.actual_return = timezone.now()
                reconciliation.outcome = ExeatReconciliation.Outcome.RETURNED
                reconciliation.return_recorded_by = request.user
                reconciliation.save()
                enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.RETURN)
                messages.success(request, f"Gate RETURN recorded for {exeat.student.full_name} ({exeat.exeat_number}).")
            else:
                movement = ExeatMovement.objects.filter(exeat=exeat).first()
                if not movement or not movement.actual_departure:
                    messages.error(request, "Cannot record a return without a confirmed departure.")
                elif movement.actual_return:
                    messages.error(request, "Gate return is already recorded.")
                else:
                    movement.actual_return = timezone.now()
                    movement.return_recorded_by = officer_name
                    movement.save()
                    enqueue_exeat_event_sms(exeat, SMSDelivery.EventType.RETURN)
                    messages.success(request, f"Gate RETURN recorded for {exeat.student.full_name} ({exeat.exeat_number}).")

    return redirect('core:gate_security')


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
        get_gate_house_scope(request.user)
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
@role_required(*REPORT_ROLES)
def reports_dashboard_view(request):
    """Show scoped summary metrics and a selectable detailed operational report."""
    return render(request, 'core/reports/dashboard.html', _reports_context(request))


@login_required
@role_required(*REPORT_ROLES)
def reports_print_view(request):
    """Render only the authorized detailed report for browser printing or PDF."""
    context = _reports_context(request)
    if not context["report_filters_valid"]:
        return HttpResponseBadRequest("Invalid report filters. Return to Reports & Stats and correct the selected values.")
    return render(request, 'core/reports/print.html', context)


def _reports_context(request):
    from core.services.reports import get_dashboard_metrics, get_detailed_report

    house_scope = get_house_scope(request.user)
    filter_data = request.GET.copy()
    if not filter_data.get("report"):
        filter_data["report"] = "housing"
    report_filters = ReportFilterForm(filter_data)
    report_filters_valid = report_filters.is_valid()
    cleaned_data = report_filters.cleaned_data
    report_type = cleaned_data.get("report") or "housing"
    term = cleaned_data.get("term")
    date_from = cleaned_data.get("date_from")
    date_to = cleaned_data.get("date_to")
    if report_filters_valid:
        report = get_detailed_report(
            report_type=report_type,
            house_id=house_scope.pk if house_scope is not None else None,
            date_from=date_from,
            date_to=date_to,
            term_id=term.pk if term else None,
        )
    else:
        report = {
            "title": "Correct filters to view this report",
            "headers": ("Status",),
            "rows": (("The selected report filters are invalid.",),),
            "point_in_time": False,
        }
    report["record_count"] = len(report["rows"])
    return {
        'metrics': get_dashboard_metrics(
            house_id=house_scope.pk if house_scope is not None else None
        ),
        'recent_infractions': DisciplinaryRecord.objects.select_related(
            'student', 'student__house'
        ).filter(
            **({'student__house_id': house_scope.pk} if house_scope is not None else {})
        ).order_by('-created_at')[:10],
        'user_house_scope': house_scope,
        'report': report,
        'report_filters': report_filters,
        'report_filters_valid': report_filters_valid,
        'report_type': report_type,
        'selected_date_from': date_from,
        'selected_date_to': date_to,
        'selected_term': term,
        'report_generated_at': timezone.localtime(),
        'active_tab': 'reports',
    }


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
    academic_terms = AcademicTerm.objects.select_related('academic_year').order_by('-academic_year__start_date', '-term')
    export_jobs = (
        StudentIDExport.objects.filter(requested_by=request.user)
        .order_by('-created_at')[:5]
        if hasattr(request.user, 'pk') else StudentIDExport.objects.none()
    )

    context = {
        'academic_terms': academic_terms,
        'sms_enabled': SMSConfiguration.is_enabled(),
        'export_jobs': export_jobs,
        'id_export_layouts': [
            {'value': 'classic', 'label': 'Classic'},
            {'value': 'crest', 'label': 'Crest'},
            {'value': 'modern', 'label': 'Modern'},
        ],
        'id_export_formats': [
            {'value': 'PNG', 'label': 'PNG'},
            {'value': 'JPEG', 'label': 'JPEG'},
        ],
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
    
    try:
        set_active_term(term_id)
        messages.success(request, "Active academic term successfully updated.")
    except AcademicTerm.DoesNotExist:
        messages.error(request, "Academic term not found.")
        
    response = HttpResponse(status=204)
    response['HX-Redirect'] = reverse('core:configuration')
    return response


@login_required
@role_required(*ADMIN_ROLES)
@require_POST
def set_sms_enabled_view(request):
    """Update the system-wide SMS notification switch."""
    enabled_value = request.POST.get("enabled")
    if enabled_value not in {"true", "false"}:
        messages.error(request, "Choose whether SMS notifications should be enabled or disabled.")
        return redirect("core:configuration")

    configuration, _created = SMSConfiguration.objects.get_or_create(pk=1)
    configuration.enabled = enabled_value == "true"
    configuration.save(update_fields=["enabled", "updated_at"])

    state_label = "enabled" if configuration.enabled else "disabled"
    messages.success(request, f"SMS notifications are now {state_label}.")
    return redirect("core:configuration")


@login_required
@role_required(*ADMIN_ROLES)
@require_POST
def create_student_id_export_view(request):
    """Queue a background export of all enrolled students' card images."""
    layout = request.POST.get("layout", "crest").strip().lower()
    image_format = request.POST.get("image_format", "PNG").strip().upper()

    if layout not in {"classic", "crest", "modern"}:
        messages.error(request, "Choose a supported student ID card layout.")
        return redirect("core:configuration")
    if image_format not in {StudentIDExport.ImageFormat.PNG, StudentIDExport.ImageFormat.JPEG}:
        messages.error(request, "Choose PNG or JPEG format for the export.")
        return redirect("core:configuration")

    export = StudentIDExport.objects.create(
        requested_by=request.user,
        layout=layout,
        image_format=image_format,
        status=StudentIDExport.Status.QUEUED,
        expires_at=timezone.now() + timezone.timedelta(days=7),
    )

    public_origin = request.build_absolute_uri('/').rstrip('/')
    generate_student_id_export.delay(str(export.pk), public_origin)
    messages.success(request, "Student ID export queued. It will be generated in the background.")
    return redirect("core:configuration")


@login_required
@role_required(*ADMIN_ROLES)
def download_student_id_export_view(request, export_id):
    """Serve the generated student ID export ZIP for the admin user."""
    export = get_object_or_404(StudentIDExport, pk=export_id, requested_by=request.user)
    if export.status != StudentIDExport.Status.COMPLETED:
        messages.error(request, "The selected export is not ready for download yet.")
        return redirect("core:configuration")

    archive_path = Path(settings.BASE_DIR) / ".private" / "student_id_exports" / export.archive_name
    if not archive_path.exists():
        messages.error(request, "The requested export archive could not be found.")
        return redirect("core:configuration")

    response = FileResponse(open(archive_path, 'rb'))
    response["Content-Type"] = "application/zip"
    response["Content-Disposition"] = f'attachment; filename="{export.archive_name}"'
    return response
