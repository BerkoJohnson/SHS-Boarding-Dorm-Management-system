"""Audited exeat scheduling and emergency/medical authorization workflows."""

from datetime import timedelta

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AuditEvent,
    Exeat,
    ExeatMovement,
    ExeatReconciliation,
    ExeatReview,
    ExeatScheduleChange,
    StaffNotification,
    StaffRole,
    UserProfile,
    get_user_profile,
)


def _display_name(user):
    return user.get_full_name() or user.get_username()


def _notify_roles(*, roles, kind, title, message, url=""):
    users = UserProfile.objects.filter(
        role__in=roles,
        user__is_active=True,
    ).select_related("user")
    notifications = []
    for profile in users:
        notifications.append(
            StaffNotification(
                user=profile.user,
                kind=kind,
                title=title,
                message=message,
                url=url,
            )
        )
    if notifications:
        StaffNotification.objects.bulk_create(notifications)
    return len(notifications)


@transaction.atomic
def cancel_exeat(exeat, *, actor):
    """Cancel a pass only if no gate movement has been recorded."""
    locked = Exeat.objects.select_for_update(of=("self",)).get(pk=exeat.pk)
    if locked.status not in (Exeat.Status.PENDING, Exeat.Status.APPROVED):
        raise ValueError("Only pending or approved passes can be cancelled.")
    if ExeatReconciliation.objects.filter(exeat=locked).exists():
        raise ValueError("A reconciled exeat pass cannot be cancelled.")
    movement = ExeatMovement.objects.filter(exeat=locked).first()
    if movement and movement.actual_departure:
        raise ValueError(
            "An exeat pass cannot be cancelled after gate departure has been recorded."
        )

    previous_status = locked.status
    locked.status = Exeat.Status.CANCELLED
    locked.save()
    AuditEvent.record(
        actor=actor,
        action=AuditEvent.Action.EXEAT_CANCELLED,
        target_type="Exeat",
        target_id=locked.pk,
        summary=f"Cancelled {locked.exeat_number}",
        before={"status": previous_status},
        after={"status": Exeat.Status.CANCELLED},
    )
    return locked


