from django.core.management.base import CommandError

from core.models import AcademicTerm

from ._school_setup import (
    TenantSetupCommand,
    normalize_level,
    normalize_term,
    parse_date,
    resolve_academic_year,
    write_no_records,
)


class Command(TenantSetupCommand):
    help = "Create an academic term/semester in the selected school."

    entity_name = "academic term"
    guided_task = "academic-term"
    required_options = ("academic_year", "term", "start_date", "end_date")

    def add_entity_arguments(self, parser):
        parser.add_argument(
            "--academic-year",
            help="Academic year name or ID.",
        )
        parser.add_argument(
            "--term",
            help="Term: 1, 2, 3, SEM1, or SEM2.",
        )
        parser.add_argument(
            "--level",
            default=None,
            help="Target level: ALL, SHS1, SHS2, or SHS3 (default: ALL).",
        )
        parser.add_argument("--start-date", help="Start date (YYYY-MM-DD).")
        parser.add_argument("--end-date", help="End date (YYYY-MM-DD).")
        parser.add_argument(
            "--current",
            action="store_true",
            help="Make this the current term for its level.",
        )

    def perform_create(self, options):
        academic_year = resolve_academic_year(options["academic_year"])
        term = normalize_term(options["term"])
        level = normalize_level(options.get("level") or "ALL")
        start_date = parse_date(options["start_date"], "Start date")
        end_date = parse_date(options["end_date"], "End date")
        if end_date <= start_date:
            raise CommandError("End date must be after start date.")
        if AcademicTerm.objects.filter(
            academic_year=academic_year,
            term=term,
            level=level,
        ).exists():
            raise CommandError(
                f"{academic_year.name} already has that term for level {level}."
            )
        return AcademicTerm.objects.create(
            academic_year=academic_year,
            term=term,
            level=level,
            start_date=start_date,
            end_date=end_date,
            is_current=options["current"],
        )

    def success_message(self, academic_term):
        current = " (current)" if academic_term.is_current else ""
        return (
            f"Created academic term '{academic_term.term}' for "
            f"'{academic_term.academic_year.name}' "
            f"[{academic_term.level}]{current}."
        )

    def list_entities(self):
        terms = list(
            AcademicTerm.objects.select_related("academic_year").order_by(
                "-academic_year__start_date", "level", "term"
            )
        )
        if not terms:
            write_no_records(
                self,
                "academic terms",
                "Create an academic year first, then add its terms.",
            )
            return
        self.write_list_heading("Academic terms")
        for term in terms:
            current = " (current)" if term.is_current else ""
            self.stdout.write(
                f"  {term.academic_year.name} / {term.get_term_display()} "
                f"[{term.level}]: {term.start_date} to {term.end_date}{current}"
            )
