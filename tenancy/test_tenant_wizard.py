from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class TenantWizardTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands._guided.sys.stdin")
    @patch("tenancy.management.commands._guided.call_command")
    def test_commands_without_required_options_open_matching_guided_flow(
        self, call_guided, stdin
    ):
        stdin.isatty.return_value = True
        commands = (
            ("provision_school", "provision"),
            ("migrate_legacy_school", "legacy"),
            ("add_school_domain", "domain"),
            ("set_school_status", "status"),
            ("support_access", "support"),
            ("tenant_backup", "backups"),
            ("tenant_usage", "usage"),
            ("tenant_features", "features"),
            ("tenant_subscription", "plans"),
            ("tenant_invoice", "invoices"),
            ("tenant_restore", "restore"),
        )
        for command_name, task in commands:
            with self.subTest(command=command_name):
                call_guided.reset_mock()
                call_command(command_name)
                call_guided.assert_called_once()
                args = call_guided.call_args.args
                self.assertEqual(args[0], "tenant_wizard")
                self.assertEqual(args[1], f"--task={task}")

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands.tenant_wizard.sys.stdin")
    @patch("builtins.input", side_effect=["0", "12"])
    def test_menu_displays_colored_choices_and_exits(self, _input, stdin):
        stdin.isatty.return_value = True
        output = StringIO()

        call_command("tenant_wizard", stdout=output)

        self.assertIn("School and tenant administration", output.getvalue())
        self.assertIn("School setup menu", output.getvalue())
        self.assertIn("School facilities and roster menu", output.getvalue())
        self.assertIn("Goodbye.", output.getvalue())

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands.tenant_wizard.sys.stdin")
    def test_menu_requires_terminal(self, stdin):
        stdin.isatty.return_value = False

        with self.assertRaisesMessage(CommandError, "interactive terminal"):
            call_command("tenant_wizard", stdout=StringIO())

    @override_settings(TENANCY_ENABLED=True)
    def test_partial_command_options_explain_how_to_open_guided_setup(self):
        with self.assertRaisesMessage(CommandError, "Run the command without options"):
            call_command("provision_school", "--name=Partial School")

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands.tenant_wizard.call_command")
    @patch("tenancy.management.commands.tenant_wizard.sys.stdin")
    @patch("builtins.input", side_effect=["4", "12"])
    def test_menu_routes_school_inventory_action(self, _input, stdin, command):
        stdin.isatty.return_value = True

        call_command("tenant_wizard", stdout=StringIO())

        command.assert_called_once()
        self.assertEqual(command.call_args.args[:2], ("tenant_list", "--all"))

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands.tenant_wizard.Command._school_schema")
    @patch("tenancy.management.commands.tenant_wizard.call_command")
    @patch("tenancy.management.commands.tenant_wizard.sys.stdin")
    @patch(
        "builtins.input",
        side_effect=["3", "1", "Red House", "1", "", "y", "12"],
    )
    def test_menu_routes_guided_house_creation(
        self,
        _input,
        stdin,
        command,
        school_schema,
    ):
        stdin.isatty.return_value = True
        school_schema.return_value = "school_one"

        call_command("tenant_wizard", stdout=StringIO())

        command.assert_called_once()
        args = command.call_args.args
        self.assertEqual(args[0], "create_house")
        self.assertIn("--schema=school_one", args)
        self.assertIn("--name=Red House", args)
        self.assertIn("--display-order=1", args)

    @override_settings(TENANCY_ENABLED=True)
    @patch(
        "tenancy.management.commands.tenant_wizard.Command._choose_programme"
    )
    @patch("tenancy.management.commands.tenant_wizard.Command._school_schema")
    @patch("tenancy.management.commands.tenant_wizard.call_command")
    @patch("tenancy.management.commands.tenant_wizard.sys.stdin")
    @patch(
        "builtins.input",
        side_effect=["3", "4", "2", "1", "", "", "y", "12"],
    )
    def test_menu_routes_batch_class_group_creation(
        self,
        _input,
        stdin,
        command,
        school_schema,
        choose_programme,
    ):
        stdin.isatty.return_value = True
        school_schema.return_value = "school_one"
        choose_programme.return_value = MagicMock(
            pk=42,
            name="General Arts",
            code="A",
        )

        call_command("tenant_wizard", stdout=StringIO())

        command.assert_called_once()
        self.assertEqual(
            command.call_args.args,
            (
                "create_classroom",
                "--schema=school_one",
                "--form=1",
                "--programme=42",
                "--start=1",
                "--end=10",
            ),
        )

    @override_settings(TENANCY_ENABLED=False)
    def test_menu_is_not_available_in_single_school_mode(self):
        with self.assertRaisesMessage(CommandError, "TENANCY_ENABLED=True"):
            call_command("tenant_wizard", stdout=StringIO())
