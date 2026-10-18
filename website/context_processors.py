"""Template context for the public website.

Deliberately free of database access: this processor runs on every template
render, including the authenticated school application, so it must stay cheap
and must never fail. All values come from settings and static content.
"""
from __future__ import annotations

from django.conf import settings
from django.urls import NoReverseMatch, reverse

from website import addressing, content, pricing


def _digits_only(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def build_site_context() -> dict:
    phone = (getattr(settings, "EDEN_SYS_PHONE", "") or "").strip()
    email = (getattr(settings, "EDEN_SYS_EMAIL", "") or "").strip()
    # The main domain, never a school's hostname. `addressing` owns the rule
    # that the site only ever publishes its own origin.
    domain = addressing.main_domain()
    site_url = addressing.canonical_origin()

    digits = _digits_only(phone)
    whatsapp_number = digits  # wa.me expects digits only, no "+", no spaces

    return {
        "company": getattr(settings, "EDEN_SYS_COMPANY", ""),
        "short_name": getattr(settings, "EDEN_SYS_SHORT_NAME", ""),
        "product_name": getattr(settings, "EDEN_SYS_PRODUCT_NAME", ""),
        "product_summary": content.PRODUCT_SUMMARY,
        "attribution": getattr(settings, "EDEN_SYS_ATTRIBUTION", ""),
        "phone_display": phone,
        "phone_href": f"tel:+{digits}" if digits else "",
        "whatsapp_href": f"https://wa.me/{whatsapp_number}" if digits else "",
        "email": email,
        "email_href": f"mailto:{email}" if email else "",
        "domain": domain,
        "site_url": site_url,
        # Empty unless the configured URL is on the site's own domain. A school
        # hostname is dropped here rather than published as a link.
        "portal_login_url": addressing.portal_login_url(),
        "features_url": _safe_reverse("website:features"),
        "home_url": _safe_reverse("website:home"),
        "contact_url": _safe_reverse("website:contact"),
        "demo_url": _safe_reverse("website:demo_request"),
        "privacy_url": _safe_reverse("website:privacy"),
        "terms_url": _safe_reverse("website:terms"),
        # Delegated to the pricing module so the context processor cannot
        # disagree with the pricing page about whether figures are published.
        "pricing_published": pricing.pricing_is_published(),
    }


def _safe_reverse(name: str) -> str:
    try:
        return reverse(name)
    except NoReverseMatch:
        # The website may not be routed in every deployment mode (for example a
        # single-tenant install without the preview mount). Never 500 the
        # authenticated application because of a missing marketing route.
        return ""


def public_site(request) -> dict:
    """Expose brand, contact and navigation values to every template."""
    return {
        "site": build_site_context(),
        "nav_links": content.NAV_LINKS,
        "footer_links": content.FOOTER_LINKS,
        "cta": {
            "primary": content.PRIMARY_CTA_LABEL,
            "secondary": content.SECONDARY_CTA_LABEL,
            "explore": content.EXPLORE_CTA_LABEL,
            "login": content.LOGIN_CTA_LABEL,
        },
    }
