"""
Management command to configure, update, inspect, and manage tenant subscription contracts.

Manages plan tiers (Free, Starter, Standard, Premium, Pilot, Enterprise), termly
base fees, per-student monthly rates, Render & Neon hosting cost allocation,
and SMSOnlineGH packaging parameters.

Usage:
    python manage.py tenant_subscription --schema=atebubu_test --show
    python manage.py tenant_subscription --schema=atebubu_test --set-plan=pilot --apply-plan-defaults
    python manage.py tenant_subscription --schema=atebubu_test --set-plan=standard --apply-plan-defaults
    python manage.py tenant_subscription --schema=atebubu_test --set-sms-package=500 --set-sms-margin=15.0
    python manage.py tenant_subscription --schema=atebubu_test --set-infra-cost=150.00 --set-infra-margin=15.0
    python manage.py tenant_subscription --schema=atebubu_test --show --json
"""

from decimal import Decimal
import json
from typing import Any, Dict, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tenancy.feature_defaults import PLAN_DEFAULTS
from tenancy.models import PlanTier, PlatformAuditLog, Tenant, TenantSubscription

# SMSOnlineGH packages: package_price -> (credits, unit_cost_basis)
SMS_PACKAGES = {
    10: (307, Decimal("0.03257")),
    20: (649, Decimal("0.03082")),
    50: (1694, Decimal("0.02952")),
    100: (3508, Decimal("0.02851")),
    200: (7246, Decimal("0.02760")),
    500: (19083, Decimal("0.02620")),
    1000: (39062, Decimal("0.02560")),
    2000: (81300, Decimal("0.02460")),
    3000: (125000, Decimal("0.02400")),
    5000: (208333, Decimal("0.02400")),
}

# Commercial plan defaults (rates in GHS)
TIER_RATE_DEFAULTS = {
    PlanTier.FREE: {
        "base_fee": Decimal("0.00"),
        "rate_per_student_month": Decimal("0.00"),
        "is_pilot_flat_fee": False,
        "pilot_flat_amount": Decimal("0.00"),
        "sms_cost_basis": Decimal("0.00000"),
        "rate_per_sms": Decimal("0.0000"),
    },
    PlanTier.STARTER: {
        "base_fee": Decimal("500.00"),
        "rate_per_student_month": Decimal("0.50"),
        "is_pilot_flat_fee": False,
        "pilot_flat_amount": Decimal("0.00"),
        "sms_cost_basis": Decimal("0.02760"),
        "rate_per_sms": Decimal("0.0317"),
    },
    PlanTier.STANDARD: {
        "base_fee": Decimal("1000.00"),
        "rate_per_student_month": Decimal("0.65"),
        "is_pilot_flat_fee": False,
        "pilot_flat_amount": Decimal("0.00"),
        "sms_cost_basis": Decimal("0.02620"),
        "rate_per_sms": Decimal("0.0301"),
    },
    PlanTier.PREMIUM: {
        "base_fee": Decimal("2000.00"),
        "rate_per_student_month": Decimal("0.80"),
        "is_pilot_flat_fee": False,
        "pilot_flat_amount": Decimal("0.00"),
        "sms_cost_basis": Decimal("0.02560"),
        "rate_per_sms": Decimal("0.0294"),
    },
    PlanTier.PILOT: {
        "base_fee": Decimal("2500.00"),
        "rate_per_student_month": Decimal("0.00"),
        "is_pilot_flat_fee": True,
        "pilot_flat_amount": Decimal("2500.00"),
        "sms_cost_basis": Decimal("0.02620"),
        "rate_per_sms": Decimal("0.0301"),
    },
    PlanTier.ENTERPRISE: {
        "base_fee": Decimal("3000.00"),
        "rate_per_student_month": Decimal("0.60"),
        "is_pilot_flat_fee": False,
        "pilot_flat_amount": Decimal("0.00"),
        "sms_cost_basis": Decimal("0.02400"),
        "rate_per_sms": Decimal("0.0276"),
    },
}


