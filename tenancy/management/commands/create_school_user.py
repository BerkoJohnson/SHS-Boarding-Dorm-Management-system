import sys
from contextlib import nullcontext
from getpass import getpass
from uuid import UUID

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from core.models import House, HouseMaster, StaffRole, UserProfile
from ._guided import ask, choose, choose_tenant


ROLE_MAP = {
    "admin": StaffRole.ADMIN,
    "senior-housemaster": StaffRole.SENIOR_HOUSEMASTER,
    "housemaster": StaffRole.HOUSEMASTER,
    "house-tutor": StaffRole.HOUSE_TUTOR,
    "gate-officer": StaffRole.GATE_OFFICER,
    "medical": StaffRole.MEDICAL,
}
ROLE_CHOICES = ("superadmin", *ROLE_MAP)
HOUSEMASTER_ROLES = {"senior-housemaster", "housemaster"}


class Command(BaseCommand):
    help = "Create a school staff account in the selected tenant schema."

    def add_arguments(self, parser):
        parser.add_argument("--schema", help="Tenant schema to create the account in.")
        parser.add_argument("--username")
        parser.add_argument("--email", default="")
        parser.add_argument("--first-name", default="")
        parser.add_argument("--last-name", default="")
        parser.add_argument("--role", choices=ROLE_CHOICES)
        parser.add_argument("--house", help="House name for a housemaster assignment.")
        parser.add_argument("--staff-id", help="Required for housemaster accounts.")
        parser.add_argument("--phone-number", help="Required for housemaster accounts.")
        parser.add_argument(
            "--full-name",
            help="Housemaster's display name; defaults to the user's full name or username.",
        )
        parser.add_argument(
            "--unusable-password",
            action="store_true",
            help="Create an account that cannot sign in until a password is set separately.",
        )

    def handle(self, *args, **options):
        guided = (
            not options["username"]
            or not options["role"]
            or (settings.TENANCY_ENABLED and not options["schema"])
        )
        if guided:
            if not sys.stdin.isatty():
                if settings.TENANCY_ENABLED and not options["schema"]:
                    raise CommandError(
                        "TENANCY_ENABLED is True; pass --schema for scripted use or "
                        "run this command from a terminal to choose the school."
                    )
                raise CommandError(
                    "Interactive setup requires a terminal. Pass --username and --role "
                    "or run this command from a terminal."
                )
            options = self._guided_options(options)

        schema = options["schema"]
        if settings.TENANCY_ENABLED and not schema:
            raise CommandError(
                "TENANCY_ENABLED is True; pass --schema to select the school."
            )
        if schema and not settings.TENANCY_ENABLED:
            raise CommandError("--schema can only be used when TENANCY_ENABLED=True.")

        role_name = options["role"]
        self._validate_role_options(role_name, options)
        username = (options["username"] or "").strip()
        if not username:
            raise CommandError("Username cannot be empty.")

        if schema:
            from tenancy.models import Tenant

            tenant = Tenant.objects.filter(schema_name=schema).first()
            if tenant is None:
                raise CommandError(f"Tenant schema '{schema}' was not found.")
            context = tenant
            school_label = f"school '{tenant.name}' ({tenant.schema_name})"
        else:
            context = nullcontext()
            school_label = "the single-school database"

        with context:
            house = self._get_house(options["house"]) if options["house"] else None
            password = self._get_password(options["unusable_password"])
            if guided:
                self.stdout.write(
                    self.style.HTTP_INFO(
                        "\nStep 4 of 4 - creating the account and assigning its school role."
                    )
                )
            try:
                with transaction.atomic():
                    user = self._create_user(username, password, options, role_name)
                    self._create_housemaster(user, house, options, role_name)
            except (IntegrityError, ValidationError) as error:
                raise CommandError(f"Could not create user '{username}': {error}") from error

        account_type = (
            "superuser account" if role_name == "superadmin" else f"{role_name} user"
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created {account_type} '{username}' in {school_label}."
            )
        )
        if options["unusable_password"]:
            self.stdout.write(
                "The account has no usable password; set one before the user signs in."
            )

    def _guided_options(self, options):
        self.stdout.write(self.style.HTTP_INFO("\nSchool staff account setup"))
        self.stdout.write("Step 1 of 4 - choose the school and staff role.")

        if settings.TENANCY_ENABLED and not options["schema"]:
            options["schema"] = choose_tenant(stdout=self.stdout, style=self.style)
        elif not settings.TENANCY_ENABLED and options["schema"]:
            raise CommandError("--schema can only be used when TENANCY_ENABLED=True.")
        elif not settings.TENANCY_ENABLED:
            self.stdout.write("Using the configured single-school database.")

        if not options["role"]:
            options["role"] = choose(
                "staff role",
                [
                    ("superadmin", "School superadmin - full administrator access"),
                    ("admin", "Administrator - school administration"),
                    ("senior-housemaster", "Senior housemaster - school-wide house oversight"),
                    ("housemaster", "Housemaster - assigned to one house"),
                    ("house-tutor", "House tutor"),
                    ("gate-officer", "Gate officer"),
                    ("medical", "Medical / infirmary lead"),
                ],
                stdout=self.stdout,
                style=self.style,
            )

        self.stdout.write(self.style.HTTP_INFO("\nStep 2 of 4 - enter the account details."))
        options["username"] = options["username"] or ask(
            "Username", style=self.style
        )
        options["first_name"] = options["first_name"] or ask(
            "First name (optional)", default="", style=self.style
        )
        options["last_name"] = options["last_name"] or ask(
            "Last name (optional)", default="", style=self.style
        )
        options["email"] = options["email"] or ask(
            "Email (optional)", default="", style=self.style
        )

        if options["role"] in HOUSEMASTER_ROLES:
            if options["role"] == "housemaster" and not options["house"]:
                with self._tenant_context(options["schema"]):
                    houses = list(House.objects.order_by("name"))
                options["house"] = choose(
                    "house",
                    [
                        (str(house.pk), house.name)
                        for house in houses
                    ],
                    stdout=self.stdout,
                    style=self.style,
                )
            options["staff_id"] = options["staff_id"] or ask(
                "Staff ID", style=self.style
            )
            options["phone_number"] = options["phone_number"] or ask(
                "Phone number", style=self.style
            )
            if options["role"] == "senior-housemaster" and not options["house"]:
                self.stdout.write(
                    "Senior housemaster will have school-wide oversight (no house assignment)."
                )
        self.stdout.write(
            self.style.HTTP_INFO(
                "\nStep 3 of 4 - the password will be entered securely and will not be displayed."
            )
        )
        return options

    @staticmethod
    def _tenant_context(schema):
        if not schema:
            return nullcontext()
        from tenancy.models import Tenant

        tenant = Tenant.objects.filter(schema_name=schema).first()
        if tenant is None:
            raise CommandError(f"Tenant schema '{schema}' was not found.")
        return tenant

    @staticmethod
    def _validate_role_options(role_name, options):
        housemaster_role = role_name in HOUSEMASTER_ROLES
        if housemaster_role:
            missing = [
                option
                for option in ("staff_id", "phone_number")
                if not options[option] or not options[option].strip()
            ]
            if missing:
                names = ", ".join(f"--{name.replace('_', '-')}" for name in missing)
                raise CommandError(f"{role_name} accounts require {names}.")
            if role_name == "housemaster" and not options["house"]:
                raise CommandError("Housemaster accounts require --house.")
        elif options["house"] or options["staff_id"] or options["phone_number"]:
            raise CommandError(
                "--house, --staff-id, and --phone-number are only valid for "
                "housemaster or senior-housemaster accounts."
            )
        elif options["full_name"]:
            raise CommandError("--full-name is only valid for housemaster accounts.")

    @staticmethod
    def _get_house(house_name):
        try:
            house_id = UUID(house_name)
        except ValueError:
            house_id = None
        if house_id is not None:
            house = House.objects.filter(pk=house_id).first()
            if house is not None:
                return house
        matches = list(House.objects.filter(name__iexact=house_name.strip())[:2])
        if not matches:
            raise CommandError(f"House '{house_name}' was not found in this school.")
        if len(matches) > 1:
            raise CommandError(
                f"More than one house is named '{house_name}'; rename the houses "
                "so the assignment is unambiguous."
            )
        return matches[0]

    @staticmethod
    def _get_password(unusable_password):
        if unusable_password:
            return None
        if not sys.stdin.isatty():
            raise CommandError(
                "Password entry requires an interactive terminal. For a non-interactive "
                "account, pass --unusable-password and set the password separately."
            )
        password = getpass("Password: ")
        confirmation = getpass("Password (again): ")
        if not password:
            raise CommandError("Password cannot be empty.")
        if password != confirmation:
            raise CommandError("The two password entries did not match.")
        return password

    @staticmethod
    def _create_user(username, password, options, role_name):
        user_model = get_user_model()
        # Django's superuser flag grants the elevated school-admin permissions.
        is_superuser = role_name == "superadmin"
        user = user_model(
            username=username,
            email=(options["email"] or "").strip(),
            first_name=(options["first_name"] or "").strip(),
            last_name=(options["last_name"] or "").strip(),
            is_active=True,
            is_staff=True,
            is_superuser=is_superuser,
        )
        if password:
            validate_password(password, user)
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save()

        profile, _ = UserProfile.objects.get_or_create(user=user)
        profile.role = StaffRole.ADMIN if is_superuser else ROLE_MAP[role_name]
        profile.save(update_fields=["role"])
        return user

    @staticmethod
    def _create_housemaster(user, house, options, role_name):
        if role_name not in HOUSEMASTER_ROLES:
            return
        HouseMaster.objects.create(
            user=user,
            full_name=(
                (options["full_name"] or "").strip()
                or user.get_full_name()
                or user.username
            ),
            staff_id=options["staff_id"].strip(),
            phone_number=options["phone_number"].strip(),
            house=house,
            is_senior_housemaster=role_name == "senior-housemaster",
        )
