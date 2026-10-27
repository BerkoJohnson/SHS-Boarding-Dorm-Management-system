import sys
from contextlib import nullcontext
from getpass import getpass

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from tenancy.models import PlanTier

from ._guided import ask, choose, choose_tenant, confirm


class Command(BaseCommand):
    help = "Open a guided menu for common school and tenant administration tasks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--task",
            choices=(
                "users",
                "schools",
                "provision",
                "domain",
                "status",
                "legacy",
                "list",
                "usage",
                "plans",
                "features",
                "invoices",
                "backups",
                "restore",
                "support",
                "facilities",
                "house",
                "houses",
                "dormitory",
                "dormitories",
                "room",
                "rooms",
                "classroom",
                "classrooms",
                "programme",
                "programmes",
                "academic-year",
                "academic-term",
                "roster",
                "import-roster",
            ),
            help="Open directly into one guided workflow.",
        )

    def handle(self, *args, **options):
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "The guided platform menu requires TENANCY_ENABLED=True."
            )
        if not sys.stdin.isatty():
            raise CommandError(
                "The guided platform menu requires an interactive terminal."
            )

        if options.get("task"):
            self.stdout.write(self.style.HTTP_INFO("\nGuided tenant administration"))
            self._run_task(options["task"])
            return

        self.stdout.write(self.style.HTTP_INFO("\nSchool and tenant administration"))
        self.stdout.write(
            "Choose a task below. You will be prompted for the school and the required details."
        )
        tasks = [
            ("users", "Create a staff or administrator account"),
            ("schools", "School setup menu: provision, facilities, roster, hostname, status, or migration"),
            ("list", "List registered schools"),
            ("usage", "View school usage report"),
            ("plans", "View or change a subscription plan"),
            ("features", "View or change school features and limits"),
            ("invoices", "View or manage invoices"),
            ("backups", "Create or verify a backup"),
            ("restore", "Validate a backup before restore (dry run)"),
            ("support", "Manage time-limited support access"),
            ("exit", "Exit"),
        ]

        while True:
            try:
                task = choose(
                    "task",
                    tasks,
                    stdout=self.stdout,
                    style=self.style,
                )
            except CommandError as error:
                if str(error) == "Operation cancelled.":
                    continue
                raise
            if task == "exit":
                self.stdout.write(self.style.SUCCESS("Goodbye."))
                return
            try:
                self._run_task(task)
            except CommandError as error:
                self.stdout.write(self.style.ERROR(f"\nTask stopped: {error}"))
            self.stdout.write(self.style.NOTICE("\nReturning to the main menu."))

    def _run_task(self, task):
        if task == "users":
            call_command("create_school_user", stdout=self.stdout, stderr=self.stderr)
        elif task == "schools":
            self._school_setup()
        elif task == "facilities":
            self._school_setup(operation="facilities")
        elif task in {
            "provision",
            "domain",
            "status",
            "legacy",
            "house",
            "houses",
            "dormitory",
            "dormitories",
            "room",
            "rooms",
            "classroom",
            "classrooms",
            "programme",
            "programmes",
            "academic-year",
            "academic-term",
            "roster",
            "import-roster",
        }:
            self._school_setup(operation=task)
        elif task == "list":
            call_command("tenant_list", "--all", stdout=self.stdout, stderr=self.stderr)
        elif task == "usage":
            scope = choose(
                "report scope",
                [("one", "One school"), ("all", "All active schools")],
                stdout=self.stdout,
                style=self.style,
            )
            start = ask("Start date YYYY-MM-DD (default: last 30 days)", style=self.style)
            end = ask("End date YYYY-MM-DD (default: today)", style=self.style)
            args = ["tenant_usage"]
            if scope == "one":
                args.append(f"--schema={self._school_schema()}")
            else:
                args.append("--all")
            if start:
                args.append(f"--start-date={start}")
            if end:
                args.append(f"--end-date={end}")
            call_command(*args, stdout=self.stdout, stderr=self.stderr)
        elif task == "plans":
            self._plans()
        elif task == "features":
            self._features()
        elif task == "invoices":
            self._invoices()
        elif task == "backups":
            self._backups()
        elif task == "restore":
            self._restore_dry_run()
        elif task == "support":
            self._support_access()
        else:
            raise CommandError("Unknown menu selection.")

    def _school_schema(self):
        return choose_tenant(stdout=self.stdout, style=self.style)

    def _school_setup(self, operation=None):
        aliases = {
            "houses": "house",
            "dormitories": "dormitory",
            "rooms": "room",
            "classrooms": "classroom",
            "programmes": "programme",
            "import-roster": "roster",
        }
        if operation is not None:
            operation = aliases.get(operation, operation)

        facility_choices = [
            ("house", "Create a house"),
            ("dormitory", "Create a dormitory in a house"),
            ("room", "Create a room in a dormitory"),
            ("classroom", "Create a classroom / class group"),
            ("programme", "Create an academic programme"),
            ("academic-year", "Create an academic year"),
            ("academic-term", "Create an academic term"),
            ("roster", "Preview and import a student roster"),
        ]
        if operation is None:
            operation = choose(
                "school setup operation",
                [
                    ("provision", "Create and migrate a new school"),
                    ("domain", "Add a hostname to a school"),
                    ("status", "Activate or deactivate a school"),
                    ("legacy", "Copy a single-school installation into a new tenant"),
                    *facility_choices,
                ],
                stdout=self.stdout,
                style=self.style,
            )
        elif operation == "facilities":
            operation = choose(
                "school setup operation",
                facility_choices,
                stdout=self.stdout,
                style=self.style,
            )

        if operation == "provision":
            self.stdout.write(
                self.style.WARNING(
                    "You must control and verify the hostname before continuing."
                )
            )
            name = ask("School display name", style=self.style)
            schema = ask("New schema name, e.g. school_name", style=self.style)
            domain = ask("Verified hostname without https://", style=self.style)
            if not confirm("Have you verified control of this hostname?", style=self.style):
                raise CommandError("School provisioning cancelled before domain verification.")
            if not confirm("Create this school as inactive and run its migrations?", style=self.style):
                raise CommandError("School provisioning cancelled.")
            call_command(
                "provision_school",
                f"--name={name}",
                f"--schema={schema}",
                f"--domain={domain}",
                "--confirm-domain-verified",
                stdout=self.stdout,
                stderr=self.stderr,
            )
        elif operation == "domain":
            schema = self._school_schema()
            domain = ask("Hostname without https://", style=self.style)
            primary = confirm("Make this the school's primary hostname?", style=self.style)
            if not confirm("Have you verified control of this hostname?", style=self.style):
                raise CommandError("Domain registration cancelled.")
            args = ["add_school_domain", f"--schema={schema}", f"--domain={domain}"]
            if primary:
                args.append("--primary")
            args.append("--confirm-domain-verified")
            call_command(*args, stdout=self.stdout, stderr=self.stderr)
        elif operation == "status":
            schema = self._school_schema()
            action = choose(
                "status action",
                [("activate", "Activate this school"), ("deactivate", "Deactivate this school")],
                stdout=self.stdout,
                style=self.style,
            )
            if not confirm(f"{action.title()} school '{schema}'?", style=self.style):
                raise CommandError("Status change cancelled.")
            call_command(
                "set_school_status",
                f"--schema={schema}",
                f"--{action}",
                stdout=self.stdout,
                stderr=self.stderr,
            )
        elif operation == "legacy":
            self.stdout.write(
                self.style.WARNING(
                    "This copies existing school data into a new tenant. Back up the source "
                    "and stop web/Celery workers first."
                )
            )
            name = ask("School display name", style=self.style)
            schema = ask("New schema name", style=self.style)
            domain = ask("Verified hostname without https://", style=self.style)
            if not confirm("Have you verified control of this hostname?", style=self.style):
                raise CommandError("Migration cancelled before domain verification.")
            if not confirm("Continue with the legacy school migration?", style=self.style):
                raise CommandError("Legacy school migration cancelled.")
            call_command(
                "migrate_legacy_school",
                f"--name={name}",
                f"--schema={schema}",
                f"--domain={domain}",
                "--confirm-domain-verified",
                stdout=self.stdout,
                stderr=self.stderr,
            )
        elif operation in {
            "house",
            "dormitory",
            "room",
            "classroom",
            "programme",
            "academic-year",
            "academic-term",
            "roster",
        }:
            self._school_facilities(operation)
        else:
            raise CommandError("Unknown school setup operation.")

    def _school_facilities(self, operation):
        schema = self._school_schema()
        args = [f"--schema={schema}"]

        if operation == "house":
            name = ask("House name", style=self.style)
            if not name:
                raise CommandError("House name is required.")
            display_order = ask(
                "Display order", default="0", style=self.style
            )
            motto = ask("Motto (optional)", default="", style=self.style)
            if not confirm(
                f"Create house '{name}' in school '{schema}'?", style=self.style
            ):
                raise CommandError("House creation cancelled.")
            args.extend(
                (f"--name={name}", f"--display-order={display_order}")
            )
            if motto:
                args.append(f"--motto={motto}")
            call_command(
                "create_house", *args, stdout=self.stdout, stderr=self.stderr
            )
        elif operation == "dormitory":
            house_id = self._choose_house(schema)
            name = ask("Dormitory block name", style=self.style)
            if not name:
                raise CommandError("Dormitory block name is required.")
            gender = choose(
                "dormitory gender",
                [
                    ("M", "Boys dormitory"),
                    ("F", "Girls dormitory"),
                ],
                stdout=self.stdout,
                style=self.style,
            )
            capacity = ask(
                "Bed capacity", default="100", style=self.style
            )
            if not confirm(
                f"Create dormitory '{name}' in the selected house?",
                style=self.style,
            ):
                raise CommandError("Dormitory creation cancelled.")
            args.extend(
                (
                    f"--house={house_id}",
                    f"--name={name}",
                    f"--gender={gender}",
                    f"--capacity={capacity}",
                )
            )
            call_command(
                "create_dormitory",
                *args,
                stdout=self.stdout,
                stderr=self.stderr,
            )
        elif operation == "room":
            house_id = self._choose_house(schema)
            dormitory_id = self._choose_dormitory(schema, house_id)
            name = ask("Room name", style=self.style)
            if not name:
                raise CommandError("Room name is required.")
            capacity = ask("Room capacity", default="4", style=self.style)
            if not confirm(
                f"Create room '{name}' in the selected dormitory?",
                style=self.style,
            ):
                raise CommandError("Room creation cancelled.")
            args.extend(
                (
                    f"--house={house_id}",
                    f"--dormitory={dormitory_id}",
                    f"--name={name}",
                    f"--capacity={capacity}",
                )
            )
            call_command(
                "create_room", *args, stdout=self.stdout, stderr=self.stderr
            )
        elif operation == "classroom":
            name = ask("Classroom / class group name", style=self.style)
            if not name:
                raise CommandError("Classroom name is required.")
            programme_id = self._choose_programme(schema, allow_none=True)
            if not confirm(
                f"Create classroom '{name}' in school '{schema}'?",
                style=self.style,
            ):
                raise CommandError("Classroom creation cancelled.")
            args.append(f"--name={name}")
            if programme_id:
                args.append(f"--programme={programme_id}")
            call_command(
                "create_classroom",
                *args,
                stdout=self.stdout,
                stderr=self.stderr,
            )
        elif operation == "programme":
            name = ask("Programme name", style=self.style)
            if not name:
                raise CommandError("Programme name is required.")
            code = ask("Short code (optional)", default="", style=self.style)
            if not confirm(
                f"Create programme '{name}' in school '{schema}'?",
                style=self.style,
            ):
                raise CommandError("Programme creation cancelled.")
            args.append(f"--name={name}")
            if code:
                args.append(f"--code={code}")
            call_command(
                "create_programme",
                *args,
                stdout=self.stdout,
                stderr=self.stderr,
            )
        elif operation == "academic-year":
            start = ask("Start date YYYY-MM-DD", style=self.style)
            end = ask("End date YYYY-MM-DD", style=self.style)
            if not start or not end:
                raise CommandError("Academic year start and end dates are required.")
            current = confirm(
                "Make this the current academic year?", style=self.style
            )
            if not confirm(
                f"Create academic year {start} to {end} in school '{schema}'?",
                style=self.style,
            ):
                raise CommandError("Academic year creation cancelled.")
            args.extend((f"--start-date={start}", f"--end-date={end}"))
            if current:
                args.append("--current")
            call_command(
                "create_academic_year",
                *args,
                stdout=self.stdout,
                stderr=self.stderr,
            )
        elif operation == "academic-term":
            year_id = self._choose_academic_year(schema)
            term = choose(
                "term",
                [
                    ("1", "Term 1"),
                    ("2", "Term 2"),
                    ("3", "Term 3"),
                    ("SEM1", "Semester 1"),
                    ("SEM2", "Semester 2"),
                ],
                stdout=self.stdout,
                style=self.style,
            )
            level = choose(
                "term level",
                [
                    ("ALL", "All forms (single track)"),
                    ("SHS1", "SHS 1"),
                    ("SHS2", "SHS 2"),
                    ("SHS3", "SHS 3"),
                ],
                stdout=self.stdout,
                style=self.style,
            )
            start = ask("Start date YYYY-MM-DD", style=self.style)
            end = ask("End date YYYY-MM-DD", style=self.style)
            if not start or not end:
                raise CommandError("Academic term start and end dates are required.")
            current = confirm("Make this the current term for its level?", style=self.style)
            if not confirm(
                "Create this term for the selected academic year?",
                style=self.style,
            ):
                raise CommandError("Academic term creation cancelled.")
            args.extend(
                (
                    f"--academic-year={year_id}",
                    f"--term={term}",
                    f"--level={level}",
                    f"--start-date={start}",
                    f"--end-date={end}",
                )
            )
            if current:
                args.append("--current")
            call_command(
                "create_academic_term",
                *args,
                stdout=self.stdout,
                stderr=self.stderr,
            )
        elif operation == "roster":
            self.stdout.write(
                self.style.WARNING(
                    "The roster is validated and previewed before any student "
                    "records are written. You will be asked to confirm the import."
                )
            )
            file_path = ask("Path to the roster file", style=self.style)
            if not file_path:
                raise CommandError("A roster file path is required.")
            call_command(
                "tenant_roster_import",
                file_path,
                *args,
                stdout=self.stdout,
                stderr=self.stderr,
            )
        else:
            raise CommandError("Unknown school facility operation.")

    @staticmethod
    def _tenant_context(schema):
        if not schema:
            return nullcontext()
        from tenancy.models import Tenant

        tenant = Tenant.objects.filter(schema_name=schema).first()
        if tenant is None:
            raise CommandError(f"Tenant schema '{schema}' was not found.")
        return tenant

    def _choose_house(self, schema):
        from core.models import House

        with self._tenant_context(schema):
            houses = list(House.objects.order_by("display_order", "name"))
        if not houses:
            raise CommandError(
                "No houses are configured. Create a house before adding "
                "dormitories or rooms."
            )
        return choose(
            "house",
            [(str(house.pk), house.name) for house in houses],
            stdout=self.stdout,
            style=self.style,
        )

    def _choose_dormitory(self, schema, house_id):
        from core.models import Dormitory

        with self._tenant_context(schema):
            dormitories = list(
                Dormitory.objects.filter(house_id=house_id).order_by(
                    "gender", "name"
                )
            )
        if not dormitories:
            raise CommandError(
                "The selected house has no dormitories. Create a dormitory "
                "before adding rooms."
            )
        return choose(
            "dormitory",
            [
                (
                    str(dormitory.pk),
                    f"{dormitory.name} ({dormitory.get_gender_display()}, "
                    f"capacity {dormitory.capacity})",
                )
                for dormitory in dormitories
            ],
            stdout=self.stdout,
            style=self.style,
        )

    def _choose_programme(self, schema, *, allow_none=False):
        from core.models import Programme

        with self._tenant_context(schema):
            programmes = list(Programme.objects.order_by("name"))
        if not programmes and not allow_none:
            raise CommandError(
                "No programmes are configured. Create a programme first."
            )
        choices = [
            (str(programme.pk), programme.name) for programme in programmes
        ]
        if allow_none:
            choices.insert(0, ("", "No programme"))
        return choose(
            "programme",
            choices,
            stdout=self.stdout,
            style=self.style,
        )

    def _choose_academic_year(self, schema):
        from core.models import AcademicYear

        with self._tenant_context(schema):
            years = list(AcademicYear.objects.order_by("-start_date"))
        if not years:
            raise CommandError(
                "No academic years are configured. Create an academic year first."
            )
        return choose(
            "academic year",
            [(str(year.pk), year.name) for year in years],
            stdout=self.stdout,
            style=self.style,
        )

    def _plans(self):
        schema = self._school_schema()
        action = choose(
            "subscription action",
            [
                ("show", "Show current subscription and rates"),
                ("change", "Change plan tier"),
                ("rate", "Edit a subscription, hosting, or SMS rate"),
            ],
            stdout=self.stdout,
            style=self.style,
        )
        args = ["tenant_subscription", f"--schema={schema}"]
        if action == "show":
            args.append("--show")
        elif action == "change":
            plan = choose(
                "plan",
                [(tier.value, tier.label) for tier in PlanTier],
                stdout=self.stdout,
                style=self.style,
            )
            args.append(f"--set-plan={plan}")
            if confirm("Also apply this plan's default features and limits?", style=self.style):
                args.append("--apply-plan-defaults")
            if not confirm(f"Save subscription plan '{plan}' for '{schema}'?", style=self.style):
                raise CommandError("Plan change cancelled.")
        else:
            from tenancy.management.commands.tenant_subscription import SMS_PACKAGES

            setting = choose(
                "subscription setting",
                [
                    ("base-fee", "Term base fee"),
                    ("student-rate", "Monthly rate per student"),
                    ("infra-cost", "Monthly infrastructure cost basis"),
                    ("infra-margin", "Infrastructure markup percentage"),
                    ("hosting", "Whether invoices include hosting charges"),
                    ("sms-package", "SMSOnlineGH package preset"),
                    ("sms-cost", "Custom SMS cost basis"),
                    ("sms-margin", "SMS markup percentage"),
                    ("sms-rate", "Charged rate per SMS"),
                    ("currency", "Invoice currency"),
                ],
                stdout=self.stdout,
                style=self.style,
            )
            if setting == "hosting":
                include_hosting = choose(
                    "hosting invoice option",
                    [("include", "Include hosting charges"), ("exclude", "Exclude hosting charges")],
                    stdout=self.stdout,
                    style=self.style,
                )
                args.append(f"--{include_hosting}-hosting")
                description = f"hosting charges {include_hosting}d"
            else:
                options = {
                    "base-fee": ("--set-base-fee", "New term base fee"),
                    "student-rate": ("--set-rate-per-student", "New monthly per-student rate"),
                    "infra-cost": ("--set-infra-cost", "New monthly infrastructure cost basis"),
                    "infra-margin": ("--set-infra-margin", "New infrastructure margin percentage"),
                    "sms-package": ("--set-sms-package", "SMSOnlineGH package price"),
                    "sms-cost": ("--set-sms-cost-basis", "New SMS provider cost basis"),
                    "sms-margin": ("--set-sms-margin", "New SMS margin percentage"),
                    "sms-rate": ("--set-rate-per-sms", "New charged rate per SMS"),
                    "currency": ("--set-currency", "Three-letter invoice currency"),
                }
                option, prompt = options[setting]
                if setting == "sms-package":
                    value = choose(
                        "SMS package price",
                        [
                            (str(price), f"GHS {price} package")
                            for price in SMS_PACKAGES
                        ],
                        stdout=self.stdout,
                        style=self.style,
                    )
                else:
                    value = ask(prompt, style=self.style)
                args.append(f"{option}={value}")
                description = f"{prompt.lower()} to {value}"
            if not confirm(f"Save {description} for '{schema}'?", style=self.style):
                raise CommandError("Subscription update cancelled.")
        call_command(*args, stdout=self.stdout, stderr=self.stderr)

    def _features(self):
        from tenancy.management.commands.tenant_features import (
            BOOLEAN_FEATURES,
            LIMIT_FEATURES,
        )

        schema = self._school_schema()
        action = choose(
            "feature action",
            [
                ("list", "List current features and limits"),
                ("enable", "Enable a feature"),
                ("disable", "Disable a feature"),
                ("limit", "Set a numeric limit"),
                ("reset", "Reset all features to the plan defaults"),
            ],
            stdout=self.stdout,
            style=self.style,
        )
        args = ["tenant_features", f"--schema={schema}"]
        if action == "list":
            args.append("--list")
        elif action in {"enable", "disable"}:
            feature = choose(
                "feature",
                list(BOOLEAN_FEATURES.items()),
                stdout=self.stdout,
                style=self.style,
            )
            args.append(f"--{action}={feature}")
            if not confirm(f"{action.title()} '{feature}' for '{schema}'?", style=self.style):
                raise CommandError("Feature change cancelled.")
        elif action == "limit":
            key = choose(
                "limit",
                list(LIMIT_FEATURES.items()),
                stdout=self.stdout,
                style=self.style,
            )
            value = ask("New limit (use 0 for unlimited SMS)", style=self.style)
            args.append(f"--set-limit={key}={value}")
            if not confirm(f"Set {key} to {value} for '{schema}'?", style=self.style):
                raise CommandError("Limit change cancelled.")
        else:
            if not confirm(f"Reset all features for '{schema}' to plan defaults?", style=self.style):
                raise CommandError("Feature reset cancelled.")
            args.append("--reset-to-plan")
        call_command(*args, stdout=self.stdout, stderr=self.stderr)

    def _invoices(self):
        schema = self._school_schema()
        action = choose(
            "invoice action",
            [
                ("list", "List invoices"),
                ("generate", "Generate an invoice"),
                ("show", "Show an invoice"),
                ("paid", "Mark an invoice as paid"),
                ("cancel", "Cancel an unpaid invoice"),
            ],
            stdout=self.stdout,
            style=self.style,
        )
        args = ["tenant_invoice", f"--schema={schema}"]
        if action == "list":
            args.append("--list")
        elif action == "generate":
            start = ask("Billing period start YYYY-MM-DD (default: current month)", style=self.style)
            end = ask("Billing period end YYYY-MM-DD (default: current month)", style=self.style)
            if start:
                args.append(f"--billing-start={start}")
            if end:
                args.append(f"--billing-end={end}")
            if confirm("Also create a PDF invoice?", style=self.style):
                args.append("--generate-pdf")
            if not confirm(f"Generate an invoice for '{schema}'?", style=self.style):
                raise CommandError("Invoice generation cancelled.")
        else:
            invoice_number = ask("Invoice number", style=self.style)
            if action == "show":
                args.append(f"--show={invoice_number}")
            elif action == "paid":
                reference = ask("Payment reference", style=self.style)
                if not confirm(f"Mark invoice '{invoice_number}' paid?", style=self.style):
                    raise CommandError("Payment update cancelled.")
                args.extend((f"--mark-paid={invoice_number}", f"--payment-ref={reference}"))
            else:
                if not confirm(f"Cancel unpaid invoice '{invoice_number}'?", style=self.style):
                    raise CommandError("Invoice cancellation cancelled.")
                args.append(f"--cancel={invoice_number}")
        call_command(*args, stdout=self.stdout, stderr=self.stderr)

    def _backups(self):
        scope = choose(
            "backup scope",
            [("one", "One school"), ("all", "All active schools")],
            stdout=self.stdout,
            style=self.style,
        )
        include_media = confirm("Include tenant media and documents?", style=self.style)
        args = ["tenant_backup"]
        target = self._school_schema() if scope == "one" else "all active schools"
        args.append(f"--schema={target}" if scope == "one" else "--all")
        args.append("--verify")
        if include_media:
            args.append("--include-media")
            if confirm("Allow an empty media archive?", style=self.style):
                args.append("--allow-empty-media")
        if confirm("Upload a second copy to the configured encrypted offsite bucket?", style=self.style):
            args.append("--offsite")
        if not confirm(f"Create and verify a backup for '{target}'?", style=self.style):
            raise CommandError("Backup cancelled.")
        call_command(*args, stdout=self.stdout, stderr=self.stderr)

    def _restore_dry_run(self):
        self.stdout.write(
            self.style.WARNING(
                "Restore is allowed only into a new, empty schema. This guided step validates "
                "the archive but does not restore data."
            )
        )
        schema = ask("Target schema name", style=self.style)
        dump = ask("Path to the verified backup archive", style=self.style)
        if not confirm("Run restore safety checks only (dry run)?", style=self.style):
            raise CommandError("Restore validation cancelled.")
        call_command(
            "tenant_restore",
            f"--schema={schema}",
            f"--dump={dump}",
            "--dry-run",
            stdout=self.stdout,
            stderr=self.stderr,
        )
        self.stdout.write(
            self.style.NOTICE(
                "Dry run completed. Review its output before using tenant_restore directly "
                "for an actual restore."
            )
        )

    def _support_access(self):
        schema = self._school_schema()
        action = choose(
            "support access action",
            [
                ("status", "View active support access status"),
                ("generate", "Generate a time-limited access approval token"),
                ("verify", "Verify a support access token"),
                ("revoke", "Revoke active support access"),
            ],
            stdout=self.stdout,
            style=self.style,
        )
        args = ["support_access", f"--schema={schema}"]
        if action == "status":
            args.append("--status")
        elif action == "generate":
            reason = ask("Reason for access", style=self.style)
            hours = ask("Token lifetime in hours (1-168)", default="4", style=self.style)
            args.extend(("--generate", f"--reason={reason}", f"--ttl-hours={hours}"))
            if not confirm("Generate this support access token?", style=self.style):
                raise CommandError("Support access request cancelled.")
        elif action == "verify":
            token = getpass(self.style.HTTP_INFO("Paste the support token (input hidden): "))
            args.append(f"--verify={token}")
        else:
            reason = ask("Reason for revocation", style=self.style)
            if not confirm("Revoke active support access?", style=self.style):
                raise CommandError("Support access revocation cancelled.")
            args.extend(("--revoke", f"--reason={reason}"))
        call_command(*args, stdout=self.stdout, stderr=self.stderr)
