"""
Management command to generate, calculate, inspect, and manage tenant billing invoices.

Implements the 3-pillar billing model:
  1. Software Subscription (termly base fee + per-student monthly rate * term months,
     or pilot flat fee override).
  2. Cloud Hosting & Database Infrastructure (Render compute/worker/Redis + Neon DB
     cost basis with service markup margin).
  3. SMS Deliverability (actual delivered messages billed at SMSOnlineGH tiered
     cost basis with service markup margin).

Supports PDF invoice rendering via reportlab and lifecycle state management
(DRAFT -> ISSUED -> PAID / CANCELLED).

Usage:
    python manage.py tenant_invoice --schema=atebubu_test
    python manage.py tenant_invoice --schema=atebubu_test --generate-pdf
    python manage.py tenant_invoice --schema=atebubu_test --list
    python manage.py tenant_invoice --schema=atebubu_test --show=INV-ATEBUBU_TEST-202610-001
    python manage.py tenant_invoice --schema=atebubu_test --mark-paid=INV-ATEBUBU_TEST-202610-001 --payment-ref="MOMO-123"
    python manage.py tenant_invoice --schema=atebubu_test --cancel=INV-ATEBUBU_TEST-202610-001
"""

import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from tenancy.models import (
    InvoiceStatus,
    PlanTier,
    PlatformAuditLog,
    Tenant,
    TenantInvoice,
    TenantInvoiceLineItem,
    TenantSubscription,
)


