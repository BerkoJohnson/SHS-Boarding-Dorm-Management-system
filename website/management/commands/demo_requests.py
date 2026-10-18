"""Review demonstration enquiries submitted from the public website.

Why a management command instead of the Django admin
---------------------------------------------------
The ``website`` app is part of ``PUBLIC_APPS``, so ``website_demorequest``
lives in the PostgreSQL ``public`` schema. ``django.contrib.admin``, by
contrast, is a tenant application: its URLs are only routed on school hosts,
where the public schema's tables are not visible. Registering the enquiry model
in the admin would therefore expose a permanently broken page.

The platform's established pattern for public-schema operations is a documented
management command (see README section 6.4), and this command follows it.

Enquiries contain business-confidential contact details. Run this command only
on a machine and account authorised to hold them.

Examples
--------
::

    # List the most recent enquiries with a status summary
    python manage.py demo_requests

    # List every enquiry, including closed ones
    python manage.py demo_requests --all

    # Show one enquiry in full
    python manage.py demo_requests --show=12

    # Record that an enquiry has been contacted
    python manage.py demo_requests --mark=12 --status=contacted \\
        --note="Called 2026-10-18; demo proposed for Tuesday."

    # Export everything to CSV for a follow-up list
    python manage.py demo_requests --export=.private/demo-requests.csv

    # Machine-readable output
    python manage.py demo_requests --json
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from website.models import DemoRequest, DemoRequestStatus

DEFAULT_LIMIT = 25


class Command(BaseCommand):
    help = "List, review and triage demonstration requests from the public website."

    def add_arguments(self, parser):
        parser.add_argument(
            "--list",
            action="store_true",
            help="List enquiries (default action).",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Include closed and triaged enquiries in the list.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=DEFAULT_LIMIT,
            help=f"Maximum rows to list (default {DEFAULT_LIMIT}).",
        )
        parser.add_argument(
            "--show",
            type=int,
            metavar="ID",
            help="Show the full detail of one enquiry by its reference number.",
        )
        parser.add_argument(
            "--mark",
            type=int,
            metavar="ID",
            help="Update the triage status of one enquiry (requires --status).",
        )
        parser.add_argument(
            "--status",
            choices=[choice.value for choice in DemoRequestStatus],
            help="New status to apply with --mark.",
        )
        parser.add_argument(
            "--note",
            default="",
            help="Internal note to append when using --mark.",
        )
        parser.add_argument(
            "--export",
            metavar="PATH",
            help="Write all enquiries to a CSV file at PATH.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Emit machine-readable JSON instead of formatted text.",
        )

    # -- helpers ------------------------------------------------------------

    def _queryset(self, include_all: bool):
        queryset = DemoRequest.objects.all()
        if not include_all:
            queryset = queryset.exclude(status=DemoRequestStatus.CLOSED)
        return queryset

    def _as_dict(self, enquiry: DemoRequest) -> dict:
        return {
            "id": enquiry.pk,
            "school_name": enquiry.school_name,
            "contact_name": enquiry.contact_name,
            "role": enquiry.role,
            "phone": enquiry.phone,
            "email": enquiry.email,
            "region": enquiry.get_region_display(),
            "boarding_population": enquiry.boarding_population,
            "preferred_demo_method": enquiry.get_preferred_demo_method_display(),
            "message": enquiry.message,
            "status": enquiry.status,
            "internal_notes": enquiry.internal_notes,
            "notification_sent": enquiry.notification_sent,
            "created_at": enquiry.created_at.isoformat(),
        }

    def _status_summary(self) -> str:
        lines = ["Status summary:"]
        total = DemoRequest.objects.count()
        for value, label in DemoRequestStatus.choices:
            count = DemoRequest.objects.filter(status=value).count()
            lines.append(f"  {label:<22} {count:>5}")
        lines.append(f"  {'TOTAL':<22} {total:>5}")
        return "\n".join(lines)

    # -- entry point --------------------------------------------------------

    def handle(self, *args, **options):
        as_json = options["json"]

        if options["mark"] is not None:
            self._handle_mark(options, as_json)
            return

        if options["show"] is not None:
            self._handle_show(options, as_json)
            return

        if options["export"]:
            self._handle_export(options)
            return

        self._handle_list(options, as_json)

    # -- actions -----------------------------------------------------------

    def _handle_list(self, options, as_json: bool) -> None:
        limit = max(1, int(options["limit"] or DEFAULT_LIMIT))
        queryset = self._queryset(options["all"])[:limit]

        if as_json:
            self.stdout.write(
                json.dumps(
                    {
                        "count": DemoRequest.objects.count(),
                        "enquiries": [self._as_dict(row) for row in queryset],
                    },
                    indent=2,
                )
            )
            return

        self.stdout.write(self._status_summary())
        self.stdout.write("")

        rows = list(queryset)
        if not rows:
            self.stdout.write(
                self.style.WARNING("No enquiries have been submitted yet.")
            )
            return

        header = f"{'ID':>4}  {'RECEIVED':<16}  {'SCHOOL':<28}  {'CONTACT':<22}  STATUS"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))
        for enquiry in rows:
            school = enquiry.school_name[:28]
            contact = f"{enquiry.contact_name[:18]} ({enquiry.phone})"
            self.stdout.write(
                f"{enquiry.pk:>4}  "
                f"{enquiry.created_at:%Y-%m-%d %H:%M}  "
                f"{school:<28}  "
                f"{contact:<22}  "
                f"{enquiry.get_status_display()}"
            )

        self.stdout.write("")
        self.stdout.write(
            "Show one in full with:  python manage.py demo_requests --show=<ID>"
        )
        if not options["all"]:
            self.stdout.write(
                "Closed enquiries are hidden. Use --all to include them."
            )

    def _handle_show(self, options, as_json: bool) -> None:
        enquiry = DemoRequest.objects.filter(pk=options["show"]).first()
        if enquiry is None:
            raise CommandError(f"No enquiry with reference #{options['show']}.")

        if as_json:
            self.stdout.write(json.dumps(self._as_dict(enquiry), indent=2))
            return

        data = self._as_dict(enquiry)
        self.stdout.write(self.style.HTTP_INFO(f"Enquiry #{data['id']}"))
        self.stdout.write("")
        for label, key in (
            ("School", "school_name"),
            ("Contact", "contact_name"),
            ("Role", "role"),
            ("Phone", "phone"),
            ("Email", "email"),
            ("Region", "region"),
            ("Boarding students", "boarding_population"),
            ("Preferred method", "preferred_demo_method"),
            ("Received", "created_at"),
            ("Status", "status"),
            ("Notification sent", "notification_sent"),
            ("Internal notes", "internal_notes"),
        ):
            value = data.get(key)
            self.stdout.write(f"  {label:<20} {value if value not in (None, '') else '—'}")
        self.stdout.write("")
        self.stdout.write("  Message:")
        for line in (data["message"] or "(none provided)").splitlines():
            self.stdout.write(f"    {line}")

    def _handle_mark(self, options, as_json: bool) -> None:
        if not options["status"]:
            raise CommandError("--mark requires --status.")

        enquiry = DemoRequest.objects.filter(pk=options["mark"]).first()
        if enquiry is None:
            raise CommandError(f"No enquiry with reference #{options['mark']}.")

        enquiry.status = options["status"]
        note = (options["note"] or "").strip()
        if note:
            stamp = timezone.localtime().strftime("%Y-%m-%d %H:%M")
            prefix = f"[{stamp}] " if not enquiry.internal_notes else "\n"
            enquiry.internal_notes = f"{enquiry.internal_notes}{prefix}{note}"
        enquiry.save(update_fields=["status", "internal_notes", "updated_at"])

        if as_json:
            self.stdout.write(json.dumps(self._as_dict(enquiry), indent=2))
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Enquiry #{enquiry.pk} is now '{enquiry.get_status_display()}'."
            )
        )

    def _handle_export(self, options) -> None:
        target = Path(options["export"]).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "id",
            "created_at",
            "school_name",
            "contact_name",
            "role",
            "phone",
            "email",
            "region",
            "boarding_population",
            "preferred_demo_method",
            "status",
            "message",
            "internal_notes",
        ]
        rows = list(DemoRequest.objects.all())
        with target.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for enquiry in rows:
                data = self._as_dict(enquiry)
                writer.writerow({field: data.get(field, "") for field in fields})

        self.stdout.write(
            self.style.SUCCESS(f"Wrote {len(rows)} enquiries to {target}.")
        )
        self.stdout.write(
            self.style.WARNING(
                "This file contains confidential contact details. Store it "
                "securely and delete it when it is no longer needed."
            )
        )
