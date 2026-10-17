from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from core.models import (
    Dormitory,
    Exeat,
    ExeatMovement,
    Guardian,
    House,
    HouseMaster,
    Programme,
    SMSDelivery,
    SMSConfiguration,
    ConfigurationAuditLog,
    Student,
    UserProfile,
    StaffRole,
)
from core.services.sms import (
    build_exeat_event_message,
    enqueue_exeat_event_sms,
    get_exeat_sms_targets,
    normalize_phone_number,
)
from core.tasks import enqueue_overdue_exeat_reminders, send_sms_delivery_task

User = get_user_model()


class ExeatSMSTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(name="Red House")
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name="Block A",
            gender=Dormitory.GenderBlock.MALE,
            capacity=10,
        )
        self.programme = Programme.objects.create(name="General Science", code="GSCI")
        self.guardian = Guardian.objects.create(
            full_name="Guardian One",
            phone_number="0240000000",
            alt_phone_number="+233240000001",
        )
        self.student = Student.objects.create(
            admission_no="SMS001",
            index_number="SMSI001",
            full_name="Student One",
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
            guardian=self.guardian,
        )
        self.housemaster = HouseMaster.objects.create(
            full_name="Housemaster One",
            staff_id="SMS-HM-001",
            phone_number="0240000002",
            house=self.house,
        )
        now = timezone.now()
        self.exeat = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            reason="Family visit",
            destination="Accra",
            expected_departure=now - timezone.timedelta(days=4),
            expected_return=now + timezone.timedelta(hours=2),
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster,
        )
        SMSConfiguration.objects.update_or_create(pk=1, defaults={"enabled": True})

    def test_normalizes_local_and_international_ghana_numbers(self):
        self.assertEqual(normalize_phone_number("024 000 0000"), "+233240000000")
        self.assertEqual(normalize_phone_number("+233 24 000 0000"), "+233240000000")
        self.assertEqual(normalize_phone_number("invalid"), "")

    def test_admin_sms_setting_overrides_environment_default(self):
        SMSConfiguration.objects.update(enabled=False)
        with override_settings(SMS_ENABLED=True):
            self.assertFalse(SMSConfiguration.is_enabled())

        SMSConfiguration.objects.update(enabled=True)
        with override_settings(SMS_ENABLED=False):
            self.assertTrue(SMSConfiguration.is_enabled())

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.entitlements._current_schema_name", return_value="sms-tenant")
    @patch(
        "tenancy.entitlements._subscription",
        return_value=SimpleNamespace(
            features={"sms_dispatch": False},
            plan_tier="pilot",
        ),
    )
    def test_platform_sms_entitlement_overrides_local_enabled_switch(
        self, _subscription, _schema_name
    ):
        self.assertFalse(SMSConfiguration.is_enabled())

    @override_settings(TENANCY_ENABLED=True, SMS_ONLINE_GH_API_KEY="test-key")
    @patch("core.services.sms.requests.post")
    @patch("tenancy.entitlements._current_schema_name", return_value="sms-tenant")
    @patch(
        "tenancy.entitlements._subscription",
        return_value=SimpleNamespace(
            features={"sms_dispatch": False},
            plan_tier="pilot",
        ),
    )
    def test_platform_sms_entitlement_blocks_direct_sender(
        self, _subscription, _schema_name, post
    ):
        from core.services.sms import send_sms

        self.assertFalse(send_sms("+233240000000", "Test message"))
        post.assert_not_called()

    @patch("core.services.sms.sms_quota_allows", return_value=False)
    @patch("core.services.sms.requests.post")
    def test_monthly_quota_blocks_direct_sender(self, post, _quota):
        from core.services.sms import send_sms

        self.assertFalse(send_sms("+233240000000", "Test message"))
        post.assert_not_called()

    def test_disabled_setting_blocks_new_sms_outbox_records(self):
        SMSConfiguration.objects.update(enabled=False)
        with override_settings(SMS_ENABLED=True):
            deliveries = enqueue_exeat_event_sms(
                self.exeat,
                SMSDelivery.EventType.DEPARTURE,
            )

        self.assertEqual(deliveries, [])
        self.assertEqual(SMSDelivery.objects.count(), 0)

    def test_exeat_notifications_target_guardians_and_assigned_housemaster(self):
        HouseMaster.objects.create(
            full_name="Senior Housemaster",
            staff_id="SMS-SENIOR-001",
            phone_number="0240000003",
            house=self.house,
            is_senior_housemaster=True,
        )
        targets = get_exeat_sms_targets(
            self.exeat,
            SMSDelivery.EventType.DEPARTURE,
        )

        self.assertEqual(
            {number for _name, number, _audience in targets},
            {"+233240000000", "+233240000001", "+233240000002"},
        )

    def test_guardian_messages_are_clear_and_concise(self):
        event_phrases = {
            SMSDelivery.EventType.REQUESTED: "awaiting school approval",
            SMSDelivery.EventType.APPROVED: "leave is approved",
            SMSDelivery.EventType.REJECTED: "was not approved",
            SMSDelivery.EventType.DEPARTURE: "has left school",
            SMSDelivery.EventType.RETURN: "has returned to school",
            SMSDelivery.EventType.OVERDUE: "has not returned on time",
        }
        for event_type, phrase in event_phrases.items():
            with self.subTest(event_type=event_type):
                message = build_exeat_event_message(self.exeat, event_type)
                self.assertIn("Student One", message)
                self.assertIn(phrase, message)
                self.assertNotIn("Exeat ref", message)
                self.assertLess(len(message), 160)

        departure_message = build_exeat_event_message(
            self.exeat,
            SMSDelivery.EventType.DEPARTURE,
        )
        self.assertIn("Return by", departure_message)

    @override_settings(SMS_ENABLED=True)
    @patch("core.services.sms.enqueue_sms_delivery_task")
    def test_event_is_deduplicated_per_recipient(self, queue_task):
        with self.captureOnCommitCallbacks(execute=True):
            first = enqueue_exeat_event_sms(
                self.exeat,
                SMSDelivery.EventType.DEPARTURE,
            )
            second = enqueue_exeat_event_sms(
                self.exeat,
                SMSDelivery.EventType.DEPARTURE,
            )

        self.assertEqual(len(first), 3)
        self.assertEqual(len(second), 3)
        self.assertEqual(SMSDelivery.objects.count(), 3)
        self.assertEqual(queue_task.call_count, 3)

    @override_settings(SMS_ENABLED=True)
    @patch("core.services.sms.enqueue_sms_delivery_task")
    def test_overdue_reminder_is_once_per_24_hour_period(self, queue_task):
        self.exeat.expected_return = timezone.now() - timezone.timedelta(days=2)
        self.exeat.expected_departure = timezone.now() - timezone.timedelta(days=4)
        self.exeat.save()
        ExeatMovement.objects.create(
            exeat=self.exeat,
            actual_departure=timezone.now() - timezone.timedelta(days=2),
        )

        with self.captureOnCommitCallbacks(execute=True):
            first_count = enqueue_overdue_exeat_reminders.run()
            duplicate_count = enqueue_overdue_exeat_reminders.run()

        self.exeat.expected_return -= timezone.timedelta(days=1)
        self.exeat.save()
        with self.captureOnCommitCallbacks(execute=True):
            next_day_count = enqueue_overdue_exeat_reminders.run()

        self.assertEqual(first_count, 3)
        self.assertEqual(duplicate_count, 0)
        self.assertEqual(next_day_count, 3)
        self.assertEqual(
            SMSDelivery.objects.filter(event_type=SMSDelivery.EventType.OVERDUE).count(),
            6,
        )

    @override_settings(SMS_ENABLED=True)
    @patch("core.services.sms.send_sms", return_value=True)
    def test_successful_delivery_updates_outbox(self, send_sms):
        delivery = SMSDelivery.objects.create(
            exeat=self.exeat,
            event_type=SMSDelivery.EventType.APPROVED,
            recipient_name="Guardian One",
            phone_number="+233240000000",
            message="Exeat approved",
            dedupe_key=f"approved:{self.exeat.pk}:+233240000000:",
        )

        self.assertTrue(send_sms_delivery_task.run(str(delivery.pk)))
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SMSDelivery.Status.SENT)
        self.assertEqual(delivery.attempts, 1)
        self.assertIsNotNone(delivery.sent_at)
        send_sms.assert_called_once_with(
            delivery.phone_number,
            delivery.message,
            delivery_id=str(delivery.pk),
        )

    @override_settings(SMS_ENABLED=True, SMS_MAX_ATTEMPTS=2)
    @patch("core.services.sms.send_sms", return_value=False)
    def test_failed_delivery_uses_settings_retry_budget(self, send_sms):
        delivery = SMSDelivery.objects.create(
            exeat=self.exeat,
            event_type=SMSDelivery.EventType.APPROVED,
            recipient_name="Guardian One",
            phone_number="+233240000000",
            message="Exeat approved",
            dedupe_key=f"approved:{self.exeat.pk}:+233240000000:retry-budget",
        )

        with self.assertRaises(RuntimeError):
            send_sms_delivery_task.run(str(delivery.pk))

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SMSDelivery.Status.QUEUED)
        self.assertEqual(delivery.attempts, 1)
        send_sms.assert_called_once_with(
            delivery.phone_number, delivery.message, delivery_id=str(delivery.pk)
        )

    @override_settings(TENANCY_ENABLED=False)
    def test_delivery_task_ignores_malformed_identifier(self):
        self.assertFalse(send_sms_delivery_task.run("delivery-uuid-123"))


class SMSConfigurationViewTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username="sms_config_admin",
            password="password123",
        )
        self.configuration, _created = SMSConfiguration.objects.get_or_create(
            pk=1,
            defaults={"enabled": True},
        )
        if not self.configuration.enabled:
            self.configuration.enabled = True
            self.configuration.save(update_fields=["enabled", "updated_at"])

    def test_configuration_hub_shows_current_sms_state(self):
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("core:configuration"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["sms_enabled"])
        self.assertContains(response, "Disable SMS")

    def test_superuser_can_disable_sms_from_configuration_hub(self):
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("core:set_sms_enabled"),
            {"enabled": "false"},
        )

        self.assertRedirects(response, reverse("core:configuration"))
        self.configuration.refresh_from_db()
        self.assertFalse(self.configuration.enabled)
        audit_event = ConfigurationAuditLog.objects.get(
            event=ConfigurationAuditLog.Event.SMS_STATE_CHANGED,
        )
        self.assertEqual(audit_event.actor, self.admin_user)
        self.assertEqual(
            audit_event.details,
            {"previous_enabled": True, "enabled": False},
        )
        audit_response = self.client.get(reverse("core:configuration"))
        self.assertContains(audit_response, "SMS notifications disabled.")

    def test_regular_user_cannot_change_sms_state(self):
        user = User.objects.create_user(
            username="sms_config_user",
            password="password123",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("core:set_sms_enabled"),
            {"enabled": "false"},
        )

        self.assertEqual(response.status_code, 302)
        self.configuration.refresh_from_db()
        self.assertTrue(self.configuration.enabled)

    def test_senior_housemaster_cannot_change_sms_state(self):
        senior_user = User.objects.create_user(
            username="sms_config_senior",
            password="password123",
        )
        UserProfile.objects.filter(user=senior_user).update(
            role=StaffRole.SENIOR_HOUSEMASTER,
        )
        self.client.force_login(senior_user)

        response = self.client.post(
            reverse("core:set_sms_enabled"),
            {"enabled": "false"},
        )

        self.assertRedirects(response, reverse("core:dashboard"))
        self.configuration.refresh_from_db()
        self.assertTrue(self.configuration.enabled)
        self.assertFalse(
            ConfigurationAuditLog.objects.filter(
                event=ConfigurationAuditLog.Event.SMS_STATE_CHANGED,
            ).exists()
        )
