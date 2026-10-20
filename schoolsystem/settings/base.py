import os
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)


def env_int(name, default):
    """Read an integer setting, falling back to *default* when unset or blank.

    Hosting dashboards and blueprints frequently create a variable but leave it
    empty. Treating blank as "unset" keeps an image/container build (for example
    ``collectstatic``) from failing on a knob that only matters at runtime.
    """
    raw = os.getenv(name, "")
    value = raw.strip()
    if not value:
        value = str(default)
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer, got {raw!r}.") from error


def env_float(name, default):
    """Read a float setting, falling back to *default* when unset or blank."""
    raw = os.getenv(name, "")
    value = raw.strip()
    if not value:
        value = str(default)
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number, got {raw!r}.") from error


DATABASE_PROFILE = os.getenv("DATABASE_PROFILE", "local").strip().lower() or "local"
if DATABASE_PROFILE not in {"local", "neon"}:
    raise ValueError("DATABASE_PROFILE must be either 'local' or 'neon'")

TENANCY_ENABLED = os.getenv("TENANCY_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
PLATFORM_ADMIN_DOMAIN = os.getenv("PLATFORM_ADMIN_DOMAIN", "").strip() or None

if DATABASE_PROFILE == "neon":
    required_neon_settings = ("PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD")
    missing_neon_settings = [
        name for name in required_neon_settings if not os.getenv(name, "").strip()
    ]
    if missing_neon_settings:
        missing_settings = ", ".join(missing_neon_settings)
        raise ValueError(
            f"DATABASE_PROFILE='neon' requires these environment variables: "
            f"{missing_settings}"
        )

    DB_NAME = os.environ["PGDATABASE"]
    DB_USER = os.environ["PGUSER"]
    DB_PASSWORD = os.environ["PGPASSWORD"]
    DB_HOST = os.environ["PGHOST"]
    DB_PORT = os.getenv("PGPORT", "5432").strip() or "5432"
    DB_SSLMODE = os.getenv("PGSSLMODE", "require").strip() or "require"
else:
    DB_NAME = os.getenv("DB_NAME", "schoolsystem_db")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")
    DB_HOST = os.getenv("DB_HOST", "localhost")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_SSLMODE = os.getenv("DB_SSLMODE", "").strip()

# Application definition

TENANT_APPS = [
    "core.admin_apps.InstitutionalAdminConfig",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "core",
]
PUBLIC_APPS = [
    "django.contrib.contenttypes",
    "django_pgschemas",
    "django_celery_beat",
    "tenancy",
    # The public marketing website for Eden Creative Systems. It lives in the
    # public schema so its enquiry table can never be read from, or written to,
    # a school's tenant schema.
    "website",
]

TENANTS = {
    "public": {
        "APPS": PUBLIC_APPS,
        # NOTE: django-pgschemas forbids a 'URLCONF' key on the public schema
        # (see django_pgschemas.checks.ensure_public_schema). The public
        # website's URLconf is therefore declared as PUBLIC_WEBSITE_URLCONF
        # below and applied by VerifiedDomainRoutingMiddleware.
    },
    "default": {
        "TENANT_MODEL": "tenancy.Tenant",
        "DOMAIN_MODEL": "tenancy.Domain",
        "APPS": TENANT_APPS,
        "URLCONF": "schoolsystem.urls",
    },
}

if TENANCY_ENABLED:
    INSTALLED_APPS = list(dict.fromkeys(PUBLIC_APPS + TENANT_APPS))
else:
    INSTALLED_APPS = [
        "core.admin_apps.InstitutionalAdminConfig",
        "django.contrib.auth",
        "django.contrib.contenttypes",
        "django.contrib.sessions",
        "django.contrib.messages",
        "django.contrib.staticfiles",
        "django_htmx",
        "django_celery_beat",
        "core",
        "tenancy",
        "website",
    ]

MIDDLEWARE = [
    *(
        ["tenancy.middleware.VerifiedDomainRoutingMiddleware"]
        if TENANCY_ENABLED
        else []
    ),
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    (
        "tenancy.middleware.DynamicCsrfMiddleware"
        if TENANCY_ENABLED
        else "django.middleware.csrf.CsrfViewMiddleware"
    ),
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

# Silence security.W003 when DynamicCsrfMiddleware replaces CsrfViewMiddleware.
SILENCED_SYSTEM_CHECKS = ["security.W003"] if TENANCY_ENABLED else []

ROOT_URLCONF = "schoolsystem.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.header_context",
                "website.context_processors.public_site",
            ],
        },
    },
]

