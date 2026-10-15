from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from tenancy.models import Domain, Tenant, normalize_domain


class Command(BaseCommand):
    help = "Attach a manually verified hostname or alias to a school tenant."

    def add_arguments(self, parser):
        parser.add_argument("--schema", required=True)
        parser.add_argument("--domain", required=True)
        parser.add_argument("--primary", action="store_true")
        parser.add_argument(
            "--confirm-domain-verified",
            action="store_true",
            help="Confirm the platform operator has verified control of this hostname.",
        )

    def handle(self, *args, **options):
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before adding tenant domains."
            )
        if not options["confirm_domain_verified"]:
            raise CommandError(
                "Verify domain ownership first, then pass --confirm-domain-verified."
            )
        tenant = Tenant.objects.filter(schema_name=options["schema"]).first()
        if tenant is None:
            raise CommandError(f"Tenant schema '{options['schema']}' was not found.")
        try:
            domain_name = normalize_domain(options["domain"])
        except ValidationError as error:
            raise CommandError(str(error)) from error
        if Domain.objects.filter(domain=domain_name, folder="").exists():
            raise CommandError(f"Domain '{domain_name}' is already registered.")

        # Demote any existing primary domain for this tenant so there is
        # at most one primary at any time (issue #11).
        if options["primary"]:
            Domain.objects.filter(
                tenant=tenant,
                is_primary=True,
            ).update(is_primary=False)

        domain = Domain.objects.create(
            tenant=tenant,
            domain=domain_name,
            is_primary=options["primary"],
            is_verified=True,
        )
        label = "primary domain" if domain.is_primary else "domain alias"
        self.stdout.write(
            self.style.SUCCESS(
                f"Added {label} '{domain.domain}' to school '{tenant.name}'."
            )
        )
