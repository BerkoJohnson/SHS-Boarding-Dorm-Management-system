from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AuditEvent,
    DataExportLog,
    ScheduledReportEmail,
)
from core.services.scheduled_reports import (
    dispatch_due_report_emails,
    next_report_run,
)

User = get_user_model()


class ScheduledReportTimeTests(SimpleTestCase):
    def test_daily_schedule_moves_to_tomorrow_after_send_time(self):
        after = timezone.make_aware(datetime(2026, 10, 23, 8, 0))

        result = next_report_run(
            ScheduledReportEmail.Frequency.DAILY,
            time(7, 0),
            ScheduledReportEmail.Weekday.MONDAY,
            after=after,
        )

        self.assertEqual(
            timezone.localtime(result),
            timezone.make_aware(datetime(2026, 10, 24, 7, 0)),
        )

    def test_weekly_schedule_uses_next_selected_weekday(self):
        after = timezone.make_aware(datetime(2026, 10, 23, 8, 0))

        result = next_report_run(
            ScheduledReportEmail.Frequency.WEEKLY,
            time(7, 0),
            ScheduledReportEmail.Weekday.FRIDAY,
            after=after,
        )

        self.assertEqual(
            timezone.localtime(result),
            timezone.make_aware(datetime(2026, 10, 30, 7, 0)),
        )


class ScheduledReportEmailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username="scheduled_report_admin",
            email="admin@example.test",
            password="password123",
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_due_report_is_sent_to_subscriber_and_audited(self):
        subscription = ScheduledReportEmail.objects.create(
            user=self.user,
            report_type=ScheduledReportEmail.ReportType.DATA_COMPLETENESS,
            frequency=ScheduledReportEmail.Frequency.DAILY,
            send_at=time(7, 0),
            next_run_at=timezone.now() - timedelta(minutes=1),
        )

        result = dispatch_due_report_emails()

        self.assertEqual(result, {"sent": 1, "failed": 0})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertEqual(len(mail.outbox[0].attachments), 1)
        self.assertTrue(mail.outbox[0].attachments[0][0].endswith(".csv"))
        subscription.refresh_from_db()
        self.assertIsNotNone(subscription.last_sent_at)
        self.assertGreater(subscription.next_run_at, timezone.now())
        self.assertTrue(
            DataExportLog.objects.filter(
                actor=self.user,
                purpose="Opt-in scheduled report email",
            ).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                actor=self.user,
                action=AuditEvent.Action.EXPORT,
                target_type="ScheduledReportEmail",
            ).exists()
        )

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_console_backend_does_not_print_report_attachments(self):
        subscription = ScheduledReportEmail.objects.create(
            user=self.user,
            report_type=ScheduledReportEmail.ReportType.DATA_COMPLETENESS,
            frequency=ScheduledReportEmail.Frequency.DAILY,
            send_at=time(7, 0),
            next_run_at=timezone.now() - timedelta(minutes=1),
        )

        result = dispatch_due_report_emails()

        self.assertEqual(result, {"sent": 0, "failed": 1})
        subscription.refresh_from_db()
        self.assertIn("Delivery failed", subscription.last_error)
        self.assertFalse(DataExportLog.objects.exists())

    def test_subscription_settings_are_owned_and_deactivation_preserves_history(self):
        self.client.force_login(self.user)
        payload = {
            "report_type": ScheduledReportEmail.ReportType.HOUSING,
            "frequency": ScheduledReportEmail.Frequency.WEEKLY,
            "weekday": str(ScheduledReportEmail.Weekday.WEDNESDAY),
            "send_at": "08:00",
            "file_format": ScheduledReportEmail.FileFormat.XLSX,
        }

        response = self.client.post(reverse("core:scheduled_report_emails"), payload)

        self.assertRedirects(response, reverse("core:scheduled_report_emails"))
        subscription = ScheduledReportEmail.objects.get(user=self.user)
        self.assertEqual(subscription.file_format, ScheduledReportEmail.FileFormat.XLSX)

        response = self.client.post(
            reverse("core:scheduled_report_emails"),
            {"action": "deactivate", "subscription_id": subscription.pk},
        )

        self.assertRedirects(response, reverse("core:scheduled_report_emails"))
        subscription.refresh_from_db()
        self.assertFalse(subscription.is_active)
        self.assertEqual(ScheduledReportEmail.objects.filter(user=self.user).count(), 1)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_subscription_never_accepts_another_recipient_address(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("core:scheduled_report_emails"),
            {
                "report_type": ScheduledReportEmail.ReportType.HOUSING,
                "frequency": ScheduledReportEmail.Frequency.DAILY,
                "send_at": "07:00",
                "file_format": ScheduledReportEmail.FileFormat.CSV,
                "email": "external@example.test",
            },
        )

        self.assertRedirects(response, reverse("core:scheduled_report_emails"))
        subscription = ScheduledReportEmail.objects.get(user=self.user)
        subscription.next_run_at = timezone.now() - timedelta(minutes=1)
        subscription.save(update_fields=("next_run_at", "updated_at"))

        self.assertEqual(dispatch_due_report_emails(), {"sent": 1, "failed": 0})
        self.assertEqual(mail.outbox[-1].to, [self.user.email])
        self.assertNotIn("external@example.test", mail.outbox[-1].to)
