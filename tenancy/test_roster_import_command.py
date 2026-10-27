import csv
import tempfile
from io import StringIO
from pathlib import Path
from unittest import skipUnless
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from core.models import (
    ClassGroup,
    Dormitory,
    House,
    Programme,
    Student,
)
from core.services.student_import import (
    RosterValidation,
    summarize_roster_validation,
)

ROSTER_HEADERS = [
    "Admission No.",
    "Index Number",
    "Full Name",
    "Gender",
    "Programme",
    "House",
    "Class",
]


def write_roster(directory, rows, name="roster.csv", headers=None):
    path = Path(directory, name)
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output)
        writer.writerow(headers or ROSTER_HEADERS)
        writer.writerows(rows)
    return path


class RosterValidationSummaryTests(SimpleTestCase):
    def test_summary_separates_invalid_duplicate_and_conflict_rows(self):
        validation = RosterValidation(
            row_count=4,
            errors=[
                "CSV, row 2: duplicate admission number 'ADM-1'.",
                "CSV, row 3: unknown programme 'Missing'.",
                "CSV, row 4: invalid gender 'Other'.",
            ],
        )

        summary = summarize_roster_validation(validation)

        self.assertEqual(summary["total_rows"], 4)
        self.assertEqual(summary["valid_rows"], 1)
        self.assertEqual(summary["invalid_rows"], 3)
        self.assertEqual(summary["duplicate_rows"], 1)
        self.assertEqual(summary["conflict_rows"], 1)
        self.assertEqual(summary["global_errors"], 0)

    def test_global_errors_mark_every_row_as_invalid(self):
        validation = RosterValidation(
            row_count=3,
            errors=["CSV: missing required columns: gender."],
        )

        summary = summarize_roster_validation(validation)

        self.assertEqual(summary["total_rows"], 3)
        self.assertEqual(summary["valid_rows"], 0)
        self.assertEqual(summary["invalid_rows"], 3)
        self.assertEqual(summary["global_errors"], 1)


class TenantRosterImportGuardTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    def test_requires_schema_in_tenant_mode(self):
        with self.assertRaisesMessage(
            CommandError,
            "TENANCY_ENABLED is True; pass --schema to select the school.",
        ):
            call_command(
                "tenant_roster_import",
                "roster.csv",
                stdout=StringIO(),
            )

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands._roster_import.launch_guided")
    def test_missing_file_opens_the_guided_flow(self, launch):
        call_command("tenant_roster_import", stdout=StringIO())

        launch.assert_called_once()
        self.assertEqual(launch.call_args.args[1], "roster")

    @override_settings(TENANCY_ENABLED=False)
    def test_schema_is_rejected_in_single_school_mode(self):
        with self.assertRaisesMessage(
            CommandError,
            "--schema can only be used when TENANCY_ENABLED=True.",
        ):
            call_command(
                "tenant_roster_import",
                "roster.csv",
                schema="school_one",
                stdout=StringIO(),
            )

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.models.Tenant.objects")
    def test_unknown_schema_is_rejected_before_reading_the_file(self, tenant_objects):
        tenant_objects.filter.return_value.first.return_value = None

        with self.assertRaisesMessage(
            CommandError,
            "Tenant schema 'missing_school' was not found.",
        ):
            call_command(
                "tenant_roster_import",
                "does-not-exist.csv",
                schema="missing_school",
                stdout=StringIO(),
            )

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands._roster_import.commit_student_roster")
    @patch("tenancy.management.commands._roster_import.validate_student_roster")
    @patch("tenancy.models.Tenant.objects")
    def test_import_runs_inside_the_selected_tenant(
        self,
        tenant_objects,
        validate_roster,
        commit_roster,
    ):
        tenant = MagicMock()
        tenant.name = "School One"
        tenant_objects.filter.return_value.first.return_value = tenant

        validation = MagicMock()
        validation.errors = []
        validation.headers = []
        validation.sample_rows = []
        validation.row_count = 2
        validation.staged = [object(), object()]
        validate_roster.return_value = validation
        commit_roster.return_value = (2, [])

        with tempfile.TemporaryDirectory() as directory:
            roster_path = Path(directory, "roster.csv")
            roster_path.write_text("roster contents", encoding="utf-8")
            output = StringIO()

            call_command(
                "tenant_roster_import",
                str(roster_path),
                schema="school_one",
                yes=True,
                stdout=output,
            )

        tenant_objects.filter.assert_called_with(schema_name="school_one")
        tenant.__enter__.assert_called_once_with()
        tenant.__exit__.assert_called_once()
        validate_roster.assert_called_once()
        commit_roster.assert_called_once()
        self.assertIn(
            "2 student record(s) imported",
            output.getvalue(),
        )


