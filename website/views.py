"""Views for the public Eden Creative Systems marketing website.

Every view here is public by definition: they are routed from
``schoolsystem/urls_public.py`` (or the ``/site/`` preview mount in
single-tenant mode) and are reachable without authentication. None of them
touches school data — the app is part of ``PUBLIC_APPS``, so its tables live in
the PostgreSQL ``public`` schema and the tenant router refuses to serve them
from a school schema.
"""
from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_http_methods, require_safe

from core.throttling import client_ip, is_rate_limited
from website import addressing, content, legal, pricing
from website.emails import notify_demo_request
from website.forms import DemoRequestForm
from website.models import DemoRequest

# How long a browser may reuse a marketing page before revalidating. The public
# site is entirely static output, so this is safe and keeps the app responsive.
PUBLIC_PAGE_MAX_AGE = 300


def _base_context(request, page_key: str, nav_key: str = "") -> dict:
    meta = content.PAGE_META.get(page_key, {})
    title = meta.get("title", "")
    short_name = getattr(settings, "EDEN_SYS_SHORT_NAME", "Eden Sys")
    product_name = getattr(settings, "EDEN_SYS_PRODUCT_NAME", "")
    full_title = f"{title} | {short_name}" if title else f"{short_name} — {product_name}"

    # Canonical links always name the main domain rather than whichever host
    # served the request, so a school's hostname can never appear here.
    canonical = addressing.absolute(request, request.path)

    # Static paths are hashed by ManifestStaticFilesStorage in production, so
    # the URL must come from static() rather than being assembled by hand.
    # Open Graph consumers fetch og:image without a page context, so it has to
    # be absolute too.
    og_image = addressing.absolute(request, static("img/eden/og-image.png"))

    return {
        "page_key": page_key,
        "nav_key": nav_key,
        "page_title": full_title,
        "meta_description": meta.get("description", ""),
        "canonical_url": canonical,
        "og_image_url": og_image,
    }


def _render(
    request: "HttpRequest", template: str, context: dict, status: int = 200
) -> HttpResponse:
    """Render a public page and set its crawler instructions.

    ``status`` is passed to ``render()`` rather than being assigned to
    ``response.status_code`` afterwards: the header has to be derived from the
    intended status, and a late assignment also leaves the reason phrase stale
    ("OK" on a 400). Anything that is not a normal page — a validation error, a
    throttled submission — must never be indexed.
    """
    response = render(request, template, context, status=status)
    response["X-Robots-Tag"] = (
        "index, follow" if status == 200 else "noindex, nofollow"
    )
    return response


def _inline_json(payload: dict) -> str:
    """Serialise ``payload`` for safe embedding inside a ``<script>`` element.

    HTML entities are not decoded inside ``script``, so standard escaping cannot
    be used. Escaping the characters that could terminate the script element is
    the correct approach.
    """
    serialised = json.dumps(payload, ensure_ascii=False)
    return (
        serialised.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def _organisation_json_ld() -> str:
    """Structured data describing Eden Sys.

    Only facts that are configured and verifiable are emitted: no prices, no
    ratings, no certifications, no social profiles.
    """
    company = (getattr(settings, "EDEN_SYS_COMPANY", "") or "").strip()
    short_name = (getattr(settings, "EDEN_SYS_SHORT_NAME", "") or "").strip()

    payload = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": company,
    }
    # The site's own origin, never a school's hostname.
    site_url = addressing.canonical_origin()
    if site_url:
        payload["url"] = site_url
    if short_name:
        payload["alternateName"] = short_name
    email = (getattr(settings, "EDEN_SYS_EMAIL", "") or "").strip()
    if email:
        payload["email"] = email
    phone = (getattr(settings, "EDEN_SYS_PHONE", "") or "").strip()
    if phone:
        payload["telephone"] = phone

    return _inline_json(payload)


@require_safe
@cache_control(public=True, max_age=PUBLIC_PAGE_MAX_AGE)
def home(request):
    context = _base_context(request, "home", nav_key="home")
    context.update(
        {
            "headline": content.HEADLINE,
            "subheadline": content.SUBHEADLINE,
            "challenges": content.CHALLENGES,
            "benefits": content.BENEFITS,
            "features": content.FEATURES,
            "steps": content.STEPS,
            "preview": content.PRODUCT_PREVIEW,
            "privacy_facts": content.TRUST_FACTS[:6],
            "faq_preview": content.FAQS[:4],
            "pricing_published": pricing.pricing_is_published(),
            "structured_data": _organisation_json_ld(),
        }
    )
    return _render(request, "website/home.html", context)


