"""
Service layer for reporting and statistics aggregation.
"""

from django.contrib.auth import get_user_model
from django.db.models import Count, F, Max, Q, Sum
from django.utils import timezone
from core.models import (
    Student,
    Exeat,
    ExeatMovement,
    ExeatReconciliation,
    DisciplinaryRecord,
    RollCallSession,
    RollCallEntry,
    RollCallAbsenceFollowUp,
    AcademicTerm,
    House,
    HouseMaster,
    Dormitory,
    DormitoryCapacityException,
    DormitoryRoom,
    SMSDelivery,
    MovementConcernReport,
    ConfigurationAuditLog,
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
_ROLL_CALL_ENTRY_STATUS_LABELS = {
    choice.value: choice.label for choice in RollCallEntry.Status
}

# Placement vocabulary for the boarding occupancy report. Beds are read as a
# fixed set of states so a cell can be counted and sorted, not just described.
_PLACEMENT_STATES = {
    "good": "Room to spare",
    "watch": "Nearly full",
    "empty": "Empty",
    "full": "Full",
    "over": "Over capacity",
    "unrecorded": "Beds unrecorded",
    "unconfigured": "No beds configured",
}

# Beds are not interchangeable across gender, so the report groups by cohort
# rather than by house. Ordering here drives the order of the table.
_COHORT_META = {
    "F": {"label": "Girls", "order": 0},
    "M": {"label": "Boys", "order": 1},
}
_UNSPECIFIED_COHORT = {"label": "Unspecified", "order": 2}


def _plural(count, singular, plural=None):
    """Render a count with a correctly pluralised noun."""
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural or singular + 's'}"


def _placement_state(capacity, occupied):
    """
    Classify a bed space into the fixed placement vocabulary.

    Returns a (label, tone) pair so the wording and the styling both stay
    derived from the numbers instead of hand-written status sentences.
    """
    if capacity <= 0:
        if occupied:
            return _PLACEMENT_STATES["unrecorded"], "bad"
        return _PLACEMENT_STATES["unconfigured"], "neutral"
    if occupied > capacity:
        return _PLACEMENT_STATES["over"], "bad"
    if occupied == capacity:
        return _PLACEMENT_STATES["full"], "neutral"
    if occupied == 0:
        return _PLACEMENT_STATES["empty"], "watch"
    if occupied * 100 / capacity >= 85:
        return _PLACEMENT_STATES["watch"], "watch"
    return _PLACEMENT_STATES["good"], "good"


def _cohort_bucket(cohorts, gender):
    """Return the cohort bucket for a dormitory, creating it on first sight."""
    key = gender or "U"
    if key not in cohorts:
        meta = _COHORT_META.get(key, _UNSPECIFIED_COHORT)
        cohorts[key] = {
            "key": key,
            "label": meta["label"],
            "order": meta["order"],
            "rows": [],
        }
    return cohorts[key]


