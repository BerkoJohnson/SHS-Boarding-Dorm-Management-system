import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase


class ProductionSettingsTests(SimpleTestCase):
    def test_production_settings_select_neon_when_env_profile_is_local(self):
        environment = os.environ.copy()
        environment.update(
            {
                "DJANGO_SETTINGS_MODULE": "schoolsystem.settings.production",
                "DATABASE_PROFILE": "local",
                "PGHOST": "production-db.example.test",
                "PGDATABASE": "production_database",
                "PGUSER": "production_user",
                "PGPASSWORD": "test-only-password",
                "PGSSLMODE": "require",
                "DJANGO_SECRET_KEY": "test-only-key-with-more-than-fifty-unique-chars-123456789",
                "CACHE_BACKEND": "django.core.cache.backends.redis.RedisCache",
                "CACHE_LOCATION": "redis://redis.example.test:6379/1",
                "EMAIL_BACKEND": "django.core.mail.backends.smtp.EmailBackend",
                "TENANCY_ENABLED": "true",
                "MEDIA_STORAGE_BACKEND": "filesystem",
            }
        )
        script = (
            "from django.conf import settings; "
            "assert settings.DATABASE_PROFILE == 'neon'; "
            "assert settings.DATABASES['default']['HOST'] == "
            "'production-db.example.test'; "
            "assert settings.DATABASES['default']['NAME'] == "
            "'production_database'; "
            "assert settings.DATABASES['default']['OPTIONS']['sslmode'] "
            "== 'require'"
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=settings.BASE_DIR,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, "Production settings failed to load.")
