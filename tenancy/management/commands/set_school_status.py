from django.apps import AppConfig
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from tenancy.models import Domain, Tenant
from ._guided import launch_guided


def _pending_tenant_migrations(tenant):
    with tenant:
        executor = MigrationExecutor(connection)
        tenant_app_labels = {
            AppConfig.create(app_config).label
            for app_config in settings.TENANTS["default"]["APPS"]
        }
        targets = [
            target
            for app_label in tenant_app_labels
            for target in executor.loader.graph.leaf_nodes(app_label)
        ]
        return [
            f"{migration.app_label}.{migration.name}"
            for migration, backwards in executor.migration_plan(targets)
            if not backwards and migration.app_label in tenant_app_labels
        ]


class Command(BaseCommand):
    help = "Activate or deactivate an existing school tenant."

    def add_arguments(self, parser):
        parser.add_argument("--schema")
        action = parser.add_mutually_exclusive_group()
        action.add_argument("--activate", action="store_true")
        action.add_argument("--deactivate", action="store_true")

    def handle(self, *args, **options):
        if not options.get("schema") or not (
            options.get("activate") or options.get("deactivate")
        ):
            launch_guided(self, "status", options=options)
            return
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before managing tenant status."
            )

        tenant = Tenant.objects.filter(schema_name=options["schema"]).first()
        if tenant is None:
            raise CommandError(f"Tenant schema '{options['schema']}' was not found.")

        if options["activate"]:
            if not Domain.objects.filter(
                tenant=tenant,
                is_verified=True,
                folder="",
            ).exists():
                raise CommandError(
                    "Add and verify at least one hostname before activating "
                    "the school."
                )

            # Verify the PostgreSQL schema exists and has been migrated.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = %s",
                    [tenant.schema_name],
                )
                if not cursor.fetchone():
                    raise CommandError(
                        f"PostgreSQL schema '{tenant.schema_name}' does not "
                        "exist. Run 'provision_school' or create the schema "
                        "before activating."
                    )
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name = %s",
                    [tenant.schema_name, "core_schoolconfiguration"],
                )
                if not cursor.fetchone():
                    raise CommandError(
                        f"Schema '{tenant.schema_name}' has not been migrated. "
                        "Run migrations before activating."
                    )

            pending_migrations = _pending_tenant_migrations(tenant)
            if pending_migrations:
                migration_list = ", ".join(pending_migrations)
                raise CommandError(
                    f"Schema '{tenant.schema_name}' has unapplied tenant "
                    f"migrations: {migration_list}. Run 'migrate "
                    f"--schema={tenant.schema_name}' before activating."
                )

        tenant.is_active = options["activate"]
        tenant.save(update_fields=["is_active"])
        state = "active" if tenant.is_active else "inactive"
        self.stdout.write(self.style.SUCCESS(f"School '{tenant.name}' is now {state}."))
