from __future__ import annotations

import logging
import re
from typing import Sequence
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_pgschemas.schema import get_current_schema

from core.models import (
    Exeat,
    HouseMaster,
    SMSConfiguration,
    SMSDelivery,
    SchoolConfiguration,
)
from tenancy.entitlements import quota_guard, sms_quota_allows

logger = logging.getLogger(__name__)
GUARDIAN_AUDIENCE = "guardian"
HOUSEMASTER_AUDIENCE = "housemaster"
GSM_03_38_BASIC = (
    "@\u00a3$\u00a5\u00e8\u00e9\u00f9\u00ec\u00f2\u00c7\n\u00d8\u00f8\r"
    "\u00c5\u00e5\u0394_\u03a6\u0393\u039b\u03a9\u03a0\u03a8\u03a3\u0398\u039e"
    "\u00c6\u00e6\u00df\u00c9 !\"#\u00a4%&'()*+,-./0123456789:;<=>?"
    "\u00a1ABCDEFGHIJKLMNOPQRSTUVWXYZ\u00c4\u00d6\u00d1\u00dc\u00a7\u00bf"
    "abcdefghijklmnopqrstuvwxyz\u00e4\u00f6\u00f1\u00fc\u00e0"
)
GSM_03_38_EXTENDED = "\f^{}\\[~]|\u20ac"


def sms_segment_count(message: str) -> int:
    """Return the standard GSM-7 or UCS-2 segment count for a message."""
    if all(character in GSM_03_38_BASIC for character in message):
        septets = sum(
            2 if character in GSM_03_38_EXTENDED else 1 for character in message
        )
        return 1 if septets <= 160 else (septets + 152) // 153

    code_units = len(message.encode("utf-16-be")) // 2
    return 1 if code_units <= 70 else (code_units + 66) // 67


def _school_short_name() -> str:
    """Return the school's short display name for SMS message text."""
    configuration = SchoolConfiguration.get_solo()
    raw = (configuration.short_name or configuration.name or "ATSEC").strip()
    # Keep the message prefix short and safe for the GSM 03.38 alphabet.
    sanitized = re.sub(r"[^A-Za-z0-9]", "", raw)[:11]
    return sanitized.upper() if sanitized else "EDENSYS"


def _sms_sender_id() -> str:
    """Resolve the approved SMS sender ID.

    The deployment-level ``SMS_SENDER_ID`` is the approved value maintained by
    Eden Sys. The school short name is only a fallback when no sender has been
    configured. Both paths are normalised to the GSM 03.38 alphanumeric sender
    format (maximum 11 characters).
    """
    configured = (getattr(settings, "SMS_SENDER_ID", "") or "").strip()
    if not configured:
        configuration = SchoolConfiguration.get_solo()
        configured = (configuration.short_name or configuration.name or "ATSEC").strip()

    sanitized = re.sub(r"[^A-Za-z0-9]", "", configured)[:11].upper()
    sender = sanitized or "EDENSYS"
    if configured.upper() != sender:
        logger.warning(
            "SMS sender ID %r was normalised to %r",
            configured,
            sender,
        )
    return sender


def normalize_phone_number(value: str | None) -> str:
    if not value:
        return ""

    digits = "".join(
        character for character in str(value).strip() if character.isdigit()
    )
    if digits.startswith("0"):
        digits = f"233{digits[1:]}"
    if not 9 <= len(digits) <= 15:
        return ""
    return f"+{digits}"


def _housemaster_recipients(exeat: Exeat) -> list[tuple[str, str, str]]:
    if not exeat.student_id or not exeat.student.house_id:
        return []

    housemasters = HouseMaster.objects.filter(
        house_id=exeat.student.house_id,
        is_senior_housemaster=False,
    ).order_by("full_name")
    recipients = []
    for housemaster in housemasters:
        phone = normalize_phone_number(housemaster.phone_number)
        if phone:
            recipients.append((housemaster.full_name, phone, HOUSEMASTER_AUDIENCE))
    return recipients


def _guardian_recipients(exeat: Exeat) -> list[tuple[str, str, str]]:
    if not exeat.student_id or not exeat.student.guardian_id:
        return []

    guardian = exeat.student.guardian
    recipients = []
    for label, number in (
        ("Guardian", guardian.phone_number),
        ("Guardian alternate", guardian.alt_phone_number),
    ):
        normalized = normalize_phone_number(number)
        if normalized:
            recipients.append(
                (f"{guardian.full_name} ({label})", normalized, GUARDIAN_AUDIENCE)
            )
    return recipients


