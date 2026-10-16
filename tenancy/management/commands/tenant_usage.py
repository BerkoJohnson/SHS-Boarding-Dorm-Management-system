"""
Management command to aggregate and display tenant usage metrics and capacity.

Tracks student counts, staff accounts, configured dormitories, SMS dispatches,
exeat requests, ID card generation, and hosting cost allocation against
subscription plan quotas.

Usage:
    python manage.py tenant_usage --schema=atebubu_test
    python manage.py tenant_usage --schema=atebubu_test --start-date=2026-09-01 --end-date=2026-10-15
    python manage.py tenant_usage --all
    python manage.py tenant_usage --all --json
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from tenancy.models import PlanTier, PlatformAuditLog, Tenant, TenantSubscription


class Command(BaseCommand):
    """Aggregate and inspect tenant resource usage and commercial plan quotas."""

    help = (
        "Aggregate operational metrics (students, staff, dorms, SMS deliverability, "
        "exeat operations) and compare against plan limits."
    )

    def add_arguments(self, parser):
        """Register CLI options and filters for usage metrics."""
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--schema",
            type=str,
            help="Target tenant schema name to inspect.",
        )
        group.add_argument(
            "--all",
            action="store_true",
            help="Aggregate and summarize usage for all active tenants.",
        )
        parser.add_argument(
            "--start-date",
            type=str,
            help="Start date for period metrics (YYYY-MM-DD, default: 30 days ago).",
        )
        parser.add_argument(
            "--end-date",
            type=str,
            help="End date for period metrics (YYYY-MM-DD, default: today).",
        )
        parser.add_argument(
            "--operator",
            type=str,
            default="cli",
            help="Operator identifier for audit logging.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output machine-readable JSON format.",
        )

    def handle(self, *args, **options):
        """Execute tenant usage reporting."""
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before using platform management commands."
            )

        # Parse date range
        today = timezone.now().date()
        start_str = options.get("start_date")
        end_str = options.get("end_date")

        try:
            start_date = (
                datetime.strptime(start_str, "%Y-%m-%d").date()
                if start_str
                else today - timedelta(days=30)
            )
            end_date = (
                datetime.strptime(end_str, "%Y-%m-%d").date()
                if end_str
                else today
            )
        except ValueError as err:
            raise CommandError("Invalid date format. Use YYYY-MM-DD.") from err

        if start_date > end_date:
            raise CommandError("Start date must be before or equal to end date.")

        # Resolve target tenants
        if options.get("all"):
            tenants = list(Tenant.objects.filter(is_active=True).order_by("schema_name"))
            if not tenants:
                self.stdout.write(self.style.WARNING("No active tenants found."))
                return
        else:
            schema_name = options["schema"]
            tenant = Tenant.objects.filter(schema_name=schema_name).first()
            if tenant is None:
                raise CommandError(f"Tenant schema '{schema_name}' was not found.")
            tenants = [tenant]

        reports: List[Dict[str, Any]] = []

        for tenant in tenants:
            report = self._collect_tenant_usage(tenant, start_date, end_date)
            reports.append(report)

            if not options.get("all") and not options.get("json"):
                self._print_single_tenant_report(report, start_date, end_date)

        # Audit logging
        target_schema = options["schema"] if not options.get("all") else "all"
        try:
            PlatformAuditLog.objects.create(
                action="tenant_usage",
                tenant_schema=target_schema,
                detail=f"Generated usage report for period {start_date} to {end_date}.",
                operator=options.get("operator", "cli"),
                metadata={
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                    "tenants_count": len(reports),
                },
            )
        except Exception:
            pass

        if options.get("json"):
            payload = {
                "period_start": str(start_date),
                "period_end": str(end_date),
                "total_tenants": len(reports),
                "reports": reports,
                "generated_at": timezone.now().isoformat(),
            }
            self.stdout.write(json.dumps(payload, indent=2, default=str))
        elif options.get("all"):
            self._print_all_tenants_summary(reports, start_date, end_date)

    def _collect_tenant_usage(
        self, tenant: Tenant, start_date: date, end_date: date
    ) -> Dict[str, Any]:
        """Query and compute all usage metrics within a tenant schema."""
        # 1. Subscription & limits
        sub = getattr(tenant, "subscription", None)
        if sub is None:
            sub, _ = TenantSubscription.objects.get_or_create(
                tenant=tenant,
                defaults={"plan_tier": PlanTier.FREE},
            )

        features = sub.features or {}
        max_students = features.get("max_students")
        max_staff = features.get("max_staff")
        max_dormitories = features.get("max_dormitories")
        max_sms_monthly = features.get("max_sms_monthly")

        # 2. Check if schema exists
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                [tenant.schema_name],
            )
            schema_exists = bool(cursor.fetchone())

        if not schema_exists:
            return {
                "schema_name": tenant.schema_name,
                "school_name": tenant.name,
                "is_active": tenant.is_active,
                "plan_tier": sub.plan_tier,
                "plan_display": sub.get_plan_tier_display(),
                "error": "Schema not migrated in database",
            }

        # 3. Query tenant schema models
        with tenant:
            from core.models import (
                Dormitory,
                Exeat,
                RollCallSession,
                SMSDelivery,
                Student,
                StudentIDExport,
            )
            User = get_user_model()

            enrolled_students = Student.objects.filter(
                status=Student.EnrollmentStatus.ENROLLED
            ).count()
            total_students = Student.objects.count()
            staff_accounts = User.objects.filter(
                is_staff=True,
                is_active=True,
            ).count()
            dormitories = Dormitory.objects.count()

            # SMS Deliverability
            sms_sent = SMSDelivery.objects.filter(
                status=SMSDelivery.Status.SENT,
                sent_at__date__range=(start_date, end_date),
            ).count()
            sms_period_qs = SMSDelivery.objects.filter(
                created_at__date__range=(start_date, end_date)
            )
            sms_failed = sms_period_qs.filter(status=SMSDelivery.Status.FAILED).count()
            sms_queued = sms_period_qs.filter(status=SMSDelivery.Status.QUEUED).count()

            # Operational events in period
            exeats_count = Exeat.objects.filter(
                created_at__date__range=(start_date, end_date)
            ).count()
            id_exports_count = StudentIDExport.objects.filter(
                created_at__date__range=(start_date, end_date)
            ).count()
            roll_calls_count = RollCallSession.objects.filter(
                created_at__date__range=(start_date, end_date)
            ).count()

        # Hosting & SMS Cost computations
        sms_cost_basis = round(Decimal(sms_sent) * sub.sms_cost_basis, 2)
        sms_billed_amount = round(Decimal(sms_sent) * sub.rate_per_sms, 2)
        monthly_hosting_charge = sub.effective_monthly_hosting_charge

        return {
            "schema_name": tenant.schema_name,
            "school_name": tenant.name,
            "is_active": tenant.is_active,
            "plan_tier": sub.plan_tier,
            "plan_display": sub.get_plan_tier_display(),
            "students": {
                "enrolled": enrolled_students,
                "total": total_students,
                "limit": max_students,
                "usage_ratio": (
                    round(enrolled_students / max_students, 2) if max_students else None
                ),
            },
            "staff": {
                "active_accounts": staff_accounts,
                "limit": max_staff,
                "usage_ratio": (
                    round(staff_accounts / max_staff, 2) if max_staff else None
                ),
            },
            "dormitories": {
                "configured": dormitories,
                "limit": max_dormitories,
                "usage_ratio": (
                    round(dormitories / max_dormitories, 2) if max_dormitories else None
                ),
            },
            "sms": {
                "sent": sms_sent,
                "failed": sms_failed,
                "queued": sms_queued,
                "failure_rate_pct": (
                    round((sms_failed / (sms_sent + sms_failed)) * 100, 1)
                    if (sms_sent + sms_failed) > 0
                    else 0.0
                ),
                "monthly_limit": max_sms_monthly,
                "unit_cost_basis": float(sub.sms_cost_basis),
                "margin_percent": float(sub.sms_margin_percent),
                "unit_rate_charged": float(sub.rate_per_sms),
                "cost_basis_total": float(sms_cost_basis),
                "billed_total": float(sms_billed_amount),
            },
            "infrastructure": {
                "cost_basis_monthly": float(sub.infra_cost_basis_monthly),
                "margin_percent": float(sub.infra_margin_percent),
                "billed_hosting_monthly": float(monthly_hosting_charge),
            },
            "operations": {
                "exeat_requests": exeats_count,
                "id_card_exports": id_exports_count,
                "roll_calls": roll_calls_count,
            },
        }

    def _render_bar(self, current: int, limit: Optional[int], width: int = 18) -> str:
        """Render ASCII capacity meter with usage percentage."""
        if not limit:
            return "[UNLIMITED]"
        ratio = min(1.0, current / limit) if limit > 0 else 1.0
        filled = int(round(ratio * width))
        bar = "=" * filled + "-" * (width - filled)
        pct = int(round((current / limit) * 100))
        return f"[{bar}] {pct:>3}%"

    def _status_badge(self, current: int, limit: Optional[int]) -> str:
        """Return styled status tag based on capacity."""
        if not limit:
            return self.style.SUCCESS("[OK]")
        ratio = current / limit
        if ratio >= 1.0:
            return self.style.ERROR("[EXCEEDED]")
        if ratio >= 0.8:
            return self.style.WARNING("[WARNING]")
        return self.style.SUCCESS("[OK]")

    def _print_single_tenant_report(
        self, report: Dict[str, Any], start_date: date, end_date: date
    ):
        """Render detailed usage metrics for a single tenant."""
        header_line = "=" * 70
        sep_line = "-" * 70

        if "error" in report:
            self.stdout.write(
                self.style.ERROR(
                    f"Cannot inspect tenant '{report['schema_name']}': {report['error']}"
                )
            )
            return

        schema = report["schema_name"]
        plan = report["plan_display"]
        active_status = (
            self.style.SUCCESS("Active")
            if report["is_active"]
            else self.style.ERROR("Inactive")
        )

        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write(
            self.style.NOTICE("|  ")
            + self.style.SUCCESS(f"TENANT USAGE REPORT: {schema}")
        )
        self.stdout.write(
            f"|  Plan: {plan:<22} | Status: "
            + active_status
            + f"{' ':>26}|"
        )
        self.stdout.write(
            f"|  Period: {start_date} to {end_date}{' ':>34}|"
        )
        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write("")

        # Students
        std = report["students"]
        std_limit = f"/ {std['limit']}" if std["limit"] else ""
        std_bar = self._render_bar(std["enrolled"], std["limit"])
        std_badge = self._status_badge(std["enrolled"], std["limit"])
        self.stdout.write(self.style.NOTICE("  STUDENTS"))
        self.stdout.write(f"  +- Enrolled:        {std['enrolled']:,} {std_limit:<8} {std_bar}  {std_badge}")
        self.stdout.write(f"  +- Total records:   {std['total']:,}")
        self.stdout.write("")

        # Staff
        stf = report["staff"]
        stf_limit = f"/ {stf['limit']}" if stf["limit"] else ""
        stf_bar = self._render_bar(stf["active_accounts"], stf["limit"])
        stf_badge = self._status_badge(stf["active_accounts"], stf["limit"])
        self.stdout.write(self.style.NOTICE("  STAFF"))
        self.stdout.write(f"  +- Active accounts: {stf['active_accounts']:,} {stf_limit:<8} {stf_bar}  {stf_badge}")
        self.stdout.write("")

        # Dormitories
        drm = report["dormitories"]
        drm_limit = f"/ {drm['limit']}" if drm["limit"] else ""
        drm_bar = self._render_bar(drm["configured"], drm["limit"])
        drm_badge = self._status_badge(drm["configured"], drm["limit"])
        self.stdout.write(self.style.NOTICE("  DORMITORIES"))
        self.stdout.write(f"  +- Configured:      {drm['configured']:,} {drm_limit:<8} {drm_bar}  {drm_badge}")
        self.stdout.write("")

        # SMS
        sms = report["sms"]
        self.stdout.write(self.style.NOTICE(f"  SMS NOTIFICATIONS ({start_date} to {end_date})"))
        self.stdout.write(f"  +- Delivered:       {sms['sent']:,} messages")
        fail_style = self.style.WARNING if sms["failed"] > 0 else self.style.SUCCESS
        self.stdout.write(f"  +- Failed:          {fail_style(str(sms['failed']))} ({sms['failure_rate_pct']}%)")
        if sms.get("queued", 0) > 0:
            self.stdout.write(f"  +- Queued:          {sms['queued']:,} messages")
        billed_sms_str = f"GHS {sms['billed_total']:.2f}"
        self.stdout.write(f"  +- Cost basis:      GHS {sms['cost_basis_total']:.2f} (@ GHS {sms['unit_cost_basis']:.5f}/msg)")
        self.stdout.write(f"  +- Service Margin:  +{sms['margin_percent']:.1f}%")
        self.stdout.write(f"  +- Billed amount:   {self.style.SUCCESS(billed_sms_str)} (@ GHS {sms['unit_rate_charged']:.4f}/msg)")
        self.stdout.write("")

        # Infrastructure
        infra = report["infrastructure"]
        self.stdout.write(self.style.NOTICE("  CLOUD INFRASTRUCTURE (Render compute + Neon DB)"))
        self.stdout.write(f"  +- Cost basis:      GHS {infra['cost_basis_monthly']:.2f} / month")
        self.stdout.write(f"  +- Service Margin:  +{infra['margin_percent']:.1f}%")
        self.stdout.write(f"  +- Billed hosting:  {self.style.SUCCESS(f'GHS {infra['billed_hosting_monthly']:.2f} / month')}")
        self.stdout.write("")

        # Operations
        ops = report["operations"]
        self.stdout.write(self.style.NOTICE(f"  OPERATIONAL ACTIVITY ({start_date} to {end_date})"))
        self.stdout.write(f"  +- Exeat requests:  {ops['exeat_requests']:,}")
        self.stdout.write(f"  +- ID card exports: {ops['id_card_exports']:,}")
        self.stdout.write(f"  +- Roll call sessions: {ops['roll_calls']:,}")

        self.stdout.write(sep_line)
        self.stdout.write(f"Report generated: {timezone.now():%Y-%m-%d %H:%M:%S UTC}\n")

    def _print_all_tenants_summary(
        self, reports: List[Dict[str, Any]], start_date: date, end_date: date
    ):
        """Render summary table across all tenants."""
        sep_line = "=" * 95
        self.stdout.write(self.style.NOTICE(f"PLATFORM USAGE SUMMARY -- {start_date} to {end_date}"))
        self.stdout.write(sep_line)

        col_fmt = "{:<16} {:<11} {:<15} {:<12} {:<10} {:<10} {:<10}"
        self.stdout.write(
            self.style.SQL_FIELD(
                col_fmt.format(
                    "Schema", "Plan", "Students", "Staff", "SMS Sent", "SMS Fail", "Status"
                )
            )
        )
        self.stdout.write("-" * 95)

        tot_students = 0
        tot_staff = 0
        tot_sms_sent = 0
        tot_sms_fail = 0

        for r in reports:
            if "error" in r:
                self.stdout.write(f"{r['schema_name']:<16} [ERROR: {r['error']}]")
                continue

            schema = r["schema_name"]
            plan = r["plan_tier"]
            std = r["students"]
            stf = r["staff"]
            sms = r["sms"]

            std_str = f"{std['enrolled']:,}" + (f"/{std['limit']}" if std["limit"] else "")
            stf_str = f"{stf['active_accounts']:,}" + (f"/{stf['limit']}" if stf["limit"] else "")

            tot_students += std["enrolled"]
            tot_staff += stf["active_accounts"]
            tot_sms_sent += sms["sent"]
            tot_sms_fail += sms["failed"]

            # Overall status
            if std["limit"] and std["enrolled"] >= std["limit"]:
                status_str = self.style.ERROR("[EXCEEDED]")
            elif std["limit"] and std["enrolled"] >= (std["limit"] * 0.8):
                status_str = self.style.WARNING("[WARNING]")
            else:
                status_str = self.style.SUCCESS("[OK]")

            row = col_fmt.format(
                schema,
                plan,
                std_str,
                stf_str,
                f"{sms['sent']:,}",
                f"{sms['failed']:,}",
                status_str,
            )
            self.stdout.write(row)

        self.stdout.write("-" * 95)
        totals_row = col_fmt.format(
            "TOTALS",
            f"{len(reports)} tenants",
            f"{tot_students:,}",
            f"{tot_staff:,}",
            f"{tot_sms_sent:,}",
            f"{tot_sms_fail:,}",
            "",
        )
        self.stdout.write(self.style.SUCCESS(totals_row))
        self.stdout.write(f"\nGenerated: {timezone.now():%Y-%m-%d %H:%M:%S UTC}\n")
