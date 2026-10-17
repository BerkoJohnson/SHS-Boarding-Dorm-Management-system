from decimal import Decimal
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django_pgschemas.models import TenantModel
from django_pgschemas.routing.models import DomainModel


class Tenant(TenantModel):
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if self._state.adding and not settings.TENANCY_ENABLED:
            raise ValidationError(
                "Set TENANCY_ENABLED=true before creating a school tenant."
            )
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Domain(DomainModel):
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(DomainModel.Meta):
        ordering = ["domain", "folder"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant"],
                condition=models.Q(is_primary=True),
                name="unique_primary_domain_per_tenant",
            ),
        ]

    def clean(self):
        super().clean()
        if self.domain:
            try:
                self.domain = normalize_domain(self.domain)
            except ValidationError as error:
                raise ValidationError({"domain": error.message}) from error
        if self.folder:
            self.folder = self.folder.strip("/").lower()
        if self.is_verified and self.verified_at is None:
            self.verified_at = timezone.now()
        elif not self.is_verified:
            self.verified_at = None

    def save(self, *args, **kwargs):
        if self._state.adding and not settings.TENANCY_ENABLED:
            raise ValidationError(
                "Set TENANCY_ENABLED=true before registering school domains."
            )
        with transaction.atomic():
            if self.is_primary and self.tenant_id:
                Tenant.objects.select_for_update().get(pk=self.tenant_id)
                # Keep demotion atomic with validation and saving the replacement.
                Domain.objects.filter(
                    tenant_id=self.tenant_id,
                    is_primary=True,
                ).exclude(pk=self.pk).update(is_primary=False)
            self.full_clean()
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.domain}/{self.folder}" if self.folder else self.domain


def normalize_domain(value: str) -> str:
    domain = value.strip().rstrip(".").lower()
    if not domain or any(character in domain for character in "/:@?#"):
        raise ValidationError("Enter a hostname without a scheme, port, or path.")
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValidationError("Enter a valid internationalized hostname.") from error
    if len(domain) > 253 or not re.fullmatch(
        r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
        domain,
    ):
        raise ValidationError("Enter a valid DNS hostname.")
    return domain


class PlanTier(models.TextChoices):
    """
    Available subscription plan tiers for schools on the Eden Platform.

    Each tier defines default quotas and feature entitlements.
    """

    FREE = "free", "Free / Trial"
    STARTER = "starter", "Starter (Essential)"
    STANDARD = "standard", "Standard (Recommended)"
    PREMIUM = "premium", "Premium (Advanced)"
    PILOT = "pilot", "Pilot (Atebubu SHS)"
    ENTERPRISE = "enterprise", "Enterprise"


class BillingCycle(models.TextChoices):
    """Supported subscription billing cycles."""

    TERMLY = "termly", "Termly (3 academic terms/year)"
    MONTHLY = "monthly", "Monthly"


