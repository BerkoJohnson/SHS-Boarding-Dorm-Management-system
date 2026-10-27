import sys

from django.core.management import call_command
from django.core.management.base import CommandError

from tenancy.models import Tenant


def launch_guided(command, task, *, options=None):
    global_options = {
        "operator",
        "verbosity",
        "settings",
        "pythonpath",
        "traceback",
        "no_color",
        "force_color",
        "skip_checks",
        # call_command() injects these when a caller captures output; they are
        # not user-supplied command options and must not look like partial input.
        "stdout",
        "stderr",
    }
    default_values = {"format": "custom", "ttl_hours": 4}
    supplied = [
        key.replace("_", "-")
        for key, value in (options or {}).items()
        if key not in global_options
        and value not in (None, False, "")
        and value != default_values.get(key)
    ]
    if supplied:
        flags = ", ".join(f"--{name}" for name in supplied)
        raise CommandError(
            f"Incomplete command options were supplied ({flags}). Run the command "
            "without options to use guided setup, or provide all required options."
        )
    if not sys.stdin.isatty():
        raise CommandError(
            "This command needs more information. Run it in a terminal for guided setup, "
            "or pass the required command-line options."
        )
    call_command(
        "tenant_wizard",
        f"--task={task}",
        stdout=command.stdout,
        stderr=command.stderr,
    )
    return True


def ask(prompt, *, default=None, style=None):
    hint = f" [{default}]" if default else ""
    text = f"{prompt}{hint}: "
    answer = input(style.HTTP_INFO(text) if style else text).strip()
    return answer or default


def choose(label, choices, *, stdout=None, style=None):
    if not choices:
        raise CommandError(f"No {label.lower()} options are available.")
    write = stdout.write if stdout else print
    write("")
    heading = f"Choose {label.lower()}:"
    write(style.HTTP_INFO(heading) if style else heading)
    for number, (value, description) in enumerate(choices, start=1):
        prefix = style.SUCCESS(f"  {number}.") if style else f"  {number}."
        write(f"{prefix} {description}")
    while True:
        prompt = "Enter a number (or 0 to cancel): "
        selection = input(style.HTTP_INFO(prompt) if style else prompt).strip()
        if selection == "0":
            raise CommandError("Operation cancelled.")
        if selection.isdigit() and 1 <= int(selection) <= len(choices):
            return choices[int(selection) - 1][0]
        message = "Please enter one of the listed numbers."
        write(style.WARNING(message) if style else message)


def choose_tenant(*, stdout=None, style=None):
    tenants = list(Tenant.objects.order_by("name", "schema_name"))
    if not tenants:
        raise CommandError("No schools are registered. Provision a school first.")
    return choose(
        "school",
        [
            (
                tenant.schema_name,
                f"{tenant.name} - {tenant.schema_name} "
                f"({'active' if tenant.is_active else 'inactive'})",
            )
            for tenant in tenants
        ],
        stdout=stdout,
        style=style,
    )


def confirm(prompt, *, style=None):
    text = f"{prompt} [y/N]: "
    answer = input(style.WARNING(text) if style else text).strip().lower()
    return answer in {"y", "yes"}
