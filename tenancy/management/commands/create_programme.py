from django.core.management.base import CommandError

from core.models import Programme

from ._school_setup import TenantSetupCommand, require_text, write_no_records


class Command(TenantSetupCommand):
    help = "Create an academic programme in the selected school."

    entity_name = "programme"
    guided_task = "programme"
    required_options = ("name",)

    def add_entity_arguments(self, parser):
        parser.add_argument("--name", help="Programme name.")
        parser.add_argument("--code", default="", help="Optional short code.")

    def perform_create(self, options):
        name = require_text(options["name"], "Programme name")
        code = (options.get("code") or "").strip() or None
        if Programme.objects.filter(name__iexact=name).exists():
            raise CommandError(f"A programme named '{name}' already exists.")
        if code and Programme.objects.filter(code__iexact=code).exists():
            raise CommandError(f"A programme with code '{code}' already exists.")
        programme = Programme(name=name, code=code)
        programme.full_clean()
        programme.save()
        return programme

    def success_message(self, programme):
        code = f" ({programme.code})" if programme.code else ""
        return f"Created programme '{programme.name}'{code}."

    def list_entities(self):
        programmes = list(Programme.objects.all())
        if not programmes:
            write_no_records(
                self,
                "programmes",
                "Create one before adding classrooms or importing students.",
            )
            return
        self.write_list_heading("Programmes")
        for programme in programmes:
            code = f" [{programme.code}]" if programme.code else ""
            self.stdout.write(f"  {programme.name}{code}")