def get_dashboard_metrics(house_id=None):
    """
    Aggregates high-level metrics, optionally scoped to one house.
    """
    now = timezone.now()
    current_term = AcademicTerm.objects.filter(is_current=True).first()

    # 1. Total Boarders
    boarders = Student.objects.exclude(
        status=Student.EnrollmentStatus.COMPLETED
    ).filter(residency=Student.ResidencyStatus.BOARDING)
    if house_id is not None:
        boarders = boarders.filter(house_id=house_id)
    total_boarders = boarders.count()

    # 2. Active Exeats & Overdue
    active_exeat_qs = Exeat.objects.filter(
        status=Exeat.Status.APPROVED,
    ).filter(
        Q(
            movement__actual_departure__isnull=False,
            movement__actual_return__isnull=True,
        )
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
                "students__disciplinary_records",
                filter=Q(
                    students__disciplinary_records__academic_term=current_term,
                    students__disciplinary_records__corrected_at__isnull=True,
                ),
            )
        ).order_by("-infraction_count")[:5]
    else:
        total_infractions = 0
        house_breakdown = []

    # 4. Roll Call Average (Last 7 Days)
    seven_days_ago = now.date() - timezone.timedelta(days=7)
    recent_sessions = RollCallSession.objects.filter(
        date__gte=seven_days_ago, status=RollCallSession.Status.COMPLETED
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
    avg_attendance = (
        round((total_present / total_expected * 100), 1) if total_expected > 0 else 0
    )

    return {
        "total_boarders": total_boarders,
        "active_exeats": active_exeats,
        "overdue_exeats": overdue_exeats,
        "total_infractions": total_infractions,
        "house_breakdown": house_breakdown,
        "avg_attendance": avg_attendance,
        "current_term": current_term,
    }


def get_detailed_report(
    report_type, house_id=None, date_from=None, date_to=None, term_id=None
):
    """Build a scoped, printable detail report as column labels and row values."""
    if report_type == "placement_exceptions":
        return _placement_exception_report(house_id=house_id)
    if report_type == "attendance_league":
        return _attendance_league_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )
    if report_type == "overdue_register":
        return _overdue_register_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )
    if report_type == "gate_conflicts":
        return _gate_conflict_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )
    if report_type == "approval_turnaround":
        return _approval_turnaround_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )
    if report_type == "exeat_analytics":
        return _exeat_analytics_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )
    if report_type == "room_register":
        return _room_register_report(house_id=house_id)
    if report_type == "staff_activity":
        return _staff_activity_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )
    if report_type == "welfare_infirmary":
        return _welfare_infirmary_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )
    if report_type == "data_completeness":
        return _data_completeness_report(house_id=house_id)
    if report_type == "movement_concerns":
        return _movement_concern_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )
    if report_type == "conduct_normalized":
        return _conduct_normalized_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )
    if report_type == "intervention_effectiveness":
        return _intervention_effectiveness_report(
            house_id=house_id,
            date_from=date_from,
            date_to=date_to,
            term_id=term_id,
        )

    if report_type == "housing":
        houses = House.objects.all().order_by("display_order", "name")
        if house_id is not None:
            houses = houses.filter(pk=house_id)
        houses = houses.annotate(
            boarder_count=Count(
                "students",
                distinct=True,
                filter=Q(
                    students__residency=Student.ResidencyStatus.BOARDING,
                    students__status__in=(
                        Student.EnrollmentStatus.ENROLLED,
                        Student.EnrollmentStatus.APPROVED,
                        Student.EnrollmentStatus.IN_PROGRESS,
                    ),
                ),
            ),
            assigned_boarder_count=Count(
                "students",
                distinct=True,
                filter=Q(
                    students__residency=Student.ResidencyStatus.BOARDING,
                    students__status__in=(
                        Student.EnrollmentStatus.ENROLLED,
                        Student.EnrollmentStatus.APPROVED,
                        Student.EnrollmentStatus.IN_PROGRESS,
                    ),
                    students__dormitory__isnull=False,
                ),
            ),
            unassigned_count=Count(
                "students",
                distinct=True,
                filter=Q(
                    students__residency=Student.ResidencyStatus.BOARDING,
                    students__status__in=(
                        Student.EnrollmentStatus.ENROLLED,
                        Student.EnrollmentStatus.APPROVED,
                        Student.EnrollmentStatus.IN_PROGRESS,
                    ),
                    students__dormitory__isnull=True,
                ),
            ),
            room_unassigned_count=Count(
                "students",
                distinct=True,
                filter=Q(
                    students__residency=Student.ResidencyStatus.BOARDING,
                    students__status__in=(
                        Student.EnrollmentStatus.ENROLLED,
                        Student.EnrollmentStatus.APPROVED,
                        Student.EnrollmentStatus.IN_PROGRESS,
                    ),
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
        dormitories = (
            Dormitory.objects.all()
            .annotate(
                occupied=Count(
                    "boarders",
                    filter=Q(
                        boarders__residency=Student.ResidencyStatus.BOARDING,
                        boarders__status__in=(
                            Student.EnrollmentStatus.ENROLLED,
                            Student.EnrollmentStatus.APPROVED,
                            Student.EnrollmentStatus.IN_PROGRESS,
                        ),
                    ),
                )
            )
            .values(
                "id",
                "house_id",
                "house__name",
                "name",
                "gender",
                "capacity",
                "occupied",
            )
            .order_by("house__display_order", "house__name", "gender", "name")
        )
        if house_id is not None:
            dormitories = dormitories.filter(house_id=house_id)
        dormitories_by_house = {}
        for dormitory in dormitories:
            dormitories_by_house.setdefault(dormitory["house_id"], []).append(dormitory)

        rooms = (
            DormitoryRoom.objects.select_related("dormitory", "dormitory__house")
            .annotate(
                occupied=Count(
                    "students",
                    filter=Q(
                        students__residency=Student.ResidencyStatus.BOARDING,
                        students__status__in=(
                            Student.EnrollmentStatus.ENROLLED,
                            Student.EnrollmentStatus.APPROVED,
                            Student.EnrollmentStatus.IN_PROGRESS,
                        ),
                    ),
                )
            )
            .order_by(
                "dormitory__house__display_order",
                "dormitory__house__name",
                "dormitory__name",
                "name",
            )
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
        cohorts = {}
        over_capacity = []
        unrecorded_beds = []
        houses_without_beds = []
        for house in houses:
            boarder_count = house["boarder_count"]
            assigned_count = house["assigned_boarder_count"]
            unassigned_count = house["unassigned_count"]
            room_unassigned_count = house["room_unassigned_count"]
            house_dormitories = dormitories_by_house.get(house["id"], [])
            house_capacity = sum(
                dormitory["capacity"] for dormitory in house_dormitories
            )
            total_boarders += boarder_count
            total_assigned += assigned_count
            total_unassigned += unassigned_count
            total_room_unassigned += room_unassigned_count
            total_capacity += house_capacity
            house_occupancy = (
                round(assigned_count * 100 / house_capacity, 1) if house_capacity else 0
            )
            rows.append(
                (
                    house["name"],
                    "House total",
                    "—",
                    house_capacity,
                    assigned_count,
                    house_capacity - assigned_count,
                    f"{house_occupancy}%",
                    f"{boarder_count} boarders; {unassigned_count} without dormitory",
                )
            )
            if not house_dormitories:
                if boarder_count:
                    houses_without_beds.append(
                        {
                            "house": house["name"],
                            "boarders": boarder_count,
                        }
                    )
                    rows.append(
                        (
                            house["name"],
                            "No dormitories configured",
                            "—",
                            0,
                            boarder_count,
                            -boarder_count,
                            "0%",
                            _PLACEMENT_STATES["unrecorded"],
                        )
                    )
                else:
                    rows.append(
                        (
                            house["name"],
                            "No dormitories configured",
                            "—",
                            0,
                            0,
                            0,
                            "0%",
                            _PLACEMENT_STATES["unconfigured"],
                        )
                    )
            for dormitory in house_dormitories:
                occupied = dormitory["occupied"]
                capacity = dormitory["capacity"]
                occupancy = round(occupied * 100 / capacity, 1) if capacity else 0
                excess = max(0, occupied - capacity)
                state, tone = _placement_state(capacity, occupied)
                if capacity > 0 and excess:
                    over_capacity_dormitories += 1
                    over_capacity.append(
                        {
                            "house": dormitory["house__name"],
                            "dormitory": dormitory["name"],
                            "excess": excess,
                        }
                    )
                elif capacity <= 0 and occupied:
                    unrecorded_beds.append(
                        f"{dormitory['house__name']} · {dormitory['name']}: "
                        f"{_plural(occupied, 'boarder')} with 0 beds configured"
                    )
                cohort = _cohort_bucket(cohorts, dormitory["gender"])
                cohort_row = {
                    "house": dormitory["house__name"],
                    "dormitory": dormitory["name"],
                    "gender_label": _DORMITORY_GENDER_LABELS.get(
                        dormitory["gender"], "—"
                    ),
                    "capacity": capacity,
                    "placed": occupied,
                    "free": capacity - occupied,
                    "occupancy": occupancy,
                    "occupancy_display": f"{occupancy}%",
                    "state": state,
                    "tone": tone,
                    "rooms": [],
                }
                cohort["rows"].append(cohort_row)
                rows.append(
                    (
                        dormitory["house__name"],
                        dormitory["name"],
                        _DORMITORY_GENDER_LABELS.get(dormitory["gender"], "—"),
                        capacity,
                        occupied,
                        capacity - occupied,
                        f"{occupancy}%",
                        state,
                    )
                )
                for room in rooms_by_dormitory.get(dormitory["id"], []):
                    room_occupancy = (
                        round(room.occupied * 100 / room.capacity, 1)
                        if room.capacity
                        else 0
                    )
                    room_state, _room_tone = _placement_state(
                        room.capacity, room.occupied
                    )
                    cohort_row["rooms"].append(
                        {
                            "name": room.name,
                            "capacity": room.capacity,
                            "placed": room.occupied,
                            "free": room.capacity - room.occupied,
                            "occupancy_display": f"{room_occupancy}%",
                            "state": room_state,
                        }
                    )
                    rows.append(
                        (
                            room.dormitory.house.name,
                            room.dormitory.name,
                            room.name,
                            room.capacity,
                            room.occupied,
                            room.capacity - room.occupied,
                            f"{room_occupancy}%",
                            room_state,
                        )
                    )
                cohort_row["room_placed"] = sum(
                    room["placed"] for room in cohort_row["rooms"]
                )
            if unassigned_count:
                rows.append(
                    (
                        house["name"],
                        "Unassigned dormitory",
                        "—",
                        "—",
                        unassigned_count,
                        "—",
                        "—",
                        "Placement required",
                    )
                )
        occupancy = (
            round(total_assigned * 100 / total_capacity, 1) if total_capacity else 0
        )
        total_free = total_capacity - total_assigned

        cohort_groups = []
        for bucket in sorted(
            cohorts.values(), key=lambda item: (item["order"], item["label"])
        ):
            group_capacity = sum(row["capacity"] for row in bucket["rows"])
            group_placed = sum(row["placed"] for row in bucket["rows"])
            group_occupancy = (
                round(group_placed * 100 / group_capacity, 1) if group_capacity else 0
            )
            cohort_groups.append(
                {
                    "key": bucket["key"],
                    "label": bucket["label"],
                    "rows": bucket["rows"],
                    "subtotal": {
                        "capacity": group_capacity,
                        "placed": group_placed,
                        "free": group_capacity - group_placed,
                        "occupancy": group_occupancy,
                        "occupancy_display": f"{group_occupancy}%",
                    },
                    "rooms_recorded": sum(
                        room["placed"]
                        for row in bucket["rows"]
                        for room in row["rooms"]
                    ),
                }
            )

        # The verdict states the answer this report exists to give, before anyone
        # has to subtract a column or total a house by hand.
        free_parts = [
            f"{group['subtotal']['free']} {group['label'].lower()}'"
            for group in cohort_groups
            if group["rows"]
        ]
        headline = f"{total_free} free beds"
        if free_parts:
            headline += " — " + ", ".join(free_parts)
        headline += f" ({occupancy}% full)"
        notes = []
        if over_capacity_dormitories:
            notes.append(
                f"{_plural(over_capacity_dormitories, 'dormitory', 'dormitories')} over capacity"
            )
        else:
            notes.append("Nothing over capacity")
        if total_unassigned:
            notes.append(
                f"{_plural(total_unassigned, 'boarder')} waiting for a dormitory"
            )
        else:
            notes.append("Nobody waiting for a dormitory")
        if total_room_unassigned:
            notes.append(
                f"{_plural(total_room_unassigned, 'boarder')} in a dormitory without a room"
            )

        awaiting_boarders = Student.objects.exclude(
            status=Student.EnrollmentStatus.COMPLETED
        ).filter(
            residency=Student.ResidencyStatus.BOARDING,
            dormitory__isnull=True,
        )
        if house_id is not None:
            awaiting_boarders = awaiting_boarders.filter(house_id=house_id)
        awaiting_students = [
            {
                "name": student.full_name,
                "house": student.house.name if student.house else "—",
                "admission_no": student.admission_no,
            }
            for student in awaiting_boarders.select_related("house").order_by(
                "house__name", "full_name"
            )
        ]

        attention = []
        if awaiting_students:
            attention.append(
                {
                    "label": "Awaiting a dormitory",
                    "tone": "bad",
                    "detail": f"{_plural(len(awaiting_students), 'boarder')} placed in a house with no dormitory",
                    "students": awaiting_students,
                }
            )
        if over_capacity:
            attention.append(
                {
                    "label": "Over capacity",
                    "tone": "bad",
                    "detail": "; ".join(
                        f"{item['house']} · {item['dormitory']} over capacity by {item['excess']}"
                        for item in over_capacity
                    ),
                    "students": [],
                }
            )
        if houses_without_beds:
            unrecorded_beds.extend(
                f"{item['house']}: {_plural(item['boarders'], 'boarder')} with no dormitory blocks configured"
                for item in houses_without_beds
            )
        if unrecorded_beds:
            attention.append(
                {
                    "label": "Beds unrecorded",
                    "tone": "bad",
                    "detail": "; ".join(unrecorded_beds),
                    "students": [],
                }
            )
        if total_room_unassigned:
            attention.append(
                {
                    "label": "Awaiting a room",
                    "tone": "watch",
                    "detail": f"{_plural(total_room_unassigned, 'boarder')} in a dormitory but not assigned to a room",
                    "students": [],
                }
            )

        return {
            "title": "Boarding Occupancy & Placement",
            "headers": (
                "House",
                "Dormitory",
                "Block / Room",
                "Beds",
                "Placed",
                "Free",
                "Fill",
                "State",
            ),
            "rows": rows,
            "cohort_groups": cohort_groups,
            "totals": {
                "capacity": total_capacity,
                "placed": total_assigned,
                "free": total_free,
                "occupancy": occupancy,
                "occupancy_display": f"{occupancy}%",
            },
            "verdict": {"headline": headline, "notes": notes},
            "attention": attention,
            "record_label": (
                f"{_plural(sum(len(group['rows']) for group in cohort_groups), 'dormitory', 'dormitories')}"
                f" · {_plural(total_boarders, 'boarder')}"
            ),
            "summary": (
                ("Boarders", total_boarders),
                ("Configured beds", total_capacity),
                ("Placed boarders", total_assigned),
                ("Awaiting dorm placement", total_unassigned),
                ("Occupied capacity", f"{occupancy}%"),
                ("Free beds", total_free),
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
            rate = round(present_count * 100 / student_count, 1) if student_count else 0
            rows.append(
                (
                    session["date"],
                    session["house__name"],
                    session["dormitory__name"] or "All dormitories",
                    _ROLL_CALL_TYPE_LABELS.get(
                        session["session_type"], session["session_type"]
                    ),
                    _ROLL_CALL_STATUS_LABELS.get(session["status"], session["status"]),
                    student_count,
                    present_count,
                    session["report_absent_count"],
                    session["report_on_exeat_count"],
                    session["report_infirmary_count"],
                    session["report_unmarked_count"],
                    f"{rate}%",
                    session["conducted_by__full_name"] or "—",
                )
            )
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
            )
            .annotate(
                absence_count=Count("id"),
                sessions_count=Count("session_id", distinct=True),
            )
            .filter(absence_count__gte=2)
            .order_by("-absence_count", "student__full_name")
            .values_list(
                "student__full_name",
                "student__house__name",
                "student__dormitory__name",
                "absence_count",
                "sessions_count",
            )
        )
        return {
            "title": "Roll-call Attendance & Follow-up",
            "headers": (
                "Date",
                "House",
                "Dormitory",
                "Session",
                "Status",
                "Expected",
                "Present",
                "Absent",
                "On Exeat",
                "Infirmary",
                "Unmarked",
                "Attendance",
                "Conducted By",
            ),
            "rows": rows,
            "summary": (
                ("Sessions", len(rows)),
                (
                    "Incomplete sessions",
                    sum(1 for row in rows if row[4] == "In Progress"),
                ),
                ("Sessions with absences", sum(1 for row in rows if row[7])),
                ("Students marked absent", sum(row[7] for row in rows)),
                ("Students not yet marked", sum(row[10] for row in rows)),
                ("Students with 2+ absences", len(repeat_absence_rows)),
            ),
            "sections": (
                {
                    "title": "Students with repeated roll-call absences",
                    "headers": (
                        "Student",
                        "House",
                        "Dormitory",
                        "Absences",
                        "Sessions",
                    ),
                    "rows": repeat_absence_rows,
                },
            ),
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
            actual_departure = (
                exeat["actual_departure"] or exeat["reconciled_departure"]
            )
            actual_return = exeat["actual_return"] or exeat["reconciled_return"]
            if exeat["status"] != Exeat.Status.APPROVED:
                state = _EXEAT_STATUS_LABELS.get(exeat["status"], exeat["status"])
            elif exeat["reconciliation_outcome"] == ExeatReconciliation.Outcome.UNUSED:
                state = "Pass Unused"
            elif actual_return:
                state = (
                    "Returned (Reconciled)"
                    if exeat["reconciliation_outcome"]
                    == ExeatReconciliation.Outcome.RETURNED
                    else "Returned"
                )
            elif (
                exeat["reconciliation_outcome"]
                == ExeatReconciliation.Outcome.STUDENT_OUT
            ):
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
            rows.append(
                (
                    exeat["exeat_number"],
                    exeat["student__full_name"],
                    exeat["student__house__name"] or "Unassigned",
                    _EXEAT_TYPE_LABELS.get(exeat["exeat_type"], exeat["exeat_type"]),
                    exeat["expected_departure"],
                    actual_departure or "Not logged",
                    exeat["expected_return"],
                    actual_return or "Not logged",
                    state,
                )
            )
        return {
            "title": "Exeat & Gate Compliance",
            "headers": (
                "Pass",
                "Student",
                "House",
                "Type",
                "Scheduled Departure",
                "Recorded Departure",
                "Expected Return",
                "Recorded Return",
                "State",
            ),
            "rows": rows,
            "summary": (
                ("Passes in period", len(rows)),
                (
                    "Movement not recorded",
                    sum(1 for row in rows if row[5] == "Not logged"),
                ),
                (
                    "Return not recorded",
                    sum(1 for row in rows if row[7] == "Not logged"),
                ),
                (
                    "Overdue",
                    sum(1 for row in rows if str(row[8]).startswith("OVERDUE")),
                ),
                (
                    "Unresolved departure/return",
                    sum(
                        1
                        for row in rows
                        if row[8] in {"Pass Unresolved", "Departure Not Recorded"}
                    ),
                ),
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
    severity_breakdown = (
        active_incidents.values("severity")
        .annotate(incident_count=Count("id"))
        .order_by("severity")
    )
    offense_breakdown = (
        active_incidents.values("offense")
        .annotate(incident_count=Count("id"))
        .order_by("-incident_count", "offense")[:10]
    )
    repeat_students = (
        active_incidents.values(
            "student_id",
            "student__full_name",
            "student__house__name",
            "student__dormitory__name",
        )
        .annotate(incident_count=Count("id"))
        .filter(incident_count__gte=2)
        .order_by("-incident_count", "student__full_name")
    )
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
        (item["offense"], item["incident_count"]) for item in offense_breakdown
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
        )
        .annotate(
            incident_count=Count("id"),
            student_count=Count("student_id", distinct=True),
            major_count=Count(
                "id", filter=Q(severity=DisciplinaryRecord.Severity.MAJOR)
            ),
            critical_count=Count(
                "id", filter=Q(severity=DisciplinaryRecord.Severity.CRITICAL)
            ),
        )
        .order_by("created_at__year", "created_at__month")
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
        "headers": (
            "Logged",
            "Student",
            "House",
            "Dormitory",
            "Incident",
            "Severity",
            "Action Taken",
            "Reported By",
            "Record Status",
        ),
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
                "headers": (
                    "Month",
                    "Recorded incidents",
                    "Students",
                    "Major",
                    "Critical",
                ),
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


def _resolve_report_period(term=None, date_from=None, date_to=None):
    start = term.start_date if term is not None else date_from
    end = term.end_date if term is not None else date_to
    if date_from is not None and (start is None or date_from > start):
        start = date_from
    if date_to is not None and (end is None or date_to < end):
        end = date_to
    return start, end


def get_boarding_kpi_pack(house_id=None, term=None, date_from=None, date_to=None):
    """Build a scoped boarding and student-welfare KPI pack."""
    now = timezone.now()
    start, end = _resolve_report_period(term, date_from, date_to)

    boarders = Student.objects.exclude(
        status=Student.EnrollmentStatus.COMPLETED
    ).filter(residency=Student.ResidencyStatus.BOARDING)
    day_students = Student.objects.exclude(
        status=Student.EnrollmentStatus.COMPLETED
    ).filter(residency=Student.ResidencyStatus.DAY)
    if house_id is not None:
        boarders = boarders.filter(house_id=house_id)
        day_students = day_students.filter(house_id=house_id)

    total_boarders = boarders.count()
    placed_boarders = boarders.filter(dormitory__isnull=False).count()
    awaiting_dorm = boarders.filter(dormitory__isnull=True).count()
    awaiting_room = boarders.filter(dormitory__isnull=False, room__isnull=True).count()
    day_student_count = day_students.count()

    dormitories = Dormitory.objects.all()
    if house_id is not None:
        dormitories = dormitories.filter(house_id=house_id)
    configured_beds = dormitories.aggregate(total=Sum("capacity"))["total"] or 0
    over_capacity_dorms = (
        dormitories.annotate(
            occupied=Count(
                "boarders",
                filter=Q(
                    boarders__residency=Student.ResidencyStatus.BOARDING,
                    boarders__status__in=(
                        Student.EnrollmentStatus.ENROLLED,
                        Student.EnrollmentStatus.APPROVED,
                        Student.EnrollmentStatus.IN_PROGRESS,
                    ),
                ),
            )
        )
        .filter(occupied__gt=F("capacity"))
        .count()
    )
    occupancy = (
        round(placed_boarders * 100 / configured_beds, 1) if configured_beds else 0
    )

    exeats = Exeat.objects.all()
    if house_id is not None:
        exeats = exeats.filter(student__house_id=house_id)
    if term is not None:
        exeats = exeats.filter(academic_term=term)
    if start is not None:
        exeats = exeats.filter(expected_departure__date__gte=start)
    if end is not None:
        exeats = exeats.filter(expected_departure__date__lte=end)
    exeat_total = exeats.count()
    exeat_pending = exeats.filter(status=Exeat.Status.PENDING).count()
    exeat_approved = exeats.filter(status=Exeat.Status.APPROVED).count()
    exeat_rejected = exeats.filter(status=Exeat.Status.REJECTED).count()

    active_qs = (
        Exeat.objects.filter(status=Exeat.Status.APPROVED)
        .filter(
            Q(
                movement__actual_departure__isnull=False,
                movement__actual_return__isnull=True,
            )
            | Q(
                reconciliation__outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
                reconciliation__actual_return__isnull=True,
            )
        )
        .distinct()
    )
    if house_id is not None:
        active_qs = active_qs.filter(student__house_id=house_id)
    active_out = active_qs.count()
    overdue = active_qs.filter(expected_return__lte=now).count()

    sessions = RollCallSession.objects.filter(status=RollCallSession.Status.COMPLETED)
    if house_id is not None:
        sessions = sessions.filter(house_id=house_id)
    if term is not None:
        sessions = sessions.filter(academic_term=term)
    if start is not None:
        sessions = sessions.filter(date__gte=start)
    if end is not None:
        sessions = sessions.filter(date__lte=end)
    attendance = sessions.aggregate(
        expected=Count("entries"),
        present=Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.PRESENT)
        ),
    )
    expected_marks = attendance["expected"] or 0
    present_marks = attendance["present"] or 0
    attendance_rate = (
        round(present_marks * 100 / expected_marks, 1) if expected_marks else 0
    )

    incidents = DisciplinaryRecord.objects.all()
    if house_id is not None:
        incidents = incidents.filter(student__house_id=house_id)
    if term is not None:
        incidents = incidents.filter(academic_term=term)
    if start is not None:
        incidents = incidents.filter(created_at__date__gte=start)
    if end is not None:
        incidents = incidents.filter(created_at__date__lte=end)
    active_incidents = incidents.filter(corrected_at__isnull=True)
    active_incident_count = active_incidents.count()
    students_with_incidents = active_incidents.values("student_id").distinct().count()
    repeat_students = (
        active_incidents.values("student_id")
        .annotate(incident_count=Count("id"))
        .filter(incident_count__gte=2)
        .count()
    )

    exeat_type_rows = [
        (
            _EXEAT_TYPE_LABELS.get(row["exeat_type"], row["exeat_type"]),
            row["count"],
        )
        for row in exeats.values("exeat_type")
        .annotate(count=Count("id"))
        .order_by("-count", "exeat_type")
    ]
    attendance_rows = [
        (
            row["house__name"],
            row["expected"],
            row["present"],
            f'{round(row["present"] * 100 / row["expected"], 1) if row["expected"] else 0}%',
        )
        for row in sessions.values("house__name")
        .annotate(
            expected=Count("entries"),
            present=Count(
                "entries", filter=Q(entries__status=RollCallEntry.Status.PRESENT)
            ),
        )
        .order_by("house__name")
    ]
    severity_rows = [
        (
            _SEVERITY_LABELS.get(row["severity"], row["severity"]),
            row["count"],
        )
        for row in active_incidents.values("severity")
        .annotate(count=Count("id"))
        .order_by("severity")
    ]

    capacity_exceptions = DormitoryCapacityException.objects.select_related(
        "dormitory__house", "approved_by"
    ).filter(cancelled_at__isnull=True)
    if house_id is not None:
        capacity_exceptions = capacity_exceptions.filter(
            dormitory__house_id=house_id
        )
    if start is not None:
        capacity_exceptions = capacity_exceptions.filter(ends_at__date__gte=start)
    if end is not None:
        capacity_exceptions = capacity_exceptions.filter(starts_at__date__lte=end)
    active_exception_count = capacity_exceptions.filter(
        starts_at__lte=now, ends_at__gte=now
    ).count()
    capacity_exception_rows = [
        (
            exception.dormitory.house.name,
            exception.dormitory.name,
            f"+{exception.additional_capacity}",
            timezone.localtime(exception.starts_at).strftime("%d %b %Y %H:%M"),
            timezone.localtime(exception.ends_at).strftime("%d %b %Y %H:%M"),
            (
                exception.approved_by.get_full_name()
                or exception.approved_by.get_username()
            )
            if exception.approved_by
            else "—",
            exception.reason,
        )
        for exception in capacity_exceptions.order_by("-starts_at")[:100]
    ]

    return {
        "title": "Boarding Termly KPI Pack",
        "layout": "boarding_kpi",
        "point_in_time": False,
        "kpi_tiles": (
            ("Boarders", total_boarders, "indigo"),
            ("Day students", day_student_count, "indigo"),
            ("Configured beds", configured_beds, "sky"),
            ("Occupancy", f"{occupancy}%", "emerald"),
            ("Awaiting dorm placement", awaiting_dorm, "amber"),
            ("Awaiting room placement", awaiting_room, "amber"),
            ("Over-capacity dormitories", over_capacity_dorms, "rose"),
            ("Active capacity exceptions", active_exception_count, "amber"),
            ("Currently out", active_out, "sky"),
            ("Overdue returns", overdue, "rose"),
            ("Exeats in period", exeat_total, "violet"),
            ("Attendance", f"{attendance_rate}%", "emerald"),
            ("Active incidents", active_incident_count, "rose"),
            ("Students with incidents", students_with_incidents, "violet"),
            ("Repeat students (2+)", repeat_students, "amber"),
        ),
        "sections": (
            {
                "title": "Exeats by type",
                "headers": ("Exeat type", "Passes"),
                "rows": exeat_type_rows,
            },
            {
                "title": "Attendance by house",
                "headers": ("House", "Marks expected", "Present", "Attendance"),
                "rows": attendance_rows,
            },
            {
                "title": "Active conduct by severity",
                "headers": ("Severity", "Active cases"),
                "rows": severity_rows,
            },
            {
                "title": "Dormitory capacity exceptions (weekly review)",
                "headers": (
                    "House",
                    "Dormitory",
                    "Extra places",
                    "Starts",
                    "Ends",
                    "Approved by",
                    "Reason",
                ),
                "rows": capacity_exception_rows,
            },
        ),
        "highlights": (
            f"Exeats: {exeat_pending} pending, {exeat_approved} approved, "
            f"{exeat_rejected} rejected in the selected period.",
            "Occupancy compares placed boarders against configured dormitory capacity.",
            "Attendance counts completed roll calls only; Present ÷ Expected marks.",
            (
                f"{active_exception_count} time-bound dormitory capacity exception(s) "
                "are active now. Review exceptions weekly and do not make them permanent."
            ),
        ),
        "record_count": total_boarders,
    }


def _placement_exception_report(house_id=None):
    boarders = Student.objects.exclude(
        status=Student.EnrollmentStatus.COMPLETED
    ).filter(residency=Student.ResidencyStatus.BOARDING)
    if house_id is not None:
        boarders = boarders.filter(house_id=house_id)

    rows = []
    unplaced = boarders.filter(dormitory__isnull=True).select_related("house")
    for student in unplaced:
        rows.append(
            (
                "Unplaced boarder",
                student.house.name if student.house else "Unassigned",
                "No dormitory",
                student.full_name,
            )
        )

    roomless = boarders.filter(
        dormitory__isnull=False, room__isnull=True
    ).select_related("house", "dormitory")
    for student in roomless:
        rows.append(
            (
                "Room not assigned",
                student.house.name if student.house else "Unassigned",
                student.dormitory.name,
                student.full_name,
            )
        )

    mismatches = (
        boarders.filter(dormitory__isnull=False)
        .filter(Q(house__isnull=True) | ~Q(house_id=F("dormitory__house_id")))
        .select_related("house", "dormitory", "dormitory__house")
    )
    for student in mismatches:
        rows.append(
            (
                "House/dormitory mismatch",
                student.house.name if student.house else "Unassigned",
                f"{student.dormitory.name} ({student.dormitory.house.name})",
                student.full_name,
            )
        )

    day_misassigned = (
        Student.objects.exclude(status=Student.EnrollmentStatus.COMPLETED)
        .filter(
            residency=Student.ResidencyStatus.DAY,
        )
        .exclude(dormitory__isnull=True)
        .select_related("house", "dormitory")
    )
    if house_id is not None:
        day_misassigned = day_misassigned.filter(house_id=house_id)
    for student in day_misassigned:
        rows.append(
            (
                "Day student in dormitory",
                student.house.name if student.house else "Unassigned",
                student.dormitory.name,
                student.full_name,
            )
        )

    dormitories = Dormitory.objects.select_related("house").annotate(
        occupied=Count(
            "boarders",
            filter=Q(
                boarders__residency=Student.ResidencyStatus.BOARDING,
                boarders__status__in=(
                    Student.EnrollmentStatus.ENROLLED,
                    Student.EnrollmentStatus.APPROVED,
                    Student.EnrollmentStatus.IN_PROGRESS,
                ),
            ),
        )
    )
    if house_id is not None:
        dormitories = dormitories.filter(house_id=house_id)
    over_dorms = dormitories.filter(occupied__gt=F("capacity"))
    for dormitory in over_dorms:
        rows.append(
            (
                "Over-capacity dormitory",
                dormitory.house.name,
                dormitory.name,
                f"{dormitory.occupied}/{dormitory.capacity}",
            )
        )

    rooms = DormitoryRoom.objects.select_related(
        "dormitory", "dormitory__house"
    ).annotate(
        occupied=Count(
            "students",
            filter=Q(
                students__residency=Student.ResidencyStatus.BOARDING,
                students__status__in=(
                    Student.EnrollmentStatus.ENROLLED,
                    Student.EnrollmentStatus.APPROVED,
                    Student.EnrollmentStatus.IN_PROGRESS,
                ),
            ),
        )
    )
    if house_id is not None:
        rooms = rooms.filter(dormitory__house_id=house_id)
    over_rooms = rooms.filter(occupied__gt=F("capacity"))
    for room in over_rooms:
        rows.append(
            (
                "Over-capacity room",
                room.dormitory.house.name,
                f"{room.dormitory.name} / {room.name}",
                f"{room.occupied}/{room.capacity}",
            )
        )

    return {
        "title": "Placement Exception Queue",
        "headers": ("Exception", "House", "Location", "Detail"),
        "rows": rows,
        "summary": (
            ("Unplaced boarders", unplaced.count()),
            ("Rooms not assigned", roomless.count()),
            ("House/dormitory mismatches", mismatches.count()),
            ("Day students in dormitory", day_misassigned.count()),
            ("Over-capacity dormitories", over_dorms.count()),
            ("Over-capacity rooms", over_rooms.count()),
            ("Total exceptions", len(rows)),
        ),
        "highlights": (
            "Each row is one action item; resolve by editing placement on the student record.",
            "Over-capacity counts students beyond a dormitory or room's configured capacity.",
        ),
        "point_in_time": True,
    }


def _attendance_league_report(
    house_id=None, date_from=None, date_to=None, term_id=None
):
    sessions = RollCallSession.objects.filter(status=RollCallSession.Status.COMPLETED)
    if house_id is not None:
        sessions = sessions.filter(house_id=house_id)
    if date_from:
        sessions = sessions.filter(date__gte=date_from)
    if date_to:
        sessions = sessions.filter(date__lte=date_to)
    if term_id:
        sessions = sessions.filter(academic_term_id=term_id)

    aggregates = sessions.values("house__name").annotate(
        session_count=Count("id", distinct=True),
        expected=Count("entries"),
        present=Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.PRESENT)
        ),
        absent=Count("entries", filter=Q(entries__status=RollCallEntry.Status.ABSENT)),
        on_exeat=Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.ON_EXEAT)
        ),
        infirmary=Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.INFIRMARY)
        ),
        activity=Count(
            "entries",
            filter=Q(entries__status=RollCallEntry.Status.OFFICIAL_ACTIVITY),
        ),
        unmarked=Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.UNMARKED)
        ),
    )
    ranked = []
    for row in aggregates:
        expected = row["expected"] or 0
        rate = round(row["present"] * 100 / expected, 1) if expected else 0
        ranked.append((row, rate))
    ranked.sort(key=lambda item: (-item[1], item[0]["house__name"] or ""))

    rows = [
        (
            rank,
            row["house__name"] or "Unassigned",
            row["session_count"],
            row["expected"],
            row["present"],
            row["absent"],
            row["on_exeat"],
            row["infirmary"],
            row["activity"],
            row["unmarked"],
            f"{rate}%",
        )
        for rank, (row, rate) in enumerate(ranked, start=1)
    ]
    total_expected = sum(row[3] for row in rows)
    total_present = sum(row[4] for row in rows)
    overall = round(total_present * 100 / total_expected, 1) if total_expected else 0

    return {
        "title": "House Attendance League",
        "headers": (
            "Rank",
            "House",
            "Sessions",
            "Expected",
            "Present",
            "Absent",
            "On Exeat",
            "Infirmary",
            "Activity",
            "Unmarked",
            "Attendance",
        ),
        "rows": rows,
        "summary": (
            ("Houses ranked", len(rows)),
            ("Completed sessions", sum(row[2] for row in rows)),
            ("Attendance overall", f"{overall}%"),
            ("Highest", f"{rows[0][1]} ({rows[0][10]})" if rows else "—"),
            ("Lowest", f"{rows[-1][1]} ({rows[-1][10]})" if rows else "—"),
        ),
        "highlights": (
            "Attendance = Present marks / Expected marks across completed roll calls only.",
            "Houses with no completed roll-call records in the period are not ranked.",
        ),
        "point_in_time": False,
    }