def build_exeat_event_message(
    exeat: Exeat,
    event_type: str,
    *,
    audience: str = GUARDIAN_AUDIENCE,
) -> str:
    if audience == GUARDIAN_AUDIENCE:
        guardian_name = (
            exeat.student.guardian.full_name or "Guardian"
        ).strip() or "Guardian"
        student_name = exeat.student.full_name
        short_name = _school_short_name()
        return_by = timezone.localtime(
            exeat.expected_return,
            ZoneInfo(getattr(settings, "SMS_TIME_ZONE", settings.TIME_ZONE)),
        ).strftime("%d %b %Y %H:%M")
        guardian_messages = {
            SMSDelivery.EventType.REQUESTED: (
                f"{short_name}: Dear {guardian_name}, Your ward '{student_name}' is awaiting school approval."
            ),
            SMSDelivery.EventType.APPROVED: (
                f"{short_name}: Dear {guardian_name}, Your ward '{student_name}' leave is approved. Return by {return_by}."
            ),
            SMSDelivery.EventType.REJECTED: (
                f"{short_name}: Dear {guardian_name}, Your ward '{student_name}' leave request was not approved. "
                "Please contact the school."
            ),
            SMSDelivery.EventType.DEPARTURE: (
                f"{short_name}: Dear {guardian_name}, Your ward '{student_name}' has left school. Return by {return_by}."
            ),
            SMSDelivery.EventType.RETURN: (
                f"{short_name}: Dear {guardian_name}, Your ward '{student_name}' has returned to school."
            ),
            SMSDelivery.EventType.OVERDUE: (
                f"{short_name}: Dear {guardian_name}, Your ward '{student_name}' has not returned on time. "
                "Please contact the school."
            ),
        }
        try:
            return guardian_messages[event_type]
        except KeyError as error:
            raise ValueError(f"Unsupported exeat SMS event: {event_type}") from error

    event_messages = {
        SMSDelivery.EventType.REQUESTED: "Exeat request submitted",
        SMSDelivery.EventType.APPROVED: "Exeat approved",
        SMSDelivery.EventType.REJECTED: "Exeat request rejected",
        SMSDelivery.EventType.DEPARTURE: "Student departed campus",
        SMSDelivery.EventType.RETURN: "Student returned to campus",
        SMSDelivery.EventType.OVERDUE: "Exeat overdue: student has not returned",
    }
    try:
        prefix = event_messages[event_type]
    except KeyError as error:
        raise ValueError(f"Unsupported exeat SMS event: {event_type}") from error

    sms_timezone = ZoneInfo(getattr(settings, "SMS_TIME_ZONE", settings.TIME_ZONE))
    local_time = timezone.localtime(timezone.now(), sms_timezone)
    return (
        f"{prefix}: {exeat.student.full_name} (ref {exeat.exeat_number}). "
        f"{local_time:%d %b %Y %H:%M}."
    )


def get_exeat_sms_targets(
    exeat: Exeat,
    event_type: str,
) -> list[tuple[str, str, str]]:
    if event_type not in SMSDelivery.EventType.values:
        raise ValueError(f"Unsupported exeat SMS event: {event_type}")

    recipients = _guardian_recipients(exeat)
    recipients.extend(_housemaster_recipients(exeat))

    deduped: list[tuple[str, str, str]] = []
    seen_numbers = set()
    for name, number, audience in recipients:
        if number not in seen_numbers:
            seen_numbers.add(number)
            deduped.append((name, number, audience))
    return deduped


