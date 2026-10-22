"""Formal dispute intake and decision workflow (F-30)."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AuditEvent,
    DisputeCase,
    StaffNotification,
    StaffRole,
    UserProfile,
    get_user_profile,
)


def _notify_decision_makers(dispute):
    users = UserProfile.objects.filter(
        role__in=(StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER),
        user__is_active=True,
    ).select_related("user")
    StaffNotification.objects.bulk_create(
        [
            StaffNotification(
                user=profile.user,
                kind=StaffNotification.Kind.DISPUTE,
                title="Formal dispute opened",
                message=(
                    f"{dispute.reference}: {dispute.student.full_name} - "
                    f"{dispute.get_category_display()}"
                ),
                url=reverse("core:dispute_detail", args=[dispute.pk]),
            )
            for profile in users
        ]
    )


@transaction.atomic
def open_dispute(
    *,
    student,
    category,
    details,
    actor,
    exeat=None,
    roll_call_entry=None,
):
    dispute = DisputeCase(
        student=student,
        category=category,
        details=details,
        exeat=exeat,
        roll_call_entry=roll_call_entry,
        raised_by=actor,
    )
    dispute.save()
    _notify_decision_makers(dispute)
    AuditEvent.record(
        actor=actor,
        action=AuditEvent.Action.DISPUTE,
        target_type="DisputeCase",
        target_id=dispute.pk,
        summary=f"Opened dispute {dispute.reference}",
        after={
            "category": category,
            "student": student.full_name,
            "exeat_id": str(exeat.pk) if exeat else "",
            "roll_call_entry_id": str(roll_call_entry.pk) if roll_call_entry else "",
        },
    )
    return dispute


@transaction.atomic
def decide_dispute(
    dispute,
    *,
    actor,
    status,
    outcome,
    decision_notes,
    assigned_to=None,
):
    if status not in (
        DisputeCase.Status.UNDER_REVIEW,
        DisputeCase.Status.RESOLVED,
        DisputeCase.Status.REJECTED,
        DisputeCase.Status.CLOSED,
    ):
        raise ValueError("Choose a valid dispute status.")
    decision_notes = (decision_notes or "").strip()
    if status != DisputeCase.Status.UNDER_REVIEW and not decision_notes:
        raise ValueError("Record the reasoning for the dispute decision.")
    locked = DisputeCase.objects.select_for_update().select_related("student").get(
        pk=dispute.pk
    )
    if locked.status in (DisputeCase.Status.RESOLVED, DisputeCase.Status.CLOSED):
        raise ValueError("This dispute has already been closed.")
    profile = get_user_profile(actor)
    if profile is None or not (
        profile.is_admin or profile.is_senior_housemaster or actor.is_superuser
    ):
        raise ValueError(
            "Only the Headmaster/ADMIN or a Senior Housemaster can decide a dispute."
        )
    if locked.raised_by_id == actor.pk:
        # Staff may log a dispute, but cannot also be the sole decision maker.
        raise ValueError("A dispute cannot be decided by the person who raised it.")

    locked.status = status
    if assigned_to is not None:
        locked.assigned_to = assigned_to
    if status != DisputeCase.Status.UNDER_REVIEW:
        locked.outcome = outcome
        locked.decision_notes = decision_notes
        locked.resolved_by = actor
        locked.resolved_at = timezone.now()
    else:
        locked.decision_notes = decision_notes
    locked.save()

    if locked.raised_by_id:
        StaffNotification.objects.create(
            user=locked.raised_by,
            kind=StaffNotification.Kind.DISPUTE,
            title="Dispute case updated",
            message=(
                f"{locked.reference}: {locked.get_status_display()}."
                + (f" {decision_notes[:160]}" if decision_notes else "")
            ),
            url=reverse("core:dispute_detail", args=[locked.pk]),
        )
    AuditEvent.record(
        actor=actor,
        action=AuditEvent.Action.DISPUTE,
        target_type="DisputeCase",
        target_id=locked.pk,
        summary=f"{locked.reference}: {locked.get_status_display()}",
        after={
            "status": status,
            "outcome": locked.outcome,
            "notes": decision_notes,
        },
    )
    return locked
