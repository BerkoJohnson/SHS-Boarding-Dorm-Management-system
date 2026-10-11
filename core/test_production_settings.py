import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_SECRET_KEY = "test-only-secret-key-for-production-settings-checks"


class ProductionSettingsTests(SimpleTestCase):
    def run_settings_import(self, *, secret_key, allowed_hosts):
        environment = os.environ.copy()
        environment["DJANGO_SECRET_KEY"] = secret_key
        environment["ALLOWED_HOSTS"] = allowed_hosts
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
