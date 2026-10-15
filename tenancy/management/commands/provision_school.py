from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_pgschemas.utils import check_schema_name_not_reserved

from tenancy.models import Domain, Tenant, normalize_domain


class Command(BaseCommand):
    help = "Create a school schema and attach a manually verified hostname."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True)
        parser.add_argument("--schema", required=True)
        parser.add_argument("--domain", required=True)
        parser.add_argument(
            "--confirm-domain-verified",
            action="store_true",
            help="Confirm the platform operator has verified control of this hostname.",
        )

    def handle(self, *args, **options):
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before provisioning a tenant schema."
            )
        if not options["confirm_domain_verified"]:
            raise CommandError(
                "Verify domain ownership first, then pass --confirm-domain-verified."
            )

        try:
            domain_name = normalize_domain(options["domain"])
        except ValidationError as error:
            raise CommandError(str(error)) from error
        schema_name = options["schema"].strip().lower()
        school_name = options["name"].strip()
        if not school_name:
            raise CommandError("School name cannot be empty.")
        try:
            check_schema_name_not_reserved(schema_name)
        except ValidationError as error:
            raise CommandError(str(error)) from error

        if Tenant.objects.filter(schema_name=schema_name).exists():
            raise CommandError(f"Tenant schema '{schema_name}' already exists.")
        if Domain.objects.filter(domain=domain_name, folder="").exists():
            raise CommandError(f"Domain '{domain_name}' is already registered.")

        with transaction.atomic():
            tenant = Tenant.objects.create(
                name=school_name,
                schema_name=schema_name,
                is_active=False,
            )
            Domain.objects.create(
                tenant=tenant,
                domain=domain_name,
                is_verified=True,
            )

        # Run migrations for the new tenant schema.  This must happen
        # outside the atomic block because DDL commands (CREATE TABLE)
        # issued by the migration runner should not be rolled back by a
        # subsequent Python-level error.
        self.stdout.write("Running migrations for the new tenant schema\u2026")
        try:
            with tenant:
                call_command("migrate", verbosity=0)
                # Seed the initial SchoolConfiguration so the school name
                # and SMS sender ID are set from the start.
                from core.models import SchoolConfiguration

                SchoolConfiguration.objects.get_or_create(
                    id=1,
                    defaults={
                        "name": school_name,
                        "short_name": school_name[:50].strip(),
                    },
                )
        except Exception as error:
            raise CommandError(
                f"Schema '{schema_name}' was created but migrations failed: "
                f"{error}\n\nTo retry, run:\n"
                f"  python manage.py migrate --schema={schema_name}"
            ) from error

        self.stdout.write(
            self.style.SUCCESS(
                f"Created school '{tenant.name}' with schema "
                f"'{tenant.schema_name}' at https://{domain_name}/."
                " The tenant remains inactive until explicitly activated."
            )
        )