def _overdue_register_report(house_id=None, date_from=None, date_to=None, term_id=None):
    now = timezone.now()
    exeats = Exeat.objects.filter(status=Exeat.Status.APPROVED)
    if house_id is not None:
        exeats = exeats.filter(student__house_id=house_id)
    if date_from:
        exeats = exeats.filter(expected_return__date__gte=date_from)
    if date_to:
        exeats = exeats.filter(expected_return__date__lte=date_to)
    if term_id:
        exeats = exeats.filter(academic_term_id=term_id)

    records = exeats.values(
        "exeat_number",
        "student__full_name",
        "student__house__name",
        "exeat_type",
        "expected_return",
        actual_departure=F("movement__actual_departure"),
        actual_return=F("movement__actual_return"),
        reconciled_departure=F("reconciliation__actual_departure"),
        reconciled_return=F("reconciliation__actual_return"),
        reconciliation_outcome=F("reconciliation__outcome"),
    )
    rows = []
    overdue_rows = []
    late_return_rows = []
    overdue_now = 0
    late_returned = 0
    max_late = 0.0
    for exeat in records:
        expected_return = exeat["expected_return"]
        actual_return = exeat["actual_return"] or exeat["reconciled_return"]
        departed = exeat["actual_departure"] or exeat["reconciled_departure"]
        is_out = (departed is not None and actual_return is None) or (
            exeat["reconciliation_outcome"] == ExeatReconciliation.Outcome.STUDENT_OUT
            and actual_return is None
        )

        if is_out and expected_return and now >= expected_return:
            hours_late = round((now - expected_return).total_seconds() / 3600, 1)
            state = "OVERDUE - still out"
            overdue_now += 1
        elif (
            departed is not None
            and actual_return
            and expected_return
            and actual_return > expected_return
        ):
            hours_late = round(
                (actual_return - expected_return).total_seconds() / 3600, 1
            )
            state = "Returned late"
            late_returned += 1
        else:
            continue

        max_late = max(max_late, hours_late)
        row = (
            exeat["exeat_number"],
            exeat["student__full_name"],
            exeat["student__house__name"] or "Unassigned",
            _EXEAT_TYPE_LABELS.get(exeat["exeat_type"], exeat["exeat_type"]),
            expected_return,
            actual_return or "Still out",
            f"{hours_late}h",
            state,
        )
        rows.append(row)
        if state.startswith("OVERDUE"):
            overdue_rows.append(row)
        else:
            late_return_rows.append(row)
    rows.sort(key=lambda row: row[4])
    overdue_rows.sort(key=lambda row: row[4], reverse=True)
    late_return_rows.sort(key=lambda row: row[4], reverse=True)

    return {
        "title": "Overdue & Late-Return Register",
        "headers": (
            "Pass",
            "Student",
            "House",
            "Type",
            "Expected Return",
            "Recorded Return",
            "Delay",
            "State",
        ),
        "rows": rows,
        "summary": (
            ("Currently overdue (still out)", overdue_now),
            ("Returned late in period", late_returned),
            ("Total exceptions", len(rows)),
            ("Maximum delay", f"{max_late}h" if rows else "—"),
        ),
        "sections": (
            {
                "title": "Currently overdue — student still out",
                "headers": (
                    "Pass",
                    "Student",
                    "House",
                    "Type",
                    "Expected Return",
                    "Recorded Return",
                    "Delay",
                    "State",
                ),
                "rows": overdue_rows,
            },
            {
                "title": "Returned late during the period",
                "headers": (
                    "Pass",
                    "Student",
                    "House",
                    "Type",
                    "Expected Return",
                    "Recorded Return",
                    "Delay",
                    "State",
                ),
                "rows": late_return_rows,
            },
        ),
        "highlights": (
            "The register separates passes still out beyond the expected return from passes that have already returned late.",
            "A pass without a confirmed departure is not counted as overdue; use the reconciliation workflow to resolve missing gate events.",
            "Departed/returned timestamps come from the gate log, falling back to reconciled records.",
        ),
        "point_in_time": False,
    }


