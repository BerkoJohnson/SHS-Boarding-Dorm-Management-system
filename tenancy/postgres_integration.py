from unittest import skipUnless
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase
from django_pgschemas.routers import TenantAppsRouter
from django_pgschemas.schema import Schema, activate_public


@skipUnless(
    settings.TENANCY_ENABLED and connection.vendor == "postgresql",
    "Requires TENANCY_ENABLED=true and PostgreSQL.",
)
class TenantPostgresSchemaIntegrationTests(TransactionTestCase):
    databases = {"default"}

    def test_schema_switching_isolates_data_and_routes_tenant_apps(self):
        quote = connection.ops.quote_name
        schemas = [
            f"test_tenant_{uuid4().hex[:12]}",
            f"test_tenant_{uuid4().hex[:12]}",
        ]
        created_schemas = []
        router = TenantAppsRouter()

        try:
            with connection.cursor() as cursor:
                for schema_name in schemas:
                    cursor.execute(f"CREATE SCHEMA {quote(schema_name)}")
                    created_schemas.append(schema_name)

            for schema_name, value in zip(schemas, ("one", "two")):
                with Schema.create(schema_name):
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "CREATE TABLE schema_isolation_probe "
                            "(value text NOT NULL)"
                        )
                        cursor.execute(
                            "INSERT INTO schema_isolation_probe (value) "
                            "VALUES (%s)",
                            [value],
                        )
                    self.assertTrue(
                        router.allow_migrate("default", "contenttypes")
                    )
                    self.assertTrue(router.allow_migrate("default", "auth"))

            with Schema.create("public"):
                self.assertTrue(router.allow_migrate("default", "contenttypes"))
                self.assertFalse(router.allow_migrate("default", "auth"))

            for schema_name, expected_value in zip(schemas, ("one", "two")):
                with Schema.create(schema_name):
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT current_schema(), value "
                            "FROM schema_isolation_probe"
                        )
                        self.assertEqual(
                            cursor.fetchone(),
                            (schema_name, expected_value),
                        )
        finally:
            activate_public()
            with connection.cursor() as cursor:
                for schema_name in reversed(created_schemas):
                    cursor.execute(f"DROP SCHEMA {quote(schema_name)} CASCADE")