class Command(BaseCommand):
    """View and configure contract parameters, tiers, and pricing models."""

    help = (
        "Configure tenant subscription rates, plan tiers (including Atebubu pilot), "
        "Render/Neon hosting allocation, and SMS package presets."
    )

    def add_arguments(self, parser):
        """Register CLI options for subscription configuration."""
        parser.add_argument(
            "--schema",
            type=str,
            required=True,
            help="Target tenant schema name.",
        )
        parser.add_argument(
            "--show",
            action="store_true",
            help="Show current subscription and billing settings (default action).",
        )
        parser.add_argument(
            "--set-plan",
            type=str,
            choices=[choice.value for choice in PlanTier],
            help="Set subscription plan tier.",
        )
        parser.add_argument(
            "--set-base-fee",
            type=Decimal,
            help="Set term base platform fee (e.g. 1000.00).",
        )
        parser.add_argument(
            "--set-rate-per-student",
            type=Decimal,
            help="Set per-student monthly rate (e.g. 0.65).",
        )
        parser.add_argument(
            "--set-infra-cost",
            type=Decimal,
            help="Set monthly infrastructure cost basis in GHS (e.g. 150.00).",
        )
        parser.add_argument(
            "--set-infra-margin",
            type=Decimal,
            help="Set infrastructure service markup percentage (e.g. 15.0).",
        )
        parser.add_argument(
            "--include-hosting",
            action="store_true",
            help="Include hosting charge line on generated invoices.",
        )
        parser.add_argument(
            "--exclude-hosting",
            action="store_true",
            help="Exclude hosting charge line from generated invoices.",
        )
        parser.add_argument(
            "--set-sms-package",
            type=int,
            choices=list(SMS_PACKAGES.keys()),
            help="Apply SMSOnlineGH package tier preset (e.g. 500 for GHS 500 package).",
        )
        parser.add_argument(
            "--set-sms-cost-basis",
            type=Decimal,
            help="Set custom unit SMS cost basis (e.g. 0.02620).",
        )
        parser.add_argument(
            "--set-sms-margin",
            type=Decimal,
            help="Set SMS service markup percentage (e.g. 15.0).",
        )
        parser.add_argument(
            "--set-rate-per-sms",
            type=Decimal,
            help="Directly override the charged unit rate per SMS message.",
        )
        parser.add_argument(
            "--set-currency",
            type=str,
            help="Set 3-letter currency code (e.g. GHS, USD).",
        )
        parser.add_argument(
            "--apply-plan-defaults",
            action="store_true",
            help="When setting a plan tier, also reset feature flags and limits to plan defaults.",
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
        """Process subscription inspection or updates."""
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before using platform management commands."
            )

        schema_name = options["schema"]
        tenant = Tenant.objects.filter(schema_name=schema_name).first()
        if tenant is None:
            raise CommandError(f"Tenant schema '{schema_name}' was not found.")

        sub = getattr(tenant, "subscription", None)
        if sub is None:
            sub, _ = TenantSubscription.objects.get_or_create(
                tenant=tenant,
                defaults={"plan_tier": PlanTier.FREE},
            )

        updated_fields = []
        changes = {}
        operator = options.get("operator", "cli")

        # 1. Update Plan Tier
        if options.get("set_plan"):
            new_plan = options["set_plan"]
            old_plan = sub.plan_tier
            sub.plan_tier = new_plan
            updated_fields.append("plan_tier")
            changes["plan_tier"] = {"old": old_plan, "new": new_plan}

            # Apply rate defaults for tier
            if new_plan in TIER_RATE_DEFAULTS:
                defs = TIER_RATE_DEFAULTS[new_plan]
                sub.base_fee = defs["base_fee"]
                sub.rate_per_student_month = defs["rate_per_student_month"]
                sub.is_pilot_flat_fee = defs["is_pilot_flat_fee"]
                sub.pilot_flat_amount = defs["pilot_flat_amount"]
                sub.sms_cost_basis = defs["sms_cost_basis"]
                sub.rate_per_sms = defs["rate_per_sms"]
                updated_fields.extend(
                    [
                        "base_fee",
                        "rate_per_student_month",
                        "is_pilot_flat_fee",
                        "pilot_flat_amount",
                        "sms_cost_basis",
                        "rate_per_sms",
                    ]
                )

            # Reset feature entitlements if requested
            if options.get("apply_plan_defaults"):
                plan_key = new_plan if new_plan in PLAN_DEFAULTS else "free"
                sub.features = dict(PLAN_DEFAULTS[plan_key])
                updated_fields.append("features")
                changes["features_reset"] = plan_key

        # 2. Update Base Fee
        if options.get("set_base_fee") is not None:
            val = options["set_base_fee"]
            if val < 0:
                raise CommandError("Base fee cannot be negative.")
            changes["base_fee"] = {"old": str(sub.base_fee), "new": str(val)}
            sub.base_fee = val
            updated_fields.append("base_fee")

        # 3. Update Student Rate
        if options.get("set_rate_per_student") is not None:
            val = options["set_rate_per_student"]
            if val < 0:
                raise CommandError("Per-student rate cannot be negative.")
            changes["rate_per_student_month"] = {
                "old": str(sub.rate_per_student_month),
                "new": str(val),
            }
            sub.rate_per_student_month = val
            updated_fields.append("rate_per_student_month")

        # 4. Update Hosting Infrastructure Parameters
        if options.get("set_infra_cost") is not None:
            val = options["set_infra_cost"]
            if val < 0:
                raise CommandError("Infrastructure cost basis cannot be negative.")
            changes["infra_cost_basis_monthly"] = {
                "old": str(sub.infra_cost_basis_monthly),
                "new": str(val),
            }
            sub.infra_cost_basis_monthly = val
            updated_fields.append("infra_cost_basis_monthly")

        if options.get("set_infra_margin") is not None:
            val = options["set_infra_margin"]
            if val < 0:
                raise CommandError("Margin cannot be negative.")
            changes["infra_margin_percent"] = {
                "old": str(sub.infra_margin_percent),
                "new": str(val),
            }
            sub.infra_margin_percent = val
            updated_fields.append("infra_margin_percent")

        if options.get("include_hosting"):
            sub.include_hosting_fee = True
            updated_fields.append("include_hosting_fee")
            changes["include_hosting_fee"] = True
        elif options.get("exclude_hosting"):
            sub.include_hosting_fee = False
            updated_fields.append("include_hosting_fee")
            changes["include_hosting_fee"] = False

        # 5. Update SMS Parameters
        if options.get("set_sms_package") is not None:
            pkg_price = options["set_sms_package"]
            credits, unit_cost = SMS_PACKAGES[pkg_price]
            margin_multiplier = Decimal("1.0") + (sub.sms_margin_percent / Decimal("100.0"))
            effective_rate = round(unit_cost * margin_multiplier, 4)

            sub.sms_cost_basis = unit_cost
            sub.rate_per_sms = effective_rate
            updated_fields.extend(["sms_cost_basis", "rate_per_sms"])
            changes["sms_package"] = {
                "package_price": pkg_price,
                "credits": credits,
                "unit_cost": str(unit_cost),
                "effective_rate": str(effective_rate),
            }

        if options.get("set_sms_cost_basis") is not None:
            val = options["set_sms_cost_basis"]
            if val < 0:
                raise CommandError("SMS cost basis cannot be negative.")
            sub.sms_cost_basis = val
            updated_fields.append("sms_cost_basis")
            changes["sms_cost_basis"] = str(val)

        if options.get("set_sms_margin") is not None:
            val = options["set_sms_margin"]
            if val < 0:
                raise CommandError("SMS margin cannot be negative.")
            sub.sms_margin_percent = val
            updated_fields.append("sms_margin_percent")
            # Recalculate effective rate
            margin_mult = Decimal("1.0") + (val / Decimal("100.0"))
            sub.rate_per_sms = round(sub.sms_cost_basis * margin_mult, 4)
            updated_fields.append("rate_per_sms")
            changes["sms_margin_percent"] = str(val)

        if options.get("set_rate_per_sms") is not None:
            val = options["set_rate_per_sms"]
            if val < 0:
                raise CommandError("Rate per SMS cannot be negative.")
            sub.rate_per_sms = val
            updated_fields.append("rate_per_sms")
            changes["rate_per_sms"] = str(val)

        # 6. Currency
        if options.get("set_currency"):
            curr = options["set_currency"].strip().upper()
            if len(curr) != 3:
                raise CommandError("Currency must be a 3-letter ISO 4217 code (e.g. GHS).")
            sub.currency = curr
            updated_fields.append("currency")
            changes["currency"] = curr

        # Save updates if any
        if updated_fields:
            updated_fields.append("updated_at")
            sub.save(update_fields=list(set(updated_fields)))

            PlatformAuditLog.objects.create(
                action="subscription_update",
                tenant_schema=schema_name,
                detail=f"Subscription updated for '{schema_name}'. Changed: {', '.join(changes.keys())}.",
                operator=operator,
                metadata={"changes": changes},
            )
            self.stdout.write(
                self.style.SUCCESS(
                    f"[UPDATED] Subscription for '{schema_name}' updated successfully."
                )
            )

        # Output current configuration
        if options.get("json"):
            payload = {
                "schema_name": schema_name,
                "plan_tier": sub.plan_tier,
                "plan_display": sub.get_plan_tier_display(),
                "billing_cycle": sub.billing_cycle,
                "months_per_term": sub.months_per_term,
                "base_fee": float(sub.base_fee),
                "rate_per_student_month": float(sub.rate_per_student_month),
                "is_pilot_flat_fee": sub.is_pilot_flat_fee,
                "pilot_flat_amount": float(sub.pilot_flat_amount),
                "hosting": {
                    "include_hosting_fee": sub.include_hosting_fee,
                    "cost_basis_monthly": float(sub.infra_cost_basis_monthly),
                    "margin_percent": float(sub.infra_margin_percent),
                    "billed_monthly": float(sub.effective_monthly_hosting_charge),
                },
                "sms": {
                    "unit_cost_basis": float(sub.sms_cost_basis),
                    "margin_percent": float(sub.sms_margin_percent),
                    "unit_rate_charged": float(sub.rate_per_sms),
                },
                "currency": sub.currency,
                "updated_at": sub.updated_at.isoformat() if sub.updated_at else None,
            }
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            self._print_subscription_terminal(tenant, sub)

    def _print_subscription_terminal(self, tenant: Tenant, sub: TenantSubscription):
        """Render beautiful terminal subscription details."""
        header_line = "=" * 70
        sep_line = "-" * 70

        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write(
            self.style.NOTICE("|  ")
            + self.style.SUCCESS(f"SUBSCRIPTION CONTRACT: {tenant.name} ({tenant.schema_name})")
        )
        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))

        self.stdout.write(self.style.NOTICE("  PLAN TIER & CONTRACT TERMS"))
        self.stdout.write(f"  +- Tier:              {self.style.SUCCESS(sub.get_plan_tier_display())}")
        self.stdout.write(f"  +- Billing Cycle:     {sub.get_billing_cycle_display()}")
        self.stdout.write(f"  +- Months / Term:     {sub.months_per_term} months")
        self.stdout.write(f"  +- Currency:          {sub.currency}")
        self.stdout.write("")

        self.stdout.write(self.style.NOTICE("  SUBSCRIPTION RATES (3 Academic Terms / Year)"))
        if sub.is_pilot_flat_fee:
            self.stdout.write(
                f"  +- Agreement:         {self.style.NOTICE('SPECIAL PILOT AGREEMENT')}\n"
                f"  +- Flat Fee / Term:   {self.style.SUCCESS(f'GHS {sub.pilot_flat_amount:,.2f}')} (includes up to 2,000 students)"
            )
        else:
            term_std_rate = sub.rate_per_student_month * Decimal(sub.months_per_term or 3)
            self.stdout.write(
                f"  +- Base Fee / Term:   GHS {sub.base_fee:,.2f}\n"
                f"  +- Student / Month:   GHS   {sub.rate_per_student_month:.2f} "
                f"(GHS {term_std_rate:.2f} / student per {sub.months_per_term}-month term)"
            )
        self.stdout.write("")

        self.stdout.write(self.style.NOTICE("  CLOUD INFRASTRUCTURE (Render web/workers/Redis + Neon DB)"))
        hosting_flag = (
            self.style.SUCCESS("[ITEMIZED ON INVOICE]")
            if sub.include_hosting_fee
            else self.style.WARNING("[EXCLUDED]")
        )
        self.stdout.write(f"  +- Status:            {hosting_flag}")
        self.stdout.write(f"  +- Cost Basis:        GHS {sub.infra_cost_basis_monthly:.2f} / month (~US$12.88)")
        self.stdout.write(f"  +- Service Markup:    +{sub.infra_margin_percent:.1f}%")
        self.stdout.write(
            f"  +- Billed Monthly:    {self.style.SUCCESS(f'GHS {sub.effective_monthly_hosting_charge:.2f}')} "
            f"(GHS {sub.effective_monthly_hosting_charge * Decimal(sub.months_per_term):,.2f} / term)"
        )
        self.stdout.write("")

        self.stdout.write(self.style.NOTICE("  SMS NOTIFICATIONS (SMSOnlineGH Tiered Packaging)"))
        self.stdout.write(f"  +- Unit Cost Basis:   GHS {sub.sms_cost_basis:.5f} / message")
        self.stdout.write(f"  +- Service Markup:    +{sub.sms_margin_percent:.1f}%")
        self.stdout.write(f"  +- Effective Charge:  {self.style.SUCCESS(f'GHS {sub.rate_per_sms:.4f} / message')}")

        self.stdout.write(sep_line)
        self.stdout.write(f"Last updated: {sub.updated_at:%Y-%m-%d %H:%M:%S UTC}\n")
