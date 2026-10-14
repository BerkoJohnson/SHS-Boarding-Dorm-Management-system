import shutil
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files import File
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone
from django_pgschemas.utils import check_schema_name_not_reserved

from core.models import SchoolConfiguration, Student
from core.storage import PrivateStudentPhotoStorage
from core.services.student_id_images import private_student_id_export_path
from tenancy.models import Domain, Tenant, normalize_domain


AUTH_TABLES = {
    "auth_group",
    "auth_group_permissions",
    "auth_permission",
    "auth_user",
    "auth_user_groups",
    "auth_user_user_permissions",
    "django_admin_log",
}
AUTH_RELATION_TABLES = {
    "auth_group_permissions",
    "auth_user_groups",
    "auth_user_user_permissions",
}


class Command(BaseCommand):
    help = (
        "Copy the existing public-schema school's database rows and uploaded "
        "files into a new, inactive tenant schema."
    )

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
                "Set TENANCY_ENABLED=true and apply public-schema migrations first."
            )
        if connection.vendor != "postgresql":
            raise CommandError("Legacy school adoption requires PostgreSQL.")
        if not options["confirm_domain_verified"]:
            raise CommandError(
                "Verify domain ownership first, then pass --confirm-domain-verified."
            )

        schema_name = options["schema"].strip().lower()
        school_name = options["name"].strip()
        if not school_name:
            raise CommandError("School name cannot be empty.")
        try:
            check_schema_name_not_reserved(schema_name)
            domain_name = normalize_domain(options["domain"])
        except ValidationError as error:
            raise CommandError(str(error)) from error

        if Domain.objects.filter(domain=domain_name, folder="").exists():
            raise CommandError(f"Domain '{domain_name}' is already registered.")

        source_tables = self._legacy_tables()
        core_tables = sorted(
            table for table in source_tables if table.startswith("core_")
        )
        if not core_tables:
            raise CommandError(
                "No core tables were found in the public schema; refusing to create "
                "an empty tenant."
            )
        tables = ["auth_user", "auth_group", "auth_permission"]
        tables.extend(
            sorted(
                (source_tables & AUTH_TABLES)
                - set(tables)
                - AUTH_RELATION_TABLES
            )
        )
        tables.extend(sorted(source_tables & AUTH_RELATION_TABLES))
        tables.extend(core_tables)
        tables = [table for table in tables if table in source_tables]
        tables = list(dict.fromkeys(tables))

        tenant = Tenant.objects.filter(schema_name=schema_name).first()
        if tenant is None:
            tenant = Tenant.objects.create(
                name=school_name,
                schema_name=schema_name,
                is_active=False,
            )
        elif tenant.name != school_name or tenant.is_active:
            raise CommandError(
                f"Tenant schema '{schema_name}' already belongs to a different "
                "or active tenant; refusing to copy legacy data into it."
            )
        elif Domain.objects.filter(tenant=tenant).exists():
            raise CommandError(
                f"Tenant schema '{schema_name}' already has registered domains; "
                "refusing to resume legacy adoption."
            )
        with tenant:
            with transaction.atomic():
                self._copy_rows(tables, schema_name)
                self._copy_files(schema_name)

        Domain.objects.create(
            tenant=tenant,
            domain=domain_name,
            is_verified=True,
            verified_at=timezone.now(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Copied {len(tables)} tables for '{tenant.name}' into "
                f"'{schema_name}'. The verified domain is registered but the "
                "tenant remains inactive; validate the schema before activation."
            )
        )

    def _legacy_tables(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = %s",
                ["public"],
            )
            return {
                row[0]
                for row in cursor.fetchall()
                if row[0].startswith("core_") or row[0] in AUTH_TABLES
            }

    def _columns(self, schema_name, table_name):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                [schema_name, table_name],
            )
            return [row[0] for row in cursor.fetchall()]

    def _copy_rows(self, tables, schema_name):
        quote = connection.ops.quote_name
        replace_sms_configuration = False
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
            for table_name in tables:
                source_columns = self._columns("public", table_name)
                target_columns = self._columns(schema_name, table_name)
                if not source_columns or source_columns != target_columns:
                    raise CommandError(
                        f"Table '{table_name}' is absent or has different columns "
                        "in the source and target schemas. Apply matching migrations first."
                    )
                cursor.execute(
                    f"SELECT EXISTS (SELECT 1 FROM {quote(schema_name)}.{quote(table_name)})"
                )
                target_has_rows = cursor.fetchone()[0]
                if table_name == "core_smsconfiguration" and target_has_rows:
                    cursor.execute(
                        f"SELECT id, enabled FROM {quote(schema_name)}."
                        f"{quote(table_name)}"
                    )
                    target_sms_rows = cursor.fetchall()
                    if target_sms_rows != [(1, settings.SMS_ENABLED)]:
                        raise CommandError(
                            f"Target table '{schema_name}.{table_name}' contains "
                            "data other than its expected migration-created default."
                        )
                    cursor.execute(
                        f"SELECT EXISTS (SELECT 1 FROM {quote('public')}."
                        f"{quote(table_name)})"
                    )
                    replace_sms_configuration = cursor.fetchone()[0]
                elif table_name != "auth_permission" and target_has_rows:
                    raise CommandError(
                        f"Target table '{schema_name}.{table_name}' is not empty."
                    )

            if "auth_permission" in tables:
                cursor.execute(
                    f"DELETE FROM {quote(schema_name)}.{quote('auth_permission')}"
                )
            if replace_sms_configuration:
                cursor.execute(
                    f"DELETE FROM {quote(schema_name)}."
                    f"{quote('core_smsconfiguration')}"
                )

            for table_name in tables:
                column_sql = ", ".join(quote(column) for column in self._columns("public", table_name))
                cursor.execute(
                    f"INSERT INTO {quote(schema_name)}.{quote(table_name)} "
                    f"({column_sql}) SELECT {column_sql} "
                    f"FROM {quote('public')}.{quote(table_name)}"
                )
                self._reset_sequences(cursor, schema_name, table_name)

    def _reset_sequences(self, cursor, schema_name, table_name):
        quote = connection.ops.quote_name
        for sequence in connection.introspection.get_sequences(
            cursor,
            table_name,
        ):
            column = sequence["column"]
            cursor.execute(
                f"SELECT setval(%s::regclass, COALESCE(MAX({quote(column)}), 1), "
                f"MAX({quote(column)}) IS NOT NULL) "
                f"FROM {quote(schema_name)}.{quote(table_name)}",
                [sequence["name"]],
            )

    def _copy_files(self, schema_name):
        private_storage = PrivateStudentPhotoStorage()
        self._copy_file_field(
            Student.objects.only("pk", "photo").exclude(photo=""),
            "photo",
            private_storage,
            schema_name,
        )
        configuration = SchoolConfiguration.get_solo()
        if configuration.logo:
            old_name = configuration.logo.name
            configuration.logo.name = self._copy_file(
                default_storage,
                old_name,
                schema_name,
            )
            configuration.save(update_fields=["logo"])

        legacy_export_root = (
            settings.BASE_DIR / ".private" / "student_id_exports"
        )
        for archive in legacy_export_root.glob("*.zip"):
            target = private_student_id_export_path(
                archive.name,
                schema_name=schema_name,
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(archive, target)

    def _copy_file_field(self, objects, field_name, storage, schema_name):
        for instance in objects.iterator(chunk_size=100):
            old_name = getattr(instance, field_name).name
            new_name = self._copy_file(storage, old_name, schema_name)
            type(instance).objects.filter(pk=instance.pk).update(
                **{field_name: new_name}
            )

    def _copy_file(self, storage, old_name, schema_name):
        new_name = f"tenants/{schema_name}/{old_name.lstrip('/')}"
        if not storage.exists(old_name):
            raise CommandError(f"Referenced upload '{old_name}' is missing.")
        if not storage.exists(new_name):
            with storage.open(old_name, "rb") as source:
                saved_name = storage.save(new_name, File(source))
            if saved_name != new_name:
                raise CommandError(
                    f"Could not preserve upload path '{new_name}' during migration."
                )
        return new_name
