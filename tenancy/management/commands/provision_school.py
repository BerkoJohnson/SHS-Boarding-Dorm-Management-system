from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
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
        self.stdout.write(
            self.style.SUCCESS(
                f"Created school '{tenant.name}' with schema "
                f"'{tenant.schema_name}' at https://{domain_name}/."
                " The tenant remains inactive until explicitly activated."
            )
        )
