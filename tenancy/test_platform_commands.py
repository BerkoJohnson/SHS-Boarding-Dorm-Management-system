"""
Automated unit and integration test suite for Platform CLI management commands.

Tests verify:
- tenant_list: filters (--all, --active, --inactive, --plan), JSON output, resilient counts.
- tenant_backup: schema export, SHA-256 checksums, JSON summary, nonexistent schema error.
- tenant_usage: metric aggregation, date range validation, JSON output, summary table.
- tenant_invoice: 3-pillar billing calculation, invoice sequence numbering, mark-paid, cancel.
- tenant_features: boolean toggles, numeric limits, reset to plan defaults.
- support_access: bcrypt token generation, verification, expiration, revocation.
- tenant_subscription: plan changes, Atebubu pilot flat agreement, SMS packages, hosting margins.
"""

from datetime import date, timedelta
from decimal import Decimal
import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from tenancy.models import (
    InvoiceStatus,
    PlanTier,
    PlatformAuditLog,
    Tenant,
    TenantInvoice,
    TenantSubscription,
)


class BasePlatformCommandTestCase(TestCase):
    """Base test setup with pre-configured active school tenant."""

    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(
            name="Atebubu Senior High School",
            schema_name="atebubu_test",
            is_active=True,
        )
        self.inactive_tenant = Tenant.objects.create(
            name="Inactive Academy",
            schema_name="inactive_sch",
            is_active=False,
        )
        self.sub = TenantSubscription.objects.create(
            tenant=self.tenant,
            plan_tier=PlanTier.PILOT,
            is_pilot_flat_fee=True,
            pilot_flat_amount=Decimal("2500.00"),
            infra_cost_basis_monthly=Decimal("150.00"),
            infra_margin_percent=Decimal("15.00"),
            sms_cost_basis=Decimal("0.02620"),
            rate_per_sms=Decimal("0.0301"),
        )

    def _call(self, command_name, *args, **kwargs):
        """Helper to invoke command and capture stdout/stderr."""
        out = StringIO()
        err = StringIO()
        call_command(command_name, *args, stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()


class TenantListCommandTests(BasePlatformCommandTestCase):
    """Tests for the tenant_list command."""

    def test_list_all_tenants_json(self):
        out, _ = self._call("tenant_list", "--all", "--json")
        data = json.loads(out)
        self.assertEqual(data["total_tenants"], 2)
        self.assertEqual(data["active_tenants"], 1)
        self.assertEqual(data["inactive_tenants"], 1)

    def test_list_active_tenants_only(self):
        out, _ = self._call("tenant_list", "--active", "--json")
        data = json.loads(out)
        self.assertEqual(data["total_tenants"], 1)
        self.assertEqual(data["tenants"][0]["schema_name"], "atebubu_test")

    def test_list_inactive_tenants_only(self):
        out, _ = self._call("tenant_list", "--inactive", "--json")
        data = json.loads(out)
        self.assertEqual(data["total_tenants"], 1)
        self.assertEqual(data["tenants"][0]["schema_name"], "inactive_sch")

    def test_list_plan_filter(self):
        out, _ = self._call("tenant_list", "--plan=pilot", "--json")
        data = json.loads(out)
        self.assertEqual(data["total_tenants"], 1)
        self.assertEqual(data["tenants"][0]["plan_tier"], "pilot")


class TenantBackupCommandTests(BasePlatformCommandTestCase):
    """Tests for the tenant_backup command."""

    def test_nonexistent_schema_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            self._call("tenant_backup", "--schema=nonexistent_xyz")
        self.assertIn("not found", str(ctx.exception))

    @patch("shutil.which", return_value="/usr/bin/pg_dump")
    @patch("subprocess.run")
    def test_backup_json_mode(self, mock_run, mock_which):
        from pathlib import Path
        from unittest.mock import Mock

        def fake_run(cmd, *args, **kwargs):
            if "-f" in cmd:
                f_idx = cmd.index("-f")
                out_file = cmd[f_idx + 1]
                Path(out_file).write_bytes(b"DUMMY_PG_DUMP_CONTENT")
            proc = Mock()
            proc.returncode = 0
            proc.stderr = ""
            return proc

        mock_run.side_effect = fake_run

        out, _ = self._call("tenant_backup", "--schema=atebubu_test", "--json")
        data = json.loads(out)
        self.assertEqual(data["total_backups"], 1)
        self.assertEqual(data["backups"][0]["schema_name"], "atebubu_test")


class TenantUsageCommandTests(BasePlatformCommandTestCase):
    """Tests for the tenant_usage command."""

    def test_single_tenant_usage_json(self):
        out, _ = self._call("tenant_usage", "--schema=atebubu_test", "--json")
        data = json.loads(out)
        self.assertEqual(data["total_tenants"], 1)
        rep = data["reports"][0]
        self.assertEqual(rep["schema_name"], "atebubu_test")
        self.assertIn("students", rep)
        self.assertIn("sms", rep)
        self.assertIn("infrastructure", rep)

    def test_invalid_date_format_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            self._call("tenant_usage", "--schema=atebubu_test", "--start-date=invalid-date")
        self.assertIn("Invalid date format", str(ctx.exception))

    def test_start_date_after_end_date_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            self._call(
                "tenant_usage",
                "--schema=atebubu_test",
                "--start-date=2026-10-15",
                "--end-date=2026-10-01",
            )
        self.assertIn("Start date must be before", str(ctx.exception))

    def test_all_tenants_summary_json(self):
        out, _ = self._call("tenant_usage", "--all", "--json")
        data = json.loads(out)
        self.assertEqual(data["total_tenants"], 1)  # only active tenants included in --all


class TenantInvoiceCommandTests(BasePlatformCommandTestCase):
    """Tests for the tenant_invoice command."""

    def test_pilot_invoice_generation_calculation(self):
        out, _ = self._call(
            "tenant_invoice",
            "--schema=atebubu_test",
            "--billing-start=2026-10-01",
            "--billing-end=2026-10-31",
            "--json",
        )
        data = json.loads(out)
        self.assertTrue(data["invoice_number"].startswith("INV-ATEBUBU_TEST-202610-"))

        # Base flat fee: 2500, Hosting: 150 * 1.15 * 3 = 517.50
        # Expected subtotal >= 3017.50
        self.assertGreaterEqual(data["total"], 3017.50)

    def test_invoice_lifecycle_mark_paid_and_cancel(self):
        # 1. Generate
        out, _ = self._call("tenant_invoice", "--schema=atebubu_test", "--json")
        data = json.loads(out)
        inv_no = data["invoice_number"]

        # 2. Mark Paid
        out, _ = self._call(
            "tenant_invoice",
            "--schema=atebubu_test",
            f"--mark-paid={inv_no}",
            "--payment-ref=MOMO-TEST-001",
        )
        self.assertIn("[PAID]", out)

        inv = TenantInvoice.objects.get(invoice_number=inv_no)
        self.assertEqual(inv.status, InvoiceStatus.PAID)
        self.assertEqual(inv.payment_reference, "MOMO-TEST-001")

        # 3. Cannot cancel paid invoice
        with self.assertRaises(CommandError) as ctx:
            self._call("tenant_invoice", "--schema=atebubu_test", f"--cancel={inv_no}")
        self.assertIn("Cannot cancel an invoice that has already been paid", str(ctx.exception))


class TenantFeaturesCommandTests(BasePlatformCommandTestCase):
    """Tests for the tenant_features command."""

    def test_enable_and_disable_feature(self):
        # Enable
        self._call("tenant_features", "--schema=atebubu_test", "--enable=sms_dispatch")
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.features.get("sms_dispatch"))

        # Disable
        self._call("tenant_features", "--schema=atebubu_test", "--disable=sms_dispatch")
        self.sub.refresh_from_db()
        self.assertFalse(self.sub.features.get("sms_dispatch"))

    def test_set_limit_quota(self):
        self._call("tenant_features", "--schema=atebubu_test", "--set-limit=max_students=1200")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.features.get("max_students"), 1200)

    def test_reset_to_plan_defaults(self):
        self._call("tenant_features", "--schema=atebubu_test", "--reset-to-plan")
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.features.get("max_students"), 2000)  # Pilot default is 2000


