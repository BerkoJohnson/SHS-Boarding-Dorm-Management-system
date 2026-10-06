"""
Service layer for reporting and statistics aggregation.
"""
from django.db.models import Count, Q, Avg
from django.utils import timezone
from core.models import (
    Student,
    Exeat,
    ExeatReconciliation,
    DisciplinaryRecord,
    RollCallSession,
    AcademicTerm,
    House
)

def get_dashboard_metrics(house_id=None):
    """
    Aggregates high-level metrics, optionally scoped to one house.
    """
    now = timezone.now()
    current_term = AcademicTerm.objects.filter(is_current=True).first()
    
    # 1. Total Boarders
    boarders = Student.objects.filter(residency=Student.ResidencyStatus.BOARDING)
    if house_id is not None:
        boarders = boarders.filter(house_id=house_id)
    total_boarders = boarders.count()
    
    # 2. Active Exeats & Overdue
    active_exeat_qs = Exeat.objects.filter(
        status=Exeat.Status.APPROVED,
    ).filter(
        Q(movement__actual_departure__isnull=False, movement__actual_return__isnull=True)
        | Q(
            reconciliation__outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
            reconciliation__actual_return__isnull=True,
        )
    )
    if house_id is not None:
        active_exeat_qs = active_exeat_qs.filter(student__house_id=house_id)
    active_exeats = active_exeat_qs.count()
    overdue_exeats = active_exeat_qs.filter(expected_return__lte=now).count()
    
    # 3. Disciplinary Stats (Current Term)
    if current_term:
        term_infractions = DisciplinaryRecord.objects.filter(academic_term=current_term)
        if house_id is not None:
            term_infractions = term_infractions.filter(student__house_id=house_id)
        total_infractions = term_infractions.count()
        
        # Breakdown by House
        houses = House.objects.all()
        if house_id is not None:
            houses = houses.filter(pk=house_id)
        house_breakdown = houses.annotate(
            infraction_count=Count(
                'students__disciplinary_records',
                filter=Q(students__disciplinary_records__academic_term=current_term)
            )
        ).order_by('-infraction_count')[:5]
    else:
        total_infractions = 0
        house_breakdown = []

    # 4. Roll Call Average (Last 7 Days)
    seven_days_ago = now.date() - timezone.timedelta(days=7)
    recent_sessions = RollCallSession.objects.filter(
        date__gte=seven_days_ago,
        status=RollCallSession.Status.COMPLETED
    )
    if house_id is not None:
        recent_sessions = recent_sessions.filter(house_id=house_id)
    
    # Calculate average attendance manually if qs is small, or use aggregate
    # attendance_rate is a property, so we need to calculate it manually or just aggregate
    total_present = 0
    total_expected = 0
    for session in recent_sessions:
        total_expected += session.total_students
        total_present += session.present_count
        
    avg_attendance = round((total_present / total_expected * 100), 1) if total_expected > 0 else 0

    return {
        'total_boarders': total_boarders,
        'active_exeats': active_exeats,
        'overdue_exeats': overdue_exeats,
        'total_infractions': total_infractions,
        'house_breakdown': house_breakdown,
        'avg_attendance': avg_attendance,
        'current_term': current_term
    }
