from django.core.management.base import CommandError

from core.models import AcademicYear

from ._school_setup import (
    TenantSetupCommand,
    parse_date,
    write_no_records,
)


class Command(TenantSetupCommand):
    help = "Create an academic year in the selected school."

    entity_name = "academic year"
    guided_task = "academic-year"
    required_options = ("start_date", "end_date")

    def add_entity_arguments(self, parser):
        parser.add_argument("--start-date", help="Start date (YYYY-MM-DD).")
        parser.add_argument("--end-date", help="End date (YYYY-MM-DD).")
        parser.add_argument(
            "--current",
            action="store_true",
            help="Make this the current academic year.",
        )

    def perform_create(self, options):
        start_date = parse_date(options["start_date"], "Start date")
        end_date = parse_date(options["end_date"], "End date")
        if end_date <= start_date:
            raise CommandError("End date must be after start date.")
        if AcademicYear.objects.filter(
            start_date=start_date, end_date=end_date
        ).exists():
            raise CommandError(
                f"An academic year from {start_date} to {end_date} already exists."
            )
        return AcademicYear.objects.create(
            start_date=start_date,
            end_date=end_date,
            is_current=options["current"],
        )

    def success_message(self, academic_year):
        current = " (current)" if academic_year.is_current else ""
        return f"Created academic year '{academic_year.name}'{current}."

    def list_entities(self):
        years = list(AcademicYear.objects.order_by("-start_date"))
        if not years:
            write_no_records(
                self,
                "academic years",
                "Create one before adding terms.",
            )
            return
        self.write_list_heading("Academic years")
        for year in years:
            current = " (current)" if year.is_current else ""
            self.stdout.write(
                f"  {year.name}: {year.start_date} to {year.end_date}{current}"
            )
