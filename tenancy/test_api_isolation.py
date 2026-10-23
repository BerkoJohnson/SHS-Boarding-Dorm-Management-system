"""End-to-end tenant isolation for bearer tokens across school hostnames.

Run with the multi-tenant settings (PostgreSQL required):

    python manage.py test tenancy.test_api_isolation \
        --settings=schoolsystem.settings.tenancy_test
"""
from __future__ import annotations

import json
import unittest
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TransactionTestCase
from django_pgschemas.schema import activate_public

from tenancy.models import Tenant

PASSWORD = "StrongPassw0rd!2026"


class ApiTokenTenantIsolationTests(TransactionTestCase):
    databases = {"default"}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.conf import settings

        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL is required for tenant isolation.")
        if not settings.TENANCY_ENABLED:
            raise unittest.SkipTest(
                "TENANCY_ENABLED must be true; use "
                "schoolsystem.settings.tenancy_test."
            )

        suffix = uuid.uuid4().hex[:10]
        cls.schema_a = f"api_a_{suffix}"
        cls.schema_b = f"api_b_{suffix}"
        cls.domain_a = f"api-a-{suffix}.example.test"
        cls.domain_b = f"api-b-{suffix}.example.test"

        for schema, domain, name in (
            (cls.schema_a, cls.domain_a, "Tenant A High School"),
            (cls.schema_b, cls.domain_b, "Tenant B High School"),
        ):
            call_command(
                "provision_school",
                f"--name={name}",
                f"--schema={schema}",
                f"--domain={domain}",
                "--confirm-domain-verified",
                verbosity=0,
            )

        cls.tenant_a = Tenant.objects.get(schema_name=cls.schema_a)
        cls.tenant_b = Tenant.objects.get(schema_name=cls.schema_b)
        Tenant.objects.filter(pk__in=[cls.tenant_a.pk, cls.tenant_b.pk]).update(
            is_active=True
        )
        cls.tenant_a.refresh_from_db()
        cls.tenant_b.refresh_from_db()

        cls._create_staff_member(cls.tenant_a, "staff-a", "Ada", "Mensah", "Tenant A High School")
        cls._create_staff_member(cls.tenant_b, "staff-b", "Kojo", "Boateng", "Tenant B High School")
        activate_public()

    @classmethod
    def _create_staff_member(cls, tenant, username, first_name, last_name, school_name):
        from core.models import SchoolConfiguration, StaffRole, UserProfile

        with tenant:
            user = get_user_model().objects.create_user(
                username=username,
                password=PASSWORD,
                first_name=first_name,
                last_name=last_name,
            )
            UserProfile.objects.update_or_create(
                user=user, defaults={"role": StaffRole.ADMIN}
            )
            SchoolConfiguration.objects.update_or_create(
                id=1,
                defaults={"name": school_name, "short_name": school_name[:50]},
            )

    def setUp(self):
        cache.clear()
        activate_public()
        self.token_a = self._login(self.domain_a, "staff-a")
        self.token_b = self._login(self.domain_b, "staff-b")

    def _login(self, domain, username):
        response = self.client.post(
            "/api/v1/auth/login/",
            data=json.dumps({"username": username, "password": PASSWORD}),
            content_type="application/json",
            HTTP_HOST=domain,
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["data"]["token"]

    def _fixture_teardown(self):
        # Each test issues fresh tokens. Do not flush: the provisioned tenant
        # schemas and their Domain rows are class-level fixtures, and the
        # default TransactionTestCase flush cannot handle the multi-schema
        # content-type foreign keys.
        activate_public()

    def _call(self, method, domain, path, token=None, **extra):
        headers = {"HTTP_HOST": domain}
        if token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        headers.update(extra)
        return getattr(self.client, method)(path, **headers)

    def test_tokens_are_bound_to_their_school_hostname(self):
        me_a = self._call("get", self.domain_a, "/api/v1/auth/me/", self.token_a)
        self.assertEqual(me_a.status_code, 200, me_a.content)
        self.assertEqual(me_a.json()["data"]["user"]["username"], "staff-a")

        me_b = self._call("get", self.domain_b, "/api/v1/auth/me/", self.token_b)
        self.assertEqual(me_b.status_code, 200, me_b.content)
        self.assertEqual(me_b.json()["data"]["user"]["username"], "staff-b")

        cross_b = self._call("get", self.domain_b, "/api/v1/auth/me/", self.token_a)
        self.assertEqual(cross_b.status_code, 401, cross_b.content)
        self.assertEqual(
            cross_b.json()["error"]["code"], "authentication_required"
        )

        cross_a = self._call("get", self.domain_a, "/api/v1/auth/me/", self.token_b)
        self.assertEqual(cross_a.status_code, 401, cross_a.content)
        self.assertEqual(
            cross_a.json()["error"]["code"], "authentication_required"
        )

    def test_hostname_pins_the_api_origin(self):
        meta_a = self._call("get", self.domain_a, "/api/v1/meta/", self.token_a)
        self.assertEqual(meta_a.status_code, 200)
        self.assertEqual(
            meta_a.json()["data"]["canonical_origin"], f"http://{self.domain_a}"
        )
        self.assertEqual(
            meta_a.json()["data"]["api_base_url"],
            f"http://{self.domain_a}/api/v1",
        )

        meta_b = self._call("get", self.domain_b, "/api/v1/meta/", self.token_b)
        self.assertEqual(
            meta_b.json()["data"]["school"]["name"], "Tenant B High School"
        )

    def test_logout_on_one_school_does_not_revoke_the_other(self):
        revoked = self._call(
            "post", self.domain_a, "/api/v1/auth/logout/", self.token_a
        )
        self.assertEqual(revoked.status_code, 204, revoked.content)

        still_valid = self._call(
            "get", self.domain_b, "/api/v1/auth/me/", self.token_b
        )
        self.assertEqual(still_valid.status_code, 200, still_valid.content)

        rejected = self._call(
            "get", self.domain_a, "/api/v1/auth/me/", self.token_a
        )
        self.assertEqual(rejected.status_code, 401)

    @classmethod
    def tearDownClass(cls):
        try:
            activate_public()
            Tenant.objects.filter(
                schema_name__in=[cls.schema_a, cls.schema_b]
            ).delete()
            quote = connection.ops.quote_name
            with connection.cursor() as cursor:
                for schema in (cls.schema_a, cls.schema_b):
                    cursor.execute(f"DROP SCHEMA IF EXISTS {quote(schema)} CASCADE")
        finally:
            super().tearDownClass()
