"""In-app alerts for SMS quota pressure and terminal delivery failures."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.utils import timezone

from core.models import StaffNotification, StaffRole


def _notify_sms_administrators(key, title, message):
    user_model = get_user_model()
    recipients = user_model.objects.filter(is_active=True).filter(
        Q(is_superuser=True)
        | Q(profile__role__in=(StaffRole.ADMIN, StaffRole.SENIOR_HOUSEMASTER))
    )
    existing = set(
        StaffNotification.objects.filter(
            kind=StaffNotification.Kind.SMS_ALERT,
            url=f"/reports/sms-usage/?alert={key}",
            user_id__in=recipients.values("pk"),
        ).values_list("user_id", flat=True)
    )
    StaffNotification.objects.bulk_create(
        [
            StaffNotification(
                user_id=user_id,
                kind=StaffNotification.Kind.SMS_ALERT,
                title=title,
                message=message,
                url=f"/reports/sms-usage/?alert={key}",
            )
            for user_id in recipients.values_list("pk", flat=True)
            if user_id not in existing
        ]
    )


def notify_sms_delivery_failed(delivery, *, failure_reason=None):
    """Notify school administrators after the delivery retry budget is spent."""
    message = failure_reason or (
        f"Delivery {delivery.pk} to {delivery.recipient_name or 'a recipient'} failed after "
        f"{delivery.attempts} attempts. Review the SMS delivery report."
    )
    _notify_sms_administrators(
        f"delivery-{delivery.pk}",
        "SMS delivery failed",
        message,
    )


def notify_sms_quota_low():
    """Warn once per month when remaining tenant SMS quota reaches its threshold."""
    from tenancy.entitlements import quota_limit, sms_monthly_usage

    limit = quota_limit("max_sms_monthly")
    if not limit:
        return False

    used = sms_monthly_usage()
    threshold = max(
        1,
        min(100, int(getattr(settings, "SMS_LOW_QUOTA_THRESHOLD_PERCENT", 20))),
    )
    remaining = max(0, limit - used)
    if remaining > limit * threshold / 100:
        return False

    month_key = timezone.localtime().strftime("%Y-%m")
    _notify_sms_administrators(
        f"quota-{month_key}",
        "SMS quota is running low",
        f"{remaining} of {limit} monthly SMS messages remain. Review usage and top up or adjust the quota.",
    )
    return True