@transaction.atomic
def reschedule_exeat(exeat, *, new_departure, new_return, reason, actor):
    """Change an approved/pending pass's travel window and preserve the old one.

    The original dates are copied into ``ExeatScheduleChange`` before the pass is
    updated.  A pass with any recorded gate movement or reconciliation cannot be
    rescheduled; use the reconciliation/correction workflow instead.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Record why the exeat schedule is changing.")

    locked = (
        Exeat.objects.select_for_update(of=("self",))
        .select_related("student", "movement", "reconciliation")
        .get(pk=exeat.pk)
    )
    if locked.status not in (Exeat.Status.PENDING, Exeat.Status.APPROVED):
        raise ValueError("Only pending or approved passes can be rescheduled.")
    if hasattr(locked, "movement") and (
        locked.movement.actual_departure or locked.movement.actual_return
    ):
        raise ValueError(
            "A pass with recorded gate movement cannot be rescheduled. "
            "Use the reconciliation or correction process."
        )
    if ExeatReconciliation.objects.filter(exeat=locked).exists():
        raise ValueError("A reconciled pass cannot be rescheduled.")
    if new_return <= new_departure:
        raise ValueError("The new return time must be after the new departure time.")

    previous = {
        "expected_departure": locked.expected_departure,
        "expected_return": locked.expected_return,
    }
    change = ExeatScheduleChange(
        exeat=locked,
        change_type=ExeatScheduleChange.ChangeType.RESCHEDULE,
        previous_departure=locked.expected_departure,
        previous_return=locked.expected_return,
        new_departure=new_departure,
        new_return=new_return,
        reason=reason,
        changed_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    change.save()

    locked.expected_departure = new_departure
    locked.expected_return = new_return
    locked.save()

    AuditEvent.record(
        actor=actor,
        action=AuditEvent.Action.EXEAT_RESCHEDULED,
        target_type="Exeat",
        target_id=locked.pk,
        summary=f"Rescheduled {locked.exeat_number}: {reason}",
        before={
            key: value.isoformat() if value else None
            for key, value in previous.items()
        },
        after={
            "expected_departure": new_departure.isoformat(),
            "expected_return": new_return.isoformat(),
        },
    )
    return change


@transaction.atomic
def approve_with_override(exeat, *, actor, reason):
    """Approve a pending pass through the documented emergency/medical path.

    General emergency overrides are restricted to the Headmaster/ADMIN and
    Senior Housemaster roles.  A Medical/Infirmary Lead may use the path only
    for medical passes.  Every use creates a 24-hour review item.
    """
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("An immediate reason is required for an emergency override.")

    locked = (
        Exeat.objects.select_for_update(of=("self",))
        .select_related("student", "student__house", "movement")
        .get(pk=exeat.pk)
    )
    if locked.status != Exeat.Status.PENDING:
        raise ValueError("Only pending requests can be approved through an override.")
    if locked.exeat_type not in (
        Exeat.ExeatType.MEDICAL,
        Exeat.ExeatType.EMERGENCY,
    ):
        raise ValueError(
            "The emergency override is limited to Medical and general Emergency "
            "passes. Use the normal approval workflow for other leave types."
        )
    profile = get_user_profile(actor)
    if profile is None or not profile.can_approve_exeat(locked):
        raise ValueError("This account is not authorized to approve this pass.")

    if locked.exeat_type == Exeat.ExeatType.MEDICAL:
        review_type = ExeatReview.ReviewType.MEDICAL_AFTER_HOURS
        if not (profile.is_admin or profile.is_senior_housemaster or profile.is_medical):
            raise ValueError(
                "Medical overrides require a Medical/Infirmary Lead, Senior "
                "Housemaster, or administrator."
            )
    else:
        review_type = ExeatReview.ReviewType.EMERGENCY_OVERRIDE
        if not (profile.is_admin or profile.is_senior_housemaster):
            raise ValueError(
                "General emergency overrides require the Headmaster/ADMIN or "
                "Senior Housemaster. Housemasters may initiate, not authorize."
            )

    locked.approved_by_user = actor
    if profile.is_housemaster:
        locked.approved_by = getattr(actor, "housemaster", None)
    locked.status = Exeat.Status.APPROVED
    locked.is_emergency_override = True
    locked.approval_remarks = (
        f"{locked.approval_remarks}\nEmergency override: {reason}"
        if locked.approval_remarks
        else f"Emergency override: {reason}"
    )
    locked.save()
    ExeatMovement.objects.get_or_create(exeat=locked)

    now = timezone.now()
    review, _ = ExeatReview.objects.get_or_create(
        exeat=locked,
        review_type=review_type,
        defaults={
            "reason": reason,
            "authorized_by": actor,
            "authorized_at": now,
            "due_at": now + timedelta(hours=24),
            "status": ExeatReview.Status.PENDING,
        },
    )
    if review.reason != reason:
        review.reason = reason
        review.authorized_by = actor
        review.authorized_at = now
        review.due_at = now + timedelta(hours=24)
        review.save()

    queue_url = reverse("core:exeat_review_queue")
    _notify_roles(
        roles=(StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER),
        kind=StaffNotification.Kind.EXEAT_REVIEW,
        title="Emergency exeat review required",
        message=(
            f"{locked.exeat_number} for {locked.student.full_name} was approved "
            f"through an emergency path by {_display_name(actor)}. Review is due "
            "within 24 hours."
        ),
        url=queue_url,
    )
    AuditEvent.record(
        actor=actor,
        action=AuditEvent.Action.EMERGENCY_OVERRIDE,
        target_type="Exeat",
        target_id=locked.pk,
        summary=f"Emergency override used for {locked.exeat_number}: {reason}",
        after={
            "review_type": review_type,
            "review_id": str(review.pk),
            "due_at": review.due_at.isoformat(),
        },
    )
    return locked, review


@transaction.atomic
def decide_exeat_review(review, *, reviewer, outcome, notes=""):
    """Record the post-hoc outcome of an emergency/medical review."""
    if outcome not in ExeatReview.Outcome.values:
        raise ValueError("Choose a valid review outcome.")
    locked = ExeatReview.objects.select_for_update().select_related("exeat").get(
        pk=review.pk
    )
    if locked.status != ExeatReview.Status.PENDING:
        raise ValueError("This review has already been decided.")
    profile = get_user_profile(reviewer)
    if profile is None or not (profile.is_admin or profile.is_senior_housemaster):
        raise ValueError("Only an administrator or Senior Housemaster can review this case.")
    if locked.authorized_by_id == reviewer.pk and not reviewer.is_superuser:
        raise ValueError("The authorizer cannot complete their own post-hoc review.")

    locked.status = (
        ExeatReview.Status.ESCALATED
        if outcome == ExeatReview.Outcome.ESCALATED
        else ExeatReview.Status.REVIEWED
    )
    locked.outcome = outcome
    locked.reviewed_by = reviewer
    locked.reviewed_at = timezone.now()
    locked.review_notes = (notes or "").strip()
    locked.save()

    if locked.authorized_by_id:
        StaffNotification.objects.create(
            user=locked.authorized_by,
            kind=StaffNotification.Kind.EXEAT_REVIEW,
            title="Exeat review completed",
            message=(
                f"{locked.exeat.exeat_number}: {locked.get_outcome_display()}. "
                f"{locked.review_notes}"
            ).strip(),
            url=reverse("core:exeat_detail", args=[locked.exeat_id]),
        )
    AuditEvent.record(
        actor=reviewer,
        action=AuditEvent.Action.EXEAT_REVIEW_DECIDED,
        target_type="ExeatReview",
        target_id=locked.pk,
        summary=f"{locked.exeat.exeat_number}: {locked.get_outcome_display()}",
        after={"outcome": outcome, "notes": locked.review_notes},
    )
    return locked
