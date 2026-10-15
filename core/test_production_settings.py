import os
import subprocess
import sys
from pathlib import Path

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from schoolsystem.security import ContentSecurityPolicyReportOnlyMiddleware


PROJECT_ROOT = Path(__file__).resolve().parent.parent
TEST_SECRET_KEY = "test-only-secret-key-for-production-settings-checks"


class ProductionSettingsTests(SimpleTestCase):
    def run_settings_import(
        self,
        *,
        secret_key,
        allowed_hosts,
        database_profile="neon",
        pg_sslmode="require",
        secure_hsts_seconds=None,
        hsts_subdomains=None,
        hsts_preload=None,
        csp_report_uri=None,
    ):
        environment = os.environ.copy()
        environment["DJANGO_SECRET_KEY"] = secret_key
        environment["ALLOWED_HOSTS"] = allowed_hosts
        environment["DATABASE_PROFILE"] = database_profile
        environment["PGHOST"] = "neon.example.neon.tech"
        environment["PGDATABASE"] = "neon_db"
        environment["PGUSER"] = "neon_user"
        environment["PGPASSWORD"] = "test-password"
        environment["PGSSLMODE"] = pg_sslmode
        environment.pop("SECURE_HSTS_SECONDS", None)
        environment.pop("SECURE_HSTS_INCLUDE_SUBDOMAINS", None)
        environment.pop("SECURE_HSTS_PRELOAD", None)
        environment.pop("CSP_REPORT_URI", None)
        if secure_hsts_seconds is not None:
            environment["SECURE_HSTS_SECONDS"] = secure_hsts_seconds
        if hsts_subdomains is not None:
            environment["SECURE_HSTS_INCLUDE_SUBDOMAINS"] = hsts_subdomains
        if hsts_preload is not None:
            environment["SECURE_HSTS_PRELOAD"] = hsts_preload
        if csp_report_uri is not None:
            environment["CSP_REPORT_URI"] = csp_report_uri
        environment["TENANCY_ENABLED"] = "false"
        return subprocess.run(
            [
                sys.executable,
                "-c",
                "import schoolsystem.settings.production as settings; "
                "print(settings.SECURE_HSTS_SECONDS, "
                "settings.SECURE_HSTS_INCLUDE_SUBDOMAINS, "
                "settings.SECURE_HSTS_PRELOAD); "
                "print(settings.CSP_REPORT_ONLY_POLICY); "
                "print('Content-Security-Policy-Report-Only'); "
                "print('schoolsystem.security.ContentSecurityPolicyReportOnlyMiddleware' "
                "in settings.MIDDLEWARE)",
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

    def test_render_defaults_to_production_settings(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import os; os.environ.pop('DJANGO_SETTINGS_MODULE', None); "
                "os.environ['RENDER'] = 'true'; import schoolsystem.wsgi; "
                "print(os.environ['DJANGO_SETTINGS_MODULE'])",
            ],
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "DJANGO_SECRET_KEY": TEST_SECRET_KEY,
                "ALLOWED_HOSTS": "school.example.org",
                "DATABASE_PROFILE": "neon",
                "PGHOST": "neon.example.neon.tech",
                "PGDATABASE": "neon_db",
                "PGUSER": "neon_user",
                "PGPASSWORD": "test-password",
                "PGSSLMODE": "require",
            },
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("schoolsystem.settings.production", result.stdout)

    def test_production_settings_require_allowed_hosts(self):
        result = self.run_settings_import(secret_key=TEST_SECRET_KEY, allowed_hosts=" , ")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set ALLOWED_HOSTS", result.stderr)

    def test_production_settings_reject_wildcard_allowed_hosts(self):
        result = self.run_settings_import(secret_key=TEST_SECRET_KEY, allowed_hosts="*")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'*' is not allowed", result.stderr)

    def test_tenant_mode_uses_verified_domain_routing_for_dynamic_hosts(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import schoolsystem.settings.production as settings; "
                "assert settings.TENANCY_ENABLED; "
                "assert settings.ALLOWED_HOSTS == ['*']; "
                "assert settings.DATABASES['default']['ENGINE'] == "
                "'django_pgschemas.postgresql'; "
                "assert settings.MIDDLEWARE[0] == "
                "'tenancy.middleware.VerifiedDomainRoutingMiddleware'; "
                "assert settings.DATABASE_ROUTERS == "
                "['django_pgschemas.routers.TenantAppsRouter']",
            ],
            cwd=PROJECT_ROOT,
            env={
                **os.environ,
                "DJANGO_SECRET_KEY": TEST_SECRET_KEY,
                "ALLOWED_HOSTS": "",
                "TENANCY_ENABLED": "true",
                "DATABASE_PROFILE": "neon",
                "PGHOST": "neon.example.neon.tech",
                "PGDATABASE": "neon_db",
                "PGUSER": "neon_user",
                "PGPASSWORD": "test-password",
                "PGSSLMODE": "require",
            },
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_settings_reject_the_local_database_profile(self):
        result = self.run_settings_import(
            secret_key=TEST_SECRET_KEY,
            allowed_hosts="school.example.org",
            database_profile="local",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Production requires DATABASE_PROFILE=neon", result.stderr)

    def test_production_settings_require_an_explicit_secure_tls_mode(self):
        result = self.run_settings_import(
            secret_key=TEST_SECRET_KEY,
            allowed_hosts="school.example.org",
            pg_sslmode="",
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Production requires PGSSLMODE", result.stderr)

    def test_production_settings_default_to_host_only_non_preload_hsts(self):
        result = self.run_settings_import(
            secret_key=TEST_SECRET_KEY,
            allowed_hosts="school.example.org",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 False False", result.stdout)

    def test_hsts_preload_requires_year_long_subdomain_policy(self):
        result = self.run_settings_import(
            secret_key=TEST_SECRET_KEY,
            allowed_hosts="school.example.org",
            secure_hsts_seconds="31536000",
            hsts_subdomains="true",
            hsts_preload="true",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        invalid_result = self.run_settings_import(
            secret_key=TEST_SECRET_KEY,
            allowed_hosts="school.example.org",
            secure_hsts_seconds="31536000",
            hsts_preload="true",
        )
        self.assertNotEqual(invalid_result.returncode, 0)
        self.assertIn("SECURE_HSTS_INCLUDE_SUBDOMAINS=true", invalid_result.stderr)

    def test_production_settings_install_a_report_only_csp(self):
        result = self.run_settings_import(
            secret_key=TEST_SECRET_KEY,
            allowed_hosts="school.example.org",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Content-Security-Policy-Report-Only", result.stdout)
        self.assertIn("script-src 'self'", result.stdout)
        self.assertIn("style-src 'self'", result.stdout)
        self.assertNotIn("'unsafe-inline'", result.stdout)
        self.assertIn("True", result.stdout)

    def test_csp_reporting_endpoint_must_be_same_origin_path_or_https(self):
        valid_result = self.run_settings_import(
            secret_key=TEST_SECRET_KEY,
            allowed_hosts="school.example.org",
            csp_report_uri="/security/csp-report/",
        )
        self.assertEqual(valid_result.returncode, 0, valid_result.stderr)
        self.assertIn("report-uri /security/csp-report/", valid_result.stdout)

        invalid_result = self.run_settings_import(
            secret_key=TEST_SECRET_KEY,
            allowed_hosts="school.example.org",
            csp_report_uri="//attacker.example/report",
        )
        self.assertNotEqual(invalid_result.returncode, 0)
        self.assertIn("CSP_REPORT_URI must be a safe", invalid_result.stderr)

    @override_settings(CSP_REPORT_ONLY_POLICY="default-src 'self'")
    def test_report_only_csp_middleware_adds_policy_without_overwriting_header(self):
        middleware = ContentSecurityPolicyReportOnlyMiddleware(
            lambda request: HttpResponse("ok")
        )
        request = RequestFactory().get("/")

        response = middleware(request)

        self.assertEqual(
            response["Content-Security-Policy-Report-Only"],
            "default-src 'self'",
        )
        existing_response = HttpResponse("ok")
        existing_response["Content-Security-Policy-Report-Only"] = "existing-policy"
        unchanged = middleware.process_response(request, existing_response)
        self.assertEqual(
            unchanged["Content-Security-Policy-Report-Only"],
            "existing-policy",
        )

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

    def test_neon_object_storage_uses_private_path_style_s3(self):
        environment = {
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "schoolsystem.settings.development",
            "MEDIA_STORAGE_BACKEND": "neon_s3",
            "NEON_STORAGE_BUCKET": "school-uploads",
            "AWS_ENDPOINT_URL_S3": "https://branch.storage.example.neon.tech",
            "AWS_REGION": "us-east-2",
            "AWS_ACCESS_KEY_ID": "test-access-key",
            "AWS_SECRET_ACCESS_KEY": "test-secret-key",
        }
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import django; django.setup(); "
                "from django.core.files.storage import storages; "
                "from django.urls import get_resolver; "
                "storage = storages['default']; "
                "assert storage.bucket_name == 'school-uploads'; "
                "assert storage.default_acl is None; "
                "assert storage.querystring_auth; "
                "assert storage.addressing_style == 'path'; "
                "assert storage.signature_version == 's3v4'; "
                "assert 'X-Amz-Signature=' in "
                "storage.url('school/logos/crest.png'); "
                "assert 'media/school/logos/<path:path>' not in "
                "[str(pattern.pattern) for pattern in "
                "get_resolver().url_patterns]",
            ],
            cwd=PROJECT_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_neon_object_storage_requires_all_connection_settings(self):
        environment = {
            **os.environ,
            "MEDIA_STORAGE_BACKEND": "neon_s3",
            "NEON_STORAGE_BUCKET": "school-uploads",
            "AWS_ENDPOINT_URL_S3": "",
            "AWS_REGION": "us-east-2",
            "AWS_ACCESS_KEY_ID": "test-access-key",
            "AWS_SECRET_ACCESS_KEY": "test-secret-key",
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
        self.assertIn("AWS_ENDPOINT_URL_S3", result.stderr)

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
