"""
Service layer for reporting and statistics aggregation.
"""
from django.db.models import Count, F, Q
from django.utils import timezone
from core.models import (
    Student,
    Exeat,
    ExeatReconciliation,
    DisciplinaryRecord,
    RollCallSession,
    RollCallEntry,
    AcademicTerm,
    House,
    Dormitory,
    DormitoryRoom,
)

_DORMITORY_GENDER_LABELS = {"M": "Boys Dormitory", "F": "Girls Dormitory"}
_ROLL_CALL_TYPE_LABELS = {
    "Evening": "Evening Roll Call (Lights Out)",
    "Morning": "Morning Roll Call & Inspection",
    "Special": "Emergency / Special Muster",
}
_ROLL_CALL_STATUS_LABELS = {"In Progress": "In Progress", "Completed": "Completed"}
_EXEAT_STATUS_LABELS = {
    "Pending": "Pending Approval",
    "Approved": "Approved (Pass Issued)",
    "Rejected": "Rejected",
    "Cancelled": "Cancelled",
}
_EXEAT_TYPE_LABELS = {
    "Medical": "Medical Exeat",
    "Town": "Town Day Exeat",
    "Weekend": "Weekend Exeat",
    "Emergency": "Emergency / Personal Exeat",
    "Vacation": "End of Term / Vacation Exeat",
}
_SEVERITY_LABELS = {
    "Minor": "Minor Offense",
    "Major": "Major Offense",
    "Critical": "Critical Infraction",
}


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
        term_infractions = DisciplinaryRecord.objects.filter(
            academic_term=current_term,
            corrected_at__isnull=True,
        )
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
                filter=Q(
                    students__disciplinary_records__academic_term=current_term,
                    students__disciplinary_records__corrected_at__isnull=True,
                )
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
    
    attendance_totals = recent_sessions.aggregate(
        total_expected=Count("entries"),
        total_present=Count(
            "entries",
            filter=Q(entries__status=RollCallEntry.Status.PRESENT),
        ),
    )
    total_expected = attendance_totals["total_expected"]
    total_present = attendance_totals["total_present"]
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