WSGI_APPLICATION = "schoolsystem.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": (
            "django_pgschemas.postgresql"
            if TENANCY_ENABLED
            else "django.db.backends.postgresql"
        ),
        "NAME": DB_NAME,
        "USER": DB_USER,
        "PASSWORD": DB_PASSWORD,
        "HOST": DB_HOST,
        "PORT": DB_PORT,
        "CONN_MAX_AGE": 600,
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {
            "options": "-c statement_timeout=30000ms",
            **({"sslmode": DB_SSLMODE} if DB_SSLMODE else {}),
        },
    }
}

if TENANCY_ENABLED:
    DATABASE_ROUTERS = ["django_pgschemas.routers.TenantAppsRouter"]


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {
            "min_length": 8,
        },
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# -----------------------------------------------------------------------------
# SESSION & SECURITY TIMEOUTS (Added for Security Audit)
# -----------------------------------------------------------------------------
SESSION_COOKIE_AGE = 1800  # 30 minutes in seconds
SESSION_SAVE_EVERY_REQUEST = True  # Refresh the 30-min window on every user action
SESSION_EXPIRE_AT_BROWSER_CLOSE = True  # Force re-login if browser is closed


# -----------------------------------------------------------------------------
# CACHE & RATE LIMITING
# -----------------------------------------------------------------------------
# The cache backs the built-in rate limits below. LocMemCache is per-process,
# so a multi-worker deployment should point CACHE_BACKEND at a shared backend
# (e.g. django.core.cache.backends.redis.RedisCache with CACHE_LOCATION set to
# the Redis URL) for accurate, cluster-wide throttling.
CACHES = {
    "default": {
        "BACKEND": os.getenv("CACHE_BACKEND", "").strip()
        or "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": os.getenv("CACHE_LOCATION", "").strip() or "schoolsystem-cache",
    }
}

# Login throttling: max attempts per client IP within the window (seconds).
LOGIN_RATE_LIMIT = env_int("LOGIN_RATE_LIMIT", 10)
LOGIN_RATE_WINDOW = env_int("LOGIN_RATE_WINDOW", 300)

# Public "report a concern" throttling: max reports per client IP per window.
PUBLIC_REPORT_RATE_LIMIT = env_int("PUBLIC_REPORT_RATE_LIMIT", 5)
PUBLIC_REPORT_RATE_WINDOW = env_int("PUBLIC_REPORT_RATE_WINDOW", 3600)

# Number of trusted reverse-proxy hops in front of the app. Used to select the
# real client address from X-Forwarded-For when building rate-limit keys
# (Render terminates TLS and adds one hop, so the default of 1 is correct).
TRUSTED_PROXY_COUNT = max(1, env_int("TRUSTED_PROXY_COUNT", 1))

# Lifetime of a public student-ID QR token, in days. 0 disables expiry.
STUDENT_ID_PUBLIC_TOKEN_TTL_DAYS = env_int("STUDENT_ID_PUBLIC_TOKEN_TTL_DAYS", 0)


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

