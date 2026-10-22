from contextlib import nullcontext
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from core.services.student_import import (
    import_student_roster,
    validate_student_roster,
)


class Command(BaseCommand):
    help = "Validate and import a student roster from CSV or Excel .xlsx"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="Path to the roster file")
        parser.add_argument(
            "--schema",
            default=None,
            help="Target tenant schema (required when TENANCY_ENABLED=True).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Validate the roster and show a preview without importing students.",
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

    def handle(self, *args, **options):
        schema = options.get("schema")
        if settings.TENANCY_ENABLED and not schema:
            raise CommandError(
                "TENANCY_ENABLED is True; pass --schema to select the school."
            )
        if schema and not settings.TENANCY_ENABLED:
            raise CommandError(
                "--schema can only be used when TENANCY_ENABLED=True."
            )

        if schema:
            from tenancy.models import Tenant

            tenant = Tenant.objects.filter(schema_name=schema).first()
            if tenant is None:
                raise CommandError(f"Tenant schema '{schema}' was not found.")
            context = tenant
        else:
            context = nullcontext()

        file_path = Path(options["file_path"])
        try:
            with context:
                with file_path.open("rb") as source:
                    roster_file = File(source, name=file_path.name)
                    if options["dry_run"]:
                        validation = validate_student_roster(
                            roster_file,
                            progress_callback=self._progress_callback,
                        )
                        errors = validation.errors
                        imported_count = 0
                    else:
                        imported_count, errors = import_student_roster(
                            roster_file,
                            progress_callback=self._progress_callback,
                        )
        except OSError as error:
            raise CommandError(
                f"Could not read roster file '{file_path}': {error}"
            ) from error

        if getattr(self.stdout, "isatty", lambda: False)():
            self.stdout.write("\r" + (" " * 60) + "\r", ending="")
        if errors:
            details = "\n".join(f"- {error}" for error in errors)
            action = "validation" if options["dry_run"] else "import"
            raise CommandError(f"Roster {action} failed:\n{details}")

        if options["dry_run"]:
            for sheet in validation.headers:
                self.stdout.write(f"Worksheet {sheet['sheet']} columns:")
                for column in sheet["columns"]:
                    target = column["field"] or "unrecognized (ignored)"
                    self.stdout.write(f"  {column['source'] or '(blank)'} -> {target}")
            self.stdout.write(f"Rows validated: {validation.row_count}")
            if validation.sample_rows:
                self.stdout.write("Sample rows:")
                for row in validation.sample_rows:
                    self.stdout.write(
                        f"  {row['sheet']}, row {row['row']}: {row['values']}"
                    )
            self.stdout.write(
                self.style.SUCCESS(
                    "Roster validation passed. No student records were imported."
                )
            )
            return

        self.stdout.write(
            self.style.SUCCESS(
                f"Roster import completed successfully: "
                f"{imported_count} student record(s) imported."
            )
        )
