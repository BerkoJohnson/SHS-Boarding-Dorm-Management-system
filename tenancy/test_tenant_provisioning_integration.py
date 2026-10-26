"""PostgreSQL integration coverage for tenant provisioning and migrations."""

from datetime import date
from io import StringIO
import uuid
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django_pgschemas.schema import activate_public

from core.models import AcademicYear, StaffRole
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

        call_command(
            "create_school_user",
            f"--schema={self.schema_name}",
            "--username=tenant-gate-officer",
            "--role=gate-officer",
            "--unusable-password",
            verbosity=0,
        )
        with tenant:
            user = get_user_model().objects.get(username="tenant-gate-officer")
            self.assertEqual(user.profile.role, StaffRole.GATE_OFFICER)
            self.assertFalse(user.has_usable_password())

        with (
            patch("tenancy.management.commands.tenant_wizard.sys.stdin") as stdin,
            patch(
                "builtins.input",
                side_effect=[
                    "1",
                    "1",
                    "6",
                    "guided-tenant-gate",
                    "",
                    "",
                    "",
                    "11",
                ],
            ),
            patch(
                "tenancy.management.commands.create_school_user.getpass",
                side_effect=["StrongPassw0rd!2026", "StrongPassw0rd!2026"],
            ),
        ):
            stdin.isatty.return_value = True
            output = StringIO()
            call_command("tenant_wizard", stdout=output, verbosity=0)

        with tenant:
            guided_user = get_user_model().objects.get(username="guided-tenant-gate")
            self.assertEqual(guided_user.profile.role, StaffRole.GATE_OFFICER)
            self.assertTrue(guided_user.check_password("StrongPassw0rd!2026"))
        self.assertIn(tenant.name, output.getvalue())

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
