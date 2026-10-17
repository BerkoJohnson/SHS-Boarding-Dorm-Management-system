from contextlib import nullcontext
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from core.services.student_import import import_student_roster


class Command(BaseCommand):
    help = "Validate and import a student roster from CSV or Excel .xlsx"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str, help="Path to the roster file")
        parser.add_argument(
            "--schema",
            default=None,
            help="Target tenant schema (required when TENANCY_ENABLED=True).",
        )

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
                    imported_count, errors = import_student_roster(roster_file)
        except OSError as error:
            raise CommandError(
                f"Could not read roster file '{file_path}': {error}"
            ) from error

        if errors:
            details = "\n".join(f"- {error}" for error in errors)
            raise CommandError(f"Roster import failed:\n{details}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Roster import completed successfully: "
                f"{imported_count} student record(s) imported."
            )
        )
