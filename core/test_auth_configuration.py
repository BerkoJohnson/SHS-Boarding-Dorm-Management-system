import io
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core.models import ConfigurationAuditLog, SchoolConfiguration, SMSConfiguration


User = get_user_model()


class StaffLoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(
            username="remember-me-user",
            password="secure-test-password",
        )

    def test_login_session_expires_when_browser_closes(self):
        response = self.client.post(
            reverse("core:login"),
            {
                "username": "remember-me-user",
                "password": "secure-test-password",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.assertEqual(self.client.session.get_expiry_age(), 1800)

    def test_login_page_directs_password_recovery_to_administrator(self):
        response = self.client.get(reverse("core:login"))

        self.assertContains(response, "Contact your system administrator")
        self.assertNotContains(response, "Keep me signed in")
        self.assertNotContains(response, 'href="#"')


class LogoutMethodTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="logout-user",
            password="secure-test-password",
        )
        self.client.force_login(self.user)

    def test_logout_rejects_get_without_ending_the_session(self):
        response = self.client.get(reverse("core:logout"))

        self.assertEqual(response.status_code, 405)
        self.assertEqual(
            self.client.session.get("_auth_user_id"),
            str(self.user.pk),
        )

    def test_logout_post_ends_the_session_and_redirects_to_login(self):
        response = self.client.post(reverse("core:logout"))

        self.assertRedirects(response, reverse("core:login"))
        self.assertNotIn("_auth_user_id", self.client.session)


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
        self.assertContains(response, "Powered by")
        self.assertContains(response, "Eden Creative Systems")
        self.assertContains(response, "eden-creative-systems-mark.png")
        self.assertContains(response, "favicon.ico")

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
                logo_response = self.client.get(configuration.logo.url)
                logo_content = b"".join(logo_response.streaming_content)

                self.assertEqual(logo_response.status_code, 200)
                self.assertEqual(logo_response["Content-Type"], "image/png")
                self.assertEqual(
                    logo_content,
                    image_bytes.getvalue(),
                )

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
        audit_event = ConfigurationAuditLog.objects.get(
            event=ConfigurationAuditLog.Event.SCHOOL_IDENTITY_CHANGED,
        )
        self.assertEqual(audit_event.actor, admin_user)
        self.assertEqual(
            audit_event.details["name"],
            {"from": None, "to": "Admin Configured School"},
        )

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

    def test_sms_admin_page_does_not_bypass_audited_configuration_action(self):
        admin_user = User.objects.create_superuser(
            username="sms-admin-readonly",
            password="secure-test-password",
        )
        configuration, _created = SMSConfiguration.objects.update_or_create(
            pk=1,
            defaults={"enabled": True},
        )
        self.client.force_login(admin_user)

        self.client.post(
            reverse("admin:core_smsconfiguration_change", args=[configuration.pk]),
            {"enabled": "false", "_save": "Save"},
        )

        configuration.refresh_from_db()
        self.assertTrue(configuration.enabled)