@require_safe
@cache_control(public=True, max_age=PUBLIC_PAGE_MAX_AGE)
def features(request):
    context = _base_context(request, "features", nav_key="features")
    context["features"] = content.FEATURES
    context["not_included"] = content.NOT_INCLUDED
    return _render(request, "website/features.html", context)


@require_safe
@cache_control(public=True, max_age=PUBLIC_PAGE_MAX_AGE)
def services(request):
    context = _base_context(request, "services", nav_key="services")
    context.update(
        {
            "services": content.SERVICES,
            "services_intro": content.SERVICES_INTRO,
            "provisional_note": content.SERVICES_PROVISIONAL_NOTE,
        }
    )
    return _render(request, "website/services.html", context)


@require_safe
@cache_control(public=True, max_age=PUBLIC_PAGE_MAX_AGE)
def how_it_works(request):
    context = _base_context(request, "how_it_works", nav_key="how_it_works")
    context.update(
        {
            "steps": content.STEPS,
            "preparation": content.ROLLOUT_PREPARATION,
            "rollout_notes": content.ROLLOUT_NOTES,
        }
    )
    return _render(request, "website/how_it_works.html", context)


@require_safe
@cache_control(public=True, max_age=PUBLIC_PAGE_MAX_AGE)
def pricing_page(request):
    context = _base_context(request, "pricing", nav_key="pricing")
    context["pricing"] = pricing.get_pricing_context()
    return _render(request, "website/pricing.html", context)


@require_safe
@cache_control(public=True, max_age=PUBLIC_PAGE_MAX_AGE)
def faq(request):
    context = _base_context(request, "faq", nav_key="faq")
    context["faqs"] = content.FAQS
    context["faq_structured_data"] = _inline_json(
        {
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "mainEntity": [
                {
                    "@type": "Question",
                    "name": item["question"],
                    "acceptedAnswer": {
                        "@type": "Answer",
                        "text": item["answer"],
                    },
                }
                for item in content.FAQS
            ],
        }
    )
    return _render(request, "website/faq.html", context)


@require_safe
@cache_control(public=True, max_age=PUBLIC_PAGE_MAX_AGE)
def contact(request):
    context = _base_context(request, "contact", nav_key="contact")
    context.update(
        {
            "intro": content.CONTACT_INTRO,
            "response_note": content.CONTACT_RESPONSE_NOTE,
            "helpful": content.CONTACT_HELPFUL,
        }
    )
    return _render(request, "website/contact.html", context)


@require_safe
@cache_control(public=True, max_age=PUBLIC_PAGE_MAX_AGE)
def privacy(request):
    context = _base_context(request, "privacy")
    context.update(
        {
            "intro": legal.PRIVACY_INTRO,
            "sections": legal.PRIVACY_SECTIONS,
            "review_notice": legal.LEGAL_REVIEW_NOTICE,
            "last_updated": legal.LAST_UPDATED,
            "trust_facts": content.TRUST_FACTS,
            "trust_limits": content.TRUST_LIMITS,
        }
    )
    return _render(request, "website/privacy.html", context)


@require_safe
@cache_control(public=True, max_age=PUBLIC_PAGE_MAX_AGE)
def terms(request):
    context = _base_context(request, "terms")
    context.update(
        {
            "intro": legal.TERMS_INTRO,
            "sections": legal.TERMS_SECTIONS,
            "withheld": legal.TERMS_WITHHELD,
            "review_notice": legal.LEGAL_REVIEW_NOTICE,
            "last_updated": legal.LAST_UPDATED,
        }
    )
    return _render(request, "website/terms.html", context)


def _hash_ip(request) -> str:
    """Store a salted hash of the client address, never the raw address."""
    address = client_ip(request) or ""
    salt = (settings.SECRET_KEY or "eden")[:32]
    return hashlib.sha256(f"{salt}:{address}".encode("utf-8")).hexdigest()


