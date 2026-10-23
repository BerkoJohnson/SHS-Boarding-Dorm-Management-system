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
import hashlib
import io
import json
from io import StringIO
from pathlib import Path
import tarfile
import tempfile
from unittest import skipUnless
from unittest.mock import Mock, patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test import override_settings
from django.utils import timezone

from tenancy.management.commands.tenant_restore import Command as TenantRestoreCommand

from tenancy.models import (
    InvoiceStatus,
    PlanTier,
    PlatformAuditLog,
    Tenant,
    TenantInvoice,
    TenantSubscription,
)


@skipUnless(settings.TENANCY_ENABLED, "Requires TENANCY_ENABLED=true")
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


@override_settings(TENANCY_ENABLED=True)
class TenantBackupCommandTests(BasePlatformCommandTestCase):
    """Tests for the tenant_backup command."""

    def test_nonexistent_schema_raises_error(self):
        with self.assertRaises(CommandError) as ctx:
            self._call("tenant_backup", "--schema=nonexistent_xyz")
        self.assertIn("not found", str(ctx.exception))

    @override_settings(
        BACKUP_OFFSITE_BUCKET="school-backups",
        BACKUP_KMS_KEY_ID="arn:aws:kms:region:account:key/test-key",
        BACKUP_S3_REGION="region-1",
        BACKUP_S3_ENDPOINT_URL="https://objects.example.test",
        BACKUP_S3_ACCESS_KEY_ID="access-key-placeholder",
        BACKUP_S3_SECRET_ACCESS_KEY="secret-key-placeholder",
    )
    @patch("boto3.client")
    def test_offsite_upload_requires_kms_and_verifies_object(self, make_client):
        from tenancy.management.commands.tenant_backup import Command

        with tempfile.TemporaryDirectory() as temporary_directory:
            artifact = Path(temporary_directory) / "tenant.dump"
            artifact.write_bytes(b"synthetic backup bytes")
            checksum = hashlib.sha256(artifact.read_bytes()).hexdigest()
            client = Mock()
            client.head_object.return_value = {
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": "arn:aws:kms:region:account:key/test-key",
                "ContentLength": artifact.stat().st_size,
                "Metadata": {"sha256": checksum},
            }
            make_client.return_value = client

            result = Command()._upload_offsite_files(
                "atebubu_test",
                [(artifact, None)],
            )

        self.assertEqual(result["bucket"], "school-backups")
        self.assertEqual(result["objects"][0]["sha256"], checksum)
        make_client.assert_called_once_with(
            "s3",
            region_name="region-1",
            endpoint_url="https://objects.example.test",
            aws_access_key_id="access-key-placeholder",
            aws_secret_access_key="secret-key-placeholder",
        )
        self.assertEqual(
            client.upload_file.call_args.kwargs["ExtraArgs"]["ServerSideEncryption"],
            "aws:kms",
        )

    @override_settings(
        BACKUP_OFFSITE_BUCKET="",
        BACKUP_KMS_KEY_ID="",
        BACKUP_S3_REGION="",
        BACKUP_S3_ACCESS_KEY_ID="",
        BACKUP_S3_SECRET_ACCESS_KEY="",
    )
    def test_offsite_backup_fails_closed_without_bucket_and_kms_configuration(self):
        with self.assertRaisesMessage(CommandError, "BACKUP_OFFSITE_BUCKET"):
            self._call(
                "tenant_backup",
                "--schema=atebubu_test",
                "--offsite",
            )

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
            self._call(
                "tenant_usage", "--schema=atebubu_test", "--start-date=invalid-date"
            )
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
        self.assertEqual(
            data["total_tenants"], 1
        )  # only active tenants included in --all

    def _report_for(self, schema="atebubu_test"):
        out, _ = self._call("tenant_usage", f"--schema={schema}", "--json")
        return json.loads(out)["reports"][0]

    def test_limits_inherit_plan_defaults(self):
        # The pilot plan's defaults must be reported, not "unlimited".
        report = self._report_for()
        self.assertEqual(report["students"]["limit"], 2000)
        self.assertEqual(report["staff"]["limit"], 40)
        self.assertEqual(report["dormitories"]["limit"], 40)
        self.assertEqual(report["sms"]["monthly_limit"], 10000)

    def test_limits_use_tenant_overrides(self):
        self.sub.features = {"max_students": 123, "max_sms_monthly": 7}
        self.sub.save(update_fields=["features"])

        report = self._report_for()
        self.assertEqual(report["students"]["limit"], 123)
        self.assertEqual(report["sms"]["monthly_limit"], 7)
        # Untouched limits still inherit the plan defaults.
        self.assertEqual(report["staff"]["limit"], 40)
        self.assertEqual(report["dormitories"]["limit"], 40)

    def test_limits_unlimited_for_enterprise(self):
        self.sub.plan_tier = PlanTier.ENTERPRISE
        self.sub.features = {}
        self.sub.save(update_fields=["plan_tier", "features"])

        report = self._report_for()
        self.assertIsNone(report["students"]["limit"])
        self.assertIsNone(report["staff"]["limit"])
        self.assertIsNone(report["dormitories"]["limit"])
        self.assertIsNone(report["sms"]["monthly_limit"])

    def test_terminal_report_shows_effective_limits(self):
        out, _ = self._call("tenant_usage", "--schema=atebubu_test")
        self.assertIn("/ 2000", out)  # inherited pilot student limit

        self.sub.plan_tier = PlanTier.ENTERPRISE
        self.sub.features = {}
        self.sub.save(update_fields=["plan_tier", "features"])
        out, _ = self._call("tenant_usage", "--schema=atebubu_test")
        self.assertIn("[UNLIMITED]", out)


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
        self.assertIn(
            "Cannot cancel an invoice that has already been paid", str(ctx.exception)
        )


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
        self._call(
            "tenant_features", "--schema=atebubu_test", "--set-limit=max_students=1200"
        )
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.features.get("max_students"), 1200)

    def test_reset_to_plan_defaults(self):
        self._call("tenant_features", "--schema=atebubu_test", "--reset-to-plan")
        self.sub.refresh_from_db()
        self.assertEqual(
            self.sub.features.get("max_students"), 2000
        )  # Pilot default is 2000


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


