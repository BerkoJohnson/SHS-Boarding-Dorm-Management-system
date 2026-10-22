"""Late-return incident creation, escalation, and resolution."""

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AuditEvent,
    Exeat,
    HouseMaster,
    LateReturnIncident,
    StaffNotification,
    StaffRole,
    UserProfile,
)
from core.services.operational import late_return_thresholds


def _house_owner(exeat):
    if not exeat.student.house_id:
        return None
    return (
        HouseMaster.objects.filter(
            house_id=exeat.student.house_id,
            user__isnull=False,
            is_senior_housemaster=False,
        )
        .select_related("user")
        .first()
        or HouseMaster.objects.filter(
            house_id=exeat.student.house_id,
            user__isnull=False,
        )
        .select_related("user")
        .first()
    )


def _notification_users_for_level(exeat, level):
    users = []
    if level >= LateReturnIncident.Escalation.HOUSEMASTER:
        owner = _house_owner(exeat)
        if owner is not None:
            users.append(owner.user)
    if level >= LateReturnIncident.Escalation.SENIOR_HOUSEMASTER:
        # The pilot's assistant head has a SENIOR_HOUSEMASTER profile and may not
        # have a HouseMaster row. Include both representations.
        users.extend(
            profile.user
            for profile in UserProfile.objects.filter(
                role=StaffRole.SENIOR_HOUSEMASTER,
                user__is_active=True,
            ).select_related("user")
            if profile.user is not None
        )
        users.extend(
            profile.user
            for profile in HouseMaster.objects.filter(
                is_senior_housemaster=True,
                user__isnull=False,
            ).select_related("user")
            if profile.user is not None
        )
    if level >= LateReturnIncident.Escalation.HEADMASTER:
        users.extend(
            profile.user
            for profile in UserProfile.objects.filter(
                role=StaffRole.ADMIN,
                user__is_active=True,
            ).select_related("user")
            if profile.user is not None
        )
    seen = set()
    result = []
    for user in users:
        if user and user.pk not in seen:
            seen.add(user.pk)
            result.append(user)
    return result


def _escalation_for_delay(delay):
    thresholds = late_return_thresholds()
    if delay >= thresholds["headmaster"]:
        return LateReturnIncident.Escalation.HEADMASTER
    if delay >= thresholds["senior"]:
        return LateReturnIncident.Escalation.SENIOR_HOUSEMASTER
    return LateReturnIncident.Escalation.HOUSEMASTER


def notify_missed_return(exeat, *, detected_at=None):
    """Notify the Housemaster as soon as an expected return is missed.

    This is the first D3 stage and deliberately precedes the incident threshold:
    a short delay still needs a written alert and guardian contact without
    opening a formal late-return incident. The notification URL carries the
    exeat ID so repeated scheduler runs remain idempotent.
    """
    detected_at = detected_at or timezone.now()
    if not exeat.expected_return or detected_at < exeat.expected_return:
        return False
    owner = _house_owner(exeat)
    if owner is None or owner.user_id is None:
        return False
    url = f"{reverse('core:late_return_incidents')}?exeat={exeat.pk}"
    if StaffNotification.objects.filter(
        user=owner.user,
        kind=StaffNotification.Kind.LATE_RETURN,
        url=url,
        title="Student missed expected return",
    ).exists():
        return False
    StaffNotification.objects.create(
        user=owner.user,
        kind=StaffNotification.Kind.LATE_RETURN,
        title="Student missed expected return",
        message=(
            f"{exeat.student.full_name} ({exeat.exeat_number}) was expected back "
            f"at {timezone.localtime(exeat.expected_return):%d %b %Y, %H:%M}. "
            "Contact the guardian and confirm the student's location."
        ),
        url=url,
    )
    return True


