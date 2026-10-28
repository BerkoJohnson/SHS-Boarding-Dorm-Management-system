from django.core.management.base import CommandError

from core.models import ClassGroup

from ._guided import launch_guided
from ._school_setup import (
    TenantSetupCommand,
    build_class_group_names,
    parse_integer,
    require_text,
    resolve_programme,
    write_no_records,
)


class Command(TenantSetupCommand):
    help = "Create one classroom or a batch of class groups in the selected school."

    entity_name = "classroom"
    guided_task = "classroom"

    def add_entity_arguments(self, parser):
        parser.add_argument(
            "--name",
            help="Class group name for a single classroom, e.g. 1A1.",
        )
        parser.add_argument(
            "--programme",
            default=None,
            help=(
                "Programme name, code, or ID. Required when --form is used; "
                "optional with --name."
            ),
        )
        parser.add_argument(
            "--form",
            type=int,
            default=None,
            help="Form for generated class groups: 1, 2, or 3.",
        )
        parser.add_argument(
            "--start",
            type=int,
            default=None,
            help=(
                "First class number when --form is used. For a single class "
                "group, set --end to the same value."
            ),
        )
        parser.add_argument(
            "--end",
            type=int,
            default=None,
            help=(
                "Last class number when --form is used. For a single class "
                "group, set --start to the same value."
            ),
        )

    def handle(self, *args, **options):
        if options.get("list"):
            return super().handle(*args, **options)

        name = options.get("name")
        form = options.get("form")
        if name and form is not None:
            raise CommandError(
                "Use --name for a free-form classroom name or --form with "
                "--programme, --start, and --end for generated class-group "
                "names, not both."
            )
        if not name and form is None:
            launch_guided(self, self.guided_task, options=options)
            return
        if form is not None:
            missing = [
                flag
                for option, flag in (
                    ("programme", "--programme"),
                    ("start", "--start"),
                    ("end", "--end"),
                )
                if options.get(option) in (None, "")
            ]
            if missing:
                raise CommandError(
                    "Creating class groups from a form requires "
                    f"{', '.join(missing)}."
                )
        return super().handle(*args, **options)

    def perform_create(self, options):
        if options.get("name"):
            return self._create_single(options)
        return self._create_batch(options)

    def _create_single(self, options):
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

    def _create_batch(self, options):
        programme = resolve_programme(options["programme"])
        form = parse_integer(options["form"], "Form", minimum=1, maximum=3)
        first = parse_integer(options["start"], "First class number", minimum=1)
        last = parse_integer(
            options["end"], "Last class number", minimum=first
        )
        names = build_class_group_names(form, programme.code, first, last)
        existing = {
            name.casefold()
            for name in ClassGroup.objects.values_list("name", flat=True)
        }
        duplicates = [name for name in names if name.casefold() in existing]
        if duplicates:
            raise CommandError(
                "These class groups already exist: "
                f"{', '.join(duplicates)}."
            )
        classrooms = [
            ClassGroup(name=name, programme=programme) for name in names
        ]
        for classroom in classrooms:
            classroom.full_clean()
        ClassGroup.objects.bulk_create(classrooms)
        return classrooms

    def success_message(self, classroom):
        if isinstance(classroom, list):
            if len(classroom) == 1:
                return f"Created classroom '{classroom[0].name}'."
            return (
                f"Created {len(classroom)} class groups: "
                f"{classroom[0].name} to {classroom[-1].name}."
            )
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
