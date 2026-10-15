import os
from pathlib import Path
from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

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
]
TENANTS = {
    "public": {"APPS": PUBLIC_APPS},
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
        }
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
MEDIA_STORAGE_BACKEND = os.getenv(
    "MEDIA_STORAGE_BACKEND", "filesystem"
).strip().lower()
if MEDIA_STORAGE_BACKEND not in {"filesystem", "neon_s3"}:
    raise ValueError(
        "MEDIA_STORAGE_BACKEND must be either 'filesystem' or 'neon_s3'"
    )


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}

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
        name for name, value in neon_storage_environment.items()
        if not value
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
SMS_SENDER_ID = os.getenv("SMS_SENDER_ID", "ATSEC")
SMS_ENABLED = os.getenv("SMS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
SMS_TIME_ZONE = os.getenv("SMS_TIME_ZONE", TIME_ZONE)
SMS_HTTP_TIMEOUT = float(os.getenv("SMS_HTTP_TIMEOUT", "10"))
SMS_MAX_ATTEMPTS = int(os.getenv("SMS_MAX_ATTEMPTS", "5"))

# Celery / Redis
CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
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
}