"""
Management command to list and inspect school tenants on the Eden Platform.

Provides inventory visibility across all tenants, with status filtering,
plan tier display, primary domain routing resolution, and student headcounts.

Usage:
    python manage.py tenant_list
    python manage.py tenant_list --all
    python manage.py tenant_list --active
    python manage.py tenant_list --inactive
    python manage.py tenant_list --plan=standard
    python manage.py tenant_list --json
"""

import json
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from tenancy.models import Domain, PlanTier, PlatformAuditLog, Tenant, TenantSubscription


class Command(BaseCommand):
    """List and inspect all registered school tenants on the platform."""

    help = (
        "List and inspect school tenants registered on the Eden Platform, "
        "with filters for status (--active, --inactive), plan tier (--plan), "
        "and machine-readable JSON output (--json)."
    )

    def add_arguments(self, parser):
        """Register CLI options and filters for tenant listing."""
        group = parser.add_mutually_exclusive_group()
        group.add_argument(
            "--all",
            action="store_true",
            default=True,
            help="List all registered tenants (default).",
        )
        group.add_argument(
            "--active",
            action="store_true",
            help="Filter to active tenants only.",
        )
        group.add_argument(
            "--inactive",
            action="store_true",
            help="Filter to inactive tenants only.",
        )
        parser.add_argument(
            "--plan",
            type=str,
            choices=[choice.value for choice in PlanTier],
            help="Filter by subscription plan tier (free, starter, standard, premium, pilot, enterprise).",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output data as structured JSON instead of formatted text.",
        )
        parser.add_argument(
            "--operator",
            type=str,
            default="cli",
            help="Identifier of the operator running the command (recorded in audit log).",
        )

    def handle(self, *args, **options):
        """Execute tenant inventory query and render output."""
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before using platform management commands."
            )

        # 1. Build QuerySet based on filters
        qs = Tenant.objects.all().order_by("name")
        filter_label = "All tenants"

        if options.get("active"):
            qs = qs.filter(is_active=True)
            filter_label = "Active tenants only"
        elif options.get("inactive"):
            qs = qs.filter(is_active=False)
            filter_label = "Inactive tenants only"

        plan_filter = options.get("plan")
        if plan_filter:
            qs = qs.filter(subscription__plan_tier=plan_filter)
            filter_label += f" [Plan: {plan_filter}]"

        # 2. Gather data for each tenant
        tenants_data: List[Dict[str, Any]] = []
        total_enrolled = 0
        active_count = 0
        inactive_count = 0
        pilot_count = 0

        for tenant in qs:
            if tenant.is_active:
                active_count += 1
            else:
                inactive_count += 1

            # Primary domain lookup
            primary_domain = (
                Domain.objects.filter(tenant=tenant, is_primary=True).first()
                or Domain.objects.filter(tenant=tenant, is_verified=True).first()
                or Domain.objects.filter(tenant=tenant).first()
            )
            domain_str = primary_domain.domain if primary_domain else "—"

            # Subscription lookup
            try:
                sub = getattr(tenant, "subscription", None)
                if sub is None:
                    sub = TenantSubscription.objects.filter(tenant=tenant).first()
            except Exception:
                sub = None

            plan_tier = sub.plan_tier if sub else PlanTier.FREE
            plan_display = sub.get_plan_tier_display() if sub else "Free / Trial"

            if plan_tier == PlanTier.PILOT:
                pilot_count += 1

            # Student headcount from tenant schema
            enrolled_count = self._get_student_count(tenant)
            if enrolled_count is not None:
                total_enrolled += enrolled_count

            tenants_data.append(
                {
                    "schema_name": tenant.schema_name,
                    "name": tenant.name,
                    "primary_domain": domain_str,
                    "plan_tier": plan_tier,
                    "plan_display": plan_display,
                    "is_active": tenant.is_active,
                    "enrolled_students": enrolled_count,
                    "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
                }
            )

        # 3. Log Audit Trail
        try:
            PlatformAuditLog.objects.create(
                action="tenant_list",
                tenant_schema="all",
                detail=f"Listed tenants with filter '{filter_label}' (found {len(tenants_data)} tenants).",
                operator=options.get("operator", "cli"),
                metadata={
                    "filter": filter_label,
                    "count": len(tenants_data),
                    "plan_filter": plan_filter,
                },
            )
        except Exception:
            pass

        # 4. Output either JSON or Rich Text
        if options.get("json"):
            payload = {
                "filter": filter_label,
                "total_tenants": len(tenants_data),
                "active_tenants": active_count,
                "inactive_tenants": inactive_count,
                "pilot_tenants": pilot_count,
                "total_enrolled_students": total_enrolled,
                "tenants": tenants_data,
                "generated_at": timezone.now().isoformat(),
            }
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            self._print_rich_table(
                tenants_data=tenants_data,
                filter_label=filter_label,
                active_count=active_count,
                inactive_count=inactive_count,
                pilot_count=pilot_count,
                total_enrolled=total_enrolled,
            )

    def _get_student_count(self, tenant: Tenant) -> Optional[int]:
        """
        Safely count enrolled students in the tenant's schema.

        Args:
            tenant: Tenant instance whose PostgreSQL schema is queried.

        Returns:
            int: Number of enrolled students, or None if the schema has not been migrated.
        """
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT 1 FROM information_schema.tables 
                    WHERE table_schema = %s AND table_name = 'core_student'
                    """,
                    [tenant.schema_name],
                )
                if not cursor.fetchone():
                    return None

            with tenant:
                from core.models import Student

                # Student.EnrollmentStatus.ENROLLED is 'Enrolled'
                enrolled_status = getattr(
                    getattr(Student, "EnrollmentStatus", None), "ENROLLED", "Enrolled"
                )
                return Student.objects.filter(status=enrolled_status).count()
        except Exception:
            try:
                with tenant:
                    from core.models import Student

                    return Student.objects.count()
            except Exception:
                return None

    def _print_rich_table(
        self,
        *,
        tenants_data: List[Dict[str, Any]],
        filter_label: str,
        active_count: int,
        inactive_count: int,
        pilot_count: int,
        total_enrolled: int,
    ):
        """Render colorized table to stdout with safe Windows-compatible characters."""
        header_line = "=" * 105
        sep_line = "-" * 105

        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write(
            self.style.NOTICE("|  ")
            + self.style.SUCCESS("EDEN SYS -- REGISTERED SCHOOL TENANTS")
            + self.style.NOTICE(f"{' ':>68}|")
        )
        platform_status = self.style.SUCCESS("Active")
        filter_str = f"|  Filter: {filter_label:<32} | Platform: "
        self.stdout.write(
            self.style.NOTICE(filter_str)
            + platform_status
            + self.style.NOTICE(f"{' ':>51}|")
        )
        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write("")

        col_fmt = "{:<16} {:<26} {:<28} {:<11} {:<10} {:<10}"
        self.stdout.write(
            self.style.SQL_FIELD(
                col_fmt.format(
                    "Schema", "School Name", "Primary Domain", "Plan", "Students", "Status"
                )
            )
        )
        self.stdout.write(sep_line)

        if not tenants_data:
            self.stdout.write(
                self.style.WARNING("No school tenants match the specified criteria.")
            )
        else:
            for item in tenants_data:
                schema_name = item["schema_name"]
                name = item["name"][:25]
                domain = item["primary_domain"][:27]
                plan = item["plan_tier"]

                students_val = (
                    f"{item['enrolled_students']:,}"
                    if item["enrolled_students"] is not None
                    else "-"
                )

                if item["is_active"]:
                    status_str = self.style.SUCCESS("[ACTIVE]")
                else:
                    status_str = self.style.ERROR("[INACTIVE]")

                if plan == "pilot":
                    plan_str = self.style.NOTICE("pilot")
                elif plan in ("premium", "enterprise"):
                    plan_str = self.style.SQL_KEYWORD(plan)
                else:
                    plan_str = plan

                row = f"{schema_name:<16} {name:<26} {domain:<28} {plan_str:<20} {students_val:<10} {status_str}"
                self.stdout.write(row)

        self.stdout.write(sep_line)

        totals_row = f"TOTALS          {len(tenants_data)} registered schools"
        self.stdout.write(
            f"{totals_row:<81} {self.style.SUCCESS(f'{total_enrolled:,} students')}"
        )
        self.stdout.write("")
        self.stdout.write(
            f"Platform Summary: {self.style.SUCCESS(f'{active_count} active')}  |  "
            f"{self.style.ERROR(f'{inactive_count} inactive')}  |  "
            f"{self.style.NOTICE(f'{pilot_count} pilot agreement(s)')}"
        )
        self.stdout.write(
            f"Generated: {timezone.now():%Y-%m-%d %H:%M:%S UTC}\n"
        )