def _gate_conflict_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Report roll-call marks that conflicted with logged gate departures."""
    entries = RollCallEntry.objects.filter(gate_conflict_at__isnull=False)
    if house_id is not None:
        entries = entries.filter(session__house_id=house_id)
    if date_from:
        entries = entries.filter(session__date__gte=date_from)
    if date_to:
        entries = entries.filter(session__date__lte=date_to)
    if term_id:
        entries = entries.filter(session__academic_term_id=term_id)

    data = entries.values(
        "session__date",
        "session__session_type",
        "session__house__name",
        "student__full_name",
        "student__house__name",
        "student__dormitory__name",
        "status",
        "gate_conflict_at",
        "gate_conflict_note",
        "gate_conflict_reviewed_at",
        "gate_conflict_reviewed_by__username",
    ).order_by("-gate_conflict_at")
    headers = (
        "Session Date",
        "Session",
        "House",
        "Student",
        "Student House",
        "Dormitory",
        "Roll-call status",
        "Conflict logged",
        "Note",
        "Review",
    )
    rows = []
    pending_rows = []
    present_count = 0
    absent_count = 0
    for entry in data:
        reviewed_at = entry["gate_conflict_reviewed_at"]
        review_state = (
            f"Reviewed {timezone.localtime(reviewed_at):%Y-%m-%d %H:%M}"
            if reviewed_at
            else "Pending review"
        )
        status_label = _ROLL_CALL_ENTRY_STATUS_LABELS.get(
            entry["status"], entry["status"]
        )
        row = (
            entry["session__date"],
            _ROLL_CALL_TYPE_LABELS.get(
                entry["session__session_type"],
                entry["session__session_type"],
            ),
            entry["session__house__name"] or "Unassigned",
            entry["student__full_name"],
            entry["student__house__name"] or "Unassigned",
            entry["student__dormitory__name"] or "Unassigned",
            status_label,
            timezone.localtime(entry["gate_conflict_at"]),
            entry["gate_conflict_note"] or "—",
            review_state,
        )
        rows.append(row)
        if not reviewed_at:
            pending_rows.append(row)
        if entry["status"] == RollCallEntry.Status.PRESENT:
            present_count += 1
        if entry["status"] == RollCallEntry.Status.ABSENT:
            absent_count += 1

    return {
        "title": "Gate / Roll-call Conflict Report",
        "headers": headers,
        "rows": rows,
        "summary": (
            ("Conflicts", len(rows)),
            ("Pending review", len(pending_rows)),
            ("Reviewed", len(rows) - len(pending_rows)),
            ("Still marked Present", present_count),
            ("Marked Absent", absent_count),
        ),
        "sections": (
            {
                "title": "Conflicts still pending review",
                "headers": headers,
                "rows": pending_rows,
            },
        ),
        "highlights": (
            "A gate conflict means the roll-call mark disagreed with the gate log when the departure was reconciled.",
            "Pending-review rows have not yet been acknowledged by a housemaster.",
        ),
        "point_in_time": False,
    }


def _approval_turnaround_report(
    house_id=None, date_from=None, date_to=None, term_id=None
):
    """Summarize decision volume and immutable approval turnaround."""
    exeats = Exeat.objects.exclude(status=Exeat.Status.PENDING)
    if house_id is not None:
        exeats = exeats.filter(student__house_id=house_id)
    if date_from:
        exeats = exeats.filter(created_at__date__gte=date_from)
    if date_to:
        exeats = exeats.filter(created_at__date__lte=date_to)
    if term_id:
        exeats = exeats.filter(academic_term_id=term_id)

    data = exeats.values(
        "exeat_number",
        "student__full_name",
        "student__house__name",
        "exeat_type",
        "status",
        "created_at",
        "decided_at",
        "expected_departure",
        "approved_by__full_name",
        "approved_by_user__first_name",
        "approved_by_user__last_name",
        "approved_by_user__username",
    ).order_by("-created_at")
    rows = []
    turnarounds = []
    approver_totals = {}
    type_totals = {}
    approved = rejected = cancelled = 0
    for exeat in data:
        submitted = exeat["created_at"]
        decided = exeat["decided_at"]
        # Turnaround is a performance measure for positive/negative decisions.
        # A cancelled request is not an approval decision and must not distort it.
        turnaround = None
        if (
            exeat["status"] in (Exeat.Status.APPROVED, Exeat.Status.REJECTED)
            and submitted
            and decided
        ):
            turnaround = round((decided - submitted).total_seconds() / 3600, 1)
        lead_time = (
            round(
                (exeat["expected_departure"] - submitted).total_seconds() / 3600,
                1,
            )
            if submitted and exeat["expected_departure"]
            else None
        )
        if turnaround is not None:
            turnarounds.append(turnaround)

        if exeat["status"] == Exeat.Status.APPROVED:
            approved += 1
        elif exeat["status"] == Exeat.Status.REJECTED:
            rejected += 1
        elif exeat["status"] == Exeat.Status.CANCELLED:
            cancelled += 1

        type_label = _EXEAT_TYPE_LABELS.get(exeat["exeat_type"], exeat["exeat_type"])
        approver_name = (
            exeat["approved_by__full_name"]
            or " ".join(
                part
                for part in (
                    exeat["approved_by_user__first_name"],
                    exeat["approved_by_user__last_name"],
                )
                if part
            ).strip()
            or exeat["approved_by_user__username"]
            or "Unassigned"
        )
        rows.append(
            (
                exeat["exeat_number"],
                exeat["student__full_name"],
                exeat["student__house__name"] or "Unassigned",
                type_label,
                timezone.localtime(submitted) if submitted else "—",
                _EXEAT_STATUS_LABELS.get(exeat["status"], exeat["status"]),
                timezone.localtime(decided) if decided else "—",
                f"{turnaround}h" if turnaround is not None else "—",
                (
                    timezone.localtime(exeat["expected_departure"])
                    if exeat["expected_departure"]
                    else "—"
                ),
                f"{lead_time}h" if lead_time is not None else "—",
                approver_name,
            )
        )

        if exeat["status"] != Exeat.Status.CANCELLED:
            totals = approver_totals.setdefault(
                approver_name, {"decisions": 0, "hours": 0.0, "measured": 0}
            )
            totals["decisions"] += 1
            if turnaround is not None:
                totals["hours"] += turnaround
                totals["measured"] += 1
        type_totals[type_label] = type_totals.get(type_label, 0) + 1

    approver_rows = [
        (
            name,
            totals["decisions"],
            (
                f"{round(totals['hours'] / totals['measured'], 1)}h"
                if totals["measured"]
                else "—"
            ),
        )
        for name, totals in sorted(
            approver_totals.items(),
            key=lambda item: (-item[1]["decisions"], item[0]),
        )
    ]
    type_rows = [
        (name, count)
        for name, count in sorted(
            type_totals.items(), key=lambda item: (-item[1], item[0])
        )
    ]
    average = round(sum(turnarounds) / len(turnarounds), 1) if turnarounds else None

    return {
        "title": "Exeat Approval Turnaround & Rejection",
        "headers": (
            "Pass",
            "Student",
            "House",
            "Type",
            "Submitted",
            "Decision",
            "Decided",
            "Turnaround",
            "Expected Departure",
            "Lead Time",
            "Approver",
        ),
        "rows": rows,
        "summary": (
            ("Decided passes", approved + rejected),
            ("Approved", approved),
            ("Rejected", rejected),
            ("Cancelled (excluded from turnaround)", cancelled),
            ("Avg turnaround", f"{average}h" if average is not None else "—"),
            ("Fastest", f"{min(turnarounds)}h" if turnarounds else "—"),
            ("Slowest", f"{max(turnarounds)}h" if turnarounds else "—"),
        ),
        "sections": (
            {
                "title": "Turnaround by approver",
                "headers": ("Approver", "Decisions", "Avg turnaround"),
                "rows": approver_rows,
            },
            {
                "title": "Decisions by leave type",
                "headers": ("Exeat type", "Decisions"),
                "rows": type_rows,
            },
        ),
        "highlights": (
            "Turnaround uses the immutable decision timestamp recorded when the request was approved or rejected.",
            "Cancelled requests are reported separately and excluded from turnaround and approver averages.",
            "Lead time is scheduled departure minus request creation (how much notice was given).",
        ),
        "point_in_time": False,
    }


def _exeat_analytics_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Analyze exeat types, free-text destinations, and scheduled durations."""
    exeats = Exeat.objects.all()
    if house_id is not None:
        exeats = exeats.filter(student__house_id=house_id)
    if date_from:
        exeats = exeats.filter(expected_departure__date__gte=date_from)
    if date_to:
        exeats = exeats.filter(expected_departure__date__lte=date_to)
    if term_id:
        exeats = exeats.filter(academic_term_id=term_id)

    records = exeats.values(
        "exeat_type", "destination", "expected_departure", "expected_return"
    )
    total = 0
    type_totals = {}
    destination_totals = {}
    duration_buckets = {
        "Under 6 hours": 0,
        "6–24 hours": 0,
        "1–3 days": 0,
        "3–7 days": 0,
        "Over 7 days": 0,
    }
    durations = []
    for record in records:
        total += 1
        departure = record["expected_departure"]
        expected_return = record["expected_return"]
        hours = (
            (expected_return - departure).total_seconds() / 3600
            if departure and expected_return
            else None
        )
        if hours is not None and hours >= 0:
            durations.append(hours)

        type_label = _EXEAT_TYPE_LABELS.get(record["exeat_type"], record["exeat_type"])
        totals = type_totals.setdefault(
            type_label, {"count": 0, "hours": 0.0, "measured": 0}
        )
        totals["count"] += 1
        if hours is not None and hours >= 0:
            totals["hours"] += hours
            totals["measured"] += 1

        destination = (record["destination"] or "").strip() or "Not stated"
        destination_totals[destination] = destination_totals.get(destination, 0) + 1

        if hours is not None and hours >= 0:
            if hours < 6:
                duration_buckets["Under 6 hours"] += 1
            elif hours < 24:
                duration_buckets["6–24 hours"] += 1
            elif hours < 72:
                duration_buckets["1–3 days"] += 1
            elif hours < 168:
                duration_buckets["3–7 days"] += 1
            else:
                duration_buckets["Over 7 days"] += 1

    type_rows = [
        (
            label,
            totals["count"],
            f"{round(totals['count'] * 100 / total, 1) if total else 0}%",
            (
                f"{round(totals['hours'] / totals['measured'], 1)}h"
                if totals["measured"]
                else "—"
            ),
        )
        for label, totals in sorted(
            type_totals.items(), key=lambda item: (-item[1]["count"], item[0])
        )
    ]
    destination_rows = [
        (destination, count)
        for destination, count in sorted(
            destination_totals.items(), key=lambda item: (-item[1], item[0])
        )[:15]
    ]
    average = round(sum(durations) / len(durations), 1) if durations else None

    return {
        "title": "Exeat Type, Destination & Duration Analytics",
        "headers": ("Exeat type", "Passes", "Share", "Avg duration"),
        "rows": type_rows,
        "summary": (
            ("Passes in period", total),
            ("Avg duration", f"{average}h" if average is not None else "—"),
            ("Longest", f"{round(max(durations), 1)}h" if durations else "—"),
            ("Shortest", f"{round(min(durations), 1)}h" if durations else "—"),
            ("Distinct destinations", len(destination_totals)),
        ),
        "sections": (
            {
                "title": "Top destinations",
                "headers": ("Destination", "Passes"),
                "rows": destination_rows,
            },
            {
                "title": "Duration distribution",
                "headers": ("Duration band", "Passes"),
                "rows": list(duration_buckets.items()),
            },
        ),
        "highlights": (
            "Destinations are free text, so similar entries may appear separately.",
            "Duration uses scheduled departure and return; actual gate times are covered by the exeat compliance report.",
        ),
        "point_in_time": False,
    }