@transaction.atomic
def open_late_return_incident(
    exeat,
    *,
    detected_at=None,
    actual_return=None,
    source="automatic",
):
    """Open or update one incident for an overdue pass.

    ``actual_return`` is supplied when the gate records a late return.  Without
    it, the pass is still out and the configured incident threshold applies.
    """
    exeat = (
        Exeat.objects.select_for_update(of=("self",))
        .select_related("student", "student__house")
        .get(pk=exeat.pk)
    )
    detected_at = detected_at or timezone.now()
    if not exeat.expected_return:
        return None
    delay = (actual_return or detected_at) - exeat.expected_return
    if delay.total_seconds() < 0:
        return None
    thresholds = late_return_thresholds()
    if actual_return is None and delay < thresholds["incident"]:
        return None

    owner = _house_owner(exeat)
    level = _escalation_for_delay(delay)
    incident = (
        LateReturnIncident.objects.select_for_update()
        .filter(exeat=exeat)
        .first()
    )
    created = False
    previous_level = None
    if incident is None:
        incident = LateReturnIncident(
            exeat=exeat,
            student=exeat.student,
            expected_return=exeat.expected_return,
            detected_at=detected_at,
            actual_return=actual_return,
            escalation_level=level,
            owner=owner,
            summary=(
                f"{'Returned ' if actual_return else 'Still out '}"
                f"{round(delay.total_seconds() / 3600, 1)} hour(s) after the "
                f"expected return time. Source: {source}."
            ),
        )
        incident.save()
        created = True
    else:
        previous_level = incident.escalation_level
        if actual_return is not None and incident.actual_return is None:
            incident.actual_return = actual_return
        if level > incident.escalation_level:
            incident.escalation_level = level
        if incident.owner_id is None and owner is not None:
            incident.owner = owner
        incident.summary = (
            f"{'Returned ' if incident.actual_return else 'Still out '}"
            f"{incident.delay_hours} hour(s) after the expected return time. "
            f"Source: {source}."
        )
        incident.save()

    escalated = created or (
        previous_level is not None and level > previous_level
    )
    if escalated:
        url = reverse("core:late_return_incidents")
        for user in _notification_users_for_level(exeat, level):
            StaffNotification.objects.create(
                user=user,
                kind=StaffNotification.Kind.LATE_RETURN,
                title="Late-return incident opened",
                message=(
                    f"{exeat.student.full_name} ({exeat.exeat_number}) is "
                    f"{'recorded as returned late' if actual_return else 'still out'} "
                    f"and requires investigation."
                ),
                url=url,
            )
        incident.last_notified_at = timezone.now()
        incident.save(update_fields=["last_notified_at", "updated_at"])
        AuditEvent.record(
            actor=None,
            action=AuditEvent.Action.LATE_RETURN_OPENED,
            target_type="LateReturnIncident",
            target_id=incident.pk,
            summary=incident.summary[:255],
            after={
                "escalation_level": level,
                "actual_return": actual_return.isoformat() if actual_return else None,
            },
        )
    return incident


@transaction.atomic
def resolve_late_return_incident(incident, *, actor, resolution, status=None):
    resolution = (resolution or "").strip()
    if not resolution:
        raise ValueError("Record the investigation outcome before closing.")
    status = status or LateReturnIncident.Status.RESOLVED
    if status not in (
        LateReturnIncident.Status.INVESTIGATING,
        LateReturnIncident.Status.RESOLVED,
        LateReturnIncident.Status.CLOSED,
    ):
        raise ValueError("Choose a valid incident status.")
    locked = LateReturnIncident.objects.select_for_update().get(pk=incident.pk)
    locked.status = status
    locked.resolution = resolution
    locked.resolved_by = actor
    locked.resolved_at = timezone.now()
    locked.save()
    AuditEvent.record(
        actor=actor,
        action=AuditEvent.Action.LATE_RETURN_UPDATED,
        target_type="LateReturnIncident",
        target_id=locked.pk,
        summary=f"Late-return incident {locked.get_status_display()}: {resolution[:180]}",
    )
    return locked
