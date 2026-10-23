from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse

from core.models import (
    Dormitory,
    Exeat,
    ExeatMovement,
    GroupSMSCampaign,
    Guardian,
    House,
    HouseMaster,
    Programme,
    SMSDelivery,
    SMSConfiguration,
    StaffNotification,
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
    sms_segment_count,
)
from core.services.sms_alerts import notify_sms_quota_low
from core.services.group_sms import (
    create_group_sms_campaign,
    decide_group_sms_campaign,
)
from core.tasks import (
    dispatch_queued_sms_deliveries,
    enqueue_overdue_exeat_reminders,
    send_sms_delivery_task,
)
from tenancy.entitlements import sms_monthly_usage

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

    def test_sms_segment_count_handles_gsm_and_unicode_boundaries(self):
        self.assertEqual(sms_segment_count("A" * 160), 1)
        self.assertEqual(sms_segment_count("A" * 161), 2)
        self.assertEqual(sms_segment_count("^" * 77), 2)
        self.assertEqual(sms_segment_count("😀" * 35), 1)
        self.assertEqual(sms_segment_count("😀" * 36), 2)

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

    @patch("core.services.sms.sms_quota_allows", return_value=False)
    @patch("core.services.sms.requests.post")
    def test_direct_send_reserves_all_message_segments(self, post, quota_check):
        from core.services.sms import send_sms

        self.assertFalse(send_sms("+233240000000", "A" * 200))

        quota_check.assert_called_once_with(2, exclude_delivery_id=None)
        post.assert_not_called()

    def test_sms_quota_counts_segments_and_ignores_stale_reservations(self):
        now = timezone.now()
        SMSDelivery.objects.create(
            exeat=self.exeat,
            event_type=SMSDelivery.EventType.APPROVED,
            recipient_name="Guardian One",
            phone_number="+233240000000",
            message="A" * 200,
            status=SMSDelivery.Status.SENT,
            sent_at=now,
            dedupe_key=f"segments:{self.exeat.pk}",
        )
        SMSDelivery.objects.create(
            exeat=self.exeat,
            event_type=SMSDelivery.EventType.APPROVED,
            recipient_name="Guardian One",
            phone_number="+233240000000",
            message="B" * 200,
            status=SMSDelivery.Status.QUEUED,
            dedupe_key=f"current-queue:{self.exeat.pk}",
        )
        stale = SMSDelivery.objects.create(
            exeat=self.exeat,
            event_type=SMSDelivery.EventType.APPROVED,
            recipient_name="Guardian One",
            phone_number="+233240000000",
            message="Stuck queued message",
            status=SMSDelivery.Status.QUEUED,
            dedupe_key=f"stale-queue:{self.exeat.pk}",
        )
        SMSDelivery.objects.filter(pk=stale.pk).update(
            created_at=now - timezone.timedelta(days=2)
        )

        self.assertEqual(sms_monthly_usage(), 4)

    @patch("core.services.sms.enqueue_sms_delivery_task")
    def test_dispatch_expires_stale_queued_delivery(self, enqueue_delivery):
        delivery = SMSDelivery.objects.create(
            exeat=self.exeat,
            event_type=SMSDelivery.EventType.APPROVED,
            recipient_name="Guardian One",
            phone_number="+233240000000",
            message="Stuck queued message",
            status=SMSDelivery.Status.QUEUED,
            dedupe_key=f"stale-dispatch:{self.exeat.pk}",
        )
        SMSDelivery.objects.filter(pk=delivery.pk).update(
            created_at=timezone.now() - timezone.timedelta(days=2)
        )

        dispatch_queued_sms_deliveries.run()

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SMSDelivery.Status.FAILED)
        enqueue_delivery.assert_not_called()

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
            SMSDelivery.objects.filter(
                event_type=SMSDelivery.EventType.OVERDUE
            ).count(),
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

    @override_settings(SMS_ENABLED=True, SMS_MAX_ATTEMPTS=1)
    @patch("core.services.sms.send_sms", return_value=False)
    def test_terminal_delivery_failure_notifies_administrators(self, send_sms):
        administrator = User.objects.create_superuser(
            username="sms_failure_admin",
            password="password123",
        )
        delivery = SMSDelivery.objects.create(
            exeat=self.exeat,
            event_type=SMSDelivery.EventType.APPROVED,
            recipient_name="Guardian One",
            phone_number="+233240000000",
            message="Exeat approved",
            dedupe_key=f"approved:{self.exeat.pk}:terminal-failure",
        )

        self.assertFalse(send_sms_delivery_task.run(str(delivery.pk)))

        delivery.refresh_from_db()
        self.assertEqual(delivery.status, SMSDelivery.Status.FAILED)
        alert = StaffNotification.objects.get(
            user=administrator,
            kind=StaffNotification.Kind.SMS_ALERT,
        )
        self.assertIn("failed after 1 attempts", alert.message)
        send_sms.assert_called_once()

    @patch("tenancy.entitlements.quota_limit", return_value=5000)
    @patch("tenancy.entitlements.sms_monthly_usage", return_value=4200)
    def test_low_monthly_quota_notifies_administrators_once(self, _usage, _limit):
        administrator = User.objects.create_superuser(
            username="sms_quota_admin",
            password="password123",
        )

        self.assertTrue(notify_sms_quota_low())
        self.assertTrue(notify_sms_quota_low())

        alerts = StaffNotification.objects.filter(
            user=administrator,
            kind=StaffNotification.Kind.SMS_ALERT,
        )
        self.assertEqual(alerts.count(), 1)
        self.assertIn("800 of 5000", alerts.get().message)

    @override_settings(SMS_ENABLED=True)
    @patch("core.services.group_sms.enqueue_sms_delivery_task")
    def test_group_sms_requires_second_person_approval_and_queues_deliveries(
        self, queue_delivery
    ):
        requester = User.objects.create_superuser(
            username="sms_campaign_requester",
            password="password123",
        )
        approver = User.objects.create_superuser(
            username="sms_campaign_approver",
            password="password123",
        )
        campaign = create_group_sms_campaign(
            actor=requester,
            scope=GroupSMSCampaign.Scope.ALL_BOARDERS,
            house=None,
            message="School closes at 3 pm today.",
        )

        self.assertEqual(campaign.status, GroupSMSCampaign.Status.PENDING)
        self.assertEqual(campaign.recipient_count, 2)
        with self.captureOnCommitCallbacks(execute=True):
            approved = decide_group_sms_campaign(
                campaign_id=campaign.pk,
                actor=approver,
                approve=True,
            )

        self.assertEqual(approved.status, GroupSMSCampaign.Status.APPROVED)
        self.assertEqual(approved.decided_by, approver)
        self.assertEqual(approved.deliveries.count(), 2)
        self.assertEqual(queue_delivery.call_count, 2)
        self.assertTrue(
            approved.deliveries.filter(
                event_type=SMSDelivery.EventType.GROUP,
                status=SMSDelivery.Status.QUEUED,
            ).exists()
        )

    def test_group_sms_cannot_be_self_approved(self):
        requester = User.objects.create_superuser(
            username="sms_campaign_self_approver",
            password="password123",
        )
        campaign = create_group_sms_campaign(
            actor=requester,
            scope=GroupSMSCampaign.Scope.ALL_BOARDERS,
            house=None,
            message="School closes at 3 pm today.",
        )

        with self.assertRaisesMessage(ValidationError, "different administrator"):
            decide_group_sms_campaign(
                campaign_id=campaign.pk,
                actor=requester,
                approve=True,
            )

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, GroupSMSCampaign.Status.PENDING)
        self.assertEqual(campaign.deliveries.count(), 0)

    def test_group_sms_denies_senior_housemaster_without_headmaster_role(self):
        senior = User.objects.create_user(
            username="sms_campaign_senior",
            password="password123",
        )
        UserProfile.objects.update_or_create(
            user=senior,
            defaults={"role": StaffRole.SENIOR_HOUSEMASTER},
        )

        with self.assertRaisesMessage(PermissionDenied, "Headmaster account"):
            create_group_sms_campaign(
                actor=senior,
                scope=GroupSMSCampaign.Scope.ALL_BOARDERS,
                house=None,
                message="School closes at 3 pm today.",
            )

    @patch("core.services.group_sms.sms_quota_allows", return_value=False)
    def test_group_sms_approval_stops_when_quota_is_insufficient(self, _quota):
        requester = User.objects.create_superuser(
            username="sms_campaign_quota_requester",
            password="password123",
        )
        approver = User.objects.create_superuser(
            username="sms_campaign_quota_approver",
            password="password123",
        )
        campaign = create_group_sms_campaign(
            actor=requester,
            scope=GroupSMSCampaign.Scope.ALL_BOARDERS,
            house=None,
            message="School closes at 3 pm today.",
        )

        with self.assertRaisesMessage(ValidationError, "monthly SMS quota"):
            decide_group_sms_campaign(
                campaign_id=campaign.pk,
                actor=approver,
                approve=True,
            )

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, GroupSMSCampaign.Status.PENDING)
        self.assertEqual(campaign.deliveries.count(), 0)

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

    def test_admin_can_preview_group_sms_without_creating_campaign(self):
        house = House.objects.create(name="Group SMS Preview House")
        dormitory = Dormitory.objects.create(
            house=house,
            name="Group SMS Preview Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=2,
        )
        programme = Programme.objects.create(name="Group SMS Preview Programme")
        guardian = Guardian.objects.create(
            full_name="Preview Guardian",
            phone_number="0240000100",
            alt_phone_number="0240000101",
        )
        Student.objects.create(
            admission_no="GROUP-SMS-PREVIEW",
            index_number="GROUP-SMS-PREVIEW-1",
            full_name="Preview Student",
            gender=Student.Gender.MALE,
            programme=programme,
            house=house,
            dormitory=dormitory,
            guardian=guardian,
        )
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("core:group_sms"),
            {
                "scope": GroupSMSCampaign.Scope.ALL_BOARDERS,
                "message": "School closes at 3 pm today.",
                "action": "preview",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.context["form"].is_valid(), response.context["form"].errors
        )
        self.assertEqual(response.context["estimated_recipients"], 2)
        self.assertEqual(len(response.context["preview_recipients"]), 2)
        self.assertNotContains(response, "0240000100")
        self.assertEqual(GroupSMSCampaign.objects.count(), 0)

    def test_sms_usage_page_displays_unread_sms_alerts(self):
        StaffNotification.objects.create(
            user=self.admin_user,
            kind=StaffNotification.Kind.SMS_ALERT,
            title="SMS quota is running low",
            message="800 of 5000 monthly SMS segments remain.",
        )
        self.client.force_login(self.admin_user)

        response = self.client.get(reverse("core:sms_usage"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SMS alerts")
        self.assertContains(response, "800 of 5000 monthly SMS segments remain.")
        self.assertContains(response, "SMS segments")

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

    @override_settings(BACKGROUND_JOBS_ENABLED=False)
    def test_sms_cannot_be_enabled_when_background_jobs_are_disabled(self):
        self.configuration.enabled = False
        self.configuration.save(update_fields=["enabled", "updated_at"])
        self.client.force_login(self.admin_user)

        response = self.client.post(
            reverse("core:set_sms_enabled"),
            {"enabled": "true"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.configuration.refresh_from_db()
        self.assertFalse(self.configuration.enabled)
        self.assertFalse(
            ConfigurationAuditLog.objects.filter(
                event=ConfigurationAuditLog.Event.SMS_STATE_CHANGED,
            ).exists()
        )
        self.assertContains(
            response,
            "Cannot enable SMS while background jobs are disabled.",
        )

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