class TenantSubscription(models.Model):
    """
    Platform-level subscription and billing configuration for a tenant.

    Stores contract terms, plan tier, commercial limits, SMS packaging
    parameters, Render and Neon infrastructure cost-basis, and support tokens.
    Lives in the public PostgreSQL schema.

    Example:
        >>> sub = TenantSubscription.objects.get(tenant__schema_name="atebubu_shs")
        >>> sub.plan_tier
        'pilot'
        >>> sub.pilot_flat_amount
        Decimal('2500.00')
    """

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="subscription",
        help_text="School tenant to which this subscription applies.",
    )
    plan_tier = models.CharField(
        max_length=20,
        choices=PlanTier.choices,
        default=PlanTier.FREE,
        help_text="Current commercial subscription plan tier.",
    )
    billing_cycle = models.CharField(
        max_length=10,
        choices=BillingCycle.choices,
        default=BillingCycle.TERMLY,
        help_text="Billing frequency (termly or monthly).",
    )
    months_per_term = models.PositiveSmallIntegerField(
        default=3,
        help_text="Number of months per academic term for billing calculations (default 3).",
    )

    # Termly Billing rates (in GHS)
    base_fee = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1000.00"),
        help_text="Base platform fee per term (e.g. GH₵500 Starter, GH₵1,000 Standard, GH₵2,000 Premium).",
    )
    rate_per_student_month = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0.65"),
        help_text="Monthly rate per student (e.g. GH₵0.50 Starter, GH₵0.65 Standard, GH₵0.80 Premium).",
    )

    # Pilot agreement override (e.g. Atebubu SHS)
    is_pilot_flat_fee = models.BooleanField(
        default=False,
        help_text="If True, charges pilot_flat_amount per term regardless of student formula.",
    )
    pilot_flat_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("2500.00"),
        help_text="Flat fee per term for pilot agreement (default GH₵2,500 for Atebubu SHS).",
    )

    # Cloud Infrastructure Hosting Cost-basis & Margin model (Render + Neon)
    include_hosting_fee = models.BooleanField(
        default=True,
        help_text="Whether to itemize Render + Neon hosting fees on the invoice.",
    )
    infra_cost_basis_monthly = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("150.00"),
        help_text=(
            "Tenant's monthly infrastructure cost basis in GHS. "
            "Platform hosting total is ~GH₵430/mo ($37 USD). For shared multi-tenant, "
            "default is GH₵150.00/mo (or GH₵430.00 for dedicated single-tenant)."
        ),
    )
    infra_margin_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("15.00"),
        help_text="Service markup percentage applied to hosting cost basis (e.g. 15.00 for 15%).",
    )

    # SMS Cost-basis & Margin model (SMSOnlineGH tiered packages)
    sms_cost_basis = models.DecimalField(
        max_digits=6,
        decimal_places=5,
        default=Decimal("0.02620"),
        help_text=(
            "Unit provider cost per SMS (B/Q from SMSOnlineGH package). "
            "Default GH₵0.02620 corresponds to the GH₵500 (19,083 credits) tier."
        ),
    )
    sms_margin_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("15.00"),
        help_text="Service markup percentage applied to SMS cost basis (e.g. 15.00 for 15%).",
    )
    rate_per_sms = models.DecimalField(
        max_digits=6,
        decimal_places=4,
        default=Decimal("0.0301"),
        help_text=(
            "Effective charge per SMS message sent to school. Computed as "
            "sms_cost_basis * (1 + sms_margin_percent / 100), or overridden manually."
        ),
    )
    currency = models.CharField(
        max_length=3,
        default="GHS",
        help_text="ISO 4217 currency code (e.g. GHS, USD, NGN).",
    )

    # Feature entitlements and limit quotas — stored as JSON for dynamic evolution
    features = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Feature flags and limits. Keys: roll_call (bool), exeat_gate (bool), "
            "student_id_cards (bool), sms_dispatch (bool), roster_import (bool), "
            "advanced_reports (bool), priority_support (bool), max_students (int), "
            "max_staff (int), max_dormitories (int), max_sms_monthly (int)."
        ),
    )

    # Support access token (bcrypt-hashed, with expiration)
    support_token_hash = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="bcrypt hash of the current temporary support access token.",
    )
    support_token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Expiration timestamp for the active support access token.",
    )
    support_token_granted_by = models.CharField(
        max_length=150,
        blank=True,
        default="",
        help_text="Operator identifier who granted support access.",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "tenant subscription"
        verbose_name_plural = "tenant subscriptions"

    def __str__(self):
        return f"{self.tenant.name} ({self.get_plan_tier_display()})"

    @property
    def is_support_token_valid(self) -> bool:
        """
        Check if an active support access token exists and has not expired.

        Returns:
            bool: True if token hash is present and expiration is in the future.
        """
        if not self.support_token_hash:
            return False
        if self.support_token_expires_at is None:
            return False
        return timezone.now() < self.support_token_expires_at

    @property
    def effective_monthly_hosting_charge(self):
        """
        Compute monthly hosting charge with service margin.

        Returns:
            Decimal: infra_cost_basis_monthly * (1 + infra_margin_percent / 100)
        """
        margin_multiplier = 1 + (self.infra_margin_percent / 100)
        return round(self.infra_cost_basis_monthly * margin_multiplier, 2)


class InvoiceStatus(models.TextChoices):
    """Lifecycle states for a tenant invoice."""

    DRAFT = "draft", "Draft"
    ISSUED = "issued", "Issued"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"
    CANCELLED = "cancelled", "Cancelled"


class TenantInvoice(models.Model):
    """
    A generated formal billing invoice for a school tenant.

    Numbers follow the format: INV-{SCHEMA}-{YYYYMM}-{SEQ:03d}
    e.g. INV-ATEBUBU_SHS-202610-001

    Example:
        >>> invoice = TenantInvoice.objects.filter(tenant__schema_name="atebubu_shs").first()
        >>> invoice.total_amount
        Decimal('3120.20')
    """

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="invoices",
        help_text="Tenant school billed by this invoice.",
    )
    invoice_number = models.CharField(
        max_length=50,
        unique=True,
        help_text="Unique auto-generated invoice identifier (INV-SCHEMA-YYYYMM-SEQ).",
    )
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.DRAFT,
        help_text="Current payment and lifecycle state.",
    )

    # Billing period
    billing_period_start = models.DateField(
        help_text="Start date of the invoiced term or period."
    )
    billing_period_end = models.DateField(
        help_text="End date of the invoiced term or period."
    )

    # Amounts (in tenant subscription currency)
    subtotal = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Subtotal of all line items before tax.",
    )
    tax_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Applicable tax amount (default 0.00).",
    )
    total_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Final payable amount (subtotal + tax_amount).",
    )
    currency = models.CharField(
        max_length=3,
        default="GHS",
        help_text="Currency code.",
    )

    # Historical snapshot of subscription at invoice generation time
    plan_tier_snapshot = models.CharField(
        max_length=20,
        default="free",
        help_text="Snapshot of the tenant's plan tier at invoice generation time.",
    )

    # Payment tracking
    paid_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when invoice was marked paid.",
    )
    payment_reference = models.CharField(
        max_length=200,
        blank=True,
        default="",
        help_text="External payment receipt or reference number (e.g. MoMo transaction ID).",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Additional administrative or payment notes.",
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-billing_period_start", "-created_at"]
        verbose_name = "tenant invoice"
        verbose_name_plural = "tenant invoices"

    def __str__(self):
        return f"{self.invoice_number} ({self.tenant.name}) - {self.get_status_display()}"


class TenantInvoiceLineItem(models.Model):
    """
    An itemized charge line within a tenant invoice.

    Categories:
        - base: Software license or term platform fee
        - students: Per-student term charge
        - infrastructure: Render compute & Neon database hosting
        - sms: SMSOnlineGH notifications dispatch
        - addon: Physical ID printing or custom setup
    """

    invoice = models.ForeignKey(
        TenantInvoice,
        on_delete=models.CASCADE,
        related_name="line_items",
        help_text="Parent invoice.",
    )
    description = models.CharField(
        max_length=255,
        help_text="Human-readable line item description with calculation context.",
    )
    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("1.00"),
        help_text="Item quantity (e.g. student count, message count, or months).",
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Unit price per quantity unit.",
    )
    line_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total for this line item (quantity * unit_price).",
    )
    category = models.CharField(
        max_length=30,
        default="base",
        help_text="Category code (base, students, infrastructure, sms, addon).",
    )

    class Meta:
        ordering = ["id"]
        verbose_name = "invoice line item"
        verbose_name_plural = "invoice line items"

    def __str__(self):
        return f"{self.description}: {self.line_total}"


