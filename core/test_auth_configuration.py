import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import SchoolConfiguration


User = get_user_model()


class RememberMeLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="remember-me-user",
            password="secure-test-password",
        )

    def test_remember_me_keeps_session_for_two_weeks(self):
        response = self.client.post(
            reverse("core:login"),
            {
                "username": "remember-me-user",
                "password": "secure-test-password",
                "remember_me": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertGreater(
            self.client.session.get_expiry_age(),
            13 * 24 * 60 * 60,
        )

    def test_session_expires_when_browser_closes_without_remember_me(self):
        response = self.client.post(
            reverse("core:login"),
            {
                "username": "remember-me-user",
                "password": "secure-test-password",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session.get_expire_at_browser_close())


class SchoolConfigurationTests(TestCase):
    def test_development_media_route_does_not_serve_roster_files(self):
        response = self.client.get("/media/admissions_roster.xlsx")

        self.assertEqual(response.status_code, 404)

    def test_login_page_uses_configured_school_identity_and_contact(self):
        SchoolConfiguration.objects.update_or_create(
            pk=1,
            defaults={
                "name": "North Campus School",
                "address": "12 School Road",
                "contact_number": "+233 20 000 0000",
            },
        )

        response = self.client.get(reverse("core:login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "North Campus School")
        self.assertContains(response, "12 School Road")
        self.assertContains(response, "+233 20 000 0000")

    def test_login_page_displays_uploaded_school_logo(self):
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root):
                image_bytes = io.BytesIO()
                Image.new("RGB", (1, 1), color="white").save(image_bytes, format="PNG")
                configuration = SchoolConfiguration.objects.create(
                    name="Logo Test School",
                    logo=SimpleUploadedFile(
                        "crest.png",
                        image_bytes.getvalue(),
                        content_type="image/png",
                    ),
                )

                response = self.client.get(reverse("core:login"))

                self.assertContains(response, configuration.logo.url)

    def test_school_configuration_can_be_added_in_django_admin(self):
        admin_user = User.objects.create_superuser(
            username="school-config-admin",
            password="secure-test-password",
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse("admin:core_schoolconfiguration_add"),
            {
                "name": "Admin Configured School",
                "address": "34 Campus Lane",
                "contact_number": "+233 30 000 0000",
            },
        )

        self.assertEqual(response.status_code, 302)
        configuration = SchoolConfiguration.objects.get(pk=1)
        self.assertEqual(configuration.name, "Admin Configured School")
        self.assertEqual(configuration.address, "34 Campus Lane")
        self.assertEqual(configuration.contact_number, "+233 30 000 0000")

    def test_configuration_hub_links_to_school_settings_in_admin(self):
        admin_user = User.objects.create_superuser(
            username="school-config-link-admin",
            password="secure-test-password",
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("core:configuration"))

        self.assertContains(
            response,
            reverse("admin:core_schoolconfiguration_changelist"),
        )
