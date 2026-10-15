import os
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings

from core.models import (
    ClassGroup,
    Dormitory,
    DormitoryRoom,
    Guardian,
    House,
    HouseMaster,
    Programme,
    SMSConfiguration,
    StaffRole,
    Student,
    UserProfile,
)


class SyntheticPilotSeederTests(TestCase):
    def setUp(self):
        super().setUp()
        SMSConfiguration.objects.all().delete()

    @override_settings(SMS_ENABLED=False)
    def test_default_command_only_prints_plan(self):
        output = StringIO()

        call_command("seed_synthetic_pilot", stdout=output)

        self.assertIn("100 fictional students", output.getvalue())
        self.assertEqual(Student.objects.count(), 0)

    @override_settings(SMS_ENABLED=False)
    def test_apply_requires_explicit_staging_confirmation(self):
        with patch.dict(
            os.environ,
            {"SYNTHETIC_PILOT_DATABASE": connection.settings_dict["NAME"]},
        ):
            with self.assertRaisesMessage(CommandError, "--confirm-empty-staging"):
                call_command("seed_synthetic_pilot", "--apply")

        self.assertEqual(Student.objects.count(), 0)

    @override_settings(SMS_ENABLED=False)
    def test_apply_creates_only_fictional_records_and_inactive_staff(self):
        output = StringIO()
        get_user_model().objects.create_superuser(
            username="staging-admin",
            password="Staging-only-passphrase-42!",
            email="admin@example.invalid",
        )
        with patch.dict(
            os.environ,
            {"SYNTHETIC_PILOT_DATABASE": connection.settings_dict["NAME"]},
        ):
            call_command(
                "seed_synthetic_pilot",
                "--apply",
                "--confirm-empty-staging",
                stdout=output,
            )

        self.assertEqual(Student.objects.count(), 100)
        self.assertEqual(
            Student.objects.filter(
                residency=Student.ResidencyStatus.BOARDING,
            ).count(),
            80,
        )
        self.assertEqual(
            Student.objects.filter(
                residency=Student.ResidencyStatus.DAY,
            ).count(),
            20,
        )
        self.assertEqual(House.objects.count(), 4)
        self.assertEqual(Dormitory.objects.count(), 8)
        self.assertEqual(DormitoryRoom.objects.count(), 16)
        self.assertEqual(Programme.objects.count(), 3)
        self.assertEqual(ClassGroup.objects.count(), 6)
        self.assertEqual(Guardian.objects.count(), 0)
        self.assertEqual(SMSConfiguration.objects.count(), 0)

        synthetic_accounts = UserProfile.objects.filter(
            user__username__startswith="pilot-"
        ).select_related("user")
        self.assertEqual(synthetic_accounts.count(), 10)
        self.assertTrue(all(not profile.user.is_active for profile in synthetic_accounts))
        self.assertTrue(
            all(not profile.user.has_usable_password() for profile in synthetic_accounts)
        )
        self.assertEqual(
            UserProfile.objects.filter(role=StaffRole.GATE_OFFICER).count(),
            1,
        )
        self.assertEqual(
            UserProfile.objects.filter(role=StaffRole.HOUSE_TUTOR).count(),
            4,
        )

    @override_settings(SMS_ENABLED=False)
    def test_apply_refuses_nonempty_school_data(self):
        Programme.objects.create(name="Existing programme")
        output = StringIO()
        with patch.dict(
            os.environ,
            {"SYNTHETIC_PILOT_DATABASE": connection.settings_dict["NAME"]},
        ):
            with self.assertRaisesMessage(CommandError, "fresh, isolated staging"):
                call_command(
                    "seed_synthetic_pilot",
                    "--apply",
                    "--confirm-empty-staging",
                    stdout=output,
                )

        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(House.objects.count(), 0)
