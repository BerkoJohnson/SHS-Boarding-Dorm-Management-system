"""Tenant operational settings and small policy helpers.

The helpers in this module deliberately read the singleton on demand rather than
caching it.  A settings change must take effect for the next task/request without
requiring a worker restart, and tests frequently override the values.
"""

from datetime import timedelta

from django.utils import timezone

from core.models import GateDutyAssignment, OperationalSettings


def get_operational_settings():
    """Return the tenant operational settings, or safe defaults if unset."""
    return OperationalSettings.get_solo()


def roll_call_timeout():
    """Return the configured roll-call completion window as a timedelta."""
    return timedelta(minutes=get_operational_settings().roll_call_timeout_minutes)


def late_return_thresholds():
    """Return the configured late-return thresholds as timedeltas."""
    settings = get_operational_settings()
    return {
        "incident": timedelta(
            minutes=settings.late_return_incident_after_minutes
        ),
        "senior": timedelta(
            minutes=settings.senior_housemaster_escalation_after_minutes
        ),
        "headmaster": timedelta(
            minutes=settings.headmaster_escalation_after_minutes
        ),
    }


def active_gate_assignment(user, at=None):
    """Return the user's active gate-duty assignment, if any.

    A missing roster is not treated as an active assignment.  Enforcement is
    opt-in through ``OperationalSettings.gate_duty_enforcement_enabled`` so a
    pilot can introduce the roster without locking every gate officer out.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return None
    at = at or timezone.now()
    return (
        GateDutyAssignment.objects.filter(
            user=user,
            cancelled_at__isnull=True,
            starts_at__lte=at,
            ends_at__gte=at,
        )
        .order_by("-starts_at")
        .first()
    )


def gate_duty_enforcement_enabled():
    return get_operational_settings().gate_duty_enforcement_enabled