def _room_register_report(house_id=None):
    """Summarize room capacity, boarding occupancy, and vacancies."""
    rooms = DormitoryRoom.objects.select_related(
        "dormitory", "dormitory__house"
    ).annotate(
        occupied=Count(
            "students",
            filter=Q(
                students__residency=Student.ResidencyStatus.BOARDING,
                students__status__in=(
                    Student.EnrollmentStatus.ENROLLED,
                    Student.EnrollmentStatus.APPROVED,
                    Student.EnrollmentStatus.IN_PROGRESS,
                ),
            ),
        )
    )
    if house_id is not None:
        rooms = rooms.filter(dormitory__house_id=house_id)
    rooms = rooms.order_by(
        "dormitory__house__display_order",
        "dormitory__house__name",
        "dormitory__name",
        "name",
    )

    headers = (
        "House",
        "Dormitory",
        "Room",
        "Gender",
        "Capacity",
        "Occupied",
        "Vacant",
        "Occupancy",
        "Status",
    )
    rows = []
    over_capacity_rows = []
    empty_rows = []
    total_capacity = 0
    total_occupied = 0
    for room in rooms:
        capacity = room.capacity
        occupied = room.occupied
        vacant = capacity - occupied
        occupancy = round(occupied * 100 / capacity, 1) if capacity else 0
        if occupied > capacity:
            status = f"Over capacity by {occupied - capacity}"
        elif vacant == 0:
            status = "At capacity"
        elif occupied == 0:
            status = "Empty"
        else:
            status = f"{vacant} spaces available"
        row = (
            room.dormitory.house.name,
            room.dormitory.name,
            room.name,
            _DORMITORY_GENDER_LABELS.get(room.dormitory.gender, "—"),
            capacity,
            occupied,
            max(0, vacant),
            f"{occupancy}%",
            status,
        )
        rows.append(row)
        total_capacity += capacity
        total_occupied += occupied
        if occupied > capacity:
            over_capacity_rows.append(row)
        if occupied == 0:
            empty_rows.append(row)

    total_occupancy = (
        round(total_occupied * 100 / total_capacity, 1) if total_capacity else 0
    )
    return {
        "title": "Room Bed-Allocation & Vacancy Register",
        "headers": headers,
        "rows": rows,
        "summary": (
            ("Rooms", len(rows)),
            ("Total room capacity", total_capacity),
            ("Boarders in rooms", total_occupied),
            ("Vacant beds", max(0, total_capacity - total_occupied)),
            ("Room occupancy", f"{total_occupancy}%"),
            ("Over-capacity rooms", len(over_capacity_rows)),
            ("Empty rooms", len(empty_rows)),
        ),
        "sections": (
            {
                "title": "Over-capacity rooms",
                "headers": headers,
                "rows": over_capacity_rows,
            },
            {
                "title": "Empty rooms",
                "headers": headers,
                "rows": empty_rows,
            },
        ),
        "highlights": (
            "Vacancy is room capacity minus boarders currently assigned to that room.",
            "Empty rooms are ready for allocation and are worth surfacing during intake.",
        ),
        "point_in_time": True,
    }