@skipUnless(
    not settings.TENANCY_ENABLED,
    "School models live in tenant schemas when TENANCY_ENABLED=True.",
)
@override_settings(TENANCY_ENABLED=False)
class TenantRosterImportCommandTests(TestCase):
    def setUp(self):
        self.programme = Programme.objects.create(
            name="General Science",
            code="GSCI",
        )
        self.house = House.objects.create(name="Red House")
        Dormitory.objects.create(
            house=self.house,
            name="Boys Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=80,
        )
        Dormitory.objects.create(
            house=self.house,
            name="Girls Block",
            gender=Dormitory.GenderBlock.FEMALE,
            capacity=80,
        )
        ClassGroup.objects.create(name="1A1", programme=self.programme)

    def _valid_row(self, admission_no="ADM-1", index_number="IDX-1"):
        return [
            admission_no,
            index_number,
            "Valid Student",
            "Male",
            "General Science",
            "Red House",
            "1A1",
        ]

    def test_dry_run_previews_without_writing_students(self):
        with tempfile.TemporaryDirectory() as directory:
            roster_path = write_roster(directory, [self._valid_row()])
            output = StringIO()

            call_command(
                "tenant_roster_import",
                str(roster_path),
                dry_run=True,
                stdout=output,
            )

        rendered = output.getvalue()
        self.assertIn("Roster preview", rendered)
        self.assertIn("Total rows: 1", rendered)
        self.assertIn("Valid records: 1", rendered)
        self.assertIn("Invalid rows: 0", rendered)
        self.assertIn("No student records were imported.", rendered)
        self.assertFalse(Student.objects.exists())

    def test_import_requires_confirmation_in_non_interactive_run(self):
        with tempfile.TemporaryDirectory() as directory:
            roster_path = write_roster(directory, [self._valid_row()])

            with self.assertRaisesMessage(CommandError, "explicit confirmation"):
                call_command(
                    "tenant_roster_import",
                    str(roster_path),
                    stdout=StringIO(),
                )

        self.assertFalse(Student.objects.exists())

    def test_yes_imports_the_roster_and_reports_results(self):
        with tempfile.TemporaryDirectory() as directory:
            roster_path = write_roster(directory, [self._valid_row()])
            output = StringIO()

            call_command(
                "tenant_roster_import",
                str(roster_path),
                yes=True,
                stdout=output,
            )

        self.assertIn(
            "Roster import completed successfully: 1 student record(s) imported; "
            "0 skipped.",
            output.getvalue(),
        )
        self.assertTrue(
            Student.objects.filter(admission_no="ADM-1").exists()
        )

    def test_invalid_roster_is_rejected_before_writing(self):
        invalid_row = self._valid_row(admission_no="ADM-2", index_number="IDX-2")
        invalid_row[4] = "Unknown Programme"

        with tempfile.TemporaryDirectory() as directory:
            roster_path = write_roster(directory, [invalid_row])
            output = StringIO()

            with self.assertRaisesMessage(
                CommandError,
                "Roster validation failed",
            ):
                call_command(
                    "tenant_roster_import",
                    str(roster_path),
                    yes=True,
                    stdout=output,
                )

        rendered = output.getvalue()
        self.assertIn("Invalid rows: 1", rendered)
        self.assertIn("Potential conflicts: 1", rendered)
        self.assertIn("unknown programme", rendered)
        self.assertFalse(Student.objects.exists())

    def test_duplicate_reimport_is_rejected_without_creating_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            roster_path = write_roster(directory, [self._valid_row()])
            call_command(
                "tenant_roster_import",
                str(roster_path),
                yes=True,
                stdout=StringIO(),
            )

            output = StringIO()
            with self.assertRaisesMessage(
                CommandError,
                "Roster validation failed",
            ):
                call_command(
                    "tenant_roster_import",
                    str(roster_path),
                    yes=True,
                    stdout=output,
                )

        self.assertIn("Duplicate rows: 1", output.getvalue())
        self.assertEqual(Student.objects.count(), 1)

    def test_alias_command_uses_the_same_preview_and_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            roster_path = write_roster(directory, [self._valid_row()])

            with self.assertRaisesMessage(CommandError, "explicit confirmation"):
                call_command(
                    "import_tenant_roster",
                    str(roster_path),
                    stdout=StringIO(),
                )

        self.assertFalse(Student.objects.exists())
