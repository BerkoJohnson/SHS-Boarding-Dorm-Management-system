"""PostgreSQL integration coverage for tenant provisioning and migrations."""

from datetime import date
import uuid

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django_pgschemas.schema import activate_public

from core.models import AcademicYear
from tenancy.models import Tenant


class TenantProvisioningMigrationTests(TransactionTestCase):
    """Verify the real provisioning command installs tenant application tables."""

    databases = {"default"}

    def setUp(self):
        super().setUp()
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL is required for this integration test.")
        if not settings.TENANCY_ENABLED:
            self.skipTest("TENANCY_ENABLED must be true for this integration test.")
        self.schema_name = None

    def test_provisioning_creates_core_tables_and_supports_orm_queries(self):
        self.schema_name = f"it_{uuid.uuid4().hex[:12]}"
        domain_name = f"it-{uuid.uuid4().hex[:12]}.example.test"

        call_command(
            "provision_school",
            "--name=Tenant Migration Integration Test",
            f"--schema={self.schema_name}",
            f"--domain={domain_name}",
            "--confirm-domain-verified",
            verbosity=0,
        )

        tenant = Tenant.objects.get(schema_name=self.schema_name)
        with tenant:
            tables = set(connection.introspection.table_names())
            expected_tables = {"core_academicyear", "core_house", "auth_user"}
            missing_tables = expected_tables - tables
            self.assertFalse(
                missing_tables,
                f"Expected tenant tables are missing: {missing_tables}",
            )

            academic_year = AcademicYear.objects.create(
                start_date=date(2026, 1, 1),
                end_date=date(2027, 1, 1),
            )
            self.assertTrue(
                AcademicYear.objects.filter(pk=academic_year.pk).exists()
            )

    def tearDown(self):
        if self.schema_name is not None:
            self._cleanup_tenant(self.schema_name)
        super().tearDown()

    @staticmethod
    def _cleanup_tenant(schema_name):
        activate_public()
        Tenant.objects.filter(schema_name=schema_name).delete()
        quote = connection.ops.quote_name
        with connection.cursor() as cursor:
            cursor.execute(
                f"DROP SCHEMA IF EXISTS {quote(schema_name)} CASCADE"
            )
