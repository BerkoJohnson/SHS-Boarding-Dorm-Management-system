"""Authorised post-closure roll-call corrections (F-12)."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AuditEvent,
    RollCallAbsenceFollowUp,
    RollCallCorrection,
    RollCallEntry,
    RollCallSession,
    StaffNotification,
    StaffRole,
    UserProfile,
    get_user_profile,
)
from core.services.roll_call import (
    mark_roll_call_infraction_corrected,
    resolve_pending_absence_follow_ups,
)

SENSITIVE_STATUSES = {
    RollCallEntry.Status.PRESENT,
    RollCallEntry.Status.ABSENT,
}


class RollCallCorrectionError(ValueError):
    """A correction workflow error with a stable API code and field errors."""

    def __init__(self, message, *, code="correction_error", fields=None):
        super().__init__(message)
        self.code = code
        self.fields = fields or {}


def _requires_evidence(entry, requested_status):
    return bool(
        entry.status in SENSITIVE_STATUSES
        or requested_status in SENSITIVE_STATUSES
        or entry.disciplinary_records.filter(corrected_at__isnull=True).exists()
    )


def _notify_approvers(correction):
    users = (
        UserProfile.objects.filter(
            role__in=(StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER),
            user__is_active=True,
        )
        .exclude(user_id=correction.requested_by_id)
        .select_related("user")
    )
    StaffNotification.objects.bulk_create(
        [
            StaffNotification(
                user=profile.user,
                kind=StaffNotification.Kind.ROLL_CALL_CORRECTION,
                title="Roll-call correction needs approval",
                message=(
                    f"{correction.entry.student.full_name}: "
                    f"{correction.previous_status} → {correction.requested_status}. "
                    f"Reason: {correction.reason[:160]}"
                ),
                url=reverse("core:roll_call_corrections"),
            )
            for profile in users
        ]
    )


@transaction.atomic
def request_roll_call_correction(
    entry,
    *,
    requested_status,
    requested_remarks="",
    reason,
    evidence="",
    actor,
):
    """Create a pending correction for a completed or expired session."""
    if requested_status not in RollCallEntry.Status.values:
        raise RollCallCorrectionError(
            "Choose a valid attendance status.",
            code="validation_error",
            fields={"requested_status": ["Choose a valid attendance status."]},
        )
    reason = (reason or "").strip()
    if not reason:
        raise RollCallCorrectionError(
            "Record the reason for the correction.",
            code="validation_error",
            fields={"reason": ["This field is required."]},
        )
    locked_entry = (
        RollCallEntry.objects.select_for_update()
        .select_related("session", "student")
        .get(pk=entry.pk)
    )
    session = RollCallSession.objects.select_for_update().get(
        pk=locked_entry.session_id
    )
    if session.status not in (
        RollCallSession.Status.COMPLETED,
        RollCallSession.Status.EXPIRED,
    ):
        raise RollCallCorrectionError(
            "Use the normal attendance controls while the roll call is still in progress.",
            code="roll_call_state_error",
        )
    if requested_status == locked_entry.status:
        raise RollCallCorrectionError(
            "The requested status is already recorded.",
            code="validation_error",
            fields={"requested_status": ["The requested status is already recorded."]},
        )
    if RollCallCorrection.objects.filter(
        entry=locked_entry,
        status=RollCallCorrection.Status.PENDING,
    ).exists():
        raise RollCallCorrectionError(
            "This entry already has a pending correction request.",
            code="correction_pending",
        )

    sensitive = _requires_evidence(locked_entry, requested_status)
    evidence = (evidence or "").strip()
    if sensitive and not evidence:
        raise RollCallCorrectionError(
            "A sensitive correction (Present/Absent or conduct-linked) requires evidence.",
            code="validation_error",
            fields={"evidence": ["Supporting evidence is required for this correction."]},
        )
    correction = RollCallCorrection(
        entry=locked_entry,
        previous_status=locked_entry.status,
        requested_status=requested_status,
        previous_remarks=locked_entry.remarks or "",
        requested_remarks=(requested_remarks or "").strip(),
        reason=reason,
        evidence=evidence,
        requested_by=actor,
        requires_second_approval=sensitive,
        status=RollCallCorrection.Status.PENDING,
    )
    correction.save()
    _notify_approvers(correction)
    AuditEvent.record(
        actor=actor,
        action=AuditEvent.Action.ROLL_CALL_CORRECTION_REQUESTED,
        target_type="RollCallCorrection",
        target_id=correction.pk,
        summary=(
            f"{correction.entry.student.full_name}: "
            f"{correction.previous_status} → {correction.requested_status}"
        ),
        before={"status": correction.previous_status},
        after={"status": correction.requested_status, "reason": reason},
    )
    return correction


@transaction.atomic
def review_roll_call_correction(correction, *, reviewer, approve, review_note=""):
    """Approve and apply, or reject, a pending correction."""
    locked = (
        RollCallCorrection.objects.select_for_update()
        .select_related("entry__session", "entry__student")
        .get(pk=correction.pk)
    )
    if locked.status != RollCallCorrection.Status.PENDING:
        raise RollCallCorrectionError(
            "This correction has already been decided.",
            code="correction_decided",
        )
    profile = get_user_profile(reviewer)
    if profile is None or not (profile.is_admin or profile.is_senior_housemaster):
        raise RollCallCorrectionError(
            "Only an administrator or Senior Housemaster can decide corrections.",
            code="approval_forbidden",
        )
    if locked.requested_by_id == reviewer.pk:
        raise RollCallCorrectionError(
            "A correction cannot be approved by the person who requested it.",
            code="approval_forbidden",
        )

    if not approve:
        locked.status = RollCallCorrection.Status.REJECTED
        locked.reviewed_by = reviewer
        locked.reviewed_at = timezone.now()
        locked.review_note = (review_note or "").strip()
        locked.save()
        _notify_requester(locked, approved=False)
        AuditEvent.record(
            actor=reviewer,
            action=AuditEvent.Action.ROLL_CALL_CORRECTION_DECIDED,
            target_type="RollCallCorrection",
            target_id=locked.pk,
            summary=f"Correction rejected for {locked.entry.student.full_name}",
            after={"notes": locked.review_note},
        )
        return locked

    session = RollCallSession.objects.select_for_update().get(
        pk=locked.entry.session_id
    )
    if session.status not in (
        RollCallSession.Status.COMPLETED,
        RollCallSession.Status.EXPIRED,
    ):
        raise RollCallCorrectionError(
            "The session is no longer closed; use normal attendance controls.",
            code="roll_call_state_error",
        )
    entry = RollCallEntry.objects.select_for_update().get(pk=locked.entry_id)
    if entry.status != locked.previous_status:
        raise RollCallCorrectionError(
            "The original entry changed after this request was created. "
            "Reject this request and create a fresh correction.",
            code="correction_stale",
        )
    if locked.requires_second_approval and not (locked.evidence or "").strip():
        raise RollCallCorrectionError(
            "This sensitive correction is missing supporting evidence.",
            code="validation_error",
            fields={"evidence": ["Supporting evidence is required for this correction."]},
        )

    now = timezone.now()
    RollCallEntry.objects.filter(pk=entry.pk).update(
        status=locked.requested_status,
        remarks=locked.requested_remarks,
        status_source=RollCallEntry.StatusSource.STAFF,
        updated_at=now,
    )
    entry.refresh_from_db()
    is_excused = locked.requested_status in (
        RollCallEntry.Status.ON_EXEAT,
        RollCallEntry.Status.INFIRMARY,
        RollCallEntry.Status.OFFICIAL_ACTIVITY,
    )
    resolve_pending_absence_follow_ups(
        entry,
        (
            RollCallAbsenceFollowUp.Status.EXCUSED
            if is_excused
            else RollCallAbsenceFollowUp.Status.CORRECTED
        ),
        reviewer,
        locked.review_note or locked.reason,
        locked.requested_status,
    )
    if locked.previous_status == RollCallEntry.Status.ABSENT and (
        locked.requested_status != RollCallEntry.Status.ABSENT
    ):
        mark_roll_call_infraction_corrected(
            entry,
            locked.requested_status,
            reviewer,
        )

    locked.status = RollCallCorrection.Status.APPROVED
    locked.reviewed_by = reviewer
    locked.reviewed_at = now
    locked.review_note = (review_note or "").strip()
    locked.applied_at = now
    locked.save()
    _notify_requester(locked, approved=True)
    AuditEvent.record(
        actor=reviewer,
        action=AuditEvent.Action.ROLL_CALL_CORRECTION_DECIDED,
        target_type="RollCallCorrection",
        target_id=locked.pk,
        summary=(
            f"Correction approved for {locked.entry.student.full_name}: "
            f"{locked.previous_status} → {locked.requested_status}"
        ),
        after={"notes": locked.review_note},
    )
    return locked


def _notify_requester(correction, *, approved):
    if not correction.requested_by_id:
        return
    StaffNotification.objects.create(
        user=correction.requested_by,
        kind=StaffNotification.Kind.ROLL_CALL_CORRECTION,
        title="Roll-call correction decided",
        message=(
            f"{correction.entry.student.full_name}: correction "
            f"{'approved' if approved else 'rejected'}."
        ),
        url=reverse("core:roll_call_sessions"),
    )