def get_detailed_report(report_type, house_id=None, date_from=None, date_to=None, term_id=None):
    """Build a scoped, printable detail report as column labels and row values."""
    if report_type == "housing":
        houses = House.objects.all().order_by("display_order", "name")
        if house_id is not None:
            houses = houses.filter(pk=house_id)
        houses = houses.annotate(
            boarder_count=Count(
                "students",
                distinct=True,
                filter=Q(students__residency=Student.ResidencyStatus.BOARDING),
            ),
            assigned_boarder_count=Count(
                "students",
                distinct=True,
                filter=Q(
                    students__residency=Student.ResidencyStatus.BOARDING,
                    students__dormitory__isnull=False,
                ),
            ),
            unassigned_count=Count(
                "students",
                distinct=True,
                filter=Q(
                    students__residency=Student.ResidencyStatus.BOARDING,
                    students__dormitory__isnull=True,
                ),
            ),
            room_unassigned_count=Count(
                "students",
                distinct=True,
                filter=Q(
                    students__residency=Student.ResidencyStatus.BOARDING,
                    students__room__isnull=True,
                ),
            ),
        ).values(
            "id",
            "name",
            "unassigned_count",
            "boarder_count",
            "assigned_boarder_count",
            "room_unassigned_count",
        )
        dormitories = Dormitory.objects.all().annotate(
            occupied=Count(
                "boarders",
                filter=Q(boarders__residency=Student.ResidencyStatus.BOARDING),
            )
        ).values(
            "id",
            "house_id", "house__name", "name", "gender", "capacity", "occupied"
        ).order_by("house__display_order", "house__name", "gender", "name")
        if house_id is not None:
            dormitories = dormitories.filter(house_id=house_id)
        dormitories_by_house = {}
        for dormitory in dormitories:
            dormitories_by_house.setdefault(dormitory["house_id"], []).append(dormitory)

        rooms = DormitoryRoom.objects.select_related(
            "dormitory", "dormitory__house"
        ).annotate(
            occupied=Count(
                "students",
                filter=Q(students__residency=Student.ResidencyStatus.BOARDING),
            )
        ).order_by(
            "dormitory__house__display_order",
            "dormitory__house__name",
            "dormitory__name",
            "name",
        )
        if house_id is not None:
            rooms = rooms.filter(dormitory__house_id=house_id)
        rooms_by_dormitory = {}
        total_room_capacity = 0
        total_room_occupied = 0
        for room in rooms:
            rooms_by_dormitory.setdefault(room.dormitory_id, []).append(room)
            total_room_capacity += room.capacity
            total_room_occupied += room.occupied

        rows = []
        total_boarders = 0
        total_assigned = 0
        total_unassigned = 0
        total_capacity = 0
        over_capacity_dormitories = 0
        total_room_unassigned = 0
        for house in houses:
            boarder_count = house["boarder_count"]
            assigned_count = house["assigned_boarder_count"]
            unassigned_count = house["unassigned_count"]
            room_unassigned_count = house["room_unassigned_count"]
            house_dormitories = dormitories_by_house.get(house["id"], [])
            house_capacity = sum(dormitory["capacity"] for dormitory in house_dormitories)
            total_boarders += boarder_count
            total_assigned += assigned_count
            total_unassigned += unassigned_count
            total_room_unassigned += room_unassigned_count
            total_capacity += house_capacity
            house_occupancy = (
                round(assigned_count * 100 / house_capacity, 1)
                if house_capacity else 0
            )
            rows.append((
                house["name"],
                "House total",
                "—",
                house_capacity,
                assigned_count,
                max(0, house_capacity - assigned_count),
                f"{house_occupancy}%",
                f"{boarder_count} boarders; {unassigned_count} without dormitory",
            ))
            if not house_dormitories:
                if not boarder_count:
                    rows.append((
                        house["name"], "No dormitories configured", "—", 0, 0, 0, "0%",
                        "No capacity configured",
                    ))
            for dormitory in house_dormitories:
                occupied = dormitory["occupied"]
                capacity = dormitory["capacity"]
                occupancy = round(occupied * 100 / capacity, 1) if capacity else 0
                excess = max(0, occupied - capacity)
                if excess:
                    over_capacity_dormitories += 1
                rows.append((
                    dormitory["house__name"],
                    dormitory["name"],
                    _DORMITORY_GENDER_LABELS.get(dormitory["gender"], "—"),
                    capacity,
                    occupied,
                    max(0, capacity - occupied),
                    f"{occupancy}%",
                    f"Over capacity by {excess}" if excess else f"{max(0, capacity - occupied)} spaces available",
                ))
                for room in rooms_by_dormitory.get(dormitory["id"], []):
                    room_occupancy = (
                        round(room.occupied * 100 / room.capacity, 1)
                        if room.capacity
                        else 0
                    )
                    rows.append((
                        room.dormitory.house.name,
                        room.dormitory.name,
                        room.name,
                        room.capacity,
                        room.occupied,
                        max(0, room.capacity - room.occupied),
                        f"{room_occupancy}%",
                        (
                            "At capacity"
                            if room.occupied == room.capacity
                            else f"{room.capacity - room.occupied} spaces available"
                        ),
                    ))
            if unassigned_count:
                rows.append((
                    house["name"],
                    "Unassigned dormitory",
                    "—",
                    "—",
                    unassigned_count,
                    "—",
                    "—",
                    "Placement required",
                ))
        occupancy = round(total_assigned * 100 / total_capacity, 1) if total_capacity else 0
        row_groups = []
        for row in rows:
            if row[1] == "House total":
                row_groups.append({"house": row[0], "rows": []})
            row_groups[-1]["rows"].append(row)
        return {
            "title": "Boarding Occupancy & Placement",
            "headers": (
                "House",
                "Dormitory",
                "Block / Room",
                "Capacity",
                "Placed",
                "Vacant",
                "Occupancy",
                "Capacity / Placement",
            ),
            "rows": rows,
            "row_groups": row_groups,
            "summary": (
                ("Boarders", total_boarders),
                ("Configured beds", total_capacity),
                ("Placed boarders", total_assigned),
                ("Awaiting dorm placement", total_unassigned),
                ("Occupied capacity", f"{occupancy}%"),
                ("Over-capacity dormitories", over_capacity_dormitories),
                ("Configured rooms", len(rooms)),
                ("Total room capacity", total_room_capacity),
                ("Boarders assigned to rooms", total_room_occupied),
                ("Awaiting room placement", total_room_unassigned),
            ),
            "highlights": (
                "Capacity is based on configured dormitory capacity and cannot include beds not recorded in the system.",
                "Unassigned boarders need a dormitory placement review.",
                "Room occupancy is enforced against each room's configured capacity.",
            ),
            "point_in_time": True,
        }

    if report_type == "attendance":
        sessions = RollCallSession.objects.all().order_by("-date", "-created_at")
        if house_id is not None:
            sessions = sessions.filter(house_id=house_id)
        if date_from:
            sessions = sessions.filter(date__gte=date_from)
        if date_to:
            sessions = sessions.filter(date__lte=date_to)
        if term_id:
            sessions = sessions.filter(academic_term_id=term_id)
        session_rows = sessions.values(
            "date",
            "house__name",
            "dormitory__name",
            "session_type",
            "status",
            "conducted_by__full_name",
        ).annotate(
            report_student_count=Count("entries"),
            report_present_count=Count(
                "entries", filter=Q(entries__status=RollCallEntry.Status.PRESENT)
            ),
            report_absent_count=Count(
                "entries", filter=Q(entries__status=RollCallEntry.Status.ABSENT)
            ),
            report_on_exeat_count=Count(
                "entries", filter=Q(entries__status=RollCallEntry.Status.ON_EXEAT)
            ),
            report_infirmary_count=Count(
                "entries", filter=Q(entries__status=RollCallEntry.Status.INFIRMARY)
            ),
            report_unmarked_count=Count(
                "entries", filter=Q(entries__status=RollCallEntry.Status.UNMARKED)
            ),
        )
        rows = []
        for session in session_rows:
            student_count = session["report_student_count"]
            present_count = session["report_present_count"]
            rate = (
                round(present_count * 100 / student_count, 1)
                if student_count else 0
            )
            rows.append((
                session["date"],
                session["house__name"],
                session["dormitory__name"] or "All dormitories",
                _ROLL_CALL_TYPE_LABELS.get(session["session_type"], session["session_type"]),
                _ROLL_CALL_STATUS_LABELS.get(session["status"], session["status"]),
                student_count,
                present_count,
                session["report_absent_count"],
                session["report_on_exeat_count"],
                session["report_infirmary_count"],
                session["report_unmarked_count"],
                f"{rate}%",
                session["conducted_by__full_name"] or "—",
            ))
        absent_entries = RollCallEntry.objects.filter(
            status=RollCallEntry.Status.ABSENT
        )
        if house_id is not None:
            absent_entries = absent_entries.filter(session__house_id=house_id)
        if date_from:
            absent_entries = absent_entries.filter(session__date__gte=date_from)
        if date_to:
            absent_entries = absent_entries.filter(session__date__lte=date_to)
        if term_id:
            absent_entries = absent_entries.filter(session__academic_term_id=term_id)
        repeat_absence_rows = list(
            absent_entries.values(
                "student__full_name",
                "student__house__name",
                "student__dormitory__name",
            ).annotate(
                absence_count=Count("id"),
                sessions_count=Count("session_id", distinct=True),
            ).filter(
                absence_count__gte=2
            ).order_by("-absence_count", "student__full_name").values_list(
                "student__full_name",
                "student__house__name",
                "student__dormitory__name",
                "absence_count",
                "sessions_count",
            )
        )
        return {
            "title": "Roll-call Attendance & Follow-up",
            "headers": ("Date", "House", "Dormitory", "Session", "Status", "Expected", "Present", "Absent", "On Exeat", "Infirmary", "Unmarked", "Attendance", "Conducted By"),
            "rows": rows,
            "summary": (
                ("Sessions", len(rows)),
                ("Incomplete sessions", sum(1 for row in rows if row[4] == "In Progress")),
                ("Sessions with absences", sum(1 for row in rows if row[7])),
                ("Students marked absent", sum(row[7] for row in rows)),
                ("Students not yet marked", sum(row[10] for row in rows)),
                ("Students with 2+ absences", len(repeat_absence_rows)),
            ),
            "sections": ({
                "title": "Students with repeated roll-call absences",
                "headers": ("Student", "House", "Dormitory", "Absences", "Sessions"),
                "rows": repeat_absence_rows,
            },),
            "highlights": (
                "Use sessions with absences as a follow-up queue; attendance records do not by themselves establish why a student was absent.",
                "Repeated absence means at least two entries marked Absent in the selected period; On Exeat and Infirmary statuses are not counted as absences.",
            ),
            "point_in_time": False,
        }

    if report_type == "exeats":
        exeats = Exeat.objects.all().order_by("-expected_departure")
        if house_id is not None:
            exeats = exeats.filter(student__house_id=house_id)
        if date_from:
            exeats = exeats.filter(expected_departure__date__gte=date_from)
        if date_to:
            exeats = exeats.filter(expected_departure__date__lte=date_to)
        if term_id:
            exeats = exeats.filter(academic_term_id=term_id)
        exeat_rows = exeats.values(
            "exeat_number",
            "student__full_name",
            "student__house__name",
            "exeat_type",
            "expected_departure",
            "expected_return",
            "status",
            actual_departure=F("movement__actual_departure"),
            actual_return=F("movement__actual_return"),
            reconciled_departure=F("reconciliation__actual_departure"),
            reconciled_return=F("reconciliation__actual_return"),
            reconciliation_outcome=F("reconciliation__outcome"),
        )
        rows = []
        now = timezone.now()
        for exeat in exeat_rows:
            actual_departure = exeat["actual_departure"] or exeat["reconciled_departure"]
            actual_return = exeat["actual_return"] or exeat["reconciled_return"]
            if exeat["status"] != Exeat.Status.APPROVED:
                state = _EXEAT_STATUS_LABELS.get(exeat["status"], exeat["status"])
            elif exeat["reconciliation_outcome"] == ExeatReconciliation.Outcome.UNUSED:
                state = "Pass Unused"
            elif actual_return:
                state = (
                    "Returned (Reconciled)"
                    if exeat["reconciliation_outcome"] == ExeatReconciliation.Outcome.RETURNED
                    else "Returned"
                )
            elif exeat["reconciliation_outcome"] == ExeatReconciliation.Outcome.STUDENT_OUT:
                state = (
                    "OVERDUE (Reconciled)"
                    if now >= exeat["expected_return"]
                    else "Student Out (Reconciled)"
                )
            elif actual_departure:
                state = (
                    "OVERDUE (Student Out)"
                    if now >= exeat["expected_return"]
                    else "Active (Student Out)"
                )
            elif now >= exeat["expected_return"]:
                state = "Pass Unresolved"
            elif now >= exeat["expected_departure"]:
                state = "Departure Not Recorded"
            else:
                state = "Awaiting Departure"
            rows.append((
                exeat["exeat_number"],
                exeat["student__full_name"],
                exeat["student__house__name"] or "Unassigned",
                _EXEAT_TYPE_LABELS.get(exeat["exeat_type"], exeat["exeat_type"]),
                exeat["expected_departure"],
                actual_departure or "Not logged",
                exeat["expected_return"],
                actual_return or "Not logged",
                state,
            ))
        return {
            "title": "Exeat & Gate Compliance",
            "headers": ("Pass", "Student", "House", "Type", "Scheduled Departure", "Recorded Departure", "Expected Return", "Recorded Return", "State"),
            "rows": rows,
            "summary": (
                ("Passes in period", len(rows)),
                ("Movement not recorded", sum(1 for row in rows if row[5] == "Not logged")),
                ("Return not recorded", sum(1 for row in rows if row[7] == "Not logged")),
                ("Overdue", sum(1 for row in rows if str(row[8]).startswith("OVERDUE"))),
                ("Unresolved departure/return", sum(1 for row in rows if row[8] in {"Pass Unresolved", "Departure Not Recorded"})),
            ),
            "point_in_time": False,
        }

    incidents = DisciplinaryRecord.objects.all().order_by("-created_at")
    if house_id is not None:
        incidents = incidents.filter(student__house_id=house_id)
    if date_from:
        incidents = incidents.filter(created_at__date__gte=date_from)
    if date_to:
        incidents = incidents.filter(created_at__date__lte=date_to)
    if term_id:
        incidents = incidents.filter(academic_term_id=term_id)
    active_incidents = incidents.filter(corrected_at__isnull=True)
    incident_rows = incidents.values(
        "created_at",
        "student__full_name",
        "student__house__name",
        "student__dormitory__name",
        "offense",
        "severity",
        "action_taken",
        "logged_by__full_name",
        "corrected_at",
        "correction_note",
    )
    severity_breakdown = active_incidents.values("severity").annotate(
        incident_count=Count("id")
    ).order_by("severity")
    offense_breakdown = active_incidents.values("offense").annotate(
        incident_count=Count("id")
    ).order_by("-incident_count", "offense")[:10]
    repeat_students = active_incidents.values(
        "student_id",
        "student__full_name",
        "student__house__name",
        "student__dormitory__name",
    ).annotate(
        incident_count=Count("id")
    ).filter(incident_count__gte=2).order_by("-incident_count", "student__full_name")
    repeat_student_rows = [
        (
            student["student__full_name"],
            student["student__house__name"] or "Unassigned",
            student["student__dormitory__name"] or "Unassigned",
            student["incident_count"],
        )
        for student in repeat_students
    ]
    severity_counts = {
        _SEVERITY_LABELS.get(item["severity"], item["severity"]): item["incident_count"]
        for item in severity_breakdown
    }
    offense_rows = [
        (item["offense"], item["incident_count"])
        for item in offense_breakdown
    ]
    monthly_trend_rows = [
        (
            f'{item["created_at__year"]}-{item["created_at__month"]:02d}',
            item["incident_count"],
            item["student_count"],
            item["major_count"],
            item["critical_count"],
        )
        for item in active_incidents.values(
            "created_at__year",
            "created_at__month",
        ).annotate(
            incident_count=Count("id"),
            student_count=Count("student_id", distinct=True),
            major_count=Count(
                "id", filter=Q(severity=DisciplinaryRecord.Severity.MAJOR)
            ),
            critical_count=Count(
                "id", filter=Q(severity=DisciplinaryRecord.Severity.CRITICAL)
            ),
        ).order_by("created_at__year", "created_at__month")
    ]
    incident_count = incidents.count()
    distinct_student_count = incidents.values("student_id").distinct().count()
    repeated_student_count = repeat_students.count()
    rows = [
        (
            incident["created_at"],
            incident["student__full_name"],
            incident["student__house__name"] or "Unassigned",
            incident["student__dormitory__name"] or "Unassigned",
            incident["offense"],
            _SEVERITY_LABELS.get(incident["severity"], incident["severity"]),
            incident["action_taken"],
            incident["logged_by__full_name"] or "—",
            (
                f"Corrected {incident['corrected_at']}: {incident['correction_note']}"
                if incident["corrected_at"]
                else "Active"
            ),
        )
        for incident in incident_rows
    ]
    return {
        "title": "Disciplinary Trends & Committee Review",
        "headers": ("Logged", "Student", "House", "Dormitory", "Incident", "Severity", "Action Taken", "Reported By", "Record Status"),
        "rows": rows,
        "summary": (
            ("Recorded incidents", incident_count),
            ("Active incidents", active_incidents.count()),
            ("Corrected records", incident_count - active_incidents.count()),
            ("Students with incidents", distinct_student_count),
            ("Students with 2+ recorded incidents", repeated_student_count),
            ("Minor", severity_counts.get("Minor Offense", 0)),
            ("Major", severity_counts.get("Major Offense", 0)),
            ("Critical", severity_counts.get("Critical Infraction", 0)),
        ),
        "sections": (
            {
                "title": "Incidents by month",
                "headers": ("Month", "Recorded incidents", "Students", "Major", "Critical"),
                "rows": monthly_trend_rows,
            },
            {
                "title": "Students with multiple recorded incidents",
                "headers": ("Student", "House", "Dormitory", "Recorded incidents"),
                "rows": repeat_student_rows,
            },
            {
                "title": "Most frequently recorded incident descriptions",
                "headers": ("Incident description", "Recorded cases"),
                "rows": offense_rows,
            },
        ),
        "highlights": (
            "Repeated incidents are students with at least two records in the selected period.",
            "The system records actions taken but not intervention outcomes, so these figures cannot show whether an intervention was effective.",
            "Incident descriptions are free text; similar cases may use different wording.",
        ),
        "point_in_time": False,
    }
