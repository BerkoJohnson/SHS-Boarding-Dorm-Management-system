"""Notifications for demonstration enquiries.

Delivery is best-effort by design. The default ``EMAIL_BACKEND`` in this
project is Django's console backend, which never sends real mail, and an SMTP
or gateway outage must not turn a successfully stored enquiry into an error for
the visitor. Failures are logged and the enquiry is kept.
"""
from __future__ import annotations

import logging

import requests
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)

# The SMSOnlineGH v5 endpoint, shared with the school SMS pipeline.
SMSONLINEGH_SMS_URL = "https://api.smsonlinegh.com/v5/message/sms/send"


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


def sms_alert_recipients() -> list[str]:
    """Return the normalised SMS alert numbers, or ``[]`` when the alert is off.

    Numbers are normalised with the same helper the school SMS pipeline uses so
    that a local number such as ``024 816 0391`` is sent in the same
    international form the gateway expects.
    """
    configured = getattr(settings, "DEMO_REQUEST_SMS_TO", None) or []
    if not configured:
        return []

    # Imported lazily: the public site must not pull the school app's models
    # into its import path unless an SMS alert is actually being sent.
    from core.services.sms import normalize_phone_number

    recipients = []
    for raw in configured:
        normalized = normalize_phone_number(raw)
        if normalized:
            recipients.append(normalized)
        else:
            logger.warning(
                "Ignoring unparseable DEMO_REQUEST_SMS_TO entry %r.", raw
            )
    return recipients


def build_sms_alert_body(demo_request) -> str:
    """Return a one-segment summary of a new enquiry.

    Deliberately short: the full detail lives in the stored record and the
    email. Truncating the school name keeps the message inside a single GSM
    segment (160 characters) for the common case.
    """
    school = (demo_request.school_name or "").strip()
    if len(school) > 60:
        school = school[:57].rstrip() + "..."
    return (
        f"Edensys: new demo request #{demo_request.pk} from {school} "
        f"({demo_request.get_region_display()}). {demo_request.phone}"
    )


def notify_demo_request_sms(demo_request) -> bool:
    """Send an SMS alert for a new enquiry. Returns ``True`` on acceptance.

    Best-effort, like the email notification: a gateway outage is logged and
    reported to the caller but never breaks a stored enquiry. This bypasses the
    school SMS pipeline on purpose — a demonstration enquiry has no school
    tenant, so it must not depend on ``SMSConfiguration``, ``SMSDelivery``, the
    monthly quota or a Celery worker.
    """
    recipients = sms_alert_recipients()
    if not recipients:
        return False

    api_key = (getattr(settings, "SMS_ONLINE_GH_API_KEY", "") or "").strip()
    if not api_key:
        logger.error(
            "DEMO_REQUEST_SMS_TO is set but SMS_ONLINE_GH_API_KEY is empty; "
            "no SMS alert was sent for enquiry #%s.",
            demo_request.pk,
        )
        return False

    sender = (
        (getattr(settings, "DEMO_REQUEST_SMS_SENDER", "") or "").strip()
        or (getattr(settings, "SMS_SENDER_ID", "") or "").strip()
        or "EDENSYS"
    )
    payload = {
        "key": api_key,
        "text": build_sms_alert_body(demo_request),
        "type": 0,
        "sender": sender,
        "to": ",".join(recipients),
    }
    try:
        response = requests.post(
            SMSONLINEGH_SMS_URL,
            data=payload,
            headers={"Accept": "application/json"},
            timeout=getattr(settings, "SMS_HTTP_TIMEOUT", 10),
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.exception(
            "SMSOnlineGH alert failed for enquiry #%s.", demo_request.pk
        )
        return False

    handshake = data.get("handshake") if isinstance(data, dict) else None
    if isinstance(handshake, dict) and handshake.get("label") == "HSHK_OK":
        return True

    logger.error(
        "SMSOnlineGH rejected the SMS alert for enquiry #%s: %s",
        demo_request.pk,
        handshake if isinstance(handshake, dict) else data,
    )
    return False

