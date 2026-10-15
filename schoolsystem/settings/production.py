import os
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

from .base import *

database_profile = os.getenv("DATABASE_PROFILE", "").strip().lower()
if database_profile != "neon":
    raise ImproperlyConfigured(
        "Production requires DATABASE_PROFILE=neon and explicit PGHOST, "
        "PGDATABASE, PGUSER, PGPASSWORD, and PGSSLMODE settings."
    )

ssl_mode = os.getenv("PGSSLMODE", "").strip().lower()
if ssl_mode not in {"require", "verify-ca", "verify-full"}:
    raise ImproperlyConfigured(
        "Production requires PGSSLMODE to be one of: require, verify-ca, "
        "verify-full."
    )

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "").strip()
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY must be at least 50 characters and contain at least "
        "five unique characters."
    )

DEBUG = False

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
if not ALLOWED_HOSTS and not TENANCY_ENABLED:
    raise ImproperlyConfigured(
        "Set ALLOWED_HOSTS to the production hostnames, separated by commas."
    )
if "*" in ALLOWED_HOSTS and not TENANCY_ENABLED:
    raise ImproperlyConfigured(
        "ALLOWED_HOSTS must list explicit production hostnames; '*' is not allowed."
    )
if TENANCY_ENABLED:
    ALLOWED_HOSTS = ["*"]

# Static origins from the environment. Dynamic tenant domains are handled
# by tenancy.middleware.DynamicCsrfMiddleware when TENANCY_ENABLED is True.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

# --- SECURITY HARDENING ---
MIDDLEWARE.insert(
    1,
    "schoolsystem.security.ContentSecurityPolicyReportOnlyMiddleware",
)
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

CSP_REPORT_ONLY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'; "
    "form-action 'self'"
)
csp_report_uri = os.getenv("CSP_REPORT_URI", "").strip()
if csp_report_uri:
    parsed_report_uri = urlsplit(csp_report_uri)
    relative_report_uri = (
        csp_report_uri.startswith("/")
        and not csp_report_uri.startswith("//")
        and not parsed_report_uri.scheme
        and not parsed_report_uri.netloc
        and "\\" not in csp_report_uri
    )
    absolute_report_uri = (
        parsed_report_uri.scheme == "https"
        and bool(parsed_report_uri.hostname)
        and parsed_report_uri.username is None
        and parsed_report_uri.password is None
    )
    if (
        any(character.isspace() for character in csp_report_uri)
        or not (relative_report_uri or absolute_report_uri)
    ):
        raise ImproperlyConfigured(
            "CSP_REPORT_URI must be a safe relative or HTTPS URL."
        )
    CSP_REPORT_ONLY_POLICY += f"; report-uri {csp_report_uri}"


def _environment_flag(name, default="false"):
    value = os.getenv(name, default).strip().lower()
    if value not in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
        raise ImproperlyConfigured(f"{name} must be a boolean value.")
    return value in {"1", "true", "yes", "on"}


try:
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))
except ValueError as error:
    raise ImproperlyConfigured(
        "SECURE_HSTS_SECONDS must be a non-negative integer."
    ) from error
if SECURE_HSTS_SECONDS < 0:
    raise ImproperlyConfigured(
        "SECURE_HSTS_SECONDS must be a non-negative integer."
    )
SECURE_HSTS_INCLUDE_SUBDOMAINS = _environment_flag(
    "SECURE_HSTS_INCLUDE_SUBDOMAINS"
)
SECURE_HSTS_PRELOAD = _environment_flag("SECURE_HSTS_PRELOAD")
if SECURE_HSTS_INCLUDE_SUBDOMAINS or SECURE_HSTS_PRELOAD:
    if SECURE_HSTS_SECONDS == 0:
        raise ImproperlyConfigured(
            "Enable HSTS before enabling subdomain or preload directives."
        )
if SECURE_HSTS_PRELOAD and (
    SECURE_HSTS_SECONDS < 31536000 or not SECURE_HSTS_INCLUDE_SUBDOMAINS
):
    raise ImproperlyConfigured(
        "HSTS preload requires at least one year of HSTS and "
        "SECURE_HSTS_INCLUDE_SUBDOMAINS=true."
    )

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True