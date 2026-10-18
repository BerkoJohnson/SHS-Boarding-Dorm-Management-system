"""System checks that keep the public website on its own domain.

A school is served from its own hostname. The marketing site must never publish
one of those hostnames, and every absolute URL it emits must name the main
domain. These checks report the two settings that could break that rule, so a
misconfiguration shows up at deploy time instead of on a live page.
"""
from __future__ import annotations

from django.conf import settings
from django.core.checks import Tags, Warning, register

from website import addressing

# The Login link is hidden rather than broken when it points elsewhere, so this
# is a warning and not an error: the site stays safe either way.
PORTAL_LOGIN_OFF_DOMAIN = "website.W001"
SITE_URL_OFF_MAIN_DOMAIN = "website.W002"


@register(Tags.security)
def portal_login_url_is_on_the_site_own_domain(app_configs, **kwargs):
    """The Login link must not point at a school's hostname."""
    configured = (getattr(settings, "EDEN_PORTAL_LOGIN_URL", "") or "").strip()
    if not configured or addressing.own_origin_link(configured):
        return []

    expected = addressing.canonical_origin() or "the site's own domain"
    return [
        Warning(
            "EDEN_PORTAL_LOGIN_URL is not on the public website's own domain, "
            "so the Login link is hidden. The site never links to a school's "
            "hostname.",
            hint=(
                f"Point it at {expected} (or a path on it), or leave it empty. "
                "Staff reach their own school by using the address that school "
                "provided."
            ),
            id=PORTAL_LOGIN_OFF_DOMAIN,
        )
    ]


@register(Tags.security)
def site_url_names_the_main_domain(app_configs, **kwargs):
    """SITE_URL decides the canonical origin, so it must be the main domain."""
    site_url = (getattr(settings, "SITE_URL", "") or "").strip()
    if not site_url:
        return []

    domain = addressing.main_domain()
    if not domain:
        return []

    host = addressing.hostname_of(site_url)
    if not host:
        return [
            Warning(
                f"SITE_URL ({site_url!r}) is not a usable absolute origin.",
                hint="Use a full origin such as 'https://ecreativesys.com'.",
                id=SITE_URL_OFF_MAIN_DOMAIN,
            )
        ]

    if host == domain:
        return []

    return [
        Warning(
            f"SITE_URL ({site_url!r}) does not match the main domain "
            f"({domain!r}), so the site advertises a different hostname in its "
            "canonical links, sitemap and Open Graph tags.",
            hint=(
                "Set SITE_URL to the main domain, or update EDEN_SYS_DOMAIN to "
                "match if the site really is served from that hostname."
            ),
            id=SITE_URL_OFF_MAIN_DOMAIN,
        )
    ]
