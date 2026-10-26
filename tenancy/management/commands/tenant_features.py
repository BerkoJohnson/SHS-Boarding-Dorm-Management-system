"""
Management command to view, toggle, configure limits, and reset tenant feature entitlements.

Feature flags gate core subsystem capabilities (roll call, exeats, QR IDs, SMS)
and enforce resource capacity boundaries (max students, max staff, max dormitories).

Usage:
    python manage.py tenant_features --schema=atebubu_test
    python manage.py tenant_features --schema=atebubu_test --enable=sms_dispatch
    python manage.py tenant_features --schema=atebubu_test --disable=exeat_gate
    python manage.py tenant_features --schema=atebubu_test --set-limit=max_students=2000
    python manage.py tenant_features --schema=atebubu_test --reset-to-plan
    python manage.py tenant_features --schema=atebubu_test --json
"""

import json
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tenancy.feature_defaults import PLAN_DEFAULTS
from tenancy.models import PlanTier, PlatformAuditLog, Tenant, TenantSubscription
from ._guided import launch_guided

BOOLEAN_FEATURES = {
    "roll_call": "Roll call and attendance tracking",
    "exeat_gate": "Exeat requests, approvals, and gate movement records",
    "student_id_cards": "QR-coded student ID card generation and export",
    "sms_dispatch": "Parent/guardian SMS notifications and broadcast",
    "roster_import": "Bulk student roster import (CSV/Excel)",
    "advanced_reports": "Advanced operational reports and audit exports",
    "priority_support": "Priority business-hours SLA support",
}

LIMIT_FEATURES = {
    "max_students": "Maximum enrolled students",
    "max_staff": "Maximum staff accounts",
    "max_dormitories": "Maximum configured dormitories",
    "max_sms_monthly": "Monthly SMS segment limit (0=unlimited)",
}


