"""Tests for the demonstration request form, view and notification."""
from __future__ import annotations

from django.core import mail
from django.core.cache import cache
from django.core.mail.backends.base import BaseEmailBackend
from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from unittest import mock

import requests

from website.models import DemoRequest, DemoRequestStatus, PreferredDemoMethod

LOCMEM_EMAIL = "django.core.mail.backends.locmem.EmailBackend"


def sms_ok_response():
    """A minimal stand-in for a successful SMSOnlineGH response."""
    response = mock.Mock()
    response.status_code = 200
    response.json.return_value = {"handshake": {"label": "HSHK_OK"}}
    return response


def sms_rejected_response(label="HSHK_BAD_API_KEY"):
    """A gateway response that reports the key was refused."""
    response = mock.Mock()
    response.json.return_value = {
        "handshake": {"label": label},
        "error": "invalid key",
    }
    return response



class ExplodingEmailBackend(BaseEmailBackend):
    """Simulates an SMTP outage so notification failure paths can be tested."""

    def send_messages(self, email_messages):
        raise RuntimeError("simulated SMTP outage")


def valid_payload(**overrides) -> dict:
    payload = {
        "school_name": "Test Senior High School",
        "contact_name": "Ama Mensah",
        "role": "Assistant Headmaster",
        "phone": "024 000 0000",
        "email": "ama.mensah@example.test",
        "region": "ashanti",
        "boarding_population": "850",
        "preferred_demo_method": "video_call",
        "message": "Two boarding houses, 850 boarders.",
        "consent": "on",
    }
    payload.update(overrides)
    return payload