class PlatformAuditLog(models.Model):
    """
    Immutable audit log for all platform-level CLI operations.

    Tracks schema creation, domain verification, status toggling, backups,
    quota modifications, billing events, and support access grants.
    """

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    action = models.CharField(
        max_length=50,
        db_index=True,
        help_text=(
            "Action identifier: tenant_provision, tenant_activate, "
            "tenant_deactivate, tenant_backup, tenant_usage, invoice_generate, "
            "invoice_mark_paid, feature_toggle, feature_set_limit, "
            "support_token_generate, support_token_revoke, subscription_update."
        ),
    )
    tenant_schema = models.CharField(
        max_length=63,
        db_index=True,
        help_text="Schema name of the affected school tenant (or 'all').",
    )
    detail = models.TextField(
        help_text="Human-readable explanation of what changed.",
    )
    operator = models.CharField(
        max_length=150,
        default="cli",
        help_text="Identifier of the operator or script that executed the action.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Machine-readable parameters and change details.",
    )

    class Meta:
        ordering = ["-timestamp", "-id"]
        verbose_name = "platform audit log"
        verbose_name_plural = "platform audit logs"
        default_permissions = ("add", "view")

    def __str__(self):
        return f"[{self.timestamp:%Y-%m-%d %H:%M}] {self.action} on {self.tenant_schema} by {self.operator}"
