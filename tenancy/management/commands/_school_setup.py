"""Shared helpers for the guided school-setup management commands.

The commands in this package all follow the same conventions as the existing
tenant commands: an optional ``--schema`` selects the tenant (required when
tenancy is enabled), missing required options open the tenant wizard, and all
writes happen inside the selected tenant's schema.
"""

from contextlib import nullcontext
from datetime import datetime
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from core.models import (
    AcademicTerm,
    AcademicYear,
    Dormitory,
    House,
    Programme,
)
from tenancy.models import Tenant

from ._guided import launch_guided


def validate_tenant_mode(schema):
    """Apply the same tenancy guard used by the other platform commands."""

    if settings.TENANCY_ENABLED and not schema:
        raise CommandError(
            "TENANCY_ENABLED is True; pass --schema to select the school."
        )
    if schema and not settings.TENANCY_ENABLED:
        raise CommandError("--schema can only be used when TENANCY_ENABLED=True.")
    return schema


def tenant_context(schema):
    """Return the tenant context manager for ``schema``.

    With tenancy disabled the current (single-school) database is used.
    """

    if not schema:
        return nullcontext()
    tenant = Tenant.objects.filter(schema_name=schema).first()
    if tenant is None:
        raise CommandError(f"Tenant schema '{schema}' was not found.")
    return tenant


def format_validation_error(error):
    """Turn Django validation errors into a concise command-line message."""

    message_dict = getattr(error, "message_dict", None)
    if message_dict:
        parts = []
        for field, messages in message_dict.items():
            for message in messages:
                parts.append(message if field == "__all__" else f"{field}: {message}")
        return "; ".join(parts)
    messages = getattr(error, "messages", None)
    if messages:
        return "; ".join(messages)
    return str(error)


def require_text(value, label):
    text = str(value or "").strip()
    if not text:
        raise CommandError(f"{label} cannot be empty.")
    return text


def parse_integer(value, label, *, minimum=0, maximum=None, default=None):
    if value in (None, ""):
        if default is None:
            raise CommandError(f"{label} is required.")
        value = default
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise CommandError(f"{label} must be a whole number.") from error
    if number < minimum:
        raise CommandError(f"{label} must be at least {minimum}.")
    if maximum is not None and number > maximum:
        raise CommandError(f"{label} must be {maximum} or less.")
    return number


def parse_date(value, label):
    text = require_text(value, label)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError as error:
        raise CommandError(f"{label} must use YYYY-MM-DD.") from error


def _uuid_or_none(value):
    try:
        return UUID(str(value))
    except (AttributeError, TypeError, ValueError):
        return None


def resolve_house(value):
    label = require_text(value, "House")
    house_id = _uuid_or_none(label)
    if house_id is not None:
        house = House.objects.filter(pk=house_id).first()
        if house is not None:
            return house
    matches = list(House.objects.filter(name__iexact=label)[:2])
    if not matches:
        raise CommandError(f"House '{label}' was not found in this school.")
    if len(matches) > 1:
        raise CommandError(
            f"More than one house is named '{label}'; use the house ID so the "
            "assignment is unambiguous."
        )
    return matches[0]


def resolve_dormitory(value, house=None):
    label = require_text(value, "Dormitory")
    queryset = Dormitory.objects.select_related("house")
    if house is not None:
        queryset = queryset.filter(house=house)
    dormitory_id = _uuid_or_none(label)
    if dormitory_id is not None:
        dormitory = queryset.filter(pk=dormitory_id).first()
        if dormitory is not None:
            return dormitory
    matches = list(queryset.filter(name__iexact=label)[:2])
    if not matches:
        scope = f" in house '{house.name}'" if house is not None else ""
        raise CommandError(
            f"Dormitory '{label}' was not found{scope} in this school."
        )
    if len(matches) > 1:
        raise CommandError(
            f"More than one dormitory is named '{label}'; pass --house or use "
            "the dormitory ID so the assignment is unambiguous."
        )
    return matches[0]


def resolve_programme(value):
    label = require_text(value, "Programme")
    programme_id = _uuid_or_none(label)
    if programme_id is not None:
        programme = Programme.objects.filter(pk=programme_id).first()
        if programme is not None:
            return programme
    matches = list(
        Programme.objects.filter(name__iexact=label)[:2]
    )
    if not matches:
        matches = list(Programme.objects.filter(code__iexact=label)[:2])
    if not matches:
        raise CommandError(f"Programme '{label}' was not found in this school.")
    if len(matches) > 1:
        raise CommandError(
            f"More than one programme matches '{label}'; use the programme ID."
        )
    return matches[0]


def resolve_academic_year(value):
    label = require_text(value, "Academic year")
    year_id = _uuid_or_none(label)
    if year_id is not None:
        academic_year = AcademicYear.objects.filter(pk=year_id).first()
        if academic_year is not None:
            return academic_year
    matches = list(AcademicYear.objects.filter(name__iexact=label)[:2])
    if not matches:
        raise CommandError(f"Academic year '{label}' was not found in this school.")
    if len(matches) > 1:
        raise CommandError(
            f"More than one academic year matches '{label}'; use the year ID."
        )
    return matches[0]