class Command(BaseCommand):
    """Generate and manage tenant billing invoices and payments."""

    help = (
        "Generate 3-pillar billing invoices (Subscription + Hosting + SMS), "
        "render PDFs, and manage invoice status (mark paid, list, show, cancel)."
    )

    def add_arguments(self, parser):
        """Register CLI options and actions for invoice management."""
        parser.add_argument(
            "--schema",
            type=str,
            required=True,
            help="Target tenant schema name.",
        )
        parser.add_argument(
            "--billing-start",
            type=str,
            help="Start date of billing period (YYYY-MM-DD, default: 1st of current month).",
        )
        parser.add_argument(
            "--billing-end",
            type=str,
            help="End date of billing period (YYYY-MM-DD, default: end of current month).",
        )
        parser.add_argument(
            "--generate-pdf",
            action="store_true",
            help="Render and save a formal PDF invoice file.",
        )
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Directory for PDF invoices (default: .private/invoices/).",
        )
        parser.add_argument(
            "--mark-paid",
            type=str,
            help="Invoice number to mark as paid.",
        )
        parser.add_argument(
            "--payment-ref",
            type=str,
            default="",
            help="Payment reference / transaction ID (used with --mark-paid).",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List all billing invoices for this tenant.",
        )
        parser.add_argument(
            "--show",
            type=str,
            help="Show detailed breakdown of a specific invoice number.",
        )
        parser.add_argument(
            "--cancel",
            type=str,
            help="Cancel an unpaid draft or issued invoice number.",
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
        """Route and execute requested invoice action."""
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before using platform management commands."
            )

        schema_name = options["schema"]
        tenant = Tenant.objects.filter(schema_name=schema_name).first()
        if tenant is None:
            raise CommandError(f"Tenant schema '{schema_name}' was not found.")

        # Sub-actions
        if options.get("list"):
            self._handle_list(tenant, options)
            return

        if options.get("show"):
            self._handle_show(tenant, options["show"], options)
            return

        if options.get("mark_paid"):
            self._handle_mark_paid(
                tenant, options["mark_paid"], options.get("payment_ref", ""), options
            )
            return

        if options.get("cancel"):
            self._handle_cancel(tenant, options["cancel"], options)
            return

        # Default action: Generate new invoice
        self._handle_generate(tenant, options)

    def _handle_list(self, tenant: Tenant, options: Dict[str, Any]):
        """List all invoices for tenant."""
        invoices = tenant.invoices.all().order_by("-billing_period_start")

        if options.get("json"):
            data = [
                {
                    "invoice_number": inv.invoice_number,
                    "status": inv.status,
                    "period_start": str(inv.billing_period_start),
                    "period_end": str(inv.billing_period_end),
                    "total_amount": float(inv.total_amount),
                    "currency": inv.currency,
                    "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
                    "payment_reference": inv.payment_reference,
                }
                for inv in invoices
            ]
            self.stdout.write(json.dumps(data, indent=2))
            return

        header_line = "=" * 80
        self.stdout.write(self.style.NOTICE(f"INVOICES FOR {tenant.name} ({tenant.schema_name})"))
        self.stdout.write(header_line)

        col_fmt = "{:<26} {:<24} {:<15} {:<12}"
        self.stdout.write(
            self.style.SQL_FIELD(
                col_fmt.format("Invoice #", "Period", "Total", "Status")
            )
        )
        self.stdout.write("-" * 80)

        total_outstanding = Decimal("0.00")
        for inv in invoices:
            period_str = f"{inv.billing_period_start}..{inv.billing_period_end}"
            amt_str = f"{inv.currency} {inv.total_amount:,.2f}"

            if inv.status == InvoiceStatus.PAID:
                status_str = self.style.SUCCESS("[PAID]")
            elif inv.status == InvoiceStatus.OVERDUE:
                status_str = self.style.ERROR("[OVERDUE]")
                total_outstanding += inv.total_amount
            elif inv.status == InvoiceStatus.CANCELLED:
                status_str = self.style.NOTICE("[CANCELLED]")
            else:
                status_str = self.style.WARNING(f"[{inv.status.upper()}]")
                total_outstanding += inv.total_amount

            self.stdout.write(col_fmt.format(inv.invoice_number, period_str, amt_str, status_str))

        self.stdout.write("-" * 80)
        self.stdout.write(
            f"{len(invoices)} invoice(s)  |  "
            f"Outstanding balance: {self.style.WARNING(f'GHS {total_outstanding:,.2f}')}\n"
        )

    def _handle_show(self, tenant: Tenant, invoice_number: str, options: Dict[str, Any]):
        """Display single invoice details."""
        invoice = TenantInvoice.objects.filter(
            tenant=tenant, invoice_number=invoice_number
        ).first()
        if invoice is None:
            raise CommandError(f"Invoice '{invoice_number}' was not found for tenant '{tenant.schema_name}'.")

        line_items = list(invoice.line_items.all())

        if options.get("json"):
            data = {
                "invoice_number": invoice.invoice_number,
                "tenant": tenant.schema_name,
                "status": invoice.status,
                "period_start": str(invoice.billing_period_start),
                "period_end": str(invoice.billing_period_end),
                "currency": invoice.currency,
                "subtotal": float(invoice.subtotal),
                "tax_amount": float(invoice.tax_amount),
                "total_amount": float(invoice.total_amount),
                "plan_tier_snapshot": invoice.plan_tier_snapshot,
                "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
                "payment_reference": invoice.payment_reference,
                "notes": invoice.notes,
                "line_items": [
                    {
                        "description": item.description,
                        "category": item.category,
                        "quantity": float(item.quantity),
                        "unit_price": float(item.unit_price),
                        "line_total": float(item.line_total),
                    }
                    for item in line_items
                ],
            }
            self.stdout.write(json.dumps(data, indent=2))
            return

        self._print_invoice_terminal(invoice, line_items)

    def _handle_mark_paid(
        self, tenant: Tenant, invoice_number: str, payment_ref: str, options: Dict[str, Any]
    ):
        """Mark an invoice as paid."""
        invoice = TenantInvoice.objects.filter(
            tenant=tenant, invoice_number=invoice_number
        ).first()
        if invoice is None:
            raise CommandError(f"Invoice '{invoice_number}' was not found.")

        if invoice.status == InvoiceStatus.PAID:
            raise CommandError(f"Invoice '{invoice_number}' is already marked as paid.")

        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = timezone.now()
        invoice.payment_reference = payment_ref
        invoice.save(update_fields=["status", "paid_at", "payment_reference", "updated_at"])

        operator = options.get("operator", "cli")
        PlatformAuditLog.objects.create(
            action="invoice_mark_paid",
            tenant_schema=tenant.schema_name,
            detail=f"Invoice {invoice_number} marked as PAID. Reference: '{payment_ref}'.",
            operator=operator,
            metadata={
                "invoice_number": invoice_number,
                "total_amount": str(invoice.total_amount),
                "payment_ref": payment_ref,
            },
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"[PAID] Invoice '{invoice_number}' ({invoice.currency} {invoice.total_amount:,.2f}) "
                f"marked as paid. Ref: '{payment_ref}'."
            )
        )

    def _handle_cancel(self, tenant: Tenant, invoice_number: str, options: Dict[str, Any]):
        """Cancel an invoice."""
        invoice = TenantInvoice.objects.filter(
            tenant=tenant, invoice_number=invoice_number
        ).first()
        if invoice is None:
            raise CommandError(f"Invoice '{invoice_number}' was not found.")

        if invoice.status == InvoiceStatus.PAID:
            raise CommandError("Cannot cancel an invoice that has already been paid.")

        invoice.status = InvoiceStatus.CANCELLED
        invoice.save(update_fields=["status", "updated_at"])

        operator = options.get("operator", "cli")
        PlatformAuditLog.objects.create(
            action="invoice_cancel",
            tenant_schema=tenant.schema_name,
            detail=f"Invoice {invoice_number} cancelled by operator '{operator}'.",
            operator=operator,
            metadata={"invoice_number": invoice_number},
        )

        self.stdout.write(
            self.style.WARNING(f"[CANCELLED] Invoice '{invoice_number}' has been cancelled.")
        )

    def _handle_generate(self, tenant: Tenant, options: Dict[str, Any]):
        """Generate a new 3-pillar invoice."""
        # 1. Date resolution
        today = timezone.now().date()
        start_str = options.get("billing_start")
        end_str = options.get("billing_end")

        try:
            if start_str:
                start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            else:
                start_date = date(today.year, today.month, 1)

            if end_str:
                end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
            else:
                last_day = calendar.monthrange(start_date.year, start_date.month)[1]
                end_date = date(start_date.year, start_date.month, last_day)
        except ValueError as err:
            raise CommandError("Invalid date format. Use YYYY-MM-DD.") from err

        if start_date > end_date:
            raise CommandError("Billing start date must be before or equal to end date.")

        # 2. Check for duplicate invoice in period
        existing = TenantInvoice.objects.filter(
            tenant=tenant,
            billing_period_start=start_date,
            billing_period_end=end_date,
        ).exclude(status=InvoiceStatus.CANCELLED).first()

        if existing:
            self.stdout.write(
                self.style.WARNING(
                    f"Notice: Active invoice already exists for period {start_date} to {end_date}: "
                    f"'{existing.invoice_number}'. Use --show={existing.invoice_number} to view."
                )
            )

        # 3. Retrieve Subscription
        sub = getattr(tenant, "subscription", None)
        if sub is None:
            sub, _ = TenantSubscription.objects.get_or_create(
                tenant=tenant,
                defaults={"plan_tier": PlanTier.FREE},
            )

        # 4. Compute 3-pillar charges
        calculation = self._calculate_3pillar_charges(tenant, sub, start_date, end_date)
        line_items_data = calculation["line_items"]
        subtotal = calculation["subtotal"]
        tax = calculation["tax"]
        total = calculation["total"]

        # 5. Generate Invoice Number
        invoice_number = self._generate_invoice_number(tenant.schema_name, start_date)

        # 6. Save in DB atomically
        with transaction.atomic():
            invoice = TenantInvoice.objects.create(
                tenant=tenant,
                invoice_number=invoice_number,
                status=InvoiceStatus.DRAFT,
                billing_period_start=start_date,
                billing_period_end=end_date,
                subtotal=subtotal,
                tax_amount=tax,
                total_amount=total,
                currency=sub.currency or "GHS",
                plan_tier_snapshot=sub.plan_tier,
            )

            created_items = []
            for item in line_items_data:
                created_items.append(
                    TenantInvoiceLineItem.objects.create(
                        invoice=invoice,
                        description=item["description"],
                        quantity=item["quantity"],
                        unit_price=item["unit_price"],
                        line_total=item["line_total"],
                        category=item["category"],
                    )
                )

        # 7. Audit log
        operator = options.get("operator", "cli")
        PlatformAuditLog.objects.create(
            action="invoice_generate",
            tenant_schema=tenant.schema_name,
            detail=(
                f"Generated invoice {invoice_number} for period {start_date} to {end_date}. "
                f"Total: {invoice.currency} {total:,.2f}."
            ),
            operator=operator,
            metadata={
                "invoice_number": invoice_number,
                "subtotal": str(subtotal),
                "total": str(total),
                "plan_tier": sub.plan_tier,
            },
        )

        # 8. Render PDF if requested
        pdf_path = None
        if options.get("generate_pdf"):
            output_dir = (
                Path(options["output"])
                if options.get("output")
                else Path(settings.BASE_DIR) / ".private" / "invoices"
            )
            pdf_path = self._render_pdf(invoice, created_items, output_dir)

        # 9. Output
        if options.get("json"):
            payload = {
                "invoice_number": invoice.invoice_number,
                "schema_name": tenant.schema_name,
                "status": invoice.status,
                "period_start": str(start_date),
                "period_end": str(end_date),
                "currency": invoice.currency,
                "subtotal": float(subtotal),
                "tax": float(tax),
                "total": float(total),
                "pdf_file": str(pdf_path) if pdf_path else None,
                "line_items": [
                    {
                        "description": item.description,
                        "category": item.category,
                        "quantity": float(item.quantity),
                        "unit_price": float(item.unit_price),
                        "line_total": float(item.line_total),
                    }
                    for item in created_items
                ],
            }
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            self._print_invoice_terminal(invoice, created_items)
            if pdf_path:
                self.stdout.write(self.style.SUCCESS(f"  PDF Invoice saved to: {pdf_path}\n"))

    def _calculate_3pillar_charges(
        self, tenant: Tenant, subscription: TenantSubscription, start_date: date, end_date: date
    ) -> Dict[str, Any]:
        """
        Calculate invoice line items across the three pillars:
        1. Software Subscription
        2. Cloud Infrastructure (Render + Neon)
        3. SMS Notifications
        """
        # Query enrolled student headcount and SMS dispatch in schema
        enrolled_count = 0
        sms_sent_count = 0

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                [tenant.schema_name],
            )
            has_schema = bool(cursor.fetchone())

        if not has_schema:
            raise CommandError(
                f"Tenant schema '{tenant.schema_name}' does not exist; invoice metrics cannot be calculated."
            )

        with tenant:
            from core.models import SMSDelivery, Student

            enrolled_status = getattr(
                getattr(Student, "EnrollmentStatus", None), "ENROLLED", "Enrolled"
            )
            enrolled_count = Student.objects.filter(status=enrolled_status).count()

            sms_sent_count = SMSDelivery.objects.filter(
                status=SMSDelivery.Status.SENT,
                sent_at__date__range=(start_date, end_date),
            ).count()

        line_items: List[Dict[str, Any]] = []

        # -------------------------------------------------------------
        # Pillar 1: Software Subscription
        # -------------------------------------------------------------
        if subscription.is_pilot_flat_fee:
            line_items.append(
                {
                    "description": f"Pilot Agreement Flat Subscription ({tenant.name})",
                    "quantity": Decimal("1.00"),
                    "unit_price": subscription.pilot_flat_amount,
                    "line_total": subscription.pilot_flat_amount,
                    "category": "base",
                }
            )
        else:
            # Term base fee
            line_items.append(
                {
                    "description": f"Base platform fee ({subscription.plan_tier.capitalize()} tier / term)",
                    "quantity": Decimal("1.00"),
                    "unit_price": subscription.base_fee,
                    "line_total": subscription.base_fee,
                    "category": "base",
                }
            )

            # Per-student term charge
            months = Decimal(subscription.months_per_term or 3)
            rate_mo = subscription.rate_per_student_month
            term_student_rate = round(rate_mo * months, 2)
            student_total = round(Decimal(enrolled_count) * term_student_rate, 2)

            line_items.append(
                {
                    "description": (
                        f"Student enrollment ({enrolled_count} students @ GHS {rate_mo:.2f}/mo x {months} months)"
                    ),
                    "quantity": Decimal(enrolled_count),
                    "unit_price": term_student_rate,
                    "line_total": student_total,
                    "category": "students",
                }
            )

        # -------------------------------------------------------------
        # Pillar 2: Cloud Infrastructure (Render + Neon)
        # -------------------------------------------------------------
        if subscription.include_hosting_fee and subscription.infra_cost_basis_monthly > 0:
            months = Decimal(subscription.months_per_term or 3)
            infra_basis = subscription.infra_cost_basis_monthly
            infra_margin = subscription.infra_margin_percent or Decimal("0.0")
            infra_unit_rate = round(infra_basis * (1 + (infra_margin / Decimal("100.0"))), 2)
            infra_total = round(infra_unit_rate * months, 2)

            line_items.append(
                {
                    "description": (
                        f"Cloud Hosting & Database (Render compute/worker/Redis + Neon DB @ "
                        f"GHS {infra_unit_rate:.2f}/mo [{infra_margin:.0f}% margin on GHS {infra_basis:.2f} base] x {months} mos)"
                    ),
                    "quantity": months,
                    "unit_price": infra_unit_rate,
                    "line_total": infra_total,
                    "category": "infrastructure",
                }
            )

        # -------------------------------------------------------------
        # Pillar 3: SMS Deliverability
        # -------------------------------------------------------------
        sms_unit_rate = subscription.rate_per_sms or round(
            subscription.sms_cost_basis
            * (1 + (subscription.sms_margin_percent / Decimal("100.0"))),
            4,
        )
        sms_total = round(Decimal(sms_sent_count) * sms_unit_rate, 2)

        line_items.append(
            {
                "description": (
                    f"SMS notifications ({sms_sent_count} messages @ GHS {sms_unit_rate:.4f} "
                    f"[{subscription.sms_margin_percent:.0f}% margin on GHS {subscription.sms_cost_basis:.5f} base])"
                ),
                "quantity": Decimal(sms_sent_count),
                "unit_price": sms_unit_rate,
                "line_total": sms_total,
                "category": "sms",
            }
        )

        subtotal = sum((item["line_total"] for item in line_items), Decimal("0.00"))
        tax = Decimal("0.00")
        total = subtotal + tax

        return {
            "line_items": line_items,
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
        }

    def _generate_invoice_number(self, schema_name: str, start_date: date) -> str:
        """Generate unique invoice identifier: INV-SCHEMA-YYYYMM-SEQ."""
        prefix = f"INV-{schema_name.upper()}-{start_date:%Y%m}"
        last = (
            TenantInvoice.objects.filter(invoice_number__startswith=prefix)
            .order_by("-invoice_number")
            .first()
        )
        if last:
            try:
                seq = int(last.invoice_number.rsplit("-", 1)[1]) + 1
            except (IndexError, ValueError):
                seq = 1
        else:
            seq = 1

        return f"{prefix}-{seq:03d}"

    def _print_invoice_terminal(
        self, invoice: TenantInvoice, line_items: List[TenantInvoiceLineItem]
    ):
        """Render beautiful terminal invoice table."""
        header_line = "=" * 76
        sep_line = "-" * 76

        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write(
            self.style.NOTICE("|  ")
            + self.style.SUCCESS(f"INVOICE: {invoice.invoice_number} -- {invoice.status.upper()}")
        )
        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))

        tenant_name = invoice.tenant.name
        self.stdout.write(f"  Tenant:   {tenant_name} ({invoice.tenant.schema_name})")
        self.stdout.write(f"  Plan:     {invoice.plan_tier_snapshot.capitalize()} tier")
        self.stdout.write(
            f"  Period:   {invoice.billing_period_start} to {invoice.billing_period_end}"
        )
        self.stdout.write(f"  Currency: {invoice.currency}")
        self.stdout.write("")

        col_fmt = "  {:<48} {:>5} {:>10} {:>10}"
        self.stdout.write(
            self.style.SQL_FIELD(
                col_fmt.format("Description", "Qty", "Rate", "Amount")
            )
        )
        self.stdout.write("  " + sep_line)

        for item in line_items:
            desc = item.description[:46]
            qty_str = (
                f"{int(item.quantity)}"
                if item.quantity == int(item.quantity)
                else f"{item.quantity:.1f}"
            )
            rate_str = f"{item.unit_price:,.2f}"
            amt_str = f"{item.line_total:,.2f}"
            self.stdout.write(col_fmt.format(desc, qty_str, rate_str, amt_str))

        self.stdout.write("  " + sep_line)
        self.stdout.write(
            f"  {'SUBTOTAL':<55} {invoice.currency} {invoice.subtotal:>12,.2f}"
        )
        self.stdout.write(
            f"  {'Tax (0%)':<55} {invoice.currency} {invoice.tax_amount:>12,.2f}"
        )
        total_str = f"{invoice.currency} {invoice.total_amount:>12,.2f}"
        self.stdout.write(
            f"  {self.style.SUCCESS(f'TOTAL PAYABLE: {total_str}')}"
        )
        self.stdout.write("")

        if invoice.status != InvoiceStatus.PAID:
            self.stdout.write(
                f"  To mark this invoice as paid:\n"
                f"    python manage.py tenant_invoice --schema={invoice.tenant.schema_name} \\\n"
                f"        --mark-paid={invoice.invoice_number} --payment-ref=\"REF-HERE\"\n"
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"  Paid at: {invoice.paid_at}  |  Ref: {invoice.payment_reference}\n"
                )
            )

    def _render_pdf(
        self,
        invoice: TenantInvoice,
        line_items: List[TenantInvoiceLineItem],
        output_dir: Path,
    ) -> Path:
        """Render PDF invoice using reportlab, or fallback text file."""
        output_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = output_dir / f"{invoice.invoice_number}.pdf"

        try:
            from reportlab.lib import colors
            from reportlab.lib.pagesizes import letter
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

            doc = SimpleDocTemplate(str(pdf_path), pagesize=letter)
            styles = getSampleStyleSheet()
            elements = []

            # Title
            title_style = ParagraphStyle(
                "TitleStyle",
                parent=styles["Heading1"],
                fontSize=20,
                leading=24,
                textColor=colors.HexColor("#1d3557"),
            )
            elements.append(Paragraph(f"EDEN SYS -- INVOICE", title_style))
            elements.append(Paragraph(f"Invoice #: {invoice.invoice_number}", styles["Normal"]))
            elements.append(
                Paragraph(
                    f"Billing Period: {invoice.billing_period_start} to {invoice.billing_period_end}",
                    styles["Normal"],
                )
            )
            elements.append(
                Paragraph(
                    f"Tenant: {invoice.tenant.name} ({invoice.tenant.schema_name})",
                    styles["Normal"],
                )
            )
            elements.append(Spacer(1, 15))

            # Table
            table_data = [["Description", "Qty", "Rate (GHS)", "Total (GHS)"]]
            for item in line_items:
                table_data.append(
                    [
                        item.description,
                        str(item.quantity),
                        f"{item.unit_price:,.2f}",
                        f"{item.line_total:,.2f}",
                    ]
                )
            table_data.append(["", "", "SUBTOTAL:", f"{invoice.subtotal:,.2f}"])
            table_data.append(["", "", "TOTAL PAYABLE:", f"{invoice.total_amount:,.2f}"])

            t = Table(table_data, colWidths=[260, 50, 90, 100])
            t.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1d3557")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                        ("GRID", (0, 0), (-1, -3), 0.5, colors.grey),
                        ("FONTNAME", (2, -2), (-1, -1), "Helvetica-Bold"),
                    ]
                )
            )
            elements.append(t)
            doc.build(elements)
            return pdf_path

        except ImportError:
            # Fallback to plain text receipt
            txt_path = output_dir / f"{invoice.invoice_number}.txt"
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(f"EDEN SYS INVOICE: {invoice.invoice_number}\n")
                f.write(f"Tenant: {invoice.tenant.name}\n")
                f.write(f"Period: {invoice.billing_period_start} to {invoice.billing_period_end}\n")
                f.write(f"Total: GHS {invoice.total_amount:,.2f}\n")
            return txt_path
