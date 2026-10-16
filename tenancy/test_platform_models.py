"""
Unit tests for platform-level billing, subscription, and audit models in Layer 1.

Tests cover:
- TenantSubscription model creation, plan tiers, and computed properties.
- Support access token validity and expiration.
- TenantInvoice and TenantInvoiceLineItem relationships and status transitions.
- PlatformAuditLog persistence and ordering.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from tenancy.models import (
    BillingCycle,
    Domain,
    InvoiceStatus,
    PlanTier,
    PlatformAuditLog,
    Tenant,
    TenantInvoice,
    TenantInvoiceLineItem,
    TenantSubscription,
)


class TenantSubscriptionModelTests(TestCase):
    """Unit tests for the TenantSubscription model."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Atebubu Senior High School",
            schema_name="atebubu_test",
            is_active=True,
        )
        Domain.objects.create(
            tenant=self.tenant,
            domain="atebubu.test.edu.gh",
            is_verified=True,
        )

    def test_default_subscription_creation(self):
        """Verify default subscription values match Free tier."""
        subscription = TenantSubscription.objects.create(tenant=self.tenant)
        self.assertEqual(subscription.plan_tier, PlanTier.FREE)
        self.assertEqual(subscription.billing_cycle, BillingCycle.TERMLY)
        self.assertEqual(subscription.months_per_term, 3)
        self.assertEqual(subscription.currency, "GHS")
        self.assertEqual(subscription.base_fee, Decimal("1000.00"))
        self.assertEqual(subscription.rate_per_student_month, Decimal("0.65"))
        self.assertEqual(subscription.sms_cost_basis, Decimal("0.02620"))
        self.assertEqual(subscription.sms_margin_percent, Decimal("15.00"))
        self.assertFalse(subscription.is_pilot_flat_fee)
        self.assertTrue(subscription.include_hosting_fee)
        self.assertEqual(subscription.infra_cost_basis_monthly, Decimal("150.00"))
        self.assertEqual(subscription.infra_margin_percent, Decimal("15.00"))

    def test_atebubu_pilot_configuration(self):
        """Verify Atebubu SHS pilot agreement parameters can be persisted."""
        subscription = TenantSubscription.objects.create(
            tenant=self.tenant,
            plan_tier=PlanTier.PILOT,
            is_pilot_flat_fee=True,
            pilot_flat_amount=Decimal("2500.00"),
            features={
                "roll_call": True,
                "exeat_gate": True,
                "student_id_cards": True,
                "sms_dispatch": True,
                "max_students": 2000,
                "max_staff": 40,
                "max_dormitories": 40,
            },
        )
        self.assertTrue(subscription.is_pilot_flat_fee)
        self.assertEqual(subscription.pilot_flat_amount, Decimal("2500.00"))
        self.assertEqual(subscription.features["max_students"], 2000)
        self.assertEqual(subscription.features["max_dormitories"], 40)

    def test_effective_monthly_hosting_charge(self):
        """Verify computed monthly hosting charge with service margin."""
        subscription = TenantSubscription.objects.create(
            tenant=self.tenant,
            infra_cost_basis_monthly=Decimal("150.00"),
            infra_margin_percent=Decimal("15.00"),
        )
        # 150 * 1.15 = 172.50
        self.assertEqual(
            subscription.effective_monthly_hosting_charge,
            Decimal("172.50"),
        )

    def test_support_token_validity(self):
        """Test support token validity check with active, expired, and empty tokens."""
        subscription = TenantSubscription.objects.create(tenant=self.tenant)

        # No token
        self.assertFalse(subscription.is_support_token_valid)

        # Valid active token (expires in 4 hours)
        subscription.support_token_hash = "$2b$12$e8x...samplehash"
        subscription.support_token_expires_at = timezone.now() + timedelta(hours=4)
        subscription.save()
        self.assertTrue(subscription.is_support_token_valid)

        # Expired token
        subscription.support_token_expires_at = timezone.now() - timedelta(minutes=1)
        subscription.save()
        self.assertFalse(subscription.is_support_token_valid)

    def test_cascade_delete_with_tenant(self):
        """Subscription should be deleted when parent tenant is deleted."""
        TenantSubscription.objects.create(tenant=self.tenant)
        self.tenant.delete()
        self.assertFalse(TenantSubscription.objects.filter(tenant_id=self.tenant.id).exists())