def normalize_gender(value):
    key = str(value or "").strip().casefold()
    mapping = {
        "m": Dormitory.GenderBlock.MALE,
        "male": Dormitory.GenderBlock.MALE,
        "boy": Dormitory.GenderBlock.MALE,
        "boys": Dormitory.GenderBlock.MALE,
        "boys dormitory": Dormitory.GenderBlock.MALE,
        "f": Dormitory.GenderBlock.FEMALE,
        "female": Dormitory.GenderBlock.FEMALE,
        "girl": Dormitory.GenderBlock.FEMALE,
        "girls": Dormitory.GenderBlock.FEMALE,
        "girls dormitory": Dormitory.GenderBlock.FEMALE,
    }
    gender = mapping.get(key)
    if gender is None:
        raise CommandError("Gender must be male/boys or female/girls.")
    return gender


def normalize_term(value):
    key = "".join(character for character in str(value or "").casefold() if character.isalnum())
    mapping = {
        "1": AcademicTerm.TermChoice.TERM_1,
        "term1": AcademicTerm.TermChoice.TERM_1,
        "first": AcademicTerm.TermChoice.TERM_1,
        "firstterm": AcademicTerm.TermChoice.TERM_1,
        "2": AcademicTerm.TermChoice.TERM_2,
        "term2": AcademicTerm.TermChoice.TERM_2,
        "second": AcademicTerm.TermChoice.TERM_2,
        "secondterm": AcademicTerm.TermChoice.TERM_2,
        "3": AcademicTerm.TermChoice.TERM_3,
        "term3": AcademicTerm.TermChoice.TERM_3,
        "third": AcademicTerm.TermChoice.TERM_3,
        "thirdterm": AcademicTerm.TermChoice.TERM_3,
        "sem1": AcademicTerm.TermChoice.SEM_1,
        "semester1": AcademicTerm.TermChoice.SEM_1,
        "firstsemester": AcademicTerm.TermChoice.SEM_1,
        "sem2": AcademicTerm.TermChoice.SEM_2,
        "semester2": AcademicTerm.TermChoice.SEM_2,
        "secondsemester": AcademicTerm.TermChoice.SEM_2,
    }
    term = mapping.get(key)
    if term is None:
        raise CommandError(
            "Term must be 1, 2, 3, SEM1, or SEM2 (labels such as 'Term 1' "
            "are also accepted)."
        )
    return term


def normalize_level(value):
    key = "".join(character for character in str(value or "").casefold() if character.isalnum())
    mapping = {
        "all": AcademicTerm.LevelChoice.ALL,
        "allforms": AcademicTerm.LevelChoice.ALL,
        "singletrack": AcademicTerm.LevelChoice.ALL,
        "shs1": AcademicTerm.LevelChoice.SHS_1,
        "1": AcademicTerm.LevelChoice.SHS_1,
        "form1": AcademicTerm.LevelChoice.SHS_1,
        "shs2": AcademicTerm.LevelChoice.SHS_2,
        "2": AcademicTerm.LevelChoice.SHS_2,
        "form2": AcademicTerm.LevelChoice.SHS_2,
        "shs3": AcademicTerm.LevelChoice.SHS_3,
        "3": AcademicTerm.LevelChoice.SHS_3,
        "form3": AcademicTerm.LevelChoice.SHS_3,
    }
    level = mapping.get(key)
    if level is None:
        raise CommandError("Level must be ALL, SHS1, SHS2, or SHS3.")
    return level


class TenantSetupCommand(BaseCommand):
    """Base command for creating one school-setup entity in a tenant."""

    entity_name = "record"
    guided_task = ""
    required_options = ()

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default=None,
            help="Tenant schema (required when TENANCY_ENABLED=True).",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            help="List the configured records instead of creating one.",
        )
        self.add_entity_arguments(parser)

    def add_entity_arguments(self, parser):
        raise NotImplementedError

    def handle(self, *args, **options):
        schema = options.get("schema")
        if options.get("list"):
            validate_tenant_mode(schema)
            with tenant_context(schema):
                self.list_entities()
            return

        missing = []
        for option in self.required_options:
            value = options.get(option)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(option)
        if missing:
            launch_guided(self, self.guided_task, options=options)
            return

        validate_tenant_mode(schema)
        with tenant_context(schema):
            try:
                with transaction.atomic():
                    instance = self.perform_create(options)
            except (ValidationError, IntegrityError) as error:
                raise CommandError(
                    f"Could not create {self.entity_name}: "
                    f"{format_validation_error(error)}"
                ) from error
        self.stdout.write(
            self.style.SUCCESS(self.success_message(instance))
        )

    def perform_create(self, options):
        raise NotImplementedError

    def success_message(self, instance):
        raise NotImplementedError

    def list_entities(self):
        raise NotImplementedError

    def write_list_heading(self, label):
        self.stdout.write(self.style.HTTP_INFO(f"{label}:"))


def write_no_records(command, label, hint):
    command.stdout.write(f"No {label} are configured. {hint}")
