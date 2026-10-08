from collections.abc import Sequence

from core.services.sms import send_bulk_sms as _send_bulk_sms
from core.services.sms import send_sms as _send_sms


def send_sms(to_phone_number: str, message: str) -> bool:
    """
    Backwards-compatible wrapper around the shared SMSOnlineGH service.

    Args:
        to_phone_number (str): The recipient's phone number (local or international format).
        message (str): The content of the SMS.

    Returns:
        bool: Whether the SMS gateway accepted the message.
    """
    return _send_sms(to_phone_number, message)


def send_bulk_sms(phone_numbers: Sequence[str], message: str) -> bool:
    """
    Backwards-compatible wrapper for sending SMS to multiple recipients.

    Args:
        phone_numbers (Sequence[str]): Recipient phone numbers.
        message (str): The content of the SMS.

    Returns:
        bool: Whether the SMS gateway accepted the message.
    """
    return _send_bulk_sms(phone_numbers, message)
