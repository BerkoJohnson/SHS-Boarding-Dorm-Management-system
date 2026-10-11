import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_SECRET_KEY = "test-only-secret-key-for-production-settings-checks"


class ProductionSettingsTests(SimpleTestCase):
    def run_settings_import(self, *, secret_key, allowed_hosts, db_sslmode=""):
        environment = os.environ.copy()
        environment["DJANGO_SECRET_KEY"] = secret_key
        environment["ALLOWED_HOSTS"] = allowed_hosts
        environment["DB_SSLMODE"] = db_sslmode
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import schoolsystem.settings.production",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_production_settings_use_the_configured_secret_and_hosts(self):
        result = self.run_settings_import(
            secret_key=TEST_SECRET_KEY,
            allowed_hosts=" school.example.org, trial.example.org ",
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_settings_require_a_secret_key(self):
        result = self.run_settings_import(secret_key="", allowed_hosts="school.example.org")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY must be at least 50 characters", result.stderr)

    def test_production_settings_reject_weak_secret_keys(self):
        result = self.run_settings_import(
            secret_key="x" * 64,
            allowed_hosts="school.example.org",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY must be at least 50 characters", result.stderr)

    def test_production_settings_require_allowed_hosts(self):
        result = self.run_settings_import(secret_key=TEST_SECRET_KEY, allowed_hosts=" , ")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set ALLOWED_HOSTS", result.stderr)

    def test_production_settings_reject_wildcard_allowed_hosts(self):
        result = self.run_settings_import(secret_key=TEST_SECRET_KEY, allowed_hosts="*")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'*' is not allowed", result.stderr)

    def test_neon_profile_uses_pg_variables_and_requires_ssl(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from schoolsystem.settings.base import DATABASES; "
                "db = DATABASES['default']; "
                "assert (db['NAME'], db['USER'], db['PASSWORD'], db['HOST'], "
                "db['PORT']) == ('neon_db', 'neon_user', 'test-password', "
                "'neon.example.neon.tech', '5432'); "
                "assert db['OPTIONS']['sslmode'] == 'require'",
            ],
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "DATABASE_PROFILE": "neon",
                "PGHOST": "neon.example.neon.tech",
                "PGDATABASE": "neon_db",
                "PGUSER": "neon_user",
                "PGPASSWORD": "test-password",
            },
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_local_profile_keeps_db_variables_when_neon_variables_are_set(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from schoolsystem.settings.base import DATABASES; "
                "db = DATABASES['default']; "
                "assert (db['NAME'], db['USER'], db['PASSWORD'], db['HOST'], "
                "db['PORT']) == ('local_db', 'local_user', 'local-password', "
                "'localhost', '5433'); "
                "assert 'sslmode' not in db['OPTIONS']",
            ],
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "DATABASE_PROFILE": "local",
                "DB_NAME": "local_db",
                "DB_USER": "local_user",
                "DB_PASSWORD": "local-password",
                "DB_HOST": "localhost",
                "DB_PORT": "5433",
                "DB_SSLMODE": "",
                "PGHOST": "neon.example.neon.tech",
                "PGDATABASE": "neon_db",
                "PGUSER": "neon_user",
                "PGPASSWORD": "test-password",
            },
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_neon_profile_reports_missing_connection_settings(self):
        environment = {
            **os.environ,
            "DATABASE_PROFILE": "neon",
            "PGHOST": "",
            "PGDATABASE": "",
            "PGUSER": "",
            "PGPASSWORD": "",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import schoolsystem.settings.base",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DATABASE_PROFILE='neon' requires", result.stderr)