def get_boarding_demographics(house_id=None):
    """Summarize boarding population by programme, house, gender, and level."""
    boarders = Student.objects.exclude(
        status=Student.EnrollmentStatus.COMPLETED
    ).filter(residency=Student.ResidencyStatus.BOARDING)
    if house_id is not None:
        boarders = boarders.filter(house_id=house_id)

    total = boarders.count()
    gender_rows = [
        ("Male", boarders.filter(gender=Student.Gender.MALE).count()),
        ("Female", boarders.filter(gender=Student.Gender.FEMALE).count()),
    ]
    programme_rows = [
        (
            row["programme__name"] or "Unassigned",
            row["total"],
            row["male"],
            row["female"],
        )
        for row in boarders.values("programme__name")
        .annotate(
            total=Count("id"),
            male=Count("id", filter=Q(gender=Student.Gender.MALE)),
            female=Count("id", filter=Q(gender=Student.Gender.FEMALE)),
        )
        .order_by("-total", "programme__name")
    ]
    house_rows = [
        (
            row["house__name"] or "Unassigned",
            row["total"],
            row["male"],
            row["female"],
        )
        for row in boarders.values("house__name")
        .annotate(
            total=Count("id"),
            male=Count("id", filter=Q(gender=Student.Gender.MALE)),
            female=Count("id", filter=Q(gender=Student.Gender.FEMALE)),
        )
        .order_by("house__name")
    ]
    level_totals = {}
    for student in boarders.select_related("class_group"):
        level = student.level
        label = getattr(level, "label", str(level))
        level_totals[label] = level_totals.get(label, 0) + 1
    level_rows = sorted(level_totals.items())

    return {
        "title": "Boarders Demographics & Programme Distribution",
        "layout": "distribution",
        "point_in_time": True,
        "distribution": (
            {"title": "By gender", "rows": gender_rows},
            {
                "title": "By programme",
                "rows": [(row[0], row[1]) for row in programme_rows],
            },
            {"title": "By form level", "rows": level_rows},
        ),
        "summary": (
            ("Boarders", total),
            ("Houses", len(house_rows)),
            ("Programmes", len(programme_rows)),
            ("Male", gender_rows[0][1]),
            ("Female", gender_rows[1][1]),
        ),
        "sections": (
            {
                "title": "Programme by house composition",
                "headers": ("Programme", "Boarders", "Male", "Female"),
                "rows": programme_rows,
            },
            {
                "title": "House by gender composition",
                "headers": ("House", "Boarders", "Male", "Female"),
                "rows": house_rows,
            },
        ),
        "record_count": total,
    }


def get_sms_usage_summary(house_id=None, date_from=None, date_to=None, term_id=None):
    """Summarize delivery health and tenant-level monthly SMS quota usage."""
    from tenancy.entitlements import quota_limit, sms_monthly_usage

    deliveries = SMSDelivery.objects.all()
    if date_from:
        deliveries = deliveries.filter(created_at__date__gte=date_from)
    if date_to:
        deliveries = deliveries.filter(created_at__date__lte=date_to)
    if term_id:
        deliveries = deliveries.filter(exeat__academic_term_id=term_id)
    if house_id is not None:
        deliveries = deliveries.filter(exeat__student__house_id=house_id)

    total = deliveries.count()
    by_status = {
        row["status"]: row["count"]
        for row in deliveries.values("status").annotate(count=Count("id"))
    }
    sent = by_status.get(SMSDelivery.Status.SENT, 0)
    queued = by_status.get(SMSDelivery.Status.QUEUED, 0)
    processing = by_status.get(SMSDelivery.Status.PROCESSING, 0)
    failed = by_status.get(SMSDelivery.Status.FAILED, 0)
    attempted = sent + failed
    success_rate = round(sent * 100 / attempted, 1) if attempted else 0

    event_rows = [
        (
            dict(SMSDelivery.EventType.choices).get(
                row["event_type"], row["event_type"]
            ),
            row["count"],
        )
        for row in deliveries.values("event_type")
        .annotate(count=Count("id"))
        .order_by("-count", "event_type")
    ]
    failure_rows = [
        (row["last_error"] or "No error text", row["count"])
        for row in deliveries.filter(status=SMSDelivery.Status.FAILED)
        .values("last_error")
        .annotate(count=Count("id"))
        .order_by("-count", "last_error")[:10]
    ]
    used = sms_monthly_usage()
    limit = quota_limit("max_sms_monthly")
    percent = round(used * 100 / limit, 1) if limit else None

    return {
        "title": "SMS Delivery & Usage",
        "layout": "sms_usage",
        "point_in_time": False,
        "gauge": {"used": used, "limit": limit, "percent": percent},
        "kpi_tiles": (
            ("Sent", sent, "emerald"),
            ("Queued", queued, "amber"),
            ("Processing", processing, "sky"),
            ("Failed", failed, "rose"),
            ("Success rate", f"{success_rate}%", "violet"),
            ("Deliveries in period", total, "indigo"),
        ),
        "summary": (
            ("Sent", sent),
            ("Queued", queued),
            ("Processing", processing),
            ("Failed", failed),
            ("Success rate", f"{success_rate}%"),
            ("This month", f"{used}" + (f" / {limit}" if limit else "")),
        ),
        "sections": (
            {
                "title": "Deliveries by event",
                "headers": ("Event", "Messages"),
                "rows": event_rows,
            },
            {
                "title": "Failure reasons",
                "headers": ("Error", "Messages"),
                "rows": failure_rows,
            },
        ),
        "highlights": (
            "This-month usage counts sent messages plus outstanding queued/processing items, matching the platform quota calculation.",
            "The monthly quota is tenant-wide; the other delivery figures follow the selected filters.",
            "A null monthly limit means the plan has no SMS cap configured.",
        ),
        "record_count": total,
    }