class TenantInvoiceModelTests(TestCase):
    """Unit tests for TenantInvoice and TenantInvoiceLineItem."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Greenhill Academy",
            schema_name="greenhill_test",
            is_active=True,
        )

    def test_invoice_creation_with_line_items(self):
        """Test invoice creation with itemized line items and totals."""
        invoice = TenantInvoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-GREENHILL-202610-001",
            status=InvoiceStatus.ISSUED,
            billing_period_start=date(2026, 9, 1),
            billing_period_end=date(2026, 12, 15),
            subtotal=Decimal("3120.20"),
            tax_amount=Decimal("0.00"),
            total_amount=Decimal("3120.20"),
            currency="GHS",
            plan_tier_snapshot="pilot",
        )

        item1 = TenantInvoiceLineItem.objects.create(
            invoice=invoice,
            description="Pilot Agreement Flat Subscription",
            quantity=Decimal("1.00"),
            unit_price=Decimal("2500.0000"),
            line_total=Decimal("2500.00"),
            category="base",
        )
        item2 = TenantInvoiceLineItem.objects.create(
            invoice=invoice,
            description="Cloud Hosting (Render + Neon)",
            quantity=Decimal("3.00"),
            unit_price=Decimal("172.5000"),
            line_total=Decimal("517.50"),
            category="infrastructure",
        )
        item3 = TenantInvoiceLineItem.objects.create(
            invoice=invoice,
            description="SMS notifications (3,412 messages @ GH₵0.0301)",
            quantity=Decimal("3412.00"),
            unit_price=Decimal("0.0301"),
            line_total=Decimal("102.70"),
            category="sms",
        )

        self.assertEqual(invoice.line_items.count(), 3)
        calculated_total = sum(item.line_total for item in invoice.line_items.all())
        self.assertEqual(calculated_total, invoice.total_amount)

    def test_unique_invoice_number(self):
        """Duplicate invoice numbers must be rejected."""
        TenantInvoice.objects.create(
            tenant=self.tenant,
            invoice_number="INV-DUP-001",
            billing_period_start=date(2026, 9, 1),
            billing_period_end=date(2026, 12, 15),
        )
        with self.assertRaises(Exception):
            TenantInvoice.objects.create(
                tenant=self.tenant,
                invoice_number="INV-DUP-001",
                billing_period_start=date(2026, 9, 1),
                billing_period_end=date(2026, 12, 15),
            )


class PlatformAuditLogModelTests(TestCase):
    """Unit tests for the PlatformAuditLog model."""

    def test_audit_log_entry_creation(self):
        """Verify audit log entries record action, tenant, detail, and metadata."""
        log = PlatformAuditLog.objects.create(
            action="tenant_provision",
            tenant_schema="atebubu_shs",
            detail="Provisioned Atebubu Senior High School on pilot tier.",
            operator="admin_cobby",
            metadata={"plan": "pilot", "flat_fee": 2500.00},
        )
        self.assertIsNotNone(log.id)
        self.assertIsNotNone(log.timestamp)
        self.assertEqual(log.operator, "admin_cobby")
        self.assertEqual(log.metadata["plan"], "pilot")

    def test_audit_log_ordering(self):
        """Audit logs must be ordered newest first by default."""
        log1 = PlatformAuditLog.objects.create(
            action="action_1",
            tenant_schema="schema_1",
            detail="First action",
        )
        log2 = PlatformAuditLog.objects.create(
            action="action_2",
            tenant_schema="schema_2",
            detail="Second action",
        )
        logs = list(PlatformAuditLog.objects.all())
        self.assertEqual(logs[0].id, log2.id)
        self.assertEqual(logs[1].id, log1.id)
