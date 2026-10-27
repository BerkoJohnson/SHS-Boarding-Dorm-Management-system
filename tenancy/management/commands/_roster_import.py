"""Shared implementation for the guided tenant roster-import command."""

from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from core.services.student_import import (
    commit_student_roster,
    summarize_roster_validation,
    validate_student_roster,
)

from ._guided import launch_guided
from ._school_setup import tenant_context, validate_tenant_mode


class TenantRosterImportCommand(BaseCommand):
    help = (
        "Preview a student roster in the selected school, then import it after "
        "explicit confirmation."
    )
    guided_task = "roster"

    def add_arguments(self, parser):
        parser.add_argument(
            "file_path",
            nargs="?",
            type=str,
            help="Path to the roster file (CSV or Excel .xlsx).",
        )
        parser.add_argument(
            "--schema",
            default=None,
            help="Tenant schema (required when TENANCY_ENABLED=True).",
        )
        parser.add_argument(
            "--dry-run",
            "--preview",
            dest="dry_run",
            action="store_true",
            help="Validate and preview the roster without importing students.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm the import without an interactive prompt.",
        )

    def _progress_callback(self, stage, current, total):
        if not getattr(self.stdout, "isatty", lambda: False)():
            return
        if total:
            message = f"{stage}: {current}/{total}"
        else:
            message = stage
        self.stdout.write(f"\r{message:<60}", ending="")
        self.stdout.flush()

    def _clear_progress(self):
        if getattr(self.stdout, "isatty", lambda: False)():
            self.stdout.write("\r" + (" " * 60) + "\r", ending="")

    def _write_preview(self, validation, file_path, school_label):
        summary = summarize_roster_validation(validation)
        self.stdout.write(self.style.HTTP_INFO("\nRoster preview"))
        self.stdout.write(f"  File: {file_path}")
        self.stdout.write(f"  School: {school_label}")
        for sheet in validation.headers:
            columns = ", ".join(
                f"{column['source'] or '(blank)'} -> "
                f"{column['field'] or 'unrecognized (ignored)'}"
                for column in sheet["columns"]
            )
            self.stdout.write(f"  Worksheet {sheet['sheet']}: {columns}")
        self.stdout.write(f"  Total rows: {summary['total_rows']}")
        self.stdout.write(f"  Valid records: {summary['valid_rows']}")
        self.stdout.write(f"  Invalid rows: {summary['invalid_rows']}")
        self.stdout.write(f"  Duplicate rows: {summary['duplicate_rows']}")
        self.stdout.write(
            f"  Potential conflicts: {summary['conflict_rows']}"
        )
        if validation.sample_rows:
            self.stdout.write("  Sample rows:")
            for row in validation.sample_rows:
                self.stdout.write(
                    f"    {row['sheet']}, row {row['row']}: {row['values']}"
                )

    def _write_errors(self, errors, heading):
        self.stdout.write(self.style.ERROR(f"\n{heading}"))
        for error in errors[:200]:
            self.stdout.write(f"- {error}")
        if len(errors) > 200:
            self.stdout.write(
                f"- ... {len(errors) - 200} more error(s) not shown."
            )

    def _confirm_import(self, count, school_label, options):
        if options["yes"]:
            return True
        if not getattr(self.stdout, "isatty", lambda: False)():
            raise CommandError(
                "Roster import needs explicit confirmation. Review the preview, "
                "then rerun with --yes (or run from an interactive terminal)."
            )
        prompt = (
            f"Import {count} student record(s) into {school_label}? [y/N]: "
        )
        answer = input(self.style.WARNING(prompt)).strip().lower()
        return answer in {"y", "yes"}

    def handle(self, *args, **options):
        if not options.get("file_path"):
            launch_guided(self, self.guided_task, options=options)
            return

        schema = options.get("schema")
        validate_tenant_mode(schema)
        file_path = Path(options["file_path"])

        if schema:
            school_label = f"school '{schema}'"
            # Resolve the tenant before opening the file so a typo cannot lead
            # to validation against the wrong school.
            from tenancy.models import Tenant

            tenant = Tenant.objects.filter(schema_name=schema).first()
            if tenant is None:
                raise CommandError(f"Tenant schema '{schema}' was not found.")
            school_label = f"school '{tenant.name}' ({tenant.schema_name})"
        else:
            school_label = "the single-school database"

        with tenant_context(schema):
            try:
                with file_path.open("rb") as source:
                    roster_file = File(source, name=file_path.name)
                    validation = validate_student_roster(
                        roster_file,
                        progress_callback=self._progress_callback,
                    )
            except OSError as error:
                raise CommandError(
                    f"Could not read roster file '{file_path}': {error}"
                ) from error
            finally:
                self._clear_progress()

            self._write_preview(validation, file_path, school_label)
            if validation.errors:
                self._write_errors(
                    validation.errors, "Roster validation failed"
                )
                raise CommandError(
                    "Roster validation failed; no student records were imported. "
                    f"{len(validation.errors)} error(s) need attention."
                )

            if options["dry_run"]:
                self.stdout.write(f"Rows validated: {validation.row_count}")
                self.stdout.write(
                    self.style.SUCCESS(
                        "Roster validation passed. No student records were imported."
                    )
                )
                return

            if not validation.staged:
                raise CommandError(
                    "The roster file contains no student rows. "
                    "No student records were imported."
                )
            if not self._confirm_import(
                len(validation.staged), school_label, options
            ):
                raise CommandError(
                    "Roster import cancelled. No student records were imported."
                )

            imported_count, errors = commit_student_roster(
                validation,
                progress_callback=self._progress_callback,
            )
            self._clear_progress()
            if errors:
                self._write_errors(errors, "Roster import failed")
                self.stdout.write(
                    f"Import results: {imported_count} successful, "
                    f"{len(validation.staged) - imported_count} skipped, "
                    f"{len(errors)} error(s)."
                )
                raise CommandError(
                    "Roster import failed; no student records were imported."
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Roster import completed successfully: {imported_count} "
                "student record(s) imported; 0 skipped."
            )
        )
        self.stdout.write(f"Successful records: {imported_count}")
        self.stdout.write("Skipped records: 0")
        self.stdout.write("Errors: 0")