def _staff_activity_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Summarize attributed staff actions and records without a named officer."""
    house_masters = HouseMaster.objects.select_related("house").order_by("full_name")
    if house_id is not None:
        house_masters = house_masters.filter(house_id=house_id)

    roll_calls = RollCallSession.objects.all()
    approvals = Exeat.objects.exclude(status=Exeat.Status.PENDING).filter(
        decided_at__isnull=False
    )
    incidents = DisciplinaryRecord.objects.all()
    resolutions = RollCallAbsenceFollowUp.objects.filter(
        resolved_at__isnull=False,
        resolved_by__isnull=False,
    )
    gate_movements = ExeatMovement.objects.filter(
        Q(departure_recorded_by_user__isnull=False)
        | Q(return_recorded_by_user__isnull=False)
    )
    if house_id is not None:
        roll_calls = roll_calls.filter(house_id=house_id)
        approvals = approvals.filter(student__house_id=house_id)
        incidents = incidents.filter(student__house_id=house_id)
        resolutions = resolutions.filter(entry__session__house_id=house_id)
        gate_movements = gate_movements.filter(exeat__student__house_id=house_id)
    if date_from:
        roll_calls = roll_calls.filter(date__gte=date_from)
        approvals = approvals.filter(decided_at__date__gte=date_from)
        incidents = incidents.filter(created_at__date__gte=date_from)
        resolutions = resolutions.filter(resolved_at__date__gte=date_from)
    if date_to:
        roll_calls = roll_calls.filter(date__lte=date_to)
        approvals = approvals.filter(decided_at__date__lte=date_to)
        incidents = incidents.filter(created_at__date__lte=date_to)
        resolutions = resolutions.filter(resolved_at__date__lte=date_to)
    if term_id:
        roll_calls = roll_calls.filter(academic_term_id=term_id)
        approvals = approvals.filter(academic_term_id=term_id)
        incidents = incidents.filter(academic_term_id=term_id)
        resolutions = resolutions.filter(entry__session__academic_term_id=term_id)
        gate_movements = gate_movements.filter(exeat__academic_term_id=term_id)

    departures = gate_movements.filter(departure_recorded_by_user__isnull=False)
    returns = gate_movements.filter(return_recorded_by_user__isnull=False)
    if date_from:
        departures = departures.filter(actual_departure__date__gte=date_from)
        returns = returns.filter(actual_return__date__gte=date_from)
    if date_to:
        departures = departures.filter(actual_departure__date__lte=date_to)
        returns = returns.filter(actual_return__date__lte=date_to)
    gate_counts = {}
    for field, actions in (
        ("departure_recorded_by_user_id", departures),
        ("return_recorded_by_user_id", returns),
    ):
        for user_id, count in (
            actions.exclude(**{f"{field}__isnull": True})
            .values(field)
            .annotate(count=Count("id"))
            .values_list(field, "count")
        ):
            gate_counts[user_id] = gate_counts.get(user_id, 0) + count

    roll_call_counts = dict(
        roll_calls.values("conducted_by_id")
        .annotate(count=Count("id"))
        .values_list("conducted_by_id", "count")
    )
    incident_counts = dict(
        incidents.values("logged_by_id")
        .annotate(count=Count("id"))
        .values_list("logged_by_id", "count")
    )
    resolution_counts = dict(
        resolutions.values("resolved_by_id")
        .annotate(count=Count("id"))
        .values_list("resolved_by_id", "count")
    )

    rows = []
    matched_approval_ids = set()
    for housemaster in house_masters:
        roll_call_count = roll_call_counts.get(housemaster.pk, 0)
        # Prefer the authenticated actor. Legacy rows without an actor fall
        # back to the HouseMaster attribution, so a normal approval recorded in
        # both places is counted once (F-08/F-21).
        if housemaster.user_id:
            actor_filter = Q(approved_by_user_id=housemaster.user_id) | Q(
                approved_by_user__isnull=True,
                approved_by_id=housemaster.pk,
            )
        else:
            actor_filter = Q(
                approved_by_user__isnull=True,
                approved_by_id=housemaster.pk,
            )
        approval_ids = set(
            approvals.filter(actor_filter).values_list("pk", flat=True)
        )
        matched_approval_ids.update(approval_ids)
        approval_count = len(approval_ids)
        incident_count = incident_counts.get(housemaster.pk, 0)
        resolution_count = (
            resolution_counts.get(housemaster.user_id, 0) if housemaster.user_id else 0
        )
        gate_count = (
            gate_counts.get(housemaster.user_id, 0) if housemaster.user_id else 0
        )
        rows.append(
            (
                housemaster.full_name,
                housemaster.house.name if housemaster.house else "School-wide",
                roll_call_count,
                approval_count,
                incident_count,
                resolution_count,
                gate_count,
                roll_call_count
                + approval_count
                + incident_count
                + resolution_count
                + gate_count,
            )
        )

    linked_user_ids = {
        housemaster.user_id
        for housemaster in house_masters
        if housemaster.user_id is not None
    }
    unlogged_roll_calls = roll_calls.filter(conducted_by__isnull=True)
    unlogged_incidents = incidents.filter(logged_by__isnull=True)
    other_approval_rows = list(
        approvals.exclude(pk__in=matched_approval_ids)
        .filter(approved_by_user__isnull=False)
        .values("approved_by_user_id")
        .annotate(count=Count("id"))
    )
    approval_counts_by_user = {
        row["approved_by_user_id"]: row["count"] for row in other_approval_rows
    }
    extra_user_ids = set(approval_counts_by_user) | (set(gate_counts) - linked_user_ids)
    extra_user_ids.discard(None)
    users = get_user_model().objects.in_bulk(extra_user_ids)
    for user_id in extra_user_ids:
        user = users.get(user_id)
        if user is None:
            continue
        approval_count = approval_counts_by_user.get(user_id, 0)
        gate_count = gate_counts.get(user_id, 0)
        rows.append(
            (
                user.get_full_name().strip() or user.get_username(),
                "School-wide",
                0,
                approval_count,
                0,
                0,
                gate_count,
                approval_count + gate_count,
            )
        )
    rows.sort(key=lambda row: (-row[7], row[0].casefold()))
    total_actions = sum(row[7] for row in rows)
    return {
        "title": "Staff Activity & Workload",
        "headers": (
            "Staff member",
            "Scope",
            "Roll calls",
            "Passes approved",
            "Incidents logged",
            "Absences resolved",
            "Gate movements",
            "Total actions",
        ),
        "rows": rows,
        "summary": (
            ("Staff listed", len(rows)),
            ("Total actions", total_actions),
            ("Most active", rows[0][0] if rows else "—"),
            ("Roll calls conducted", sum(row[2] for row in rows)),
            ("Incidents logged", sum(row[4] for row in rows)),
        ),
        "sections": (
            {
                "title": "Unattributed records (no officer named)",
                "headers": ("Record type", "Count"),
                "rows": (
                    ("Roll calls without a conductor", unlogged_roll_calls.count()),
                    ("Incidents without a logger", unlogged_incidents.count()),
                ),
            },
        ),
        "highlights": (
            "Approvals use the immutable decision timestamp and the authenticated authorizer account where one is recorded.",
            "Administrators, medical leads, and gate officers appear as staff rows when they act in the selected period.",
            "Absence resolutions are matched to a housemaster through their linked login account; gate movements count departures and returns separately.",
        ),
        "point_in_time": False,
    }


def _welfare_infirmary_report(
    house_id=None, date_from=None, date_to=None, term_id=None
):
    """Summarize students with infirmary roll-call marks or medical exeats."""
    infirmary = RollCallEntry.objects.filter(status=RollCallEntry.Status.INFIRMARY)
    medical = Exeat.objects.filter(exeat_type=Exeat.ExeatType.MEDICAL)
    if house_id is not None:
        infirmary = infirmary.filter(
            session__house_id=house_id,
            student__house_id=house_id,
        )
        medical = medical.filter(student__house_id=house_id)
    if date_from:
        infirmary = infirmary.filter(session__date__gte=date_from)
        medical = medical.filter(expected_departure__date__gte=date_from)
    if date_to:
        infirmary = infirmary.filter(session__date__lte=date_to)
        medical = medical.filter(expected_departure__date__lte=date_to)
    if term_id:
        infirmary = infirmary.filter(session__academic_term_id=term_id)
        medical = medical.filter(academic_term_id=term_id)

    infirmary_rows = infirmary.values("student_id").annotate(
        count=Count("id"),
        last=Max("session__date"),
    )
    medical_rows = medical.values("student_id").annotate(count=Count("id"))
    infirmary_by_student = {
        row["student_id"]: (row["count"], row["last"]) for row in infirmary_rows
    }
    medical_by_student = {row["student_id"]: row["count"] for row in medical_rows}
    student_ids = set(infirmary_by_student) | set(medical_by_student)
    students = Student.objects.filter(pk__in=student_ids).exclude(
        status=Student.EnrollmentStatus.COMPLETED
    ).values(
        "id",
        "full_name",
        "house__name",
        "dormitory__name",
    )
    student_info = {student["id"]: student for student in students}
    # Do not let a completed/left student remain on a current welfare
    # watchlist merely because an old term record still exists (F-24).
    student_ids = set(student_info)

    rows = []
    for student_id in student_ids:
        student = student_info[student_id]
        infirmary_count, last_infirmary = infirmary_by_student.get(
            student_id, (0, None)
        )
        medical_count = medical_by_student.get(student_id, 0)
        rows.append(
            (
                student["full_name"],
                student["house__name"] or "Unassigned",
                student["dormitory__name"] or "Unassigned",
                infirmary_count,
                medical_count,
                infirmary_count + medical_count,
                last_infirmary or "—",
            )
        )
    rows.sort(key=lambda row: (-row[5], row[0].casefold()))
    watchlist = [row for row in rows if row[3] >= 2 or row[4] >= 2]
    headers = (
        "Student",
        "House",
        "Dormitory",
        "Infirmary marks",
        "Medical exeats",
        "Total referrals",
        "Last infirmary",
    )
    return {
        "title": "Welfare & Infirmary Referrals",
        "headers": headers,
        "rows": rows,
        "summary": (
            ("Students referred", len(rows)),
            ("Infirmary marks", sum(row[3] for row in rows)),
            ("Medical exeats", sum(row[4] for row in rows)),
            ("Students on watchlist (2+)", len(watchlist)),
        ),
        "sections": (
            {
                "title": "Repeat welfare watchlist",
                "headers": headers,
                "rows": watchlist,
            },
        ),
        "highlights": (
            "Combines roll-call 'Infirmary' marks with 'Medical' exeats for the same student.",
            "A watchlist student has at least two infirmary marks or at least two medical exeats in the selected period.",
        ),
        "point_in_time": False,
    }


def _data_completeness_report(house_id=None):
    """List student register records with missing guardian, identity, or placement data."""
    students = (
        Student.objects.exclude(status=Student.EnrollmentStatus.COMPLETED)
        .select_related("house", "dormitory")
        .order_by("full_name")
    )
    if house_id is not None:
        students = students.filter(house_id=house_id)

    counters = {"guardian": 0, "dob": 0, "photo": 0, "dorm": 0, "room": 0}
    rows = []
    complete = 0
    total = 0
    for student in students:
        total += 1
        gaps = []
        if not student.guardian_id:
            gaps.append("No guardian")
            counters["guardian"] += 1
        if not student.date_of_birth:
            gaps.append("No date of birth")
            counters["dob"] += 1
        if not student.photo:
            gaps.append("No photo")
            counters["photo"] += 1
        if student.residency == Student.ResidencyStatus.BOARDING:
            if not student.dormitory_id:
                gaps.append("No dormitory")
                counters["dorm"] += 1
            if not student.room_id:
                gaps.append("No room")
                counters["room"] += 1
        if gaps:
            rows.append(
                (
                    student.full_name,
                    student.admission_no,
                    student.house.name if student.house else "Unassigned",
                    student.get_residency_display(),
                    len(gaps),
                    ", ".join(gaps),
                )
            )
        else:
            complete += 1

    rows.sort(key=lambda row: (-row[4], row[0].casefold()))
    completion_rate = round(complete * 100 / total, 1) if total else 0
    return {
        "title": "Data Completeness / Register Hygiene",
        "headers": (
            "Student",
            "Admission No",
            "House",
            "Residency",
            "Missing fields",
            "Missing",
        ),
        "rows": rows,
        "summary": (
            ("Students", total),
            ("Fully complete", complete),
            ("With gaps", total - complete),
            ("Completion", f"{completion_rate}%"),
            ("No guardian", counters["guardian"]),
            ("No photo", counters["photo"]),
            ("No dormitory", counters["dorm"]),
        ),
        "sections": (
            {
                "title": "Gap checklist",
                "headers": ("Field", "Students missing"),
                "rows": (
                    ("No guardian contact", counters["guardian"]),
                    ("No date of birth", counters["dob"]),
                    ("No photo", counters["photo"]),
                    ("Boarders without dormitory", counters["dorm"]),
                    ("Boarders without room", counters["room"]),
                ),
            },
        ),
        "highlights": (
            "Day students are not expected to have a dormitory or room, so those fields are not counted against them.",
            "Missing guardian contacts block SMS notifications; missing photos block ID card printing.",
        ),
        "point_in_time": True,
    }


def _movement_concern_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Report public QR-scan concerns raised about student movements."""
    concerns = MovementConcernReport.objects.select_related(
        "student",
        "student__house",
        "exeat",
    )
    if house_id is not None:
        concerns = concerns.filter(student__house_id=house_id)
    if date_from:
        concerns = concerns.filter(created_at__date__gte=date_from)
    if date_to:
        concerns = concerns.filter(created_at__date__lte=date_to)
    if term_id:
        concerns = concerns.filter(exeat__academic_term_id=term_id)

    headers = ("Raised", "Student", "House", "Pass", "Status", "Details")
    rows = []
    open_rows = []
    for concern in concerns.order_by("-created_at", "-pk"):
        details = (concern.details or "").strip()
        if len(details) > 90:
            details = details[:87] + "..."
        row = (
            timezone.localtime(concern.created_at),
            concern.student.full_name,
            concern.student.house.name if concern.student.house else "Unassigned",
            concern.exeat.exeat_number if concern.exeat else "—",
            concern.get_status_display(),
            details or "—",
        )
        rows.append(row)
        if concern.status == MovementConcernReport.Status.OPEN:
            open_rows.append(row)

    return {
        "title": "Movement Concern Reports",
        "headers": headers,
        "rows": rows,
        "summary": (
            ("Concerns raised", len(rows)),
            ("Open", len(open_rows)),
            ("Reviewed", len(rows) - len(open_rows)),
        ),
        "sections": (
            {"title": "Open concerns", "headers": headers, "rows": open_rows},
        ),
        "highlights": (
            "Concerns are raised by scanning a student ID QR code; details are optional and free text.",
        ),
        "point_in_time": False,
    }


