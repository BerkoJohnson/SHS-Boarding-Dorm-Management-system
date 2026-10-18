"""Verify the public website renders without the school's tables.

The public marketing site is served from the platform schema, which contains
none of the ``core_*`` tables, and the tenant middleware marks every such
request with ``request.is_public_website = True``. Two things therefore have to
hold:

1. ``core.context_processors.header_context`` must skip all school-specific work
   when that flag is set, so a public page render issues no database queries.
2. If the flag were ever missed — or the school configuration table were absent
   for any other reason — the processor must degrade gracefully rather than
   raise, because a failure here is an HTTP 500 on the public host only.

A regression in either would take down the marketing site while leaving the
school application working perfectly, which is the hardest failure to notice.
"""
from __future__ import annotations

from unittest import mock

from django.conf import settings
from django.db import ProgrammingError
from django.test import RequestFactory, TestCase, override_settings

from core.context_processors import get_school_configuration_safe, header_context
from core.models import SchoolConfiguration

PUBLIC_PATHS = (
    "/",
    "/features/",
    "/services/",
    "/how-it-works/",
    "/pricing/",
    "/faqs/",
    "/contact/",
    "/request-a-demo/",
    "/privacy-policy/",
    "/terms-of-service/",
    "/sitemap.xml",
    "/robots.txt",
)


class MarkPublicWebsiteMiddleware:
    """Stands in for the tenant middleware on a public-website host.

    Production does both of these things in
    ``tenancy.middleware.VerifiedDomainRoutingMiddleware``: it marks the request
    so templates skip school-specific context, and it drops the session cookie
    so the marketing site stays stateless. Tenancy is disabled in the test
    settings, so the same two steps are applied here. The middleware's own
    behaviour is covered by ``test_public_host_routing``.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.is_public_website = True
        request.COOKIES.pop(settings.SESSION_COOKIE_NAME, None)
        return self.get_response(request)


class HeaderContextDegradationTests(TestCase):
    def test_public_website_flag_skips_every_school_query(self):
        request = RequestFactory().get("/", HTTP_HOST="ecreativesys.com")
        request.is_public_website = True
        request.user = mock.Mock(is_authenticated=True)

        with mock.patch.object(
            SchoolConfiguration,
            "get_solo",
            side_effect=AssertionError(
                "header_context must not read school data for the public site"
            ),
        ):
            with self.assertNumQueries(0):
                context = header_context(request)

        self.assertEqual(context["notification_count"], 0)
        self.assertEqual(context["notifications"], [])
        self.assertEqual(context["user_role_display"], "Guest")
        self.assertFalse(context["can_view_config"])
        # ``SchoolConfiguration`` defaults its primary key to 1, so "unsaved"
        # is what proves the database was never consulted.
        self.assertTrue(context["school_configuration"]._state.adding)

    def test_missing_school_configuration_table_returns_an_unsaved_instance(self):
        with mock.patch.object(
            SchoolConfiguration,
            "get_solo",
            side_effect=ProgrammingError("relation does not exist"),
        ):
            configuration = get_school_configuration_safe()

        self.assertIsInstance(configuration, SchoolConfiguration)
        self.assertTrue(configuration._state.adding)

    def test_header_context_does_not_raise_when_the_table_is_absent(self):
        request = RequestFactory().get("/", HTTP_HOST="school.example.test")
        request.user = mock.Mock(is_authenticated=False)

        with mock.patch.object(
            SchoolConfiguration,
            "get_solo",
            side_effect=ProgrammingError("relation does not exist"),
        ):
            context = header_context(request)

        self.assertIn("school_configuration", context)
        self.assertTrue(context["school_configuration"]._state.adding)

    def test_header_context_still_uses_the_real_configuration_for_a_school(self):
        """The guard must not disable the header for the school application."""
        SchoolConfiguration.objects.all().delete()
        SchoolConfiguration.objects.create(name="Real School")

        request = RequestFactory().get("/", HTTP_HOST="school.example.test")
        request.user = mock.Mock(is_authenticated=False)

        context = header_context(request)
        self.assertFalse(context["school_configuration"]._state.adding)
        self.assertEqual(context["school_configuration"].name, "Real School")

    def test_guest_context_has_the_same_keys_as_the_authenticated_shape(self):
        request = RequestFactory().get("/", HTTP_HOST="ecreativesys.com")
        request.is_public_website = True
        request.user = mock.Mock(is_authenticated=False)
        guest_keys = set(header_context(request))

        for expected in (
            "school_configuration",
            "notifications",
            "notification_count",
            "can_view_reports",
            "can_access_roll_call",
        ):
            with self.subTest(key=expected):
                self.assertIn(expected, guest_keys)


@override_settings(
    ROOT_URLCONF="schoolsystem.urls_public",
    MIDDLEWARE=[
        "website.tests.test_public_schema_safety.MarkPublicWebsiteMiddleware",
        *settings.MIDDLEWARE,
    ],
)
class PublicPagesAreQueryFreeTests(TestCase):
    """A public page must not read from the database at all."""

    def test_every_public_page_issues_no_queries(self):
        for path in PUBLIC_PATHS:
            with self.subTest(path=path):
                with self.assertNumQueries(0):
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_stray_session_cookie_is_not_echoed_back(self):
        """A hand-set ``sessionid`` must not be re-issued by the public site.

        The session cookie itself is dropped before the session middleware runs
        (see ``test_public_host_routing``); here we assert the marketing pages
        never start a session of their own.
        """
        self.client.cookies["sessionid"] = "deadbeef" * 5
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("sessionid", response.cookies)

    def test_public_pages_write_no_cookies(self):
        for path in ("/", "/pricing/", "/request-a-demo/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertNotIn("sessionid", response.cookies)