@override_settings(
    ROOT_URLCONF="schoolsystem.urls_public",
    EMAIL_BACKEND=LOCMEM_EMAIL,
    DEMO_REQUEST_NOTIFY_EMAILS=["enquiries@example.test"],
    DEMO_REQUEST_SMS_TO=[],
)
class DemoRequestFormTests(TestCase):
    def setUp(self):
        cache.clear()

    @property
    def url(self) -> str:
        return reverse("website:demo_request")

    def test_only_a_clean_form_is_indexable(self):
        """A failed or throttled submission must not be indexed.

        The meta tag and the ``X-Robots-Tag`` header must agree; contradictory
        instructions are resolved inconsistently by crawlers.
        """
        clean = self.client.get(self.url)
        self.assertEqual(clean["X-Robots-Tag"], "index, follow")
        self.assertIn('content="index, follow"', clean.content.decode("utf-8"))

        invalid = self.client.post(self.url, {})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn(
            'content="noindex, nofollow"', invalid.content.decode("utf-8")
        )

    def test_get_renders_the_form_with_every_required_field(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        for label in (
            "School name",
            "Your name",
            "Your official role",
            "Phone number",
            "Email address",
            "Region",
            "Estimated boarding students",
            "Preferred demo method",
            "Message or special requirements",
        ):
            with self.subTest(label=label):
                self.assertContains(response, label)

    def test_valid_submission_is_stored_and_confirmed(self):
        response = self.client.post(self.url, valid_payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thank you")
        self.assertEqual(DemoRequest.objects.count(), 1)

        enquiry = DemoRequest.objects.get()
        self.assertEqual(enquiry.school_name, "Test Senior High School")
        self.assertEqual(enquiry.phone, "+233240000000")
        self.assertEqual(enquiry.email, "ama.mensah@example.test")
        self.assertEqual(enquiry.region, "ashanti")
        self.assertEqual(enquiry.boarding_population, 850)
        self.assertEqual(
            enquiry.preferred_demo_method, PreferredDemoMethod.VIDEO_CALL
        )
        self.assertEqual(enquiry.status, DemoRequestStatus.NEW)
        # The reference number is shown so the visitor can quote it.
        self.assertContains(response, f"#{enquiry.pk}")

    def test_confirmation_page_is_not_indexable(self):
        response = self.client.post(self.url, valid_payload())
        self.assertIn('content="noindex, nofollow"', response.content.decode())

    def test_success_response_is_only_shown_after_storage(self):
        self.assertNotContains(self.client.get(self.url), "Thank you")
        self.assertContains(self.client.post(self.url, valid_payload()), "Thank you")

    def test_optional_fields_may_be_omitted(self):
        payload = valid_payload()
        payload.pop("boarding_population")
        payload.pop("preferred_demo_method")
        payload.pop("message")
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        enquiry = DemoRequest.objects.get()
        self.assertIsNone(enquiry.boarding_population)
        self.assertEqual(
            enquiry.preferred_demo_method, PreferredDemoMethod.NO_PREFERENCE
        )
        self.assertEqual(enquiry.message, "")

    def test_missing_required_fields_are_rejected_and_nothing_is_stored(self):
        response = self.client.post(self.url, {"email": "someone@example.test"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DemoRequest.objects.count(), 0)
        self.assertContains(
            response, "Please check the highlighted fields below", status_code=400
        )

    def test_consent_is_required(self):
        payload = valid_payload()
        payload.pop("consent")
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DemoRequest.objects.count(), 0)

    def test_honeypot_submission_is_rejected(self):
        response = self.client.post(
            self.url, valid_payload(website="http://spam.example")
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DemoRequest.objects.count(), 0)
        self.assertContains(
            response,
            "could not be submitted automatically",
            status_code=400,
        )

    @override_settings(DEMO_REQUEST_RATE_LIMIT=0)
    def test_phone_numbers_are_normalised(self):
        for supplied in (
            "024 000 0000",
            "0240000000",
            "024-000-0000",
            "+233 24 000 0000",
            "+233240000000",
            "00233240000000",
            "233240000000",
        ):
            with self.subTest(supplied=supplied):
                DemoRequest.objects.all().delete()
                response = self.client.post(self.url, valid_payload(phone=supplied))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(DemoRequest.objects.get().phone, "+233240000000")

    def test_invalid_phone_is_rejected(self):
        for supplied in ("not-a-number", "12345", "+233 24 0000"):
            with self.subTest(supplied=supplied):
                response = self.client.post(self.url, valid_payload(phone=supplied))
                self.assertEqual(response.status_code, 400)
                self.assertEqual(DemoRequest.objects.count(), 0)

    def test_boarding_population_bounds(self):
        for supplied in ("0", "50000", "-5"):
            with self.subTest(supplied=supplied):
                response = self.client.post(
                    self.url, valid_payload(boarding_population=supplied)
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(DemoRequest.objects.count(), 0)

    def test_unknown_region_is_rejected(self):
        response = self.client.post(self.url, valid_payload(region="atlantis"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DemoRequest.objects.count(), 0)

    def test_overlong_message_is_rejected(self):
        response = self.client.post(self.url, valid_payload(message="x" * 2001))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DemoRequest.objects.count(), 0)

    def test_server_side_validation_runs_without_javascript(self):
        """The form is submitted with ``novalidate``, so nothing may rely on
        browser-side constraints."""
        response = self.client.post(self.url, valid_payload(email="not-an-email"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DemoRequest.objects.count(), 0)


@override_settings(
    ROOT_URLCONF="schoolsystem.urls_public",
    EMAIL_BACKEND=LOCMEM_EMAIL,
    DEMO_REQUEST_NOTIFY_EMAILS=["enquiries@example.test"],
    DEMO_REQUEST_SMS_TO=[],
)
class DemoRequestPrivacyTests(TestCase):
    def setUp(self):
        cache.clear()

    @property
    def url(self) -> str:
        return reverse("website:demo_request")

    def test_raw_ip_address_is_not_stored(self):
        self.client.post(
            self.url, valid_payload(), REMOTE_ADDR="203.0.113.77"
        )
        enquiry = DemoRequest.objects.get()
        self.assertNotIn("203.0.113.77", enquiry.source_ip_hash)
        self.assertEqual(len(enquiry.source_ip_hash), 64)

    def test_session_is_not_required_and_not_created(self):
        response = self.client.post(self.url, valid_payload())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sessionid", response.cookies)

    def test_absolute_uri_and_external_links_are_rejected_quietly(self):
        """A message containing markup must be stored and escaped, not rendered."""
        response = self.client.post(
            self.url,
            valid_payload(message='<script>alert("x")</script>'),
        )
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertNotIn("<script>alert", body)

    def test_form_is_not_rendered_with_previous_answers_after_success(self):
        self.client.post(self.url, valid_payload())
        body = self.client.get(self.url).content.decode("utf-8")
        self.assertNotIn("Test Senior High School", body)


@override_settings(
    ROOT_URLCONF="schoolsystem.urls_public",
    DEMO_REQUEST_SMS_TO=[],
)
class DemoRequestNotificationTests(TestCase):
    def setUp(self):
        cache.clear()

    @property
    def url(self) -> str:
        return reverse("website:demo_request")

    @override_settings(
        EMAIL_BACKEND=LOCMEM_EMAIL,
        DEMO_REQUEST_NOTIFY_EMAILS=["enquiries@example.test", "ops@example.test"],
    )
    def test_notification_is_sent_to_every_configured_recipient(self):
        self.client.post(self.url, valid_payload())
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(
            sorted(message.to), ["enquiries@example.test", "ops@example.test"]
        )
        self.assertIn("Test Senior High School", message.subject)
        self.assertIn("+233240000000", message.body)
        self.assertIn("python manage.py demo_requests", message.body)
        self.assertTrue(DemoRequest.objects.get().notification_sent)

    @override_settings(EMAIL_BACKEND=LOCMEM_EMAIL, DEMO_REQUEST_NOTIFY_EMAILS=[])
    def test_falls_back_to_the_published_business_address(self):
        """An unconfigured deployment must not send to a no-reply address."""
        self.client.post(self.url, valid_payload())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].to, [settings.EDEN_SYS_EMAIL]
        )
        self.assertTrue(DemoRequest.objects.get().notification_sent)

    @override_settings(
        EMAIL_BACKEND="website.tests.test_demo_request.ExplodingEmailBackend",
        DEMO_REQUEST_NOTIFY_EMAILS=["enquiries@example.test"],
    )
    def test_a_mail_failure_does_not_lose_the_enquiry(self):
        with self.assertLogs("website.emails", level="ERROR"):
            response = self.client.post(self.url, valid_payload())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thank you")
        enquiry = DemoRequest.objects.get()
        self.assertFalse(enquiry.notification_sent)


@override_settings(
    ROOT_URLCONF="schoolsystem.urls_public",
    EMAIL_BACKEND=LOCMEM_EMAIL,
    DEMO_REQUEST_NOTIFY_EMAILS=["enquiries@example.test"],
    DEMO_REQUEST_SMS_TO=[],
)
class DemoRequestThrottlingTests(TestCase):
    def setUp(self):
        cache.clear()

    @property
    def url(self) -> str:
        return reverse("website:demo_request")

    @override_settings(DEMO_REQUEST_RATE_LIMIT=2, DEMO_REQUEST_RATE_WINDOW=3600)
    def test_repeated_submissions_are_throttled(self):
        first = self.client.post(self.url, valid_payload())
        second = self.client.post(self.url, valid_payload())
        third = self.client.post(self.url, valid_payload())

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(third.status_code, 429)
        # The throttled page must not be indexed, in the header or the meta tag.
        self.assertEqual(third["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn('content="noindex, nofollow"', third.content.decode("utf-8"))
        self.assertContains(
            third, "Too many requests from this connection", status_code=429
        )
        # The throttled attempt is not stored.
        self.assertEqual(DemoRequest.objects.count(), 2)

    @override_settings(DEMO_REQUEST_RATE_LIMIT=2, DEMO_REQUEST_RATE_WINDOW=3600)
    def test_throttling_is_per_client_address(self):
        for _ in range(3):
            self.client.post(self.url, valid_payload(), REMOTE_ADDR="198.51.100.5")

        other = self.client.post(
            self.url, valid_payload(), REMOTE_ADDR="198.51.100.6"
        )
        self.assertEqual(other.status_code, 200)

    @override_settings(DEMO_REQUEST_RATE_LIMIT=1, DEMO_REQUEST_RATE_WINDOW=3600)
    def test_viewing_the_form_is_never_throttled(self):
        for _ in range(5):
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)

    @override_settings(DEMO_REQUEST_RATE_LIMIT=0)
    def test_a_zero_limit_disables_throttling(self):
        for _ in range(4):
            self.assertEqual(
                self.client.post(self.url, valid_payload()).status_code, 200
            )


@override_settings(
    ROOT_URLCONF="schoolsystem.urls_public",
    EMAIL_BACKEND=LOCMEM_EMAIL,
    DEMO_REQUEST_NOTIFY_EMAILS=["enquiries@example.test"],
    DEMO_REQUEST_SMS_TO=["024 816 0391"],
    DEMO_REQUEST_SMS_SENDER="EDENSYS",
    SMS_ONLINE_GH_API_KEY="test-api-key",
)
class DemoRequestSmsAlertTests(TestCase):
    """The SMS alert is a platform notification, not a school one.

    A demonstration enquiry lives in the public schema, so the alert must not
    depend on the per-school pipeline: no ``SMSConfiguration``, no
    ``SMSDelivery`` row, no monthly quota and no Celery worker.
    """

    def setUp(self):
        cache.clear()

    @property
    def url(self) -> str:
        return reverse("website:demo_request")

    @mock.patch("website.emails.requests.post")
    def test_alert_is_sent_to_the_configured_number(self, post):
        post.return_value = sms_ok_response()

        response = self.client.post(self.url, valid_payload())

        self.assertEqual(response.status_code, 200)
        post.assert_called_once()
        self.assertEqual(
            post.call_args.args[0],
            "https://api.smsonlinegh.com/v5/message/sms/send",
        )
        payload = post.call_args.kwargs["data"]
        self.assertEqual(payload["to"], "+233248160391")
        self.assertEqual(payload["key"], "test-api-key")
        self.assertEqual(payload["sender"], "EDENSYS")
        self.assertEqual(payload["type"], 0)
        self.assertIn("Test Senior High School", payload["text"])
        self.assertIn("Ashanti", payload["text"])
        self.assertTrue(DemoRequest.objects.get().notification_sms_sent)

    @mock.patch("website.emails.requests.post")
    def test_alert_stays_within_one_sms_segment(self, post):
        post.return_value = sms_ok_response()
        self.client.post(
            self.url,
            valid_payload(school_name="A" * 200, region="greater_accra"),
        )
        payload = post.call_args.kwargs["data"]
        self.assertLessEqual(len(payload["text"]), 160)

    @mock.patch("website.emails.requests.post")
    def test_a_rejected_alert_is_recorded_and_does_not_block_the_enquiry(self, post):
        post.return_value = sms_rejected_response()

        with self.assertLogs("website.emails", level="ERROR"):
            response = self.client.post(self.url, valid_payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Thank you")
        enquiry = DemoRequest.objects.get()
        self.assertFalse(enquiry.notification_sms_sent)
        # An SMS failure must not affect the independent email alert.
        self.assertTrue(enquiry.notification_sent)

    @mock.patch("website.emails.requests.post")
    def test_a_network_failure_does_not_lose_the_enquiry(self, post):
        post.side_effect = requests.ConnectionError("gateway unreachable")

        with self.assertLogs("website.emails", level="ERROR"):
            response = self.client.post(self.url, valid_payload())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(DemoRequest.objects.count(), 1)
        self.assertFalse(DemoRequest.objects.get().notification_sms_sent)

    @mock.patch("website.emails.requests.post")
    def test_a_gateway_timeout_never_blocks_the_request(self, post):
        post.side_effect = requests.Timeout("gateway slow")
        with self.assertLogs("website.emails", level="ERROR"):
            self.client.post(self.url, valid_payload())
        self.assertEqual(
            post.call_args.kwargs["timeout"],
            settings.SMS_HTTP_TIMEOUT,
        )

    @override_settings(DEMO_REQUEST_SMS_TO=[])
    @mock.patch("website.emails.requests.post")
    def test_alert_is_off_when_no_number_is_configured(self, post):
        response = self.client.post(self.url, valid_payload())
        self.assertEqual(response.status_code, 200)
        post.assert_not_called()
        self.assertFalse(DemoRequest.objects.get().notification_sms_sent)

    @override_settings(SMS_ONLINE_GH_API_KEY="")
    @mock.patch("website.emails.requests.post")
    def test_missing_api_key_is_logged_and_not_attempted(self, post):
        with self.assertLogs("website.emails", level="ERROR"):
            self.client.post(self.url, valid_payload())
        post.assert_not_called()
        self.assertFalse(DemoRequest.objects.get().notification_sms_sent)

    @override_settings(DEMO_REQUEST_SMS_TO=["not-a-number"])
    @mock.patch("website.emails.requests.post")
    def test_an_unparseable_number_is_ignored_rather_than_sent(self, post):
        with self.assertLogs("website.emails", level="WARNING"):
            self.client.post(self.url, valid_payload())
        post.assert_not_called()

    @override_settings(DEMO_REQUEST_SMS_SENDER="", SMS_SENDER_ID="SCHOOLID")
    @mock.patch("website.emails.requests.post")
    def test_sender_falls_back_to_the_registered_school_sender(self, post):
        post.return_value = sms_ok_response()
        self.client.post(self.url, valid_payload())
        self.assertEqual(post.call_args.kwargs["data"]["sender"], "SCHOOLID")

    @mock.patch("website.emails.requests.post")
    def test_every_configured_number_receives_the_alert(self, post):
        post.return_value = sms_ok_response()
        with override_settings(
            DEMO_REQUEST_SMS_TO=["024 816 0391", "+233 20 111 2222"]
        ):
            self.client.post(self.url, valid_payload())
        self.assertEqual(
            post.call_args.kwargs["data"]["to"],
            "+233248160391,+233201112222",
        )

