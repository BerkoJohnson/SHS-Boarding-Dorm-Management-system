"""Unified staff notification feed for the mobile app."""
from __future__ import annotations

import uuid

from django.conf import settings
from django.utils import timezone

from core.api.base import (
    APIError,
    NotFound,
    api_endpoint,
    parse_json,
    success,
)
from core.api.serializers import (
    roll_call_notification_payload,
    staff_notification_payload,
)
from core.models import RollCallNotification, StaffNotification


def unread_counts(user):
    staff = StaffNotification.objects.filter(user=user, read_at__isnull=True).count()
    roll_call = RollCallNotification.objects.filter(
        user=user, read_at__isnull=True
    ).count()
    return {"staff": staff, "roll_call": roll_call, "total": staff + roll_call}


def recent_notifications(user, limit=5):
    """Return the newest staff and roll-call notifications as one list."""
    staff = [
        staff_notification_payload(row)
        for row in StaffNotification.objects.filter(user=user).order_by(
            "-created_at"
        )[:limit]
    ]
    roll_call = [
        roll_call_notification_payload(row)
        for row in RollCallNotification.objects.filter(user=user).order_by(
            "-created_at"
        )[:limit]
    ]
    items = staff + roll_call
    items.sort(key=lambda item: item["created_at"] or "", reverse=True)
    return items[:limit]


def _pagination_params(request):
    try:
        page = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        raise APIError("invalid_page", "page must be a positive integer.")
    try:
        page_size = int(
            request.GET.get("page_size", getattr(settings, "API_PAGE_SIZE", 25))
        )
    except (TypeError, ValueError):
        raise APIError("invalid_page_size", "page_size must be a positive integer.")
    max_size = getattr(settings, "API_MAX_PAGE_SIZE", 100)
    if page < 1:
        raise APIError("invalid_page", "page must be a positive integer.")
    if page_size < 1 or page_size > max_size:
        raise APIError(
            "invalid_page_size",
            f"page_size must be between 1 and {max_size}.",
        )
    return page, page_size


@api_endpoint(["GET"])
def notifications(request):
    user = request.api_user
    page, page_size = _pagination_params(request)

    unread_only = (request.GET.get("unread") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    kind = (request.GET.get("kind") or "").strip()
    valid_kinds = set(StaffNotification.Kind.values) | {"roll_call"}
    if kind and kind not in valid_kinds:
        raise APIError(
            "validation_error",
            "Unknown notification kind.",
            fields={"kind": [f"Use one of: {', '.join(sorted(valid_kinds))}."]},
        )

    staff_qs = StaffNotification.objects.filter(user=user)
    roll_qs = RollCallNotification.objects.filter(user=user)
    if unread_only:
        staff_qs = staff_qs.filter(read_at__isnull=True)
        roll_qs = roll_qs.filter(read_at__isnull=True)
    if kind == "roll_call":
        staff_qs = staff_qs.none()
    elif kind:
        roll_qs = roll_qs.none()
        staff_qs = staff_qs.filter(kind=kind)

    staff_total = staff_qs.count()
    roll_total = roll_qs.count()
    total = staff_total + roll_total
    pages = (total + page_size - 1) // page_size if total else 0

    # Each source is already newest-first, so fetching the first ``needed``
    # rows from each is enough to compute the first ``needed`` rows of the
    # merged feed. The cap keeps deliberately deep paging from becoming an
    # unbounded query.
    needed = min(page * page_size, 2000)
    items = [
        staff_notification_payload(row)
        for row in staff_qs.order_by("-created_at")[:needed]
    ]
    items.extend(
        roll_call_notification_payload(row)
        for row in roll_qs.order_by("-created_at")[:needed]
    )
    items.sort(key=lambda item: item["created_at"] or "", reverse=True)
    offset = (page - 1) * page_size
    page_items = items[offset : offset + page_size]

    return success(
        request,
        page_items,
        meta={"unread": unread_counts(user)},
        pagination={
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_previous": page > 1 and total > 0,
        },
    )


def _resolve_notification(user, notification_id):
    raw = str(notification_id)
    source = None
    identifier = raw
    if ":" in raw:
        source, identifier = raw.split(":", 1)
        if source not in {"staff", "roll_call"}:
            raise NotFound("That notification was not found.")
    try:
        uuid.UUID(identifier)
    except (TypeError, ValueError, AttributeError):
        raise APIError(
            "validation_error",
            "Notification ids use the form staff:<uuid> or roll_call:<uuid>.",
        )

    if source in (None, "staff"):
        row = StaffNotification.objects.filter(user=user, pk=identifier).first()
        if row is not None:
            return "staff", row
    if source in (None, "roll_call"):
        row = RollCallNotification.objects.filter(user=user, pk=identifier).first()
        if row is not None:
            return "roll_call", row
    raise NotFound("That notification was not found.")


def _read_payload(source, row):
    if source == "staff":
        return staff_notification_payload(row)
    return roll_call_notification_payload(row)


@api_endpoint(["POST"])
def mark_read(request, notification_id):
    user = request.api_user
    source, row = _resolve_notification(user, notification_id)
    if row.read_at is None:
        row.read_at = timezone.now()
        row.save(update_fields=["read_at", "updated_at"])
    return success(
        request,
        {
            "notification": _read_payload(source, row),
            "unread": unread_counts(user),
        },
    )


@api_endpoint(["POST"])
def mark_all_read(request):
    user = request.api_user
    payload = parse_json(request)
    kind = str(payload.get("kind") or "").strip()
    valid_kinds = set(StaffNotification.Kind.values) | {"roll_call"}
    if kind and kind not in valid_kinds:
        raise APIError(
            "validation_error",
            "Unknown notification kind.",
            fields={"kind": [f"Use one of: {', '.join(sorted(valid_kinds))}."]},
        )

    now = timezone.now()
    staff_qs = StaffNotification.objects.filter(user=user, read_at__isnull=True)
    roll_qs = RollCallNotification.objects.filter(user=user, read_at__isnull=True)
    if kind == "roll_call":
        staff_qs = staff_qs.none()
    elif kind:
        roll_qs = roll_qs.none()
        staff_qs = staff_qs.filter(kind=kind)

    staff_updated = staff_qs.update(read_at=now, updated_at=now)
    roll_updated = roll_qs.update(read_at=now, updated_at=now)
    return success(
        request,
        {
            "marked_read": {
                "staff": staff_updated,
                "roll_call": roll_updated,
                "total": staff_updated + roll_updated,
            },
            "unread": unread_counts(user),
        },
    )
