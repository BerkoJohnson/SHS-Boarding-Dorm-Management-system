"""Deployment checks for background-job configuration.

The Render staging blueprint intentionally does not deploy a Celery worker or
Beat. That is safe only when the deployment knows which features are dormant.
These checks make the trade-off visible during ``manage.py check`` and deploys.
"""

from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def background_jobs_check(app_configs, **kwargs):
    """Warn when scheduled work is disabled and reject an SMS misconfiguration."""
    if getattr(settings, "BACKGROUND_JOBS_ENABLED", True):
        return []

    messages = [
        Warning(
            "Background jobs are disabled: SMS dispatch, overdue-exeat "
            "reminders, roll-call expiry, student ID export cleanup, and the "
            "SHS 3 academic-year phase-out will not run automatically.",
            hint=(
                "Set BACKGROUND_JOBS_ENABLED=True and deploy a Celery worker "
                "and Beat, or run the equivalent management tasks manually."
            ),
            id="core.W003",
        )
    ]
    if getattr(settings, "SMS_ENABLED", False):
        messages.append(
            Error(
                "SMS_ENABLED=True but BACKGROUND_JOBS_ENABLED=False. Outbound "
                "SMS deliveries would remain queued indefinitely because no "
                "Celery worker is expected to run.",
                hint=(
                    "Disable SMS_ENABLED or enable background jobs and deploy "
                    "both a worker and Beat before accepting live traffic."
                ),
                id="core.E004",
            )
        )
    return messages