MEDIA_URL = "/media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", str(BASE_DIR / "media"))).resolve()
PRIVATE_STUDENT_PHOTO_ROOT = BASE_DIR / ".private" / "student_photos"
MEDIA_STORAGE_BACKEND = os.getenv("MEDIA_STORAGE_BACKEND", "filesystem").strip().lower()
if MEDIA_STORAGE_BACKEND not in {"filesystem", "neon_s3"}:
    raise ValueError("MEDIA_STORAGE_BACKEND must be either 'filesystem' or 'neon_s3'")


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

# Django 6 still selects the mail transport with EMAIL_BACKEND; there is no
# MAILERS setting, so a console backend configured under that name was silently
# ignored and mail fell back to SMTP to localhost. Default to the console
# backend (safe for staging, never sends real mail) and allow an override.
EMAIL_BACKEND = (
    os.getenv("EMAIL_BACKEND", "").strip()
    or "django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "").strip() or "no-reply@localhost"

# Standard Django SMTP transport settings. Django does not read these from the
# environment on its own, so they are mapped here: without this an SMTP
# EMAIL_BACKEND would silently fall back to localhost:25. They are ignored by
# the console and locmem backends, so leaving them blank is harmless.
EMAIL_HOST = os.getenv("EMAIL_HOST", "").strip() or "localhost"
EMAIL_PORT = env_int("EMAIL_PORT", 25)
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "").strip()
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
# TLS and SSL are mutually exclusive: setting both makes Django refuse to start.
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
EMAIL_USE_SSL = os.getenv("EMAIL_USE_SSL", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
# Bound the SMTP conversation so an unreachable mail host cannot hold a web
# request open indefinitely (Django's default is to wait forever).
EMAIL_TIMEOUT = env_int("EMAIL_TIMEOUT", 10)
SERVER_EMAIL = os.getenv("SERVER_EMAIL", "").strip() or DEFAULT_FROM_EMAIL

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

if MEDIA_STORAGE_BACKEND == "neon_s3":
    neon_storage_environment = {
        "NEON_STORAGE_BUCKET": os.getenv("NEON_STORAGE_BUCKET", "").strip(),
        "AWS_ENDPOINT_URL_S3": os.getenv("AWS_ENDPOINT_URL_S3", "").strip(),
        "AWS_REGION": os.getenv("AWS_REGION", "").strip(),
        "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID", "").strip(),
        "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY", "").strip(),
    }
    missing_storage_settings = [
        name for name, value in neon_storage_environment.items() if not value
    ]
    if missing_storage_settings:
        raise ValueError(
            "MEDIA_STORAGE_BACKEND='neon_s3' requires: "
            + ", ".join(missing_storage_settings)
        )

    STORAGES["default"] = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": neon_storage_environment["NEON_STORAGE_BUCKET"],
            "endpoint_url": neon_storage_environment["AWS_ENDPOINT_URL_S3"],
            "region_name": neon_storage_environment["AWS_REGION"],
            "access_key": neon_storage_environment["AWS_ACCESS_KEY_ID"],
            "secret_key": neon_storage_environment["AWS_SECRET_ACCESS_KEY"],
            "addressing_style": "path",
            "signature_version": "s3v4",
            "querystring_auth": True,
        },
    }


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Authentication Redirects
LOGIN_URL = "core:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "core:login"


