from django.core.management.base import BaseCommand, CommandError

from tenancy.models import Domain, Tenant


class Command(BaseCommand):
    help = "Activate or deactivate an existing school tenant."

    def add_arguments(self, parser):
        parser.add_argument("--schema", required=True)
        action = parser.add_mutually_exclusive_group(required=True)
        action.add_argument("--activate", action="store_true")
        action.add_argument("--deactivate", action="store_true")

    def handle(self, *args, **options):
        tenant = Tenant.objects.filter(schema_name=options["schema"]).first()
        if tenant is None:
            raise CommandError(f"Tenant schema '{options['schema']}' was not found.")

        if options["activate"] and not Domain.objects.filter(
            tenant=tenant,
            is_verified=True,
            folder="",
        ).exists():
            raise CommandError(
                "Add and verify at least one hostname before activating the school."
            )

        tenant.is_active = options["activate"]
        tenant.save(update_fields=["is_active"])
        state = "active" if tenant.is_active else "inactive"
        self.stdout.write(self.style.SUCCESS(f"School '{tenant.name}' is now {state}."))
