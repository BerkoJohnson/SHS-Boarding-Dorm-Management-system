import hashlib
from contextlib import contextmanager
from functools import wraps

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from django_pgschemas.schema import get_current_schema

from tenancy.feature_defaults import PLAN_DEFAULTS
from tenancy.models import TenantSubscription


def _current_schema_name():
    schema = get_current_schema()
    schema_name = getattr(schema, "schema_name", None) if schema else None
    return schema_name if schema_name and schema_name != "public" else None


def _subscription(schema_name=None):
    if not settings.TENANCY_ENABLED:
        return None
    schema_name = schema_name or _current_schema_name()
    if not schema_name:
        return None
    return (
        TenantSubscription.objects.filter(tenant__schema_name=schema_name)
        .only("features", "plan_tier")
        .first()
    )


def entitlement_value(key, *, schema_name=None):
    """Read a tenant override, falling back to the configured plan defaults."""
    subscription = _subscription(schema_name)
    if subscription is None:
        if settings.TENANCY_ENABLED and (
            schema_name or _current_schema_name()
        ):
            return PLAN_DEFAULTS["free"].get(key)
        return None
    features = subscription.features or {}
    if key in features:
        return features[key]
    return PLAN_DEFAULTS.get(subscription.plan_tier, {}).get(key)


def feature_enabled(key, *, schema_name=None):
    if not settings.TENANCY_ENABLED:
        return True
    if not (schema_name or _current_schema_name()):
        return False
    return bool(entitlement_value(key, schema_name=schema_name))


def quota_limit(key, *, schema_name=None):
    if not settings.TENANCY_ENABLED:
        return None
    value = entitlement_value(key, schema_name=schema_name)
    if value is None or (key == "max_sms_monthly" and value == 0):
        return None
    return int(value)


def ensure_quota_available(key, current, additional=1, *, schema_name=None):
    limit = quota_limit(key, schema_name=schema_name)
    if limit is not None and current + additional > limit:
        label = key.removeprefix("max_").replace("_", " ")
        raise ValidationError(
            f"The tenant {label} limit of {limit} has been reached."
        )


def quota_lock_key(schema_name, key):
    """Derive a stable signed 64-bit advisory-lock key from schema + quota type.

    PostgreSQL advisory locks take a single ``bigint``. Hashing ``schema:key``
    keeps distinct tenant/quota pairs on distinct locks without exceeding the
    signed 64-bit range.
    """
    digest = hashlib.sha256(f"{schema_name}:{key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


@contextmanager
def quota_guard(key, *, schema_name=None):
    """Serialize a quota check with its write under a transaction-scoped lock.

    Opens a transaction, takes a PostgreSQL ``pg_advisory_xact_lock`` keyed by
    the tenant schema and quota type, then yields. The lock is held until the
    surrounding transaction commits or rolls back, so the limit check and the
    row write it guards cannot interleave with another writer for the same
    tenant/quota pair (closing the check-then-write race).

    On non-PostgreSQL backends, or when tenancy is disabled, this degrades to a
    plain atomic block because there are no cross-tenant quotas to protect.
    """
    with transaction.atomic():
        if settings.TENANCY_ENABLED and connection.vendor == "postgresql":
            target_schema = schema_name or _current_schema_name() or "public"
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    [quota_lock_key(target_schema, key)],
                )
        yield


@contextmanager
def reserve_quota(key, current, additional=1, *, schema_name=None):
    """Guard a quota check and the write it authorizes.

    ``current`` may be a callable so the live count is taken *inside* the lock
    (avoiding a stale count). Wrap the write in the same ``with`` block::

        with reserve_quota("max_dormitories", Dormitory.objects.count):
            super().save(*args, **kwargs)
    """
    with quota_guard(key, schema_name=schema_name):
        current_value = current() if callable(current) else current
        ensure_quota_available(
            key,
            current_value,
            additional=additional,
            schema_name=schema_name,
        )
        yield


def tenant_feature_required(feature):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not feature_enabled(feature):
                raise PermissionDenied(
                    f"This tenant's subscription does not include {feature.replace('_', ' ')}."
                )
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def current_month_start():
    now = timezone.localtime()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def sms_monthly_usage(*, schema_name=None, exclude_delivery_id=None):
    from core.models import SMSDelivery

    month_start = current_month_start()
    deliveries = SMSDelivery.objects.filter(
        Q(status=SMSDelivery.Status.SENT, sent_at__gte=month_start)
        | Q(status=SMSDelivery.Status.QUEUED)
        | Q(status=SMSDelivery.Status.PROCESSING)
    )
    if exclude_delivery_id is not None:
        deliveries = deliveries.exclude(pk=exclude_delivery_id)
    return deliveries.count()


def sms_quota_allows(additional=1, *, schema_name=None, exclude_delivery_id=None):
    limit = quota_limit("max_sms_monthly", schema_name=schema_name)
    if limit is None:
        return True
    current = sms_monthly_usage(
        schema_name=schema_name,
        exclude_delivery_id=exclude_delivery_id,
    )
    return current + additional <= limit
