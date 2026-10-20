"""
Operations and domain service layer for Roll Call muster sessions and attendance tracking.
Handles automated roster generation, active exeat reconciliation, and attendance status workflows.
"""

from datetime import date
from typing import Optional, List
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from core.models import (
    Student,
    House,
    Dormitory,
    HouseMaster,
    AcademicTerm,
    RollCallSession,
    RollCallEntry,
    RollCallAbsenceFollowUp,
    Exeat,
    ExeatMovement,
    DisciplinaryRecord,
)


def _require_roll_call_entitlement():
    from tenancy.entitlements import feature_enabled

    if not feature_enabled("roll_call"):
        raise ValueError("This tenant's subscription does not include roll call.")


def create_roll_call_session(
    house: House,
    session_type: str = RollCallSession.SessionType.EVENING,
    inspection_date: Optional[date] = None,
    conducted_by: Optional[HouseMaster] = None,
    created_by=None,
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
    _require_roll_call_entitlement()
    if inspection_date is None:
        inspection_date = timezone.localdate()

    if academic_term is None:
        academic_term = AcademicTerm.objects.filter(is_current=True).first()

    with transaction.atomic():
        # Serialize concurrent starts for this house. The row lock and the
        # partial unique constraint below both protect against duplicate
        # in-progress sessions.
        House.objects.select_for_update().get(pk=house.pk)

        existing_sessions = list(
            RollCallSession.objects.select_for_update()
            .filter(
                house=house,
                dormitory=dormitory,
                session_type=session_type,
                date=inspection_date,
                status=RollCallSession.Status.IN_PROGRESS,
            )
            .order_by("created_at")
        )
        active_session = None
        for candidate in existing_sessions:
            if candidate.is_expired:
                # Preserve the evidence instead of deleting it. Marking the
                # stale row EXPIRED also releases the partial unique constraint
                # so a fresh muster can start for the same slot.
                candidate.status = RollCallSession.Status.EXPIRED
                note = (
                    "Automatically expired when a new muster was started after "
                    "the one-hour completion window."
                )
                candidate.notes = (
                    f"{candidate.notes.rstrip()}\n{note}"
                    if candidate.notes
                    else note
                )
                candidate.save(
                    update_fields=["status", "notes", "updated_at"]
                )
            elif active_session is None:
                active_session = candidate

        if active_session is not None:
            # The view uses this transient flag to explain that an already-open
            # muster was reused instead of implying that a new one was started.
            active_session.reused_existing = True
            return active_session

        session = RollCallSession.objects.create(
            house=house,
            dormitory=dormitory,
            academic_term=academic_term,
            session_type=session_type,
            date=inspection_date,
            conducted_by=conducted_by,
            created_by=created_by,
            status=RollCallSession.Status.IN_PROGRESS,
            notes=notes or "",
        )

        # 1. Fetch all boarding students in this jurisdiction
        students_qs = Student.objects.exclude(
            status=Student.EnrollmentStatus.COMPLETED
        ).filter(
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
                    status_source=(
                        RollCallEntry.StatusSource.GATE
                        if student.id in active_student_ids_on_exeat
                        else RollCallEntry.StatusSource.STAFF
                    ),
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
    updated_by=None,
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
    _require_roll_call_entitlement()
    if new_status not in RollCallEntry.Status.values:
        raise ValueError(f"Invalid roll call status: {new_status}")
    with transaction.atomic():
        session = RollCallSession.objects.select_for_update().get(pk=entry.session_id)
        if session.status == RollCallSession.Status.COMPLETED:
            raise ValueError(
                "Cannot modify attendance records in a finalized roll call session."
            )
        if session.is_expired:
            raise ValueError("This roll call session has expired.")

        previous_status = entry.status
        entry.session = session
        entry.status = new_status
        entry.remarks = (remarks or "").strip()
        entry.status_source = RollCallEntry.StatusSource.STAFF
        update_fields = ["status", "status_source", "remarks", "updated_at"]
        if entry.gate_conflict_at and not entry.gate_conflict_reviewed_at:
            entry.gate_conflict_reviewed_at = timezone.now()
            entry.gate_conflict_reviewed_by = updated_by
            update_fields.extend(
                ["gate_conflict_reviewed_at", "gate_conflict_reviewed_by"]
            )
        entry.save(update_fields=update_fields)

        if new_status == RollCallEntry.Status.ABSENT:
            if previous_status != RollCallEntry.Status.ABSENT:
                RollCallAbsenceFollowUp.objects.create(
                    entry=entry,
                    reported_by=updated_by,
                )
        else:
            is_authorized_exception = new_status in (
                RollCallEntry.Status.ON_EXEAT,
                RollCallEntry.Status.INFIRMARY,
                RollCallEntry.Status.OFFICIAL_ACTIVITY,
            )
            resolve_pending_absence_follow_ups(
                entry,
                (
                    RollCallAbsenceFollowUp.Status.EXCUSED
                    if is_authorized_exception
                    else RollCallAbsenceFollowUp.Status.CORRECTED
                ),
                updated_by,
                (
                    (remarks or "").strip()
                    if is_authorized_exception
                    else (
                        f"Attendance changed from {previous_status} to {new_status} "
                        "before the absence was confirmed."
                    )
                ),
                new_status,
            )
            if previous_status == RollCallEntry.Status.ABSENT and updated_by:
                mark_roll_call_infraction_corrected(
                    entry,
                    new_status,
                    updated_by,
                )
    return entry


def resolve_pending_absence_follow_ups(
    entry: RollCallEntry,
    resolution: str,
    resolved_by,
    resolution_note: str,
    resulting_status: str = "",
) -> int:
    """Resolve any pending absence reviews while preserving who changed them and why."""
    return entry.absence_follow_ups.filter(
        status=RollCallAbsenceFollowUp.Status.PENDING
    ).update(
        status=resolution,
        resolved_by=resolved_by,
        resolved_at=timezone.now(),
        resolution_note=resolution_note,
        resulting_status=resulting_status,
        updated_at=timezone.now(),
    )


def complete_roll_call_session(session: RollCallSession) -> RollCallSession:
    """Finalize a session only after every roster entry has been verified."""
    _require_roll_call_entitlement()
    with transaction.atomic():
        locked_session = RollCallSession.objects.select_for_update().get(pk=session.pk)
        if locked_session.status == RollCallSession.Status.COMPLETED:
            return locked_session
        if locked_session.is_expired:
            raise ValueError("This roll call session has expired.")

        unmarked_count = locked_session.entries.filter(
            status=RollCallEntry.Status.UNMARKED
        ).count()
        if unmarked_count:
            raise ValueError(
                f"Cannot finalize roll call: {unmarked_count} "
                "student(s) are still unmarked."
            )

        unreviewed_conflict_count = locked_session.entries.filter(
            gate_conflict_at__isnull=False,
            gate_conflict_reviewed_at__isnull=True,
        ).count()
        if unreviewed_conflict_count:
            raise ValueError(
                f"Cannot finalize roll call: {unreviewed_conflict_count} "
                "gate/roll-call discrepancy(s) still need review."
            )

        locked_session.status = RollCallSession.Status.COMPLETED
        locked_session.save(update_fields=["status", "updated_at"])
        return locked_session


def mark_all_present(session: RollCallSession, corrected_by) -> int:
    """
    Corrects explicitly absent entries to PRESENT without changing unmarked
    students or excused statuses.

    Args:
        session: Active RollCallSession.

    Returns:
        int: Number of entries updated. Returns 0 if session is already finalized.
    """
    _require_roll_call_entitlement()
    # Guard: prevent bulk mutations on finalized sessions
    if session.status == RollCallSession.Status.COMPLETED:
        return 0

    with transaction.atomic():
        locked_session = RollCallSession.objects.select_for_update().get(pk=session.pk)
        if locked_session.status == RollCallSession.Status.COMPLETED:
            return 0
        if locked_session.is_expired:
            raise ValueError("This roll call session has expired.")

        absent_entries = list(
            locked_session.entries.filter(status=RollCallEntry.Status.ABSENT)
            .filter(
                Q(gate_conflict_at__isnull=True)
                | Q(gate_conflict_reviewed_at__isnull=False)
            )
            .select_related("student", "session")
        )
        for entry in absent_entries:
            update_entry_status(
                entry,
                RollCallEntry.Status.PRESENT,
                remarks=entry.remarks,
                updated_by=corrected_by,
            )
        return len(absent_entries)


def convert_absence_to_disciplinary_record(
    entry: RollCallEntry,
    logged_by: Optional[HouseMaster] = None,
    follow_up: Optional[RollCallAbsenceFollowUp] = None,
) -> tuple[DisciplinaryRecord, bool]:
    """
    Creates one linked conduct record for an unexcused roll-call absence.

    Args:
        entry: RollCallEntry marked as ABSENT.
        logged_by: Optional HouseMaster logging the infraction.

    Returns:
        tuple: The conduct record and whether it was newly created.
    """
    _require_roll_call_entitlement()
    if entry.status != RollCallEntry.Status.ABSENT:
        raise ValueError(
            "Only an absent roll-call entry can be logged as an infraction."
        )

    staff = logged_by or entry.session.conducted_by
    if not staff:
        staff = HouseMaster.objects.filter(house=entry.session.house).first()

    if follow_up is None:
        follow_up = (
            entry.absence_follow_ups.filter(
                status=RollCallAbsenceFollowUp.Status.CONFIRMED
            )
            .order_by("-resolved_at")
            .first()
        )
    if (
        follow_up is None
        or follow_up.status != RollCallAbsenceFollowUp.Status.CONFIRMED
    ):
        raise ValueError(
            "An absence must be reviewed and confirmed before it is logged."
        )

    record, created = DisciplinaryRecord.objects.get_or_create(
        roll_call_follow_up=follow_up,
        defaults={
            "roll_call_entry": entry,
            "student": entry.student,
            "academic_term": entry.session.academic_term,
            "offense": f"Missing {entry.session.get_session_type_display()}",
            "description": (
                f"Student was marked absent (unexcused) during the {entry.session.get_session_type_display()} "
                f"roll call on {entry.session.date} for {entry.session.house.name}. "
                f"Confirmed after review: {follow_up.resolution_note}"
            ),
            "severity": DisciplinaryRecord.Severity.MINOR,
            "action_taken": "Internal campus gating / House duty reprimand",
            "logged_by": staff,
        },
    )
    return record, created


def mark_roll_call_infraction_corrected(
    entry: RollCallEntry,
    new_status: str,
    corrected_by,
) -> bool:
    """Retain a linked infraction and record that its roll-call status was corrected."""
    records = DisciplinaryRecord.objects.filter(
        roll_call_entry=entry,
        corrected_at__isnull=True,
    )
    if not records.exists():
        return False

    actor_name = corrected_by.get_full_name() or corrected_by.get_username()
    correction_note = (
        f"Roll-call status was changed from Absent to {new_status} by {actor_name}. "
        "The original conduct record is retained for audit and is not counted as an active incident."
    )
    records.update(
        corrected_at=timezone.now(),
        corrected_by=corrected_by,
        correction_note=correction_note,
        updated_at=timezone.now(),
    )
    return True


def resolve_absence_follow_up(
    follow_up: RollCallAbsenceFollowUp,
    resolution: str,
    resolved_by,
    resolution_note: str,
    resulting_status: str = "",
    logged_by: Optional[HouseMaster] = None,
) -> Optional[DisciplinaryRecord]:
    """Resolve a pending absence; only confirmed unexcused cases create conduct records."""
    _require_roll_call_entitlement()
    if resolution not in (
        RollCallAbsenceFollowUp.Status.CONFIRMED,
        RollCallAbsenceFollowUp.Status.EXCUSED,
        RollCallAbsenceFollowUp.Status.CORRECTED,
    ):
        raise ValueError("Choose a valid absence follow-up resolution.")
    if not resolution_note.strip():
        raise ValueError("Enter a brief note explaining the follow-up decision.")
    if resolution != RollCallAbsenceFollowUp.Status.CONFIRMED:
        allowed_statuses = {
            RollCallEntry.Status.PRESENT,
            RollCallEntry.Status.ON_EXEAT,
            RollCallEntry.Status.INFIRMARY,
            RollCallEntry.Status.OFFICIAL_ACTIVITY,
        }
        if resulting_status not in allowed_statuses:
            raise ValueError("Choose a verified attendance status for this resolution.")
        if (
            resolution == RollCallAbsenceFollowUp.Status.EXCUSED
            and resulting_status == RollCallEntry.Status.PRESENT
        ):
            raise ValueError(
                "An authorized absence must use an excused attendance status."
            )

    with transaction.atomic():
        locked_session = RollCallSession.objects.select_for_update().get(
            pk=follow_up.entry.session_id
        )
        # Completed and expired records are immutable in this batch. A separate
        # audited post-closure correction workflow is required before reopening
        # either state (F-12 in the audit report).
        if locked_session.status == RollCallSession.Status.COMPLETED:
            raise ValueError(
                "Cannot resolve an absence on a completed roll call session; "
                "records are locked for audit integrity."
            )
        if locked_session.is_expired:
            raise ValueError(
                "Cannot resolve an absence on an expired roll call session; "
                "attendance records are locked for audit integrity."
            )
        locked_follow_up = (
            RollCallAbsenceFollowUp.objects.select_for_update()
            .select_related("entry__session", "entry__student")
            .get(pk=follow_up.pk)
        )
        if locked_follow_up.status != RollCallAbsenceFollowUp.Status.PENDING:
            raise ValueError("This absence follow-up has already been resolved.")
        if locked_follow_up.entry.status != RollCallEntry.Status.ABSENT:
            raise ValueError("The roll-call entry is no longer marked absent.")

        locked_follow_up.status = resolution
        locked_follow_up.resolved_by = resolved_by
        locked_follow_up.resolved_at = timezone.now()
        locked_follow_up.resolution_note = resolution_note.strip()
        locked_follow_up.resulting_status = resulting_status
        locked_follow_up.save(
            update_fields=[
                "status",
                "resolved_by",
                "resolved_at",
                "resolution_note",
                "resulting_status",
                "updated_at",
            ]
        )

        if resolution != RollCallAbsenceFollowUp.Status.CONFIRMED:
            entry = locked_follow_up.entry
            entry.status = resulting_status
            entry.status_source = RollCallEntry.StatusSource.STAFF
            entry.remarks = resolution_note.strip()
            RollCallEntry.objects.filter(pk=entry.pk).update(
                status=entry.status,
                status_source=entry.status_source,
                remarks=entry.remarks,
                updated_at=timezone.now(),
            )
            entry.refresh_from_db()
            mark_roll_call_infraction_corrected(
                entry,
                resulting_status,
                resolved_by,
            )
            return None

        return convert_absence_to_disciplinary_record(
            locked_follow_up.entry,
            logged_by=logged_by,
            follow_up=locked_follow_up,
        )[0]


def reconcile_gate_departure_with_roll_calls(
    exeat: Exeat, movement: ExeatMovement
) -> int:
    """Reconcile gate departures into active roll calls without overwriting staff marks."""
    student = exeat.student
    if not movement.actual_departure or not student.house_id:
        return 0

    departure_date = timezone.localtime(movement.actual_departure).date()
    reconciled = 0
    with transaction.atomic():
        sessions = list(
            RollCallSession.objects.select_for_update()
            .filter(
                house_id=student.house_id,
                date=departure_date,
                status=RollCallSession.Status.IN_PROGRESS,
            )
            .filter(Q(dormitory__isnull=True) | Q(dormitory_id=student.dormitory_id))
        )
        for entry in (
            RollCallEntry.objects.select_for_update()
            .filter(
                session__in=sessions,
                student=student,
            )
            .select_related("session")
        ):
            if entry.status == RollCallEntry.Status.UNMARKED:
                entry.status = RollCallEntry.Status.ON_EXEAT
                entry.status_source = RollCallEntry.StatusSource.GATE
                entry.remarks = (
                    f"Gate departure recorded at "
                    f"{timezone.localtime(movement.actual_departure).strftime('%Y-%m-%d %H:%M UTC')}."
                )
                entry.save(
                    update_fields=["status", "status_source", "remarks", "updated_at"]
                )
                reconciled += 1
            elif entry.status != RollCallEntry.Status.ON_EXEAT:
                entry.gate_conflict_at = movement.actual_departure
                entry.gate_conflict_note = (
                    f"Gate departure was recorded while roll call showed "
                    f"{entry.get_status_display()}. Review both records; attendance was not changed automatically."
                )
                entry.gate_conflict_reviewed_at = None
                entry.gate_conflict_reviewed_by = None
                entry.save(
                    update_fields=[
                        "gate_conflict_at",
                        "gate_conflict_note",
                        "gate_conflict_reviewed_at",
                        "gate_conflict_reviewed_by",
                        "updated_at",
                    ]
                )
    return reconciled
