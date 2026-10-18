"""Data model for the public marketing website.

Deliberately minimal. The only thing the public site persists is a
demonstration enquiry, and because this app is part of ``PUBLIC_APPS`` that
record is created in the PostgreSQL ``public`` schema — never inside a
school's tenant schema. There is therefore no code path by which one school
could read another school's enquiry, or any enquiry at all.
"""
from __future__ import annotations

from django.db import models
from django.utils import timezone


class GhanaRegion(models.TextChoices):
    """The sixteen administrative regions of Ghana."""

    AHAFO = "ahafo", "Ahafo"
    ASHANTI = "ashanti", "Ashanti"
    BONO = "bono", "Bono"
    BONO_EAST = "bono_east", "Bono East"
    CENTRAL = "central", "Central"
    EASTERN = "eastern", "Eastern"
    GREATER_ACCRA = "greater_accra", "Greater Accra"
    NORTH_EAST = "north_east", "North East"
    NORTHERN = "northern", "Northern"
    OTI = "oti", "Oti"
    SAVANNAH = "savannah", "Savannah"
    UPPER_EAST = "upper_east", "Upper East"
    UPPER_WEST = "upper_west", "Upper West"
    VOLTA = "volta", "Volta"
    WESTERN = "western", "Western"
    WESTERN_NORTH = "western_north", "Western North"


class PreferredDemoMethod(models.TextChoices):
    """How a school would like the demonstration delivered.

    Only methods the business has confirmed it can offer appear here. In
    particular there is no on-site option, because on-site visits have not
    been approved as a commitment.
    """

    VIDEO_CALL = "video_call", "Online video call"
    PHONE_CALL = "phone_call", "Phone call"
    WHATSAPP = "whatsapp", "WhatsApp call or chat"
    NO_PREFERENCE = "no_preference", "No preference"


class DemoRequestStatus(models.TextChoices):
    """Internal triage state. Never shown to the visitor."""

    NEW = "new", "New"
    CONTACTED = "contacted", "Contacted"
    SCHEDULED = "scheduled", "Demo scheduled"
    ONBOARDING = "onboarding", "Moved to onboarding"
    CLOSED = "closed", "Closed / not proceeding"


class DemoRequest(models.Model):
    """A demonstration request submitted from the public website.

    Contact details are business-confidential. They are readable only by
    authorised Edensys personnel through the ``demo_requests`` management
    command (the public schema has no Django admin — see the module docstring
    of ``website/management/commands/demo_requests.py``).
    """

    school_name = models.CharField(
        max_length=200,
        help_text="Name of the senior high school requesting the demonstration.",
    )
    contact_name = models.CharField(
        max_length=150,
        help_text="Full name of the person making the request.",
    )
    role = models.CharField(
        max_length=120,
        help_text="Official role, for example Headmaster, Assistant Headmaster or Matron.",
    )
    phone = models.CharField(
        max_length=32,
        help_text="Contact number, stored in international format where possible.",
    )
    email = models.EmailField()
    region = models.CharField(max_length=32, choices=GhanaRegion.choices)
    boarding_population = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Estimated number of boarding students. Optional.",
    )
    preferred_demo_method = models.CharField(
        max_length=20,
        choices=PreferredDemoMethod.choices,
        default=PreferredDemoMethod.NO_PREFERENCE,
    )
    message = models.TextField(
        blank=True,
        default="",
        help_text="Free-text message or special requirements. Optional.",
    )

    status = models.CharField(
        max_length=20,
        choices=DemoRequestStatus.choices,
        default=DemoRequestStatus.NEW,
    )
    internal_notes = models.TextField(blank=True, default="")

    # Abuse diagnostics. The IP address is stored only as a salted hash so the
    # record can be rate-limit-reviewed without holding a raw address.
    source_ip_hash = models.CharField(max_length=64, blank=True, default="")
    user_agent = models.CharField(max_length=300, blank=True, default="")
    notification_sent = models.BooleanField(default=False)
    notification_sms_sent = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "demonstration request"
        verbose_name_plural = "demonstration requests"
        indexes = [
            models.Index(fields=["-created_at"], name="website_demo_created_idx"),
            models.Index(fields=["status"], name="website_demo_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.school_name} ({self.created_at:%Y-%m-%d})"

    @property
    def age_days(self) -> int:
        return (timezone.now() - self.created_at).days