def enqueue_exeat_event_sms(
    exeat: Exeat,
    event_type: str,
    *,
    occurrence_key: str = "",
) -> list[SMSDelivery]:
    if not SMSConfiguration.is_enabled():
        return []

    recipients = get_exeat_sms_targets(exeat, event_type)
    deliveries = []
    tenant_schema = None
    if settings.TENANCY_ENABLED:
        schema = get_current_schema()
        tenant_schema = getattr(schema, "schema_name", None) if schema else None
        if not tenant_schema or tenant_schema == "public":
            raise RuntimeError("SMS deliveries must be queued inside a school tenant.")
    for recipient_name, phone_number, audience in recipients:
        dedupe_key = f"{event_type}:{exeat.pk}:{phone_number}:{occurrence_key}"
        existing = SMSDelivery.objects.filter(dedupe_key=dedupe_key).first()
        if existing is not None:
            deliveries.append(existing)
            continue
        message = build_exeat_event_message(exeat, event_type, audience=audience)
        with transaction.atomic():
            # Serialize the monthly-SMS check with the row that consumes budget.
            with quota_guard("max_sms_monthly", schema_name=tenant_schema):
                if not sms_quota_allows(
                    sms_segment_count(message),
                    schema_name=tenant_schema,
                ):
                    logger.warning(
                        "SMS delivery for exeat %s was not queued because the "
                        "tenant monthly SMS limit was reached",
                        exeat.pk,
                    )
                    continue
                delivery, created = SMSDelivery.objects.get_or_create(
                    dedupe_key=dedupe_key,
                    defaults={
                        "exeat": exeat,
                        "event_type": event_type,
                        "recipient_name": recipient_name,
                        "phone_number": phone_number,
                        "message": message,
                        "status": SMSDelivery.Status.QUEUED,
                    },
                )
        deliveries.append(delivery)
        if created:
            transaction.on_commit(
                lambda delivery_id=delivery.pk, schema=tenant_schema: enqueue_sms_delivery_task(
                    delivery_id, tenant_schema=schema
                )
            )
    if deliveries:
        from core.services.sms_alerts import notify_sms_quota_low

        notify_sms_quota_low()
    return deliveries


def enqueue_sms_delivery_task(delivery_id: str, *, tenant_schema=None) -> None:
    from core.tasks import send_sms_delivery_task

    if settings.TENANCY_ENABLED:
        if not tenant_schema:
            schema = get_current_schema()
            tenant_schema = getattr(schema, "schema_name", None) if schema else None
        if not tenant_schema or tenant_schema == "public":
            raise RuntimeError(
                "SMS delivery tasks must be queued with a school tenant."
            )

    try:
        if settings.TENANCY_ENABLED:
            send_sms_delivery_task.delay(
                str(delivery_id),
                tenant_schema=tenant_schema,
            )
        else:
            send_sms_delivery_task.delay(str(delivery_id))
    except Exception:
        logger.exception(
            "Could not queue SMS delivery %s; it remains in the outbox", delivery_id
        )


def send_bulk_sms(
    phone_numbers: Sequence[str],
    message: str,
    *,
    delivery_id: str | None = None,
) -> bool:
    cleaned_numbers = [
        normalized
        for number in phone_numbers
        if (normalized := normalize_phone_number(number))
    ]
    if not cleaned_numbers:
        logger.warning(
            "SMS was not sent because the recipient list contains no valid numbers"
        )
        return False
    if not SMSConfiguration.is_enabled():
        logger.warning("SMS was not sent because SMS dispatch is disabled")
        return False
    if not sms_quota_allows(
        len(cleaned_numbers) * sms_segment_count(message),
        exclude_delivery_id=delivery_id,
    ):
        logger.warning(
            "SMS was not sent because the tenant monthly SMS limit was reached"
        )
        return False

    api_key = getattr(settings, "SMS_ONLINE_GH_API_KEY", "")
    if not api_key:
        logger.error("SMS_ONLINE_GH_API_KEY is not configured; SMS was not sent")
        return False

    sender = _sms_sender_id()
    logger.info("Sending SMS with resolved sender ID %s", sender)
    payload = {
        "key": api_key,
        "text": message,
        "type": 0,
        "sender": sender,
        "to": ",".join(cleaned_numbers),
    }
    try:
        response = requests.post(
            "https://api.smsonlinegh.com/v5/message/sms/send",
            data=payload,
            headers={"Accept": "application/json"},
            timeout=getattr(settings, "SMS_HTTP_TIMEOUT", 10),
        )
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        logger.exception("SMSOnlineGH request failed")
        return False

    handshake = data.get("handshake", {})
    if handshake.get("label") == "HSHK_OK":
        return True

    logger.error(
        "SMSOnlineGH rejected the request: %s %s",
        handshake.get("label"),
        data.get("error"),
    )
    return False


def send_sms(
    to_phone_number: str,
    message: str,
    *,
    delivery_id: str | None = None,
) -> bool:
    return send_bulk_sms(
        [to_phone_number],
        message,
        delivery_id=delivery_id,
    )
