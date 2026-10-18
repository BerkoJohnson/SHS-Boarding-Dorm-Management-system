"""Where the public website says it lives, and which links it may publish.

Eden Creative Systems publishes the marketing site at one address (the main
domain). Each school, by contrast, is served from its own hostname — its tenant
domain. The two must never be mixed on this site:

* every absolute URL the site emits (canonical, Open Graph, sitemap, robots)
  has to be the main domain, never a subdomain;
* no link may point at a school's hostname.

Both rules are enforced here, in one place, so that no view, context processor
or template can invent its own idea of "the site's own origin".
"""
from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings


def main_domain() -> str:
    """The main Eden Creative Systems domain, without scheme or slashes."""
    value = getattr(settings, "EDEN_SYS_DOMAIN", "") or ""
    return value.strip().strip("/").lower()


def canonical_origin(request=None) -> str:
    """Return the site's own origin, without a trailing slash.

    Precedence:

    1. ``SITE_URL``, when explicitly configured;
    2. ``https://<EDEN_SYS_DOMAIN>`` — the main domain;
    3. the origin of the current request, used only when both of the above are
       blank (local development and the ``/site/`` single-tenant preview).

    A tenant hostname can never be the result: it is not read from either
    setting, and the request fallback is last. Pass ``request=None`` when no
    request is available (for example from a context processor or a check).
    """
    site_url = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    if site_url:
        return site_url
    domain = main_domain()
    if domain:
        return f"https://{domain}"
    if request is not None:
        return request.build_absolute_uri("/").rstrip("/")
    return ""


def absolute(request, path: str) -> str:
    """Build an absolute URL on the site's own origin.

    Used for canonical links, Open Graph images, sitemap entries and the
    ``Sitemap:`` line in robots.txt.
    """
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{canonical_origin(request)}{path}"


def hostname_of(url: str) -> str:
    """Lower-case hostname of an absolute http(s) URL, or ``""`` otherwise."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"}:
        return ""
    return (parts.hostname or "").lower()


def is_own_origin(url: str, request=None) -> bool:
    """True when ``url`` is a relative path or on the site's own hostname.

    A URL on any other hostname — including a ``www.`` or school subdomain of
    the main domain — is not the site's own origin.
    """
    value = (url or "").strip()
    if not value:
        return False
    # A protocol-relative URL ("//host/path") borrows the current scheme but
    # names its own host, so it is treated as absolute below. Everything else
    # starting with "/" stays on whatever host served the page.
    if value.startswith("/") and not value.startswith("//"):
        return True

    expected = hostname_of(canonical_origin(request))
    if not expected:
        return False
    return hostname_of(value) == expected


def own_origin_link(url: str, request=None) -> str:
    """Return ``url`` only when the site is allowed to publish it, else ``""``.

    Fail closed: a value that cannot be proven to be the site's own origin is
    dropped rather than rendered. This is what keeps a misconfigured setting
    from turning the marketing site into a directory of tenant addresses.
    """
    value = (url or "").strip()
    return value if is_own_origin(value, request) else ""


def portal_login_url(request=None) -> str:
    """The configured Login link, but only when it is on the site's own origin."""
    return own_origin_link(getattr(settings, "EDEN_PORTAL_LOGIN_URL", ""), request)