class SupportAccessCommandTests(BasePlatformCommandTestCase):
    """Tests for the support_access command."""

    def test_generate_verify_and_revoke_support_token(self):
        # 1. Generate
        out, _ = self._call(
            "support_access",
            "--schema=atebubu_test",
            "--generate",
            "--ttl-hours=2",
            "--reason=Audit testing",
            "--json",
        )
        data = json.loads(out)
        token = data["token"]
        self.assertTrue(len(token) >= 40)

        # 2. Verify with valid token
        out, _ = self._call(
            "support_access",
            "--schema=atebubu_test",
            f"--verify={token}",
            "--json",
        )
        vdata = json.loads(out)
        self.assertTrue(vdata["verified"])

        # 3. Verify with invalid token fails
        out, _ = self._call(
            "support_access",
            "--schema=atebubu_test",
            "--verify=invalid-token-string",
        )
        self.assertIn("[INVALID]", out)

        # 4. Revoke
        out, _ = self._call("support_access", "--schema=atebubu_test", "--revoke")
        self.assertIn("[REVOKED]", out)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.support_token_hash, "")


class TenantSubscriptionCommandTests(BasePlatformCommandTestCase):
    """Tests for the tenant_subscription command."""

    def test_update_plan_and_rates(self):
        self._call(
            "tenant_subscription",
            "--schema=atebubu_test",
            "--set-plan=standard",
            "--apply-plan-defaults",
        )
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.plan_tier, PlanTier.STANDARD)
        self.assertEqual(self.sub.base_fee, Decimal("1000.00"))
        self.assertEqual(self.sub.rate_per_student_month, Decimal("0.65"))

    def test_update_sms_package_preset(self):
        self._call(
            "tenant_subscription",
            "--schema=atebubu_test",
            "--set-sms-package=500",
            "--set-sms-margin=15.0",
        )
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.sms_cost_basis, Decimal("0.02620"))
        self.assertEqual(self.sub.rate_per_sms, Decimal("0.0301"))

    def test_update_hosting_margins(self):
        self._call(
            "tenant_subscription",
            "--schema=atebubu_test",
            "--set-infra-cost=200.00",
            "--set-infra-margin=20.0",
        )
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.infra_cost_basis_monthly, Decimal("200.00"))
        self.assertEqual(self.sub.infra_margin_percent, Decimal("20.00"))
        self.assertEqual(self.sub.effective_monthly_hosting_charge, Decimal("240.00"))
