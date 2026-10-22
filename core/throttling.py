"""Dependency-free, cache-backed, fixed-window rate limiting.

Used to slow credential stuffing against the staff login and spam against the
public "report a concern" endpoint. The cache defaults to ``LocMemCache``
(per-process); point ``CACHE_BACKEND`` / ``CACHE_LOCATION`` at a shared backend
for cluster-wide limits across multiple workers.
"""
from __future__ import annotations

import hashlib
import time

from django.core.cache import cache


class RateLimitUnavailable(RuntimeError):
    """Raised when the configured cache cannot safely enforce a rate limit."""


def client_ip(request):
    """Return a best-effort client IP for throttling keys.

    Behind a trusted reverse proxy (Render, nginx) ``X-Forwarded-For`` carries
    the client address as it was observed by each hop. A client can inject
    arbitrary values at the front of that header, so we take the address
    ``TRUSTED_PROXY_COUNT`` entries from the right (the hop added by our own
    proxy) instead of trusting the first entry. When the header is absent the
    peer address is used. This value is only ever used to bucket rate limits --
    never as an authorization decision.
    """
    from django.conf import settings

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        hops = [part.strip() for part in forwarded.split(",") if part.strip()]
        if hops:
            trusted = max(1, int(getattr(settings, "TRUSTED_PROXY_COUNT", 1)))
            index = max(0, len(hops) - trusted)
            return hops[index]
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def is_rate_limited(scope, identity, limit, window):
    """Return ``True`` when ``identity`` exceeds ``limit`` hits in ``scope``.

    ``window`` is a fixed window in seconds. Counters are namespaced by scope
    and bucketed by wall-clock window so stale buckets expire automatically.
    A non-positive ``limit`` or ``window`` disables the check.
    """
    if limit <= 0 or window <= 0:
        return False
    bucket = int(time.time() // window)
    digest = hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:32]
    cache_key = f"ratelimit:{scope}:{digest}:{bucket}"
    try:
        if cache.add(cache_key, 1, window):
            return False
        try:
            count = cache.incr(cache_key)
        except ValueError:
            # The key expired between add() and incr(); only one concurrent
            # request may recreate it.
            if cache.add(cache_key, 1, window):
                return False
            count = cache.incr(cache_key)
        return count > limit
    except Exception as error:
        raise RateLimitUnavailable(
            "The configured cache could not enforce the rate limit."
        ) from error
