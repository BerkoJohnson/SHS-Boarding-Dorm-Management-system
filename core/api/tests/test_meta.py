from datetime import date

from django.core.cache import cache
from django.test import TestCase, override_settings

from core.api.tests.factories import (
    auth,
    create_house,
    create_school,
    create_staff,
    login,
    token_from,
)
from core.models import (
    AcademicTerm,
    AcademicYear,
    ClassGroup,
    Dormitory,
    Programme,
    StaffRole,
)


class BootstrapTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.admin = create_staff("admin", role=StaffRole.ADMIN)
        self.house = create_house("Unity")
        self.programme = Programme.objects.create(name="General Science")
        self.year = AcademicYear.objects.create(
            start_date=date(2025, 9, 1), end_date=date(2026, 7, 31), is_current=True
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            start_date=date(2025, 9, 1),
            end_date=date(2025, 12, 20),
            is_current=True,
        )
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name="Block A",
            gender=Dormitory.GenderBlock.MALE,
        )
        self.class_group = ClassGroup.objects.create(
            name="1A1", programme=self.programme
        )
        self.token = token_from(login(self.client, "admin"))

    def test_meta_returns_bootstrap_context(self):
        response = self.client.get("/api/v1/meta/", **auth(self.token))
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["school"]["name"], "Test Senior High School")
        self.assertEqual(data["user"]["role"], StaffRole.ADMIN)
        self.assertTrue(data["api_base_url"].endswith("/api/v1"))
        self.assertIn("exeat_types", data["enums"])
        self.assertIn("minimum_app_version", data)

    def test_reference_returns_school_structure_and_staff(self):
        response = self.client.get("/api/v1/reference/", **auth(self.token))
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["houses"][0]["name"], "Unity")
        self.assertEqual(data["dormitories"][0]["name"], "Block A")
        self.assertEqual(data["academic_terms"][0]["id"], str(self.term.pk))
        self.assertEqual(data["class_groups"][0]["name"], "1A1")
        self.assertIn("staff", data)
        self.assertEqual(data["counts"]["houses"], 1)
        self.assertNotIn("phone_number", data["staff"][0])

    def test_unassigned_staff_are_not_listed(self):
        create_staff("unassigned", role=StaffRole.UNASSIGNED)
        response = self.client.get("/api/v1/reference/", **auth(self.token))
        usernames = [row["full_name"] for row in response.json()["data"]["staff"]]
        self.assertNotIn("unassigned", usernames)

    def test_public_school_identity_is_unauthenticated(self):
        response = self.client.get("/api/v1/public/school/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"]["school"]["name"], "Test Senior High School"
        )
        self.assertIn("api_base_url", response.json()["data"])

    def test_single_tenant_directory_returns_the_one_school(self):
        response = self.client.get("/api/v1/public/schools/")
        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Test Senior High School")
        self.assertTrue(rows[0]["api_base_url"].endswith("/api/v1"))

    @override_settings(API_PUBLIC_RATE_LIMIT=1, API_PUBLIC_RATE_WINDOW=60)
    def test_public_endpoints_are_rate_limited(self):
        first = self.client.get("/api/v1/public/schools/")
        self.assertEqual(first.status_code, 200)
        second = self.client.get("/api/v1/public/schools/")
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "rate_limited")
        self.assertEqual(second["Retry-After"], "60")

    def test_unknown_api_route_returns_json_404(self):
        response = self.client.get("/api/v1/nope/", **auth(self.token))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")
        self.assertEqual(response["Content-Type"], "application/json")

    def test_method_not_allowed_is_json(self):
        response = self.client.delete("/api/v1/meta/", **auth(self.token))
        self.assertEqual(response.status_code, 405)
        self.assertIn("Allow", response)
        self.assertEqual(response.json()["error"]["code"], "method_not_allowed")

    @override_settings(API_ENABLED=False)
    def test_api_can_be_disabled_for_rollback(self):
        response = self.client.get("/api/v1/meta/", **auth(self.token))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "api_disabled")

    @override_settings(API_MIN_APP_VERSION="2.0.0")
    def test_old_app_version_is_rejected(self):
        response = self.client.get(
            "/api/v1/meta/",
            HTTP_X_APP_VERSION="1.9.9",
            **auth(self.token),
        )
        self.assertEqual(response.status_code, 426)
        response = self.client.get(
            "/api/v1/meta/",
            HTTP_X_APP_VERSION="2.0.0",
            **auth(self.token),
        )
        self.assertEqual(response.status_code, 200)