# SMS Configuration (SMSOnlineGH)
SMS_ONLINE_GH_API_KEY = os.getenv("SMS_ONLINE_GH_API_KEY", "")
# Whitespace around a sender ID is rejected by the gateway, and it is easy to
# introduce by accident in a dashboard or .env file, so it is trimmed here.
SMS_SENDER_ID = os.getenv("SMS_SENDER_ID", "ATSEC").strip() or "ATSEC"
SMS_ENABLED = os.getenv("SMS_ENABLED", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SMS_TIME_ZONE = os.getenv("SMS_TIME_ZONE", TIME_ZONE)
SMS_HTTP_TIMEOUT = env_float("SMS_HTTP_TIMEOUT", 10)
SMS_MAX_ATTEMPTS = env_int("SMS_MAX_ATTEMPTS", 5)

# Celery / Redis
#
# BACKGROUND_JOBS_ENABLED is an explicit deployment switch. The Render staging
# blueprint ships without a Celery worker or Beat and sets this to False, so the
# app does not silently imply that queued SMS or scheduled tasks are running.
# A deployment that wants background work must run both processes and set this
# to True.
BACKGROUND_JOBS_ENABLED = os.getenv(
    "BACKGROUND_JOBS_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}

CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
CELERY_BEAT_SCHEDULER = (
    "django_celery_beat.schedulers:DatabaseScheduler"
    if BACKGROUND_JOBS_ENABLED
    else "schoolsystem.beat.DisabledScheduler"
)
CELERY_TIMEZONE = SMS_TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_IGNORE_RESULT = True
CELERY_BROKER_CONNECTION_TIMEOUT = 2
CELERY_BEAT_SCHEDULE = {
    "dispatch-queued-sms-deliveries": {
        "task": "core.tasks.dispatch_queued_sms_deliveries",
        "schedule": 60.0,
    },
    "check-overdue-exeats-for-sms": {
        "task": "core.tasks.enqueue_overdue_exeat_reminders",
        "schedule": 15 * 60.0,
    },
    "expire-unfinished-roll-calls": {
        "task": "core.tasks.expire_unfinished_roll_calls",
        "schedule": 60.0,
    },
    "cleanup-expired-student-id-exports": {
        "task": "core.tasks.cleanup_expired_student_id_exports",
        "schedule": 24 * 60 * 60.0,
    },
    "phase-out-shs3-students-after-academic-year": {
        "task": "core.tasks.phase_out_shs3_students",
        "schedule": 24 * 60 * 60.0,
    },
}
if not BACKGROUND_JOBS_ENABLED:
    # The database scheduler would otherwise load periodic rows left in
    # django_celery_beat. The explicit switch means "do not schedule anything".
    CELERY_BEAT_SCHEDULE = {}


# -----------------------------------------------------------------------------
# PUBLIC MARKETING WEBSITE (Eden Creative Systems / Edensys)
# -----------------------------------------------------------------------------
# The public website is served from the PostgreSQL "public" schema via a
# dedicated host, and from its own URLconf (schoolsystem.urls_public). Nothing
# in this block affects tenant routing or the authenticated school application.

# Hosts that serve the public marketing website. Comma-separated hostnames
# only — no scheme and no port (a request's port is stripped before
# comparison). Leave empty to disable the bypass entirely.
#
# List the main domain here. A school's hostname must never be listed: the
# marketing site would then shadow that school's own site, and the public site
# must not publish school addresses in any case.
# Production: "ecreativesys.com" (add "www.ecreativesys.com" only if that
# hostname is also configured in DNS; canonical links still name the main
# domain, so crawlers are not split between the two).
# Local tenancy development: "localhost,127.0.0.1"
PUBLIC_WEBSITE_DOMAIN = os.getenv("PUBLIC_WEBSITE_DOMAIN", "").strip()

# Master switch. When False the middleware behaves exactly as before and the
# public host falls through to the normal (fail-closed) tenant resolution.
PUBLIC_WEBSITE_ENABLED = os.getenv(
    "PUBLIC_WEBSITE_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}

# URLconf used to serve the public website. Kept as a setting rather than a
# TENANTS['public']['URLCONF'] key because django-pgschemas rejects the latter.
PUBLIC_WEBSITE_URLCONF = os.getenv(
    "PUBLIC_WEBSITE_URLCONF", "schoolsystem.urls_public"
).strip()

# Canonical origin used for absolute URLs, the sitemap, and Open Graph tags.
# Empty falls back to https://<EDEN_SYS_DOMAIN>, and only then to the host of
# the incoming request (local development and the /site/ preview). Never
# hardcode a production hostname in a template.
SITE_URL = os.getenv("SITE_URL", "").strip().rstrip("/")

# --- Business contact details ------------------------------------------------
# Blank values fall back to the confirmed Eden Creative Systems details below.
EDEN_SYS_COMPANY = os.getenv("EDEN_SYS_COMPANY", "Eden Creative Systems").strip()
EDEN_SYS_SHORT_NAME = os.getenv("EDEN_SYS_SHORT_NAME", "Edensys").strip()
EDEN_SYS_PRODUCT_NAME = os.getenv(
    "EDEN_SYS_PRODUCT_NAME", "SHS Boarding & Dorm Management System"
).strip()
EDEN_SYS_PHONE = os.getenv("EDEN_SYS_PHONE", "+233 24 816 0391").strip()
EDEN_SYS_EMAIL = os.getenv("EDEN_SYS_EMAIL", "berkojohnson@gmail.com").strip()
EDEN_SYS_DOMAIN = os.getenv("EDEN_SYS_DOMAIN", "ecreativesys.com").strip()
EDEN_SYS_ATTRIBUTION = os.getenv(
    "EDEN_SYS_ATTRIBUTION",
    "Developed by Eden Creative Systems (Edensys)",
).strip()

# Where the "Login" call to action points. Each school is served from its own
# hostname, so there is no shared login URL, and the marketing site must never
# link to a school's hostname. The value is published only when it is on the
# site's own domain (SITE_URL / EDEN_SYS_DOMAIN); anything else is dropped and
# reported by the website.W001 check. Empty hides the link.
EDEN_PORTAL_LOGIN_URL = os.getenv("EDEN_PORTAL_LOGIN_URL", "").strip()

# --- Published pricing ------------------------------------------------------
# Pricing stays unpublished until the commercial terms are explicitly approved.
# While False the pricing page shows "Contact us for pricing" and no figures are
# rendered anywhere on the site.
PRICING_PUBLIC_ENABLED = os.getenv(
    "PRICING_PUBLIC_ENABLED", "false"
).strip().lower() in {"1", "true", "yes", "on"}

# --- Demonstration enquiries -------------------------------------------------
# Recipients notified when a demonstration request is submitted. Falls back to
# DEFAULT_FROM_EMAIL when empty. Email delivery failures never block storage.
env_notify = os.getenv("DEMO_REQUEST_NOTIFY_EMAILS", "")
DEMO_REQUEST_NOTIFY_EMAILS = [
    address.strip() for address in env_notify.split(",") if address.strip()
]

# SMS alert for new demonstration requests. This is a platform alert to the
# business, not a message to a school, so it deliberately does NOT use the
# per-school SMS pipeline (SMSConfiguration, SMSDelivery, the monthly quota or
# the Celery worker): demonstration enquiries live in the public schema, which
# has no school tenant. It shares the SMSOnlineGH credentials above but is
# independent of SMS_ENABLED, so it can be switched on without enabling
# messaging to guardians. An empty list disables the alert.
env_demo_sms = os.getenv("DEMO_REQUEST_SMS_TO", "")
DEMO_REQUEST_SMS_TO = [
    number.strip() for number in env_demo_sms.split(",") if number.strip()
]
# Sender ID for the alert. Defaults to the already-registered school sender
# rather than inventing one, because unreviewed sender IDs are rejected by the
# gateway. Override only with a sender ID approved on your SMSOnlineGH account.
DEMO_REQUEST_SMS_SENDER = (
    os.getenv("DEMO_REQUEST_SMS_SENDER", "").strip() or SMS_SENDER_ID
)

# Demonstration-request throttling: max submissions per client IP per window.
DEMO_REQUEST_RATE_LIMIT = env_int("DEMO_REQUEST_RATE_LIMIT", 5)
DEMO_REQUEST_RATE_WINDOW = env_int("DEMO_REQUEST_RATE_WINDOW", 3600)
