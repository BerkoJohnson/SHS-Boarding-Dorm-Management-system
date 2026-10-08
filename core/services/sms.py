from __future__ import annotations

import logging
from typing import Sequence
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import Exeat, HouseMaster, SMSConfiguration, SMSDelivery

logger = logging.getLogger(__name__)
GUARDIAN_AUDIENCE = "guardian"
HOUSEMASTER_AUDIENCE = "housemaster"


def normalize_phone_number(value: str | None) -> str:
    if not value:
        return ""

    digits = "".join(character for character in str(value).strip() if character.isdigit())
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
        student_name = exeat.student.full_name
        return_by = timezone.localtime(
            exeat.expected_return,
            ZoneInfo(getattr(settings, "SMS_TIME_ZONE", settings.TIME_ZONE)),
        ).strftime("%d %b %Y %H:%M")
        guardian_messages = {
            SMSDelivery.EventType.REQUESTED: (
                f"EDENSYS: Leave request for {student_name} is awaiting school approval."
            ),
            SMSDelivery.EventType.APPROVED: (
                f"EDENSYS: {student_name}'s leave is approved. Return by {return_by}."
            ),
            SMSDelivery.EventType.REJECTED: (
                f"EDENSYS: {student_name}'s leave request was not approved. "
                "Please contact the school."
            ),
            SMSDelivery.EventType.DEPARTURE: (
                f"EDENSYS: {student_name} has left school. Return by {return_by}."
            ),
            SMSDelivery.EventType.RETURN: (
                f"EDENSYS: {student_name} has returned to school."
            ),
            SMSDelivery.EventType.OVERDUE: (
                f"EDENSYS: {student_name} has not returned on time. "
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
    for recipient_name, phone_number, audience in recipients:
        dedupe_key = f"{event_type}:{exeat.pk}:{phone_number}:{occurrence_key}"
        delivery, created = SMSDelivery.objects.get_or_create(
            dedupe_key=dedupe_key,
            defaults={
                "exeat": exeat,
                "event_type": event_type,
                "recipient_name": recipient_name,
                "phone_number": phone_number,
                "message": build_exeat_event_message(
                    exeat,
                    event_type,
                    audience=audience,
                ),
                "status": SMSDelivery.Status.QUEUED,
            },
        )
        deliveries.append(delivery)
        if created:
            transaction.on_commit(
                lambda delivery_id=delivery.pk: enqueue_sms_delivery_task(delivery_id)
            )
    return deliveries


def enqueue_sms_delivery_task(delivery_id: str) -> None:
    from core.tasks import send_sms_delivery_task

    try:
        send_sms_delivery_task.delay(str(delivery_id))
    except Exception:
        logger.exception("Could not queue SMS delivery %s; it remains in the outbox", delivery_id)


def send_bulk_sms(phone_numbers: Sequence[str], message: str) -> bool:
    cleaned_numbers = [
        normalized
        for number in phone_numbers
        if (normalized := normalize_phone_number(number))
    ]
    if not cleaned_numbers:
        logger.warning("SMS was not sent because the recipient list contains no valid numbers")
        return False

    api_key = getattr(settings, "SMS_ONLINE_GH_API_KEY", "")
    if not api_key:
        logger.error("SMS_ONLINE_GH_API_KEY is not configured; SMS was not sent")
        return False

    payload = {
        "key": api_key,
        "text": message,
        "type": 0,
        "sender": getattr(settings, "SMS_SENDER_ID", "EDENSYS"),
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


def send_sms(to_phone_number: str, message: str) -> bool:
    return send_bulk_sms([to_phone_number], message)