def _conduct_normalized_report(
    house_id=None, date_from=None, date_to=None, term_id=None
):
    """Compare active disciplinary records per 100 currently assigned boarders."""
    houses = House.objects.all().order_by("display_order", "name")
    if house_id is not None:
        houses = houses.filter(pk=house_id)
    boarder_counts = dict(
        houses.annotate(
            count=Count(
                "students",
                filter=Q(
                    students__residency=Student.ResidencyStatus.BOARDING,
                    students__status__in=(
                        Student.EnrollmentStatus.ENROLLED,
                        Student.EnrollmentStatus.APPROVED,
                        Student.EnrollmentStatus.IN_PROGRESS,
                    ),
                ),
                distinct=True,
            )
        ).values_list("pk", "count")
    )
    active_records = DisciplinaryRecord.objects.filter(
        corrected_at__isnull=True,
        student__status__in=(
            Student.EnrollmentStatus.ENROLLED,
            Student.EnrollmentStatus.APPROVED,
            Student.EnrollmentStatus.IN_PROGRESS,
        ),
    )
    if house_id is not None:
        active_records = active_records.filter(student__house_id=house_id)
    if date_from:
        active_records = active_records.filter(created_at__date__gte=date_from)
    if date_to:
        active_records = active_records.filter(created_at__date__lte=date_to)
    if term_id:
        active_records = active_records.filter(academic_term_id=term_id)
    incident_counts = {
        row["student__house_id"]: row
        for row in active_records.values("student__house_id").annotate(
            count=Count("id"),
            major=Count(
                "id",
                filter=Q(severity=DisciplinaryRecord.Severity.MAJOR),
            ),
            critical=Count(
                "id",
                filter=Q(severity=DisciplinaryRecord.Severity.CRITICAL),
            ),
        )
    }
    rows = []
    for house in houses:
        incident = incident_counts.get(house.pk, {})
        boarder_count = boarder_counts[house.pk]
        incident_count = incident.get("count", 0)
        rate = round(incident_count * 100 / boarder_count, 1) if boarder_count else 0
        rows.append(
            (
                house.name,
                boarder_count,
                incident_count,
                rate,
                incident.get("major", 0),
                incident.get("critical", 0),
            )
        )
    rows.sort(key=lambda row: (-row[3], row[0].casefold()))
    total_incidents = sum(row[2] for row in rows)
    total_boarders = sum(row[1] for row in rows)
    overall_rate = (
        round(total_incidents * 100 / total_boarders, 1) if total_boarders else 0
    )
    return {
        "title": "Conduct Normalized by House Size",
        "headers": (
            "House",
            "Boarders",
            "Active incidents",
            "Per 100 boarders",
            "Major",
            "Critical",
        ),
        "rows": rows,
        "summary": (
            ("Active incidents", total_incidents),
            ("Boarders", total_boarders),
            ("Per 100 boarders", overall_rate),
            ("Highest rate", f"{rows[0][0]} ({rows[0][3]})" if rows else "—"),
            ("Lowest rate", f"{rows[-1][0]} ({rows[-1][3]})" if rows else "—"),
        ),
        "highlights": (
            "The 'per 100 boarders' rate compares houses using the current boarding population.",
            "Only active (uncorrected) records created within the selected period and term for current students are counted.",
            "Houses with zero current boarders show a zero rate rather than an undefined ratio.",
        ),
        "point_in_time": False,
    }


def _intervention_effectiveness_report(
    house_id=None, date_from=None, date_to=None, term_id=None
):
    """Summarize corrections, time to correction, and recurrence by student."""
    records = DisciplinaryRecord.objects.all()
    if house_id is not None:
        records = records.filter(student__house_id=house_id)
    if date_from:
        records = records.filter(created_at__date__gte=date_from)
    if date_to:
        records = records.filter(created_at__date__lte=date_to)
    if term_id:
        records = records.filter(academic_term_id=term_id)

    student_records = {}
    for record in records.values(
        "student_id",
        "student__full_name",
        "student__house__name",
        "created_at",
        "corrected_at",
    ).order_by("student_id", "created_at"):
        entry = student_records.setdefault(
            record["student_id"],
            {
                "name": record["student__full_name"],
                "house": record["student__house__name"] or "Unassigned",
                "total": 0,
                "corrected": 0,
                "active": 0,
                "durations": [],
                "corrected_times": [],
                "active_times": [],
            },
        )
        entry["total"] += 1
        corrected_at = record["corrected_at"]
        if corrected_at:
            entry["corrected"] += 1
            duration = (corrected_at - record["created_at"]).total_seconds()
            if duration >= 0:
                entry["durations"].append(duration / 3600)
            entry["corrected_times"].append(corrected_at)
        else:
            entry["active"] += 1
            entry["active_times"].append(record["created_at"])

    rows = []
    recurrence_rows = []
    recurrence_count = 0
    for entry in student_records.values():
        durations = entry["durations"]
        average_days = (
            round(sum(durations) / len(durations) / 24, 1) if durations else None
        )
        recurrent = bool(
            entry["corrected_times"]
            and entry["active_times"]
            and max(entry["active_times"]) > min(entry["corrected_times"])
        )
        recurrence_count += int(recurrent)
        row = (
            entry["name"],
            entry["house"],
            entry["total"],
            entry["corrected"],
            entry["active"],
            f"{average_days}d" if average_days is not None else "—",
            "Yes" if recurrent else "No",
        )
        rows.append(row)
        if recurrent:
            recurrence_rows.append(row)
    rows.sort(key=lambda row: (-row[2], row[0].casefold()))
    recurrence_rows.sort(key=lambda row: (-row[2], row[0].casefold()))
    corrected_total = sum(row[3] for row in rows)
    active_total = sum(row[4] for row in rows)
    total_records = corrected_total + active_total
    correction_rate = (
        round(corrected_total * 100 / total_records, 1) if total_records else 0
    )
    headers = (
        "Student",
        "House",
        "Records",
        "Corrected",
        "Active",
        "Avg time to correct",
        "Recurrence after correction",
    )
    return {
        "title": "Intervention / Correction Effectiveness",
        "headers": headers,
        "rows": rows,
        "summary": (
            ("Students", len(rows)),
            ("Records", total_records),
            ("Corrected", corrected_total),
            ("Active", active_total),
            ("Correction rate", f"{correction_rate}%"),
            ("Recurring students", recurrence_count),
        ),
        "sections": (
            {
                "title": "Students who re-offended after a correction",
                "headers": headers,
                "rows": recurrence_rows,
            },
        ),
        "highlights": (
            "The system records actions taken but not intervention outcomes, so effectiveness is inferred from correction and recurrence, not measured results.",
            "Recurrence means an active record was created after an earlier record for the same student was corrected.",
        ),
        "point_in_time": False,
    }


def get_configuration_audit(date_from=None, date_to=None):
    """Return a date-filtered audit of privileged school configuration changes."""
    events = ConfigurationAuditLog.objects.select_related("actor")
    if date_from:
        events = events.filter(created_at__date__gte=date_from)
    if date_to:
        events = events.filter(created_at__date__lte=date_to)
    events = events.order_by("-created_at", "-pk")

    labels = dict(ConfigurationAuditLog.Event.choices)
    rows = []
    actor_names = set()
    for event in events:
        actor = (
            (event.actor.get_full_name() or "").strip() or event.actor.username
            if event.actor
            else "System"
        )
        actor_names.add(actor)
        rows.append(
            (
                timezone.localtime(event.created_at),
                actor,
                labels.get(event.event, event.event),
                event.description,
            )
        )

    type_rows = [
        (
            labels.get(row["event"], row["event"]),
            row["count"],
        )
        for row in events.values("event")
        .annotate(count=Count("id"))
        .order_by("-count", "event")
    ]
    return {
        "title": "Configuration Audit Trail",
        "headers": ("When", "Actor", "Event", "Description"),
        "rows": rows,
        "summary": (
            ("Events", len(rows)),
            ("Distinct actors", len(actor_names)),
            ("First", rows[-1][0] if rows else "—"),
            ("Latest", rows[0][0] if rows else "—"),
        ),
        "sections": (
            {
                "title": "Events by type",
                "headers": ("Event", "Count"),
                "rows": type_rows,
            },
        ),
        "highlights": (
            "This log is append-only and school-wide; it is not scoped to a single house.",
        ),
        "point_in_time": False,
        "record_count": len(rows),
    }
