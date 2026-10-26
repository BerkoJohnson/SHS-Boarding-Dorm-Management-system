from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from core.models import House, HouseMaster, StaffRole


@override_settings(TENANCY_ENABLED=False)
class CreateSchoolUserCommandTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(name="North House")

    def _call(self, *args):
        output = StringIO()
        call_command("create_school_user", *args, stdout=output)
        return output.getvalue()

    @patch("tenancy.management.commands.create_school_user.getpass")
    @patch("tenancy.management.commands.create_school_user.sys.stdin")
    def test_creates_school_superuser_with_hashed_password_and_admin_profile(
        self, stdin, get_password
    ):
        stdin.isatty.return_value = True
        get_password.side_effect = ["StrongPassw0rd!2026", "StrongPassw0rd!2026"]

        output = self._call(
            "--username=school-admin",
            "--role=superadmin",
            "--email=admin@example.test",
        )

        user = get_user_model().objects.get(username="school-admin")
        self.assertTrue(user.is_active)
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.check_password("StrongPassw0rd!2026"))
        self.assertEqual(user.profile.role, StaffRole.ADMIN)
        self.assertIn("superuser account 'school-admin'", output)
        self.assertNotIn("StrongPassw0rd!2026", output)

    def test_creates_gate_officer_with_unusable_password(self):
        output = self._call(
            "--username=gate-one",
            "--role=gate-officer",
            "--unusable-password",
        )

        user = get_user_model().objects.get(username="gate-one")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.profile.role, StaffRole.GATE_OFFICER)
        self.assertIn("no usable password", output)

    @patch("tenancy.management.commands.create_school_user.getpass")
    @patch("tenancy.management.commands.create_school_user.sys.stdin")
    def test_creates_housemaster_and_assigns_house(self, stdin, get_password):
        stdin.isatty.return_value = True
        get_password.side_effect = ["StrongPassw0rd!2026", "StrongPassw0rd!2026"]

        self._call(
            "--username=north-housemaster",
            "--role=housemaster",
            "--house=North House",
            "--staff-id=HM-001",
            "--phone-number=+233240000001",
            "--first-name=Ama",
            "--last-name=Mensah",
        )

        housemaster = HouseMaster.objects.get(staff_id="HM-001")
        self.assertEqual(housemaster.user.username, "north-housemaster")
        self.assertEqual(housemaster.full_name, "Ama Mensah")
        self.assertEqual(housemaster.house, self.house)
        self.assertFalse(housemaster.is_senior_housemaster)
        self.assertEqual(housemaster.user.profile.role, StaffRole.HOUSEMASTER)

    def test_housemaster_requires_house_and_staff_details(self):
        with self.assertRaisesMessage(CommandError, "require --staff-id, --phone-number"):
            self._call("--username=missing-details", "--role=housemaster")

        self.assertFalse(
            get_user_model().objects.filter(username="missing-details").exists()
        )

    @patch("tenancy.management.commands.create_school_user.getpass")
    @patch("tenancy.management.commands.create_school_user.sys.stdin")
    def test_mismatched_passwords_do_not_create_user(self, stdin, get_password):
        stdin.isatty.return_value = True
        get_password.side_effect = ["FirstPassword!2026", "DifferentPassword!2026"]

        with self.assertRaisesMessage(CommandError, "did not match"):
            self._call("--username=mismatch", "--role=admin")

        self.assertFalse(get_user_model().objects.filter(username="mismatch").exists())

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands.create_school_user.sys.stdin")
    def test_noninteractive_tenant_mode_requires_explicit_school_schema(self, stdin):
        stdin.isatty.return_value = False
        with self.assertRaisesMessage(CommandError, "pass --schema"):
            self._call("--username=missing-schema", "--role=admin")

        self.assertFalse(
            get_user_model().objects.filter(username="missing-schema").exists()
        )

    def test_schema_is_rejected_when_tenancy_is_disabled(self):
        with self.assertRaisesMessage(CommandError, "only be used"):
            self._call(
                "--username=wrong-mode",
                "--role=admin",
                "--schema=not-a-tenant",
            )

    @patch("tenancy.management.commands.create_school_user.getpass")
    @patch("tenancy.management.commands.create_school_user.sys.stdin")
    @patch("builtins.input", side_effect=["6", "guided-gate", "", "", ""])
    def test_running_without_arguments_guides_single_school_account_creation(
        self, _input, stdin, get_password
    ):
        stdin.isatty.return_value = True
        get_password.side_effect = ["StrongPassw0rd!2026", "StrongPassw0rd!2026"]

        output = self._call()

        user = get_user_model().objects.get(username="guided-gate")
        self.assertEqual(user.profile.role, StaffRole.GATE_OFFICER)
        self.assertIn("Step 1 of 4", output)
        self.assertIn("Step 2 of 4", output)
        self.assertIn("Created gate-officer user", output)
