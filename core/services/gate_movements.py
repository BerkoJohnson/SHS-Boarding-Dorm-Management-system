"""Serialized gate movement recording for approved exeat passes."""

from django.db import transaction
from django.utils import timezone

from core.models import Exeat, ExeatMovement, ExeatReconciliation


@transaction.atomic
def record_gate_departure(
    exeat,
    *,
    actor,
    recorded_by,
    at=None,
    reconcile_roll_calls=False,
):
    """Record one departure without allowing a concurrent request to replace it."""
    at = at or timezone.now()
    locked_exeat = Exeat.objects.select_for_update().get(pk=exeat.pk)
    if locked_exeat.status != Exeat.Status.APPROVED:
        raise ValueError("Only approved exeats can have gate departure logged.")
    if ExeatReconciliation.objects.filter(exeat=locked_exeat).exists():
        raise ValueError("This pass has already been reconciled.")
    if at < locked_exeat.expected_departure:
        raise ValueError(
            "Gate departure cannot be logged before the scheduled departure time."
        )

    movement, _ = ExeatMovement.objects.get_or_create(exeat=locked_exeat)
    movement = ExeatMovement.objects.select_for_update().get(pk=movement.pk)
    if movement.actual_departure is not None:
        return movement, False, 0

    movement.actual_departure = at
    movement.departure_recorded_by = recorded_by
    movement.departure_recorded_by_user = actor
    movement.save()

    reconciled_count = 0
    if reconcile_roll_calls:
        from core.services.roll_call import reconcile_gate_departure_with_roll_calls

        reconciled_count = reconcile_gate_departure_with_roll_calls(
            locked_exeat,
            movement,
        )
    return movement, True, reconciled_count


@transaction.atomic
def record_gate_return(exeat, *, actor, recorded_by, at=None):
    """Record one return, serializing both movement and reconciliation records."""
    at = at or timezone.now()
    locked_exeat = Exeat.objects.select_for_update().get(pk=exeat.pk)
    if locked_exeat.status != Exeat.Status.APPROVED:
        raise ValueError("Only approved exeats can have gate return logged.")

    reconciliation = (
        ExeatReconciliation.objects.select_for_update()
        .filter(
            exeat=locked_exeat,
            outcome__in=(
                ExeatReconciliation.Outcome.STUDENT_OUT,
                ExeatReconciliation.Outcome.RETURNED,
            ),
        )
        .first()
    )
    if reconciliation is not None:
        if reconciliation.actual_return is not None:
            return reconciliation, False
        reconciliation.actual_return = at
        reconciliation.outcome = ExeatReconciliation.Outcome.RETURNED
        reconciliation.return_recorded_by = actor
        reconciliation.save()
        return reconciliation, True

    movement = (
        ExeatMovement.objects.select_for_update()
        .filter(exeat=locked_exeat)
        .first()
    )
    if movement is None or movement.actual_departure is None:
        return None, False
    if movement.actual_return is not None:
        return movement, False

    movement.actual_return = at
    movement.return_recorded_by = recorded_by
    movement.return_recorded_by_user = actor
    movement.save()
    return movement, True
