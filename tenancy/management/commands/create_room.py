from django.core.management.base import CommandError

from core.models import DormitoryRoom

from ._school_setup import (
    TenantSetupCommand,
    parse_integer,
    require_text,
    resolve_dormitory,
    resolve_house,
    write_no_records,
)


class Command(TenantSetupCommand):
    help = "Create a room in a dormitory in the selected school."

    entity_name = "room"
    guided_task = "room"
    required_options = ("dormitory", "name", "capacity")

    def add_entity_arguments(self, parser):
        parser.add_argument("--dormitory", help="Dormitory name or ID.")
        parser.add_argument(
            "--house",
            default=None,
            help="Optional house name or ID to disambiguate the dormitory.",
        )
        parser.add_argument("--name", help="Room name, e.g. Room 1.")
        parser.add_argument("--capacity", type=int, help="Maximum boarders.")

    def perform_create(self, options):
        house = resolve_house(options["house"]) if options.get("house") else None
        dormitory = resolve_dormitory(options["dormitory"], house=house)
        name = require_text(options["name"], "Room name")
        capacity = parse_integer(options["capacity"], "Capacity", minimum=1)
        if DormitoryRoom.objects.filter(
            dormitory=dormitory, name__iexact=name
        ).exists():
            raise CommandError(
                f"A room named '{name}' already exists in dormitory "
                f"'{dormitory.name}'."
            )
        room = DormitoryRoom(
            dormitory=dormitory,
            name=name,
            capacity=capacity,
        )
        room.save()
        return room

    def success_message(self, room):
        return (
            f"Created room '{room.name}' in dormitory "
            f"'{room.dormitory.name}' (capacity {room.capacity})."
        )

    def list_entities(self):
        rooms = list(
            DormitoryRoom.objects.select_related("dormitory", "dormitory__house")
            .order_by(
                "dormitory__house__display_order",
                "dormitory__house__name",
                "dormitory__name",
                "name",
            )
        )
        if not rooms:
            write_no_records(
                self,
                "rooms",
                "Create a dormitory first, then add its rooms.",
            )
            return
        self.write_list_heading("Rooms")
        for room in rooms:
            self.stdout.write(
                f"  {room.dormitory.house.name} / {room.dormitory.name} / "
                f"{room.name} (capacity {room.capacity})"
            )