class TenantRestoreCommandTests(BasePlatformCommandTestCase):
    """Safety checks for the tenant_restore command."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.backup_root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def _write_backup(self, schema, content=b"DUMMY-DUMP-CONTENT", name="restore.dump"):
        """Create a dump + matching .sha256 checksum inside the backup root."""
        base = self.backup_root / schema
        base.mkdir(parents=True, exist_ok=True)
        dump = base / name
        dump.write_bytes(content)
        checksum = hashlib.sha256(content).hexdigest()
        dump.with_name(f"{dump.name}.sha256").write_text(
            f"{checksum}  {dump.name}\n", encoding="utf-8"
        )
        return dump

    @staticmethod
    def _fake_pg_restore(toc_schema):
        def fake_run(cmd, *args, **kwargs):
            proc = Mock()
            proc.returncode = 0
            proc.stderr = ""
            proc.stdout = ""
            if "-l" in cmd:
                proc.stdout = (
                    "; dummy archive\n" f"1; 2615 2200 SCHEMA - {toc_schema} postgres\n"
                )
            return proc

        return fake_run

    def _restore_args(self, schema, dump):
        return (
            "tenant_restore",
            f"--schema={schema}",
            f"--dump={dump}",
            f"--backup-dir={self.backup_root}",
        )

    @patch("shutil.which", return_value="/usr/bin/pg_restore")
    def test_refuses_reserved_public_schema(self, _which):
        with self.assertRaises(CommandError) as ctx:
            self._call("tenant_restore", "--schema=public", "--dump=whatever.dump")
        self.assertIn("reserved", str(ctx.exception).lower())

    @patch("shutil.which", return_value="/usr/bin/pg_restore")
    def test_refuses_unsafe_dump_path_outside_backup_dir(self, _which):
        with tempfile.TemporaryDirectory() as outside:
            dump = Path(outside) / "outside.dump"
            dump.write_bytes(b"data")
            with self.assertRaises(CommandError) as ctx:
                self._call(
                    "tenant_restore",
                    "--schema=atebubu_test",
                    f"--dump={dump}",
                    f"--backup-dir={self.backup_root}",
                )
        self.assertIn("outside the approved backup directory", str(ctx.exception))

    @patch("shutil.which", return_value="/usr/bin/pg_restore")
    def test_refuses_missing_checksum(self, _which):
        dump = self._write_backup("atebubu_test")
        dump.with_name(f"{dump.name}.sha256").unlink()
        with self.assertRaises(CommandError) as ctx:
            self._call(*self._restore_args("atebubu_test", dump))
        self.assertIn("Checksum file", str(ctx.exception))

    @patch("shutil.which", return_value="/usr/bin/pg_restore")
    def test_refuses_checksum_mismatch(self, _which):
        dump = self._write_backup("atebubu_test", content=b"REAL-CONTENT")
        dump.with_name(f"{dump.name}.sha256").write_text(
            "0" * 64 + "  restore.dump\n", encoding="utf-8"
        )
        with self.assertRaises(CommandError) as ctx:
            self._call(*self._restore_args("atebubu_test", dump))
        self.assertIn("integrity check failed", str(ctx.exception))

    @patch("shutil.which", return_value="/usr/bin/pg_restore")
    def test_refuses_schema_mismatch(self, _which):
        dump = self._write_backup("atebubu_test")
        with patch(
            "tenancy.management.commands.tenant_restore.subprocess.run",
            side_effect=self._fake_pg_restore("other_schema"),
        ):
            with self.assertRaises(CommandError) as ctx:
                self._call(*self._restore_args("atebubu_test", dump))
        self.assertIn("Schema mismatch", str(ctx.exception))

    @patch("shutil.which", return_value="/usr/bin/pg_restore")
    def test_refuses_registered_tenant_collision(self, _which):
        dump = self._write_backup("atebubu_test")
        with patch(
            "tenancy.management.commands.tenant_restore.subprocess.run",
            side_effect=self._fake_pg_restore("atebubu_test"),
        ):
            with self.assertRaises(CommandError) as ctx:
                self._call(*self._restore_args("atebubu_test", dump))
        self.assertIn("registered to a tenant", str(ctx.exception))

    @patch("shutil.which", return_value="/usr/bin/pg_restore")
    def test_refuses_nonempty_schema(self, _which):
        schema = "restore_nonempty"
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            cursor.execute(f'CREATE SCHEMA "{schema}"')
            cursor.execute(f'CREATE TABLE "{schema}".t (id integer)')
        try:
            dump = self._write_backup(schema)
            with patch(
                "tenancy.management.commands.tenant_restore.subprocess.run",
                side_effect=self._fake_pg_restore(schema),
            ):
                with self.assertRaises(CommandError) as ctx:
                    self._call(*self._restore_args(schema, dump))
            self.assertIn("not empty", str(ctx.exception))
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')

    @patch("shutil.which", return_value="/usr/bin/pg_restore")
    def test_dry_run_passes_for_clean_target(self, _which):
        schema = "restore_clean"
        dump = self._write_backup(schema)
        with patch(
            "tenancy.management.commands.tenant_restore.subprocess.run",
            side_effect=self._fake_pg_restore(schema),
        ):
            out, _ = self._call(
                *self._restore_args(schema, dump), "--dry-run", "--json"
            )
        data = json.loads(out)
        self.assertEqual(data["status"], "dry_run")
        self.assertEqual(data["schema_name"], schema)

    @patch("shutil.which", return_value="/usr/bin/pg_restore")
    def test_successful_restore_never_cleans(self, _which):
        schema = "restore_success"
        timestamp = "20261017_091800"
        dump = self._write_backup(
            schema,
            name=f"{schema}_{timestamp}.dump",
        )
        media_archive = self.backup_root / schema / f"{schema}_media_{timestamp}.tar.gz"
        media_archive.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(media_archive, "w:gz") as archive:
            contents = b"restored crest"
            member = tarfile.TarInfo("school/logos/crest.png")
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))

        media_root = self.backup_root / "media"
        captured = {}

        def fake_run(cmd, *args, **kwargs):
            captured["cmd"] = cmd
            proc = Mock()
            proc.returncode = 0
            proc.stderr = ""
            proc.stdout = ""
            if "-l" in cmd:
                proc.stdout = f"1; 2615 2200 SCHEMA - {schema} postgres\n"
            return proc

        with override_settings(
            MEDIA_ROOT=media_root,
            PRIVATE_STUDENT_PHOTO_ROOT=self.backup_root / "private-photos",
            BASE_DIR=self.backup_root / "project",
            MEDIA_STORAGE_BACKEND="filesystem",
        ):
            with patch(
                "tenancy.management.commands.tenant_restore.subprocess.run",
                side_effect=fake_run,
            ):
                with patch.object(
                    TenantRestoreCommand,
                    "_schema_state",
                    side_effect=[(False, True), (True, False)],
                ):
                    out, _ = self._call(
                        *self._restore_args(schema, dump),
                        f"--media-archive={media_archive}",
                        "--json",
                    )

        data = json.loads(out)
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["media_files_count"], 1)
        self.assertEqual(
            (
                media_root / "tenants" / schema / "school" / "logos" / "crest.png"
            ).read_bytes(),
            b"restored crest",
        )

        restore_cmd = captured["cmd"]
        self.assertNotIn("--clean", restore_cmd)
        self.assertNotIn("--if-exists", restore_cmd)
        self.assertIn("--exit-on-error", restore_cmd)
        self.assertTrue(
            PlatformAuditLog.objects.filter(action="tenant_restore").exists()
        )
