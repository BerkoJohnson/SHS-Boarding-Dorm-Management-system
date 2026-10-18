"""Notification email for demonstration enquiries.

Delivery is best-effort by design. The default ``EMAIL_BACKEND`` in this
project is Django's console backend, which never sends real mail, and an SMTP
outage must not turn a successfully stored enquiry into an error for the
visitor. Failures are logged and the enquiry is kept.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def notification_recipients() -> list[str]:
    """Return the configured enquiry recipients.

    Falls back to the published business address, and only then to
    ``DEFAULT_FROM_EMAIL``. An unconfigured deployment therefore sends
    enquiries somewhere a person reads, rather than to a bounce address that
    nobody monitors.
    """
    configured = getattr(settings, "DEMO_REQUEST_NOTIFY_EMAILS", None) or []
    recipients = [address for address in configured if address]
    if recipients:
        return recipients

    business_email = (getattr(settings, "EDEN_SYS_EMAIL", "") or "").strip()
    if business_email:
        return [business_email]

    fallback = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    if fallback and "@" in fallback:
        return [fallback]

    return []


def _humanise(value) -> str:
    return str(value).replace("_", " ").strip().capitalize() if value else "—"


def build_notification_body(demo_request) -> str:
    population = demo_request.boarding_population
    lines = [
        "A new demonstration request was submitted from the Edensys website.",
        "",
        f"School:            {demo_request.school_name}",
        f"Contact:           {demo_request.contact_name}",
        f"Role:              {demo_request.role}",
        f"Phone:             {demo_request.phone}",
        f"Email:             {demo_request.email}",
        f"Region:            {demo_request.get_region_display()}",
        f"Boarding students: {population if population else 'Not provided'}",
        (
            "Preferred method:  "
            + _humanise(demo_request.preferred_demo_method)
        ),
        f"Received:          {demo_request.created_at:%Y-%m-%d %H:%M %Z}",
        f"Reference:         #{demo_request.pk}",
        "",
        "Message:",
        demo_request.message or "(none provided)",
        "",
        "--",
        "Review enquiries with:  python manage.py demo_requests --list",
    ]
    return "\n".join(lines)


def notify_demo_request(demo_request) -> bool:
    """Email the business about a new enquiry. Returns ``True`` on success.

    Never raises: a mail failure is logged and reported to the caller so the
    record can be flagged, but it does not break the submission.
    """
    recipients = notification_recipients()
    if not recipients:
        logger.warning(
            "Demo request #%s stored but no notification recipients are "
            "configured (set DEMO_REQUEST_NOTIFY_EMAILS).",
            demo_request.pk,
        )
        return False

    sender = (
        getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@localhost"
    ).strip()
    subject = (
        f"[Edensys] Demo request — {demo_request.school_name} "
        f"({demo_request.get_region_display()})"
    )
    try:
        send_mail(
            subject=subject,
            message=build_notification_body(demo_request),
            from_email=sender,
            recipient_list=recipients,
            fail_silently=False,
        )
    except Exception:  # noqa: BLE001 - notification must never break submission
        logger.exception(
            "Failed to send demo-request notification for enquiry #%s.",
            demo_request.pk,
        )
        return False
    return True
