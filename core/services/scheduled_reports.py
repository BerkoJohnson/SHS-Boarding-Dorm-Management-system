"""Opt-in report-email scheduling and delivery helpers."""

import logging
import re
from datetime import datetime, timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMessage
from django.db import transaction
from django.utils import timezone

from core.models import AuditEvent, ScheduledReportEmail, StaffRole, get_user_profile
from core.permissions import REPORT_ROLES, get_house_scope
from core.services.report_export import export_report_response
from core.services.reports import get_detailed_report
from tenancy.entitlements import feature_enabled

logger = logging.getLogger(__name__)


def next_report_run(frequency, send_at, weekday, *, after=None):
    """Return the next local scheduled instant strictly after ``after``."""
    now = timezone.localtime(after or timezone.now())
    days_until = 0
    if frequency == ScheduledReportEmail.Frequency.WEEKLY:
        days_until = (int(weekday) - now.weekday()) % 7
    run_date = now.date() + timedelta(days=days_until)
    candidate = timezone.make_aware(
        datetime.combine(run_date, send_at),
        timezone=now.tzinfo,
    )
    if candidate <= now:
        candidate += timedelta(
            days=7 if frequency == ScheduledReportEmail.Frequency.WEEKLY else 1
        )
    return candidate


def _report_period(subscription, today):
    end_date = today - timedelta(days=1)
    if subscription.frequency == ScheduledReportEmail.Frequency.DAILY:
        return end_date, end_date
    return end_date - timedelta(days=6), end_date


def _filename(response):
    disposition = response.get("Content-Disposition", "")
    match = re.search(r'filename="([^\"]+)"', disposition)
    return match.group(1) if match else "scheduled-report.csv"


def _deliver_report(subscription):
    user = subscription.user
    profile = get_user_profile(user)
    if not user.is_active or not user.email:
        raise ValueError("An active staff account with an email address is required.")
    if not user.is_superuser and (profile is None or profile.role not in REPORT_ROLES):
        raise PermissionDenied("The subscriber no longer has report access.")
    if settings.EMAIL_BACKEND.endswith(".console.EmailBackend"):
        raise RuntimeError("Scheduled reports require a non-console email backend.")
    if not feature_enabled("advanced_reports"):
        raise PermissionDenied("Advanced reports are not enabled for this school.")

    house = get_house_scope(user)
    today = timezone.localdate()
    date_from, date_to = _report_period(subscription, today)
    report = get_detailed_report(
        subscription.report_type,
        house_id=house.pk if house else None,
        date_from=date_from,
        date_to=date_to,
    )
    export = export_report_response(
        report,
        file_format=subscription.file_format,
        actor=user,
        filters={
            "report": subscription.report_type,
            "scope": house.name if house else "School-wide",
            "date_from": date_from,
            "date_to": date_to,
            "frequency": subscription.frequency,
        },
        purpose="Opt-in scheduled report email",
    )
    attachment_name = _filename(export)
    content_type = export.get("Content-Type", "application/octet-stream").split(";", 1)[
        0
    ]
    message = EmailMessage(
        subject=f"{report.get('title', 'School report')} · {date_from:%Y-%m-%d} to {date_to:%Y-%m-%d}",
        body=(
            "Your scheduled school report is attached. It contains only the data "
            "available within your current staff permissions."
        ),
        to=[user.email],
    )
    message.attach(attachment_name, export.content, content_type)
    if message.send(fail_silently=False) != 1:
        raise RuntimeError("The configured email backend did not accept the report.")

    AuditEvent.record(
        actor=user,
        action=AuditEvent.Action.EXPORT,
        summary="Scheduled report emailed to its subscribing staff member.",
        target_type="ScheduledReportEmail",
        target_id=subscription.pk,
        metadata={
            "report": subscription.report_type,
            "frequency": subscription.frequency,
            "format": subscription.file_format,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
    )


def dispatch_due_report_emails(*, batch_size=100):
    """Claim and deliver due report subscriptions in the current tenant."""
    now = timezone.now()
    due_ids = list(
        ScheduledReportEmail.objects.filter(
            is_active=True,
            next_run_at__lte=now,
        )
        .order_by("next_run_at")
        .values_list("pk", flat=True)[:batch_size]
    )
    sent = 0
    failed = 0
    for subscription_id in due_ids:
        with transaction.atomic():
            subscription = (
                ScheduledReportEmail.objects.select_for_update(skip_locked=True)
                .filter(pk=subscription_id, is_active=True, next_run_at__lte=now)
                .select_related("user")
                .first()
            )
            if subscription is None:
                continue
            subscription.next_run_at = next_report_run(
                subscription.frequency,
                subscription.send_at,
                subscription.weekday,
                after=now,
            )
            subscription.last_error = ""
            subscription.save(update_fields=("next_run_at", "last_error", "updated_at"))

        try:
            _deliver_report(subscription)
        except Exception:
            logger.exception(
                "Scheduled report delivery failed for subscription %s",
                subscription_id,
            )
            ScheduledReportEmail.objects.filter(pk=subscription_id).update(
                last_error="Delivery failed; review the email configuration and staff access.",
                updated_at=timezone.now(),
            )
            failed += 1
        else:
            ScheduledReportEmail.objects.filter(pk=subscription_id).update(
                last_sent_at=timezone.now(),
                last_error="",
                updated_at=timezone.now(),
            )
            sent += 1
    return {"sent": sent, "failed": failed}
