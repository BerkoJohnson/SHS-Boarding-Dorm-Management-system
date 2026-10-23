"""Shared JSON plumbing for the mobile API.

The helpers here define the response envelope, error mapping, authentication
hook, pagination, and idempotency used by every endpoint. Keeping them in one
place is what stops the ~75 endpoint surface from drifting into 75 dialects.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from functools import wraps

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import (
    ObjectDoesNotExist,
    PermissionDenied as DjangoPermissionDenied,
    ValidationError as DjangoValidationError,
)
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone


class APIError(Exception):
    """An error that should be rendered as the standard JSON envelope."""

    def __init__(self, code, message, *, status=400, fields=None, headers=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.fields = fields
        self.headers = headers


class AuthenticationRequired(APIError):
    def __init__(self, message="Authentication is required."):
        super().__init__(
            "authentication_required",
            message,
            status=401,
            headers={"WWW-Authenticate": 'Bearer realm="api"'},
        )


class Forbidden(APIError):
    def __init__(self, message="You do not have permission to perform this action.", code="forbidden"):
        super().__init__(code, message, status=403)


class NotFound(APIError):
    def __init__(self, message="The requested object was not found."):
        super().__init__("not_found", message, status=404)


class Conflict(APIError):
    def __init__(self, message, code="conflict"):
        super().__init__(code, message, status=409)


# ---------------------------------------------------------------------------
# Request identity and envelopes
# ---------------------------------------------------------------------------
def request_id(request):
    """Return a stable request identifier, generating one when absent."""
    cached = getattr(request, "_api_request_id", None)
    if cached:
        return cached
    supplied = (request.META.get("HTTP_X_REQUEST_ID") or "").strip()[:64]
    value = supplied or uuid.uuid4().hex
    request._api_request_id = value
    return value


def _meta(request, extra=None):
    meta = {
        "request_id": request_id(request),
        "server_time": timezone.now().isoformat(),
        "school_timezone": settings.TIME_ZONE,
        "api_version": getattr(settings, "API_VERSION", "v1"),
    }
    if extra:
        meta.update(extra)
    return meta


def success(request, data, *, status=200, meta=None, pagination=None, headers=None):
    payload = {"data": data, "meta": _meta(request, meta)}
    if pagination is not None:
        payload["pagination"] = pagination
    response = JsonResponse(payload, status=status, safe=False)
    response["X-Request-ID"] = request_id(request)
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def error(request, code, message, *, status=400, fields=None, headers=None):
    body = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id(request),
        }
    }
    if fields:
        body["error"]["fields"] = fields
    response = JsonResponse(body, status=status)
    response["X-Request-ID"] = request_id(request)
    for key, value in (headers or {}).items():
        response[key] = value
    return response


def no_content(request):
    response = HttpResponse(status=204)
    response["X-Request-ID"] = request_id(request)
    return response


# ---------------------------------------------------------------------------
# Body parsing
# ---------------------------------------------------------------------------
def parse_json(request):
    """Parse a JSON request body into a dict.

    Empty bodies become ``{}`` so action endpoints can be called without a
    payload. Anything that is not a JSON object is rejected rather than being
    silently coerced.
    """
    raw = request.body
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode(request.encoding or "utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise APIError("invalid_json", "Request body must be valid JSON.", status=400) from exc
    if not isinstance(payload, dict):
        raise APIError(
            "invalid_json",
            "Request body must be a JSON object.",
            status=400,
        )
    return payload


# ---------------------------------------------------------------------------
# Version gate
# ---------------------------------------------------------------------------
def _version_tuple(value):
    parts = []
    for chunk in (value or "").strip().split("."):
        digits = "".join(character for character in chunk if character.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def _app_version_allowed(request):
    minimum = getattr(settings, "API_MIN_APP_VERSION", "") or ""
    if not minimum:
        return True
    supplied = (
        request.META.get("HTTP_X_APP_VERSION")
        or request.GET.get("app_version")
        or ""
    ).strip()
    if not supplied:
        return False
    return _version_tuple(supplied) >= _version_tuple(minimum)


# ---------------------------------------------------------------------------
# Endpoint decorator
# ---------------------------------------------------------------------------
def api_endpoint(
    methods=("GET",),
    *,
    auth=True,
    roles=None,
    features=(),
    superuser_only=False,
):
    """Wrap a view with method, authentication, role, and feature checks.

    Session cookies are never consulted. A browser session alone must fail with
    ``401`` so CSRF can never become an API attack path.
    """
    allowed_methods = tuple(method.upper() for method in methods)

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not getattr(settings, "API_ENABLED", True):
                return error(
                    request,
                    "api_disabled",
                    "The mobile API is disabled for this deployment.",
                    status=503,
                )
            if request.method not in allowed_methods:
                return error(
                    request,
                    "method_not_allowed",
                    f"Use one of: {', '.join(allowed_methods)}.",
                    status=405,
                    headers={"Allow": ", ".join(allowed_methods)},
                )
            if not _app_version_allowed(request):
                return error(
                    request,
                    "upgrade_required",
                    "This version of the app is no longer supported. Update to continue.",
                    status=426,
                )
            try:
                if auth:
                    # Imported lazily so models can be imported while apps load.
                    from core.api.auth import authenticate_bearer

                    token = authenticate_bearer(request)
                    if token is None:
                        raise AuthenticationRequired()
                    request.api_token = token
                    request.api_user = token.user

                    if superuser_only and not token.user.is_superuser:
                        raise Forbidden()

                    if roles is not None:
                        from core.api.auth import user_has_any_role

                        if not user_has_any_role(token.user, roles):
                            raise Forbidden(
                                "Your staff role is not authorized for this endpoint.",
                                code="role_forbidden",
                            )

                    if features:
                        from tenancy.entitlements import feature_enabled

                        for feature in features:
                            if not feature_enabled(feature):
                                raise Forbidden(
                                    "This school's subscription does not include this feature.",
                                    code="feature_unavailable",
                                )
                return view(request, *args, **kwargs)
            except APIError as exc:
                return error(
                    request,
                    exc.code,
                    exc.message,
                    status=exc.status,
                    fields=exc.fields,
                    headers=exc.headers,
                )
            except Http404:
                return error(request, "not_found", "The requested object was not found.", status=404)
            except ObjectDoesNotExist:
                return error(request, "not_found", "The requested object was not found.", status=404)
            except DjangoPermissionDenied as exc:
                return error(
                    request,
                    "forbidden",
                    str(exc) or "You do not have permission to perform this action.",
                    status=403,
                )
            except DjangoValidationError as exc:
                fields = (
                    exc.message_dict
                    if hasattr(exc, "message_dict")
                    else {"non_field_errors": exc.messages}
                )
                return error(
                    request,
                    "validation_error",
                    "The request could not be processed.",
                    status=400,
                    fields=fields,
                )

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Pagination and ordering
# ---------------------------------------------------------------------------
def paginate(request, queryset, serialize, *, default_size=None, max_size=None):
    """Slice ``queryset`` and return ``(items, pagination_dict)``."""
    default_size = default_size or getattr(settings, "API_PAGE_SIZE", 25)
    max_size = max_size or getattr(settings, "API_MAX_PAGE_SIZE", 100)
    try:
        page = int(request.GET.get("page", 1))
    except (TypeError, ValueError):
        raise APIError("invalid_page", "page must be a positive integer.")
    try:
        page_size = int(request.GET.get("page_size", default_size))
    except (TypeError, ValueError):
        raise APIError("invalid_page_size", "page_size must be a positive integer.")
    if page < 1:
        raise APIError("invalid_page", "page must be a positive integer.")
    if page_size < 1 or page_size > max_size:
        raise APIError(
            "invalid_page_size",
            f"page_size must be between 1 and {max_size}.",
        )

    total = queryset.count()
    pages = math.ceil(total / page_size) if total else 0
    offset = (page - 1) * page_size
    rows = list(queryset[offset : offset + page_size])
    pagination = {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": pages,
        "has_next": page < pages,
        "has_previous": page > 1 and total > 0,
    }
    return [serialize(row) for row in rows], pagination


def apply_ordering(request, queryset, allowed, *, default=None):
    """Apply a validated ``ordering`` query parameter.

    Only fields on the allowlist are accepted. Unknown values raise ``400``
    rather than silently returning unsorted data.
    """
    ordering = (request.GET.get("ordering") or default or "").strip()
    if not ordering:
        return queryset
    requested = [part.strip() for part in ordering.split(",") if part.strip()]
    cleaned = []
    for field in requested:
        descending = field.startswith("-")
        name = field[1:] if descending else field
        if name not in allowed:
            raise APIError(
                "invalid_ordering",
                f"ordering must use only: {', '.join(sorted(allowed))}.",
            )
        cleaned.append(("-" if descending else "") + name)
    return queryset.order_by(*cleaned)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
def _idempotency_cache_key(request, raw_key):
    token = getattr(request, "api_token", None)
    token_id = getattr(token, "pk", "anonymous")
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:32]
    return f"api:idem:{token_id}:{request.path}:{digest}"


def idempotent(view):
    """Make a POST action safe to retry with an ``Idempotency-Key`` header.

    A replay within the TTL returns the original response. A concurrent
    duplicate receives ``409 request_in_progress`` instead of racing the first
    request through a state transition.
    """
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        raw_key = (request.META.get("HTTP_IDEMPOTENCY_KEY") or "").strip()
        if not raw_key or request.method != "POST":
            return view(request, *args, **kwargs)

        cache_key = _idempotency_cache_key(request, raw_key)
        cached = cache.get(cache_key)
        if cached is not None:
            response = JsonResponse(cached["body"], status=cached["status"])
            response["X-Request-ID"] = request_id(request)
            response["Idempotent-Replayed"] = "true"
            return response

        lock_key = cache_key + ":lock"
        if not cache.add(lock_key, "1", 30):
            return error(
                request,
                "request_in_progress",
                "An identical request is already being processed.",
                status=409,
                headers={"Retry-After": "2"},
            )
        try:
            response = view(request, *args, **kwargs)
            if response.status_code < 500:
                try:
                    body = json.loads(response.content.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    body = None
                if body is not None:
                    cache.set(
                        cache_key,
                        {"body": body, "status": response.status_code},
                        getattr(settings, "API_IDEMPOTENCY_TTL", 86400),
                    )
            return response
        finally:
            cache.delete(lock_key)

    return wrapper


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def iso(value):
    """Serialize a datetime/date for JSON, preserving ``None``."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def get_bool(payload, key, default=None):
    if key not in payload:
        return default
    value = payload[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