@require_http_methods(["GET", "POST"])
def demo_request(request):
    """Render, validate and store a demonstration request.

    Success is reported only after the record has actually been committed. A
    notification-email failure does not change the outcome for the visitor.
    """
    context = _base_context(request, "demo_request", nav_key="")
    context["submitted"] = False

    if request.method != "POST":
        context["form"] = DemoRequestForm()
        return _render(request, "website/demo_request.html", context)

    if is_rate_limited(
        "demo-request",
        client_ip(request),
        settings.DEMO_REQUEST_RATE_LIMIT,
        settings.DEMO_REQUEST_RATE_WINDOW,
    ):
        context["form"] = DemoRequestForm(request.POST)
        context["rate_limited"] = True
        return _render(request, "website/demo_request.html", context, status=429)

    form = DemoRequestForm(request.POST)
    if not form.is_valid():
        context["form"] = form
        return _render(request, "website/demo_request.html", context, status=400)

    enquiry = form.save(commit=False)
    enquiry.source_ip_hash = _hash_ip(request)
    enquiry.user_agent = (request.META.get("HTTP_USER_AGENT", "") or "")[:300]
    enquiry.save()

    # Best-effort notification. Recorded on the enquiry, never surfaced as an
    # error to the visitor, because the enquiry itself was stored successfully.
    enquiry.notification_sent = notify_demo_request(enquiry)
    enquiry.save(update_fields=["notification_sent", "updated_at"])

    context["submitted"] = True
    context["enquiry"] = enquiry
    context["form"] = DemoRequestForm()
    return _render(request, "website/demo_request.html", context)


# ---------------------------------------------------------------------------
# robots.txt and sitemap.xml
# ---------------------------------------------------------------------------
_ROBOTS_DISALLOW = (
    "/admin/",
    "/login/",
    "/logout/",
    "/verify/",
    "/students/",
    "/exeats/",
    "/operations/",
    "/reports/",
    "/configuration/",
    "/conduct/",
    "/houses/",
    "/search/",
    "/healthz",
    "/live",
)


def _absolute(request, path: str) -> str:
    """Absolute URL on the site's own origin, for the sitemap and robots.txt."""
    return addressing.absolute(request, path)


@require_safe
@cache_control(public=True, max_age=3600)
def robots_txt(request):
    """Crawler rules for the public host.

    Only the marketing site is routed here, so the ``Disallow`` entries are a
    belt-and-braces measure: they cover the school application's paths in case
    a public hostname is ever misconfigured to serve them.
    """
    lines = ["User-agent: *", "Allow: /"]
    lines += [f"Disallow: {path}" for path in _ROBOTS_DISALLOW]
    lines += [
        "Disallow: /*?",
        "",
        f"Sitemap: {_absolute(request, '/sitemap.xml')}",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


_SITEMAP_PAGES = (
    ("website:home", "1.0"),
    ("website:features", "0.9"),
    ("website:services", "0.8"),
    ("website:how_it_works", "0.7"),
    ("website:pricing", "0.8"),
    ("website:faq", "0.7"),
    ("website:contact", "0.9"),
    ("website:demo_request", "1.0"),
    ("website:privacy", "0.3"),
    ("website:terms", "0.3"),
)


@require_safe
@cache_control(public=True, max_age=3600)
def sitemap_xml(request):
    lastmod = timezone.now().date().isoformat()
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url_name, priority in _SITEMAP_PAGES:
        try:
            path = reverse(url_name)
        except NoReverseMatch:
            continue
        parts.append(
            "<url>"
            f"<loc>{_absolute(request, path)}</loc>"
            f"<lastmod>{lastmod}</lastmod>"
            f"<priority>{priority}</priority>"
            "</url>"
        )
    parts.append("</urlset>")
    return HttpResponse(
        "\n".join(parts), content_type="application/xml; charset=utf-8"
    )


# ---------------------------------------------------------------------------
# Error handlers for the public URLconf
# ---------------------------------------------------------------------------
def _error(request, status: int, heading: str, body: str) -> HttpResponse:
    context = _base_context(request, "home")
    context.update({"status_code": status, "heading": heading, "body": body})
    response = render(request, "website/error.html", context, status=status)
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def public_page_not_found_view(request, exception=None):
    return _error(
        request,
        404,
        "We could not find that page",
        "The page you were looking for is not here. It may have been moved, or "
        "the address may have been mistyped.",
    )


def public_server_error_view(request):
    return _error(
        request,
        500,
        "Something went wrong at our end",
        "The site could not complete that request. Please try again shortly, "
        "or contact Eden Creative Systems and we will help directly.",
    )


def public_permission_denied_view(request, exception=None):
    return _error(
        request,
        403,
        "That request was not permitted",
        "This website does not require a login. If you were trying to reach a "
        "school's management system, use the portal address your school "
        "provided.",
    )
