"""
Operations and domain service layer for Roll Call muster sessions and attendance tracking.
Handles automated roster generation, active exeat reconciliation, and attendance status workflows.
"""
from datetime import date
from typing import Optional, List
from django.db import transaction
from django.utils import timezone
from core.models import (
    Student,
    House,
    Dormitory,
    HouseMaster,
    AcademicTerm,
    RollCallSession,
    RollCallEntry,
    Exeat,
    ExeatMovement,
    DisciplinaryRecord,
)


def create_roll_call_session(
    house: House,
    session_type: str = RollCallSession.SessionType.EVENING,
    inspection_date: Optional[date] = None,
    conducted_by: Optional[HouseMaster] = None,
    dormitory: Optional[Dormitory] = None,
    academic_term: Optional[AcademicTerm] = None,
    notes: Optional[str] = None,
) -> RollCallSession:
    """
    Creates a new RollCallSession and automatically provisions RollCallEntry records
    for all boarding students enrolled in the target House or Dormitory block.

    Students start UNMARKED unless an active, unreturned gate exeat automatically
    reconciles them as ON_EXEAT.

    Args:
        house: Target House for roll call muster.
        session_type: RollCallSession.SessionType choice.
        inspection_date: Date of inspection (defaults to current date).
        conducted_by: HouseMaster conducting the muster.
        dormitory: Optional specific Dormitory block filter.
        academic_term: Current academic term (falls back to active term).
        notes: Optional session notes.

    Returns:
        RollCallSession: Newly created or existing in-progress session with populated entries.
    """
    if inspection_date is None:
        inspection_date = timezone.localdate()

    if academic_term is None:
        academic_term = AcademicTerm.objects.filter(is_current=True).first()

    with transaction.atomic():
        session = RollCallSession.objects.create(
            house=house,
            dormitory=dormitory,
            academic_term=academic_term,
            session_type=session_type,
            date=inspection_date,
            conducted_by=conducted_by,
            status=RollCallSession.Status.IN_PROGRESS,
            notes=notes or "",
        )

        # 1. Fetch all boarding students in this jurisdiction
        students_qs = Student.objects.filter(
            house=house,
            residency=Student.ResidencyStatus.BOARDING,
        )
        if dormitory:
            students_qs = students_qs.filter(dormitory=dormitory)

        students = list(students_qs.select_related("dormitory"))

        # 2. Check which students are currently out on active exeat
        active_student_ids_on_exeat = set(
            ExeatMovement.objects.filter(
                exeat__student__in=students,
                actual_departure__isnull=False,
                actual_return__isnull=True,
            ).values_list("exeat__student_id", flat=True)
        )

        # 3. Create attendance entries
        entries = []
        for student in students:
            if student.id in active_student_ids_on_exeat:
                entry_status = RollCallEntry.Status.ON_EXEAT
                entry_remarks = "Active Gate Exeat (Departed)"
            else:
                entry_status = RollCallEntry.Status.UNMARKED
                entry_remarks = ""

            entries.append(
                RollCallEntry(
                    session=session,
                    student=student,
                    status=entry_status,
                    remarks=entry_remarks,
                )
            )

        if entries:
            RollCallEntry.objects.bulk_create(entries)

    return session


def update_entry_status(
    entry: RollCallEntry,
    new_status: str,
    remarks: Optional[str] = None,
) -> RollCallEntry:
    """
    Updates the attendance status of a single student entry within an active session.

    Args:
        entry: Target RollCallEntry.
        new_status: One of RollCallEntry.Status choices.
        remarks: Optional updated remarks.

    Returns:
        RollCallEntry: Saved and updated entry.

    Raises:
        ValueError: If the parent session is already finalized/completed.
    """
    if new_status not in RollCallEntry.Status.values:
        raise ValueError(f"Invalid roll call status: {new_status}")

    with transaction.atomic():
        session = RollCallSession.objects.select_for_update().get(
            pk=entry.session_id
        )
        if session.status == RollCallSession.Status.COMPLETED:
            raise ValueError(
                "Cannot modify attendance records in a finalized roll call session."
            )

        entry.session = session
        entry.status = new_status
        if remarks is not None:
            entry.remarks = remarks
        entry.save()
    return entry


def complete_roll_call_session(session: RollCallSession) -> RollCallSession:
    """Finalize a session only after every roster entry has been verified."""
    with transaction.atomic():
        locked_session = RollCallSession.objects.select_for_update().get(
            pk=session.pk
        )
        if locked_session.status == RollCallSession.Status.COMPLETED:
            return locked_session

        unmarked_count = locked_session.entries.filter(
            status=RollCallEntry.Status.UNMARKED
        ).count()
        if unmarked_count:
            raise ValueError(
                f"Cannot finalize roll call: {unmarked_count} "
                "student(s) are still unmarked."
            )

        locked_session.status = RollCallSession.Status.COMPLETED
        locked_session.save(update_fields=["status", "updated_at"])
        return locked_session


def mark_all_present(session: RollCallSession) -> int:
    """
    Corrects explicitly absent entries to PRESENT without changing unmarked
    students or excused statuses.

    Args:
        session: Active RollCallSession.

    Returns:
        int: Number of entries updated. Returns 0 if session is already finalized.
    """
    # Guard: prevent bulk mutations on finalized sessions
    if session.status == RollCallSession.Status.COMPLETED:
        return 0

    with transaction.atomic():
        locked_session = RollCallSession.objects.select_for_update().get(
            pk=session.pk
        )
        if locked_session.status == RollCallSession.Status.COMPLETED:
            return 0

        return locked_session.entries.filter(
            status=RollCallEntry.Status.ABSENT
        ).update(status=RollCallEntry.Status.PRESENT)


def convert_absence_to_disciplinary_record(
    entry: RollCallEntry,
    logged_by: Optional[HouseMaster] = None,
) -> DisciplinaryRecord:
    """
    Generates a student disciplinary infraction record for an unexcused roll call absence.

    Args:
        entry: RollCallEntry marked as ABSENT.
        logged_by: Optional HouseMaster logging the infraction.

    Returns:
        DisciplinaryRecord: Created conduct infraction.
    """
    staff = logged_by or entry.session.conducted_by
    if not staff:
        staff = HouseMaster.objects.filter(house=entry.session.house).first()

    record = DisciplinaryRecord.objects.create(
        student=entry.student,
        academic_term=entry.session.academic_term,
        offense=f"Missing {entry.session.get_session_type_display()}",
        description=(
            f"Student was marked absent (unexcused) during the {entry.session.get_session_type_display()} "
            f"inspection on {entry.session.date} for {entry.session.house.name}."
        ),
        severity=DisciplinaryRecord.Severity.MINOR,
        action_taken="Internal campus gating / House duty reprimand",
        logged_by=staff,
    )
    return record
