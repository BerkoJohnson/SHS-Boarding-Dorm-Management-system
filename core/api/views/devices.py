"""Push-device registration for background exeat and roll-call alerts."""
from __future__ import annotations

from django.utils import timezone

from core.api.base import APIError, NotFound, api_endpoint, no_content, parse_json, success
from core.models import PushDevice


def _device_payload(device):
    return {
        "id": str(device.pk),
        "platform": device.platform,
        "device_id": device.device_id,
        "app_version": device.app_version,
        "is_active": device.is_active,
        "last_seen_at": device.last_seen_at.isoformat(),
        "created_at": device.created_at.isoformat(),
    }


@api_endpoint(["GET", "POST"])
def devices(request):
    if request.method == "GET":
        queryset = PushDevice.objects.filter(
            user=request.api_user, is_active=True
        ).order_by("-last_seen_at")
        return success(request, [_device_payload(device) for device in queryset])

    payload = parse_json(request)
    token = str(payload.get("token") or "").strip()
    platform = str(payload.get("platform") or "").strip().lower()
    device_id = str(payload.get("device_id") or "").strip()[:128]
    app_version = str(payload.get("app_version") or "").strip()[:30]

    if not token:
        raise APIError(
            "validation_error",
            "A push token is required.",
            fields={"token": ["This field is required."]},
        )
    if platform not in dict(PushDevice.Platform.choices):
        raise APIError(
            "validation_error",
            "platform must be 'android' or 'ios'.",
            fields={"platform": ["Choose a supported platform."]},
        )

    if device_id:
        PushDevice.objects.filter(
            user=request.api_user, device_id=device_id
        ).exclude(token=token).update(is_active=False)

    device, _ = PushDevice.objects.update_or_create(
        token=token,
        defaults={
            "user": request.api_user,
            "platform": platform,
            "device_id": device_id,
            "app_version": app_version,
            "is_active": True,
            "last_seen_at": timezone.now(),
        },
    )
    return success(request, _device_payload(device), status=201)


@api_endpoint(["PATCH", "DELETE"])
def device_detail(request, device_id):
    try:
        device = PushDevice.objects.get(pk=device_id, user=request.api_user)
    except (PushDevice.DoesNotExist, ValueError):
        raise NotFound("That device was not found.")

    if request.method == "DELETE":
        device.is_active = False
        device.save(update_fields=["is_active", "updated_at"])
        return no_content(request)

    payload = parse_json(request)
    changed = []
    if "app_version" in payload:
        device.app_version = str(payload.get("app_version") or "")[:30]
        changed.append("app_version")
    if payload.get("is_active") is not None:
        device.is_active = bool(payload.get("is_active"))
        changed.append("is_active")
    device.last_seen_at = timezone.now()
    changed.extend(["last_seen_at", "updated_at"])
    device.save(update_fields=changed)
    return success(request, _device_payload(device))