class Command(BaseCommand):
    """Manage tenant feature flags and quota allocations."""

    help = "View, toggle, set limits on, or reset tenant feature entitlements."

    def add_arguments(self, parser):
        """Register CLI options for feature flag management."""
        parser.add_argument(
            "--schema",
            type=str,
            help="Target tenant schema name.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all features and their current state (default action).",
        )
        parser.add_argument(
            "--enable",
            type=str,
            help=f"Enable a feature flag: {', '.join(BOOLEAN_FEATURES.keys())}.",
        )
        parser.add_argument(
            "--disable",
            type=str,
            help="Disable a feature flag.",
        )
        parser.add_argument(
            "--set-limit",
            type=str,
            help="Set a resource limit in key=value format (e.g. max_students=2000).",
        )
        parser.add_argument(
            "--reset-to-plan",
            action="store_true",
            help="Reset all features and limits to plan tier defaults.",
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
        """Execute feature management action."""
        if not options.get("schema"):
            launch_guided(self, "features", options=options)
            return
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before using platform management commands."
            )

        if options.get("enable") and options.get("disable"):
            raise CommandError(
                "Cannot specify both --enable and --disable in the same command."
            )

        schema_name = options["schema"]
        tenant = Tenant.objects.filter(schema_name=schema_name).first()
        if tenant is None:
            raise CommandError(f"Tenant schema '{schema_name}' was not found.")

        # Ensure subscription exists
        sub = getattr(tenant, "subscription", None)
        if sub is None:
            sub, _ = TenantSubscription.objects.get_or_create(
                tenant=tenant,
                defaults={"plan_tier": PlanTier.FREE},
            )

        features = dict(sub.features or {})
        operator = options.get("operator", "cli")

        # 1. Action: Reset to Plan
        if options.get("reset_to_plan"):
            plan_key = sub.plan_tier if sub.plan_tier in PLAN_DEFAULTS else "free"
            new_features = dict(PLAN_DEFAULTS[plan_key])
            sub.features = new_features
            sub.save(update_fields=["features", "updated_at"])

            PlatformAuditLog.objects.create(
                action="feature_reset",
                tenant_schema=schema_name,
                detail=f"All features reset to '{plan_key}' plan defaults.",
                operator=operator,
                metadata={"plan_tier": plan_key, "defaults": new_features},
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"[RESET] Features for '{schema_name}' reset to '{plan_key}' plan defaults."
                )
            )

        # 2. Action: Enable
        elif options.get("enable"):
            key = options["enable"].strip()
            if key not in BOOLEAN_FEATURES:
                raise CommandError(
                    f"Unknown feature '{key}'. Valid boolean features:\n  "
                    + ", ".join(BOOLEAN_FEATURES.keys())
                )
            old_val = features.get(key, False)
            features[key] = True
            sub.features = features
            sub.save(update_fields=["features", "updated_at"])

            PlatformAuditLog.objects.create(
                action="feature_toggle",
                tenant_schema=schema_name,
                detail=f"Feature '{key}' changed from {old_val} to True.",
                operator=operator,
                metadata={"feature": key, "old": old_val, "new": True},
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"[ENABLED] Feature '{key}' is now ENABLED for '{schema_name}'."
                )
            )

        # 3. Action: Disable
        elif options.get("disable"):
            key = options["disable"].strip()
            if key not in BOOLEAN_FEATURES:
                raise CommandError(
                    f"Unknown feature '{key}'. Valid boolean features:\n  "
                    + ", ".join(BOOLEAN_FEATURES.keys())
                )
            old_val = features.get(key, True)
            features[key] = False
            sub.features = features
            sub.save(update_fields=["features", "updated_at"])

            PlatformAuditLog.objects.create(
                action="feature_toggle",
                tenant_schema=schema_name,
                detail=f"Feature '{key}' changed from {old_val} to False.",
                operator=operator,
                metadata={"feature": key, "old": old_val, "new": False},
            )
            self.stdout.write(
                self.style.WARNING(
                    f"[DISABLED] Feature '{key}' is now DISABLED for '{schema_name}'."
                )
            )

        # 4. Action: Set Limit
        elif options.get("set_limit"):
            expr = options["set_limit"].strip()
            if "=" not in expr:
                raise CommandError(
                    "Provide limit in key=value format (e.g. max_students=2000)."
                )
            key, val_str = expr.split("=", 1)
            key = key.strip()
            val_str = val_str.strip()

            if key not in LIMIT_FEATURES:
                raise CommandError(
                    f"Unknown limit key '{key}'. Valid limits:\n  "
                    + ", ".join(LIMIT_FEATURES.keys())
                )

            try:
                val = int(val_str)
                if val < 0:
                    raise ValueError
            except ValueError:
                raise CommandError(
                    f"Limit value must be a non-negative integer: '{val_str}'"
                )

            old_val = features.get(key)
            features[key] = val
            sub.features = features
            sub.save(update_fields=["features", "updated_at"])

            PlatformAuditLog.objects.create(
                action="feature_set_limit",
                tenant_schema=schema_name,
                detail=f"Limit '{key}' changed from {old_val} to {val}.",
                operator=operator,
                metadata={"limit": key, "old": old_val, "new": val},
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"[LIMIT SET] Limit '{key}' set to {val:,} for '{schema_name}'."
                )
            )

        # 5. Display Features List
        if options.get("json"):
            payload = {
                "schema_name": schema_name,
                "plan_tier": sub.plan_tier,
                "features": sub.features or {},
                "boolean_features": {
                    k: sub.features.get(
                        k, PLAN_DEFAULTS.get(sub.plan_tier, {}).get(k, False)
                    )
                    for k in BOOLEAN_FEATURES
                },
                "limit_features": {
                    k: sub.features.get(k, PLAN_DEFAULTS.get(sub.plan_tier, {}).get(k))
                    for k in LIMIT_FEATURES
                },
            }
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            self._print_features_terminal(tenant, sub)

    def _print_features_terminal(self, tenant: Tenant, sub: TenantSubscription):
        """Render beautiful terminal feature table."""
        header_line = "=" * 76
        sep_line = "-" * 76

        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write(
            self.style.NOTICE("|  ")
            + self.style.SUCCESS(
                f"FEATURE ENTITLEMENTS: {tenant.schema_name} ({tenant.name})"
            )
        )
        self.stdout.write(
            f"|  Plan: {sub.get_plan_tier_display():<30} | Currency: {sub.currency:<15}|"
        )
        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write("")

        features = sub.features or {}
        plan_defs = PLAN_DEFAULTS.get(sub.plan_tier, PLAN_DEFAULTS["free"])

        self.stdout.write(self.style.NOTICE("  BOOLEAN FEATURES"))
        col_fmt = "  {:<24} {:<12} {:<36}"
        for key, desc in BOOLEAN_FEATURES.items():
            val = features.get(key, plan_defs.get(key, False))
            status_str = (
                self.style.SUCCESS("[ENABLED]")
                if val
                else self.style.ERROR("[DISABLED]")
            )
            self.stdout.write(col_fmt.format(key, status_str, desc))

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("  NUMERIC LIMIT QUOTAS"))
        col_lim = "  {:<24} {:>10}   {:<36}"
        for key, desc in LIMIT_FEATURES.items():
            lim = features.get(key, plan_defs.get(key))
            lim_str = f"{lim:,}" if lim is not None else "[UNLIMITED]"
            self.stdout.write(col_lim.format(key, lim_str, desc))

        self.stdout.write(sep_line)
        self.stdout.write(f"Updated at: {sub.updated_at:%Y-%m-%d %H:%M:%S UTC}\n")
