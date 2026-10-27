from django.core.management.base import CommandError

from core.models import House

from ._school_setup import (
    TenantSetupCommand,
    parse_integer,
    require_text,
    write_no_records,
)


class Command(TenantSetupCommand):
    help = "Create a house in the selected school."

    entity_name = "house"
    guided_task = "house"
    required_options = ("name",)

    def add_entity_arguments(self, parser):
        parser.add_argument("--name", help="House name, e.g. Red House.")
        parser.add_argument(
            "--display-order",
            type=int,
            default=None,
            help="Ordering for house lists (default: 0).",
        )
        parser.add_argument("--motto", default="", help="Optional house motto.")

    def perform_create(self, options):
        name = require_text(options["name"], "House name")
        if House.objects.filter(name__iexact=name).exists():
            raise CommandError(f"A house named '{name}' already exists.")
        display_order = parse_integer(
            options.get("display_order"), "Display order", minimum=0, default=0
        )
        house = House(
            name=name,
            display_order=display_order,
            motto=(options.get("motto") or "").strip() or None,
        )
        house.full_clean()
        house.save()
        return house

    def success_message(self, house):
        return f"Created house '{house.name}'."

    def list_entities(self):
        houses = list(House.objects.order_by("display_order", "name"))
        if not houses:
            write_no_records(
                self,
                "houses",
                "Create one with this command before adding dormitories.",
            )
            return
        self.write_list_heading("Houses")
        for house in houses:
            details = f" (order {house.display_order})" if house.display_order else ""
            motto = f" - {house.motto}" if house.motto else ""
            self.stdout.write(f"  {house.name}{details}{motto}")
