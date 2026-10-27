from django.core.management.base import CommandError

from core.models import Dormitory

from ._school_setup import (
    TenantSetupCommand,
    normalize_gender,
    parse_integer,
    require_text,
    resolve_house,
    write_no_records,
)


class Command(TenantSetupCommand):
    help = "Create a dormitory in a house in the selected school."

    entity_name = "dormitory"
    guided_task = "dormitory"
    required_options = ("house", "name", "gender")

    def add_entity_arguments(self, parser):
        parser.add_argument("--house", help="House name or ID.")
        parser.add_argument("--name", help="Dormitory block name.")
        parser.add_argument(
            "--gender",
            help="Dormitory gender: male/boys or female/girls.",
        )
        parser.add_argument(
            "--capacity",
            type=int,
            default=None,
            help="Bed capacity (default: 100).",
        )

    def perform_create(self, options):
        house = resolve_house(options["house"])
        name = require_text(options["name"], "Dormitory name")
        gender = normalize_gender(options["gender"])
        capacity = parse_integer(
            options.get("capacity"), "Capacity", minimum=1, default=100
        )
        if Dormitory.objects.filter(
            house=house, gender=gender, name__iexact=name
        ).exists():
            raise CommandError(
                f"A {gender} dormitory named '{name}' already exists in "
                f"house '{house.name}'."
            )
        dormitory = Dormitory(
            house=house,
            name=name,
            gender=gender,
            capacity=capacity,
        )
        dormitory.save()
        return dormitory

    def success_message(self, dormitory):
        return (
            f"Created dormitory '{dormitory.name}' in house "
            f"'{dormitory.house.name}' ({dormitory.get_gender_display()}, "
            f"capacity {dormitory.capacity})."
        )

    def list_entities(self):
        dormitories = list(
            Dormitory.objects.select_related("house").order_by(
                "house__display_order", "house__name", "gender", "name"
            )
        )
        if not dormitories:
            write_no_records(
                self,
                "dormitories",
                "Create a house and dormitory with the school setup commands.",
            )
            return
        self.write_list_heading("Dormitories")
        for dormitory in dormitories:
            self.stdout.write(
                f"  {dormitory.house.name} / {dormitory.name} "
                f"({dormitory.get_gender_display()}, "
                f"capacity {dormitory.capacity})"
            )
