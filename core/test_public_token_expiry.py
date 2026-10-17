from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Programme, Student, House


class PublicTokenExpiryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.programme = Programme.objects.create(name="Token Programme")
        cls.house = House.objects.create(name="Token House")

    def _student(self, admission_no, **kwargs):
        return Student.objects.create(
            admission_no=admission_no,
            index_number=f"IDX-{admission_no}",
            full_name="Token Student",
            gender=Student.Gender.MALE,
            residency=Student.ResidencyStatus.DAY,
            programme=self.programme,
            house=self.house,
            **kwargs,
        )

    def test_new_tokens_have_no_expiry_by_default(self):
        student = self._student("TOK-001")

        self.assertIsNone(student.public_token_expires_at)
        self.assertFalse(student.public_token_expired)

    def test_expired_token_is_rejected_by_the_public_exeat_check(self):
        student = self._student(
            "TOK-002",
            public_token_expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertTrue(student.public_token_expired)

        response = self.client.get(
            reverse("core:public_student_exeat", args=[student.public_token])
        )

        self.assertEqual(response.status_code, 404)

    def test_active_token_still_renders_the_public_check(self):
        student = self._student(
            "TOK-003",
            public_token_expires_at=timezone.now() + timedelta(days=30),
        )

        response = self.client.get(
            reverse("core:public_student_exeat", args=[student.public_token])
        )

        self.assertEqual(response.status_code, 200)

    def test_public_photo_is_gated_by_the_tenant_feature(self):
        student = self._student("TOK-004")

        with override_settings(TENANCY_ENABLED=True), patch(
            "tenancy.entitlements.feature_enabled", return_value=False
        ):
            response = self.client.get(
                reverse("core:public_student_photo", args=[student.public_token])
            )

        self.assertEqual(response.status_code, 403)
