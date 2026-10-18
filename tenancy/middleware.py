"""
Tenant routing and CSRF middleware for the multi-tenancy system.

VerifiedDomainRoutingMiddleware resolves the current tenant from the
request hostname.  DynamicCsrfMiddleware extends Django's CsrfViewMiddleware
to trust verified tenant domains automatically.
"""

import ipaddress
import logging
import time
from urllib.parse import urlparse

from django.conf import settings
from django.core.exceptions import DisallowedHost, ValidationError
from django.http import Http404, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.urls import set_urlconf
from django_pgschemas.routing.info import DomainInfo
from django_pgschemas.schema import activate, activate_public

from .models import Domain, normalize_domain

logger = logging.getLogger(__name__)

# Paths that bypass tenant resolution for health checks and platform probes.
HEALTH_CHECK_PATHS = frozenset({"/healthz", "/healthz/", "/live", "/live/"})


class VerifiedDomainRoutingMiddleware:
    """
    New-style middleware that resolves the current tenant from the request
    hostname against verified ``Domain`` records.

    Improvements over the legacy ``MiddlewareMixin`` version:

    * Uses ``try ... finally`` to guarantee ``activate_public()`` runs even
      when downstream views raise unhandled exceptions, preventing
      connection-pool contamination with persistent connections
      (``CONN_MAX_AGE``).
    * Bypasses tenant resolution for health-check paths (``/healthz``,
      ``/live``) so that cloud probes (Render, ALB) can reach the service
      without a ``Domain`` record.
    * Correctly parses IPv6 bracket notation (e.g. ``[::1]:8000``)
      instead of splitting on ``:`` and producing garbage.
    * Optionally allows a ``PLATFORM_ADMIN_DOMAIN`` setting for
      platform-level admin access.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    # Cache of the normalized public-website hosts, keyed on the raw settings
    # that produced it so ``override_settings`` still takes effect.
    _public_hosts_cache: tuple[str, frozenset[str]] | None = None

    def __call__(self, request):
        activate_public()
        set_urlconf(None)

        # Bypass tenant resolution for health-check probes.
        if request.path in HEALTH_CHECK_PATHS:
            return JsonResponse({"status": "ok"})

        try:
            host = self._extract_hostname(request)
        except (DisallowedHost, ValidationError) as error:
            raise Http404(
                "No active school is configured for this hostname."
            ) from error

        # Platform admin domain: return a lightweight JSON status response
        # without forwarding through the full Django pipeline.  The public
        # schema may not have auth/sessions/core tables on clean multi-tenant
        # deployments, so we avoid calling get_response(request) here.
        platform_admin_domain = getattr(
            settings, "PLATFORM_ADMIN_DOMAIN", None
        )
        if platform_admin_domain and host == platform_admin_domain:
            tenant_count = Domain.objects.values("tenant").distinct().count()
            return JsonResponse({
                "status": "ok",
                "platform": "eden-multi-tenant",
                "tenant_count": tenant_count,
                "message": (
                    "Platform admin endpoint. Use management commands "
                    "(provision_school, set_school_status, add_school_domain) "
                    "for tenant operations."
                ),
            })

        # Public marketing website host: serve the public URLconf from the
        # public schema. Marketing pages therefore cannot reach any school's
        # tables, and tenant hosts are completely unaffected.
        if host in self._public_website_hosts():
            # The public schema has no session table, so drop any session
            # cookie before AuthenticationMiddleware runs. This keeps the
            # public site stateless, prevents a lookup against a table that
            # does not exist, and guarantees it can never inherit a staff
            # session from a shared hostname.
            request.COOKIES.pop(settings.SESSION_COOKIE_NAME, None)
            request.is_public_website = True
            request.urlconf = settings.PUBLIC_WEBSITE_URLCONF
            set_urlconf(request.urlconf)
            try:
                response = self.get_response(request)
            finally:
                activate_public()
                set_urlconf(None)
            return response

        domain = (
            Domain.objects.select_related("tenant")
            .filter(
                domain=host,
                folder="",
                is_verified=True,
                tenant__is_active=True,
            )
            .first()
        )
        if domain is None:
            raise Http404(
                "No active school is configured for this hostname."
            )

        tenant = domain.tenant
        tenant.routing = DomainInfo(domain=host)
        request.tenant = tenant
        request.urlconf = settings.TENANTS["default"]["URLCONF"]
        set_urlconf(request.urlconf)
        activate(tenant)
        try:
            response = self.get_response(request)
        finally:
            activate_public()
            set_urlconf(None)
        return response

    @classmethod
    def _public_website_hosts(cls):
        """Return the configured public marketing website hosts.

        Normalized and cached against the raw setting so that
        ``override_settings`` in tests still takes effect.
        """
        raw = " ".join(
            [
                getattr(settings, "PUBLIC_WEBSITE_DOMAIN", "") or "",
                str(getattr(settings, "PUBLIC_WEBSITE_ENABLED", True)),
            ]
        )
        cached = cls._public_hosts_cache
        if cached is not None and cached[0] == raw:
            return cached[1]

        hosts: frozenset[str] = frozenset()
        if getattr(settings, "PUBLIC_WEBSITE_ENABLED", True):
            configured = getattr(settings, "PUBLIC_WEBSITE_DOMAIN", "") or ""
            normalized = set()
            for entry in configured.split(","):
                candidate = entry.strip()
                if not candidate:
                    continue
                try:
                    normalized.add(normalize_domain(candidate))
                except ValidationError:
                    logger.warning(
                        "Ignoring invalid PUBLIC_WEBSITE_DOMAIN entry: %r",
                        candidate,
                    )
            hosts = frozenset(normalized)

        cls._public_hosts_cache = (raw, hosts)
        return hosts

    @staticmethod
    def _extract_hostname(request):
        """
        Extract and normalize the hostname from the request, correctly
        handling IPv6 bracket notation (e.g. ``[::1]:8000``).
        """
        raw_host = request.get_host()

        # IPv6 bracket notation: [::1]:8000 -> ::1
        if raw_host.startswith("["):
            bracket_end = raw_host.find("]")
            if bracket_end != -1:
                inner = raw_host[1:bracket_end]
                try:
                    ipaddress.ip_address(inner)
                    # Valid IPv6 literal -- will never match a Domain record
                    # but let the caller handle the resulting 404.
                    return inner
                except ValueError:
                    pass

        # Standard host:port split.
        host = raw_host.split(":", 1)[0]
        return normalize_domain(host)


class DynamicCsrfMiddleware(CsrfViewMiddleware):
    """
    Drop-in replacement for ``django.middleware.csrf.CsrfViewMiddleware``
    that dynamically trusts verified tenant domains.

    Django 4.0+ enforces ``Origin`` header checking on HTTPS POST requests.
    When tenants are mapped to custom domains the static
    ``CSRF_TRUSTED_ORIGINS`` setting cannot keep pace.  This middleware
    falls back to checking the ``Origin`` header against the ``Domain``
    model, using a class-level in-memory cache refreshed every five
    minutes to avoid per-request database queries.

    Place this middleware in the same position as
    ``django.middleware.csrf.CsrfViewMiddleware`` in the ``MIDDLEWARE``
    list (i.e. after ``SessionMiddleware``, before
    ``AuthenticationMiddleware``).
    """

    _domain_cache: frozenset[str] | None = None
    _cache_timestamp: float = 0.0
    _CACHE_TTL: int = 300  # seconds

    def _origin_verified(self, request):
        if super()._origin_verified(request):
            return True

        if not settings.TENANCY_ENABLED:
            return False

        request_origin = request.META.get("HTTP_ORIGIN")
        if not request_origin:
            return False

        try:
            parsed = urlparse(request_origin)
        except Exception:
            return False
        if not parsed.hostname:
            return False

        # Reject protocol-downgrade origins: an http:// origin on an
        # HTTPS request must never be trusted.
        expected_scheme = "https" if request.is_secure() else "http"
        if parsed.scheme != expected_scheme:
            return False

        return parsed.hostname in self._verified_domains()

    @classmethod
    def _verified_domains(cls):
        """Return a cached frozenset of verified, active tenant hostnames."""
        now = time.monotonic()
        if cls._domain_cache is None or (now - cls._cache_timestamp) > cls._CACHE_TTL:
            cls._domain_cache = frozenset(
                Domain.objects.filter(
                    is_verified=True,
                    tenant__is_active=True,
                ).values_list("domain", flat=True)
            )
            cls._cache_timestamp = now
        return cls._domain_cache

    @classmethod
    def invalidate_cache(cls):
        """Clear the cached domains (e.g. after adding a new domain)."""
        cls._domain_cache = None
        cls._cache_timestamp = 0.0
