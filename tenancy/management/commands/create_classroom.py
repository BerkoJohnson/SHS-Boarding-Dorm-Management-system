from django.core.management.base import CommandError

from core.models import ClassGroup

from ._school_setup import (
    TenantSetupCommand,
    require_text,
    resolve_programme,
    write_no_records,
)


class Command(TenantSetupCommand):
    help = "Create a classroom / class group in the selected school."

    entity_name = "classroom"
    guided_task = "classroom"
    required_options = ("name",)

    def add_entity_arguments(self, parser):
        parser.add_argument(
            "--name",
            help="Class group name, e.g. 1A1 or SHS1 Science A.",
        )
        parser.add_argument(
            "--programme",
            default=None,
            help="Optional programme name, code, or ID.",
        )

    def perform_create(self, options):
        name = require_text(options["name"], "Classroom name")
        programme = (
            resolve_programme(options["programme"])
            if options.get("programme")
            else None
        )
        if ClassGroup.objects.filter(name__iexact=name).exists():
            raise CommandError(f"A classroom named '{name}' already exists.")
        classroom = ClassGroup(name=name, programme=programme)
        classroom.full_clean()
        classroom.save()
        return classroom

    def success_message(self, classroom):
        if classroom.programme:
            return (
                f"Created classroom '{classroom.name}' for programme "
                f"'{classroom.programme.name}'."
            )
        return f"Created classroom '{classroom.name}'."

    def list_entities(self):
        classrooms = list(ClassGroup.objects.select_related("programme").all())
        if not classrooms:
            write_no_records(
                self,
                "classrooms",
                "Create programmes and classrooms with the school setup commands.",
            )
            return
        self.write_list_heading("Classrooms")
        for classroom in classrooms:
            programme = (
                f" ({classroom.programme.name})" if classroom.programme else ""
            )
            self.stdout.write(f"  {classroom.name}{programme}")
