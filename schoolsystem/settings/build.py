"""Build-time settings for static collection (``collectstatic``).

Hosting builds must be able to hash/copy static assets without any runtime
secrets (database, object storage, or ``DJANGO_SECRET_KEY``). Importing
``schoolsystem.settings.production`` at build time makes ``collectstatic`` fail
with a non-zero exit whenever a required secret is missing or blank in the
dashboard, which aborts the whole deploy.

This module forces the secrets-free ``local``/``filesystem`` profiles before
importing ``base`` (which validates the selected profiles at import time) and
supplies a throwaway secret key. The static-file pipeline is unchanged: ``base``
already selects WhiteNoise's ``CompressedManifestStaticFilesStorage``, so the
collected manifest matches production. Runtime still uses
``schoolsystem.settings.production`` (see ``render.yaml`` startCommand).
"""

import os

# Force the secrets-free profiles *before* importing base. ``python-dotenv``
# never overrides variables that are already set, so a local ``.env`` cannot
# re-introduce a required secret here.
os.environ["DATABASE_PROFILE"] = "local"
os.environ["MEDIA_STORAGE_BACKEND"] = "filesystem"
# Render still injects runtime env vars into the build container. Populate the
# minimal cache/mail values that production guards expect so static collection
# can run cleanly even when the web service is configured for production.
os.environ.setdefault("CACHE_BACKEND", "django.core.cache.backends.redis.RedisCache")
os.environ.setdefault("CACHE_LOCATION", "redis://localhost:6379/1")
os.environ.setdefault("EMAIL_BACKEND", "django.core.mail.backends.smtp.EmailBackend")

from .base import *  # noqa: E402,F401,F403

# ``base`` intentionally leaves SECRET_KEY to the environment-specific modules;
# static collection only needs a non-empty value to boot the settings.
SECRET_KEY = "collectstatic-build-only-insecure-key-not-used-at-runtime"

DEBUG = False
ALLOWED_HOSTS = ["*"]
