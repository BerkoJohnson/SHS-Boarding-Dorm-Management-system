"""Tests for the public-host routing and app-schema placement.

These assertions protect the two architectural decisions the public website
depends on:

1. ``website`` is a *public* app, so its tables live in the PostgreSQL
   ``public`` schema and can never be read from a school's schema.
2. The public host is dispatched to ``schoolsystem.urls_public``, which exposes
   only the marketing site.
"""
from __future__ import annotations

import importlib
import importlib.util
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from tenancy.middleware import VerifiedDomainRoutingMiddleware


class AppPlacementTests(SimpleTestCase):
    def test_website_is_a_public_app(self):
        self.assertIn("website", settings.PUBLIC_APPS)

    def test_website_is_not_a_tenant_app(self):
        self.assertNotIn("website", settings.TENANT_APPS)

    def test_website_models_belong_to_a_public_app(self):
        app_config = apps.get_app_config("website")
        self.assertIn(app_config.name, settings.PUBLIC_APPS)

    def test_public_schema_lists_the_website_app(self):
        self.assertIn("website", settings.TENANTS["public"]["APPS"])

    def test_the_website_has_no_admin_registration(self):
        """The enquiry inbox is a management command, not a Django admin page.

        ``django.contrib.admin`` is a tenant app, so a registration would
        produce a permanently broken page on the public schema.
        """
        importlib.import_module("website")
        self.assertFalse(
            importlib.util.find_spec("website.admin"),
            "website.admin must not exist: the public schema has no admin.",
        )


class PublicUrlconfTests(SimpleTestCase):
    def test_urlconf_setting_points_at_the_public_urlconf(self):
        self.assertEqual(
            settings.PUBLIC_WEBSITE_URLCONF, "schoolsystem.urls_public"
        )

    def test_public_urlconf_declares_public_error_handlers(self):
        module = importlib.import_module(settings.PUBLIC_WEBSITE_URLCONF)
        self.assertEqual(
            module.handler404, "website.views.public_page_not_found_view"
        )
        self.assertEqual(
            module.handler500, "website.views.public_server_error_view"
        )
        self.assertEqual(
            module.handler403, "website.views.public_permission_denied_view"
        )

    def test_public_urlconf_does_not_include_the_school_application(self):
        module = importlib.import_module(settings.PUBLIC_WEBSITE_URLCONF)
        includes = [
            getattr(pattern, "urlconf_module", None)
            for pattern in module.urlpatterns
            if hasattr(pattern, "urlconf_module")
        ]
        module_names = set()
        for entry in includes:
            if isinstance(entry, str):
                module_names.add(entry)
            else:
                module_names.add(getattr(entry, "__name__", ""))
        self.assertIn("website.urls", module_names)
        for forbidden in ("core.urls", "django.contrib.admin"):
            self.assertNotIn(forbidden, module_names)

    def test_public_urls_are_namespaced(self):
        from website import urls

        self.assertEqual(urls.app_name, "website")
        names = {pattern.name for pattern in urls.urlpatterns}
        for expected in (
            "home",
            "features",
            "services",
            "how_it_works",
            "pricing",
            "faq",
            "contact",
            "demo_request",
            "privacy",
            "terms",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)


class PublicHostParsingTests(SimpleTestCase):
    """``_public_website_hosts`` drives the middleware bypass."""

    def tearDown(self):
        # The middleware caches parsed hosts; reset between cases.
        VerifiedDomainRoutingMiddleware._public_hosts_cache = None

    def hosts(self):
        return VerifiedDomainRoutingMiddleware._public_website_hosts()

    def test_empty_setting_disables_the_bypass(self):
        with override_settings(PUBLIC_WEBSITE_DOMAIN="", PUBLIC_WEBSITE_ENABLED=True):
            self.assertEqual(self.hosts(), frozenset())

    def test_configured_hosts_are_parsed_and_normalised(self):
        with override_settings(
            PUBLIC_WEBSITE_DOMAIN="ecreativesys.com, WWW.ECreativeSys.com ,",
            PUBLIC_WEBSITE_ENABLED=True,
        ):
            self.assertEqual(
                self.hosts(),
                frozenset({"ecreativesys.com", "www.ecreativesys.com"}),
            )

    def test_trailing_dot_is_normalised(self):
        with override_settings(
            PUBLIC_WEBSITE_DOMAIN="ecreativesys.com.", PUBLIC_WEBSITE_ENABLED=True
        ):
            self.assertEqual(self.hosts(), frozenset({"ecreativesys.com"}))

    def test_entries_with_a_scheme_port_or_path_are_ignored(self):
        with override_settings(
            PUBLIC_WEBSITE_DOMAIN=(
                "ecreativesys.com,http://evil.test,localhost:8000,"
                "good.example.com/portal"
            ),
            PUBLIC_WEBSITE_ENABLED=True,
        ):
            self.assertEqual(self.hosts(), frozenset({"ecreativesys.com"}))

    def test_master_switch_disables_the_bypass(self):
        with override_settings(
            PUBLIC_WEBSITE_DOMAIN="ecreativesys.com", PUBLIC_WEBSITE_ENABLED=False
        ):
            self.assertEqual(self.hosts(), frozenset())

    def test_localhost_can_be_used_for_tenancy_development(self):
        with override_settings(
            PUBLIC_WEBSITE_DOMAIN="localhost,127.0.0.1",
            PUBLIC_WEBSITE_ENABLED=True,
        ):
            self.assertEqual(
                self.hosts(), frozenset({"localhost", "127.0.0.1"})
            )

    def test_changing_the_setting_is_not_masked_by_the_cache(self):
        with override_settings(PUBLIC_WEBSITE_DOMAIN="a.example.com"):
            self.assertEqual(self.hosts(), frozenset({"a.example.com"}))
        with override_settings(PUBLIC_WEBSITE_DOMAIN="b.example.com"):
            self.assertEqual(self.hosts(), frozenset({"b.example.com"}))


@override_settings(ALLOWED_HOSTS=["ecreativesys.com"])
class PublicHostDispatchTests(TestCase):
    """The middleware must divert the public host without querying the tenant
    ``Domain`` table.

    ``ALLOWED_HOSTS`` is set because the middleware reads ``request.get_host()``,
    which validates the header. In production with tenancy enabled
    ``ALLOWED_HOSTS`` is ``["*"]`` for exactly this reason, so the header is
    never rejected before the bypass can inspect it.
    """

    def setUp(self):
        VerifiedDomainRoutingMiddleware._public_hosts_cache = None

    def _call(self, host, cookies=None):
        from django.http import HttpResponse

        captured = {}

        def get_response(request):
            captured["urlconf"] = getattr(request, "urlconf", None)
            captured["cookies"] = dict(request.COOKIES)
            return HttpResponse("ok")

        request = RequestFactory().get("/", HTTP_HOST=host)
        if cookies:
            request.COOKIES.update(cookies)

        middleware = VerifiedDomainRoutingMiddleware(get_response)
        with mock.patch("tenancy.middleware.activate_public"), mock.patch(
            "tenancy.middleware.set_urlconf"
        ) as set_urlconf:
            response = middleware(request)
        captured["set_urlconf_calls"] = set_urlconf.call_args_list
        return response, captured

    @override_settings(
        PUBLIC_WEBSITE_DOMAIN="ecreativesys.com", PUBLIC_WEBSITE_ENABLED=True
    )
    def test_public_host_is_served_by_the_public_urlconf(self):
        response, captured = self._call("ecreativesys.com")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["urlconf"], "schoolsystem.urls_public")

    @override_settings(
        PUBLIC_WEBSITE_DOMAIN="ecreativesys.com", PUBLIC_WEBSITE_ENABLED=True
    )
    def test_public_host_ignores_the_port_in_the_request(self):
        response, captured = self._call("ecreativesys.com:8000")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["urlconf"], "schoolsystem.urls_public")

    @override_settings(
        PUBLIC_WEBSITE_DOMAIN="ecreativesys.com", PUBLIC_WEBSITE_ENABLED=True
    )
    def test_the_session_cookie_is_dropped_on_the_public_host(self):
        """Keeps the public site stateless: the public schema has no session
        table, and no staff session may be inherited."""
        _, captured = self._call(
            "ecreativesys.com", cookies={"sessionid": "abc123"}
        )
        self.assertNotIn("sessionid", captured["cookies"])

    @override_settings(
        PUBLIC_WEBSITE_DOMAIN="ecreativesys.com", PUBLIC_WEBSITE_ENABLED=True
    )
    def test_the_bypass_does_not_touch_the_domain_table(self):
        with self.assertNumQueries(0):
            self._call("ecreativesys.com")

    @override_settings(
        PUBLIC_WEBSITE_DOMAIN="ecreativesys.com", PUBLIC_WEBSITE_ENABLED=False
    )
    def test_disabling_the_bypass_falls_through_to_tenant_resolution(self):
        """With the bypass off, an unknown host is still refused (fail-closed)."""
        with self.assertRaises(Http404):
            self._call("ecreativesys.com")

    @override_settings(PUBLIC_WEBSITE_DOMAIN="", PUBLIC_WEBSITE_ENABLED=True)
    def test_an_unconfigured_public_domain_falls_through(self):
        with self.assertRaises(Http404):
            self._call("ecreativesys.com")


class PublicWebsiteSettingsTests(SimpleTestCase):
    def test_contact_details_are_configured(self):
        self.assertEqual(settings.EDEN_SYS_PHONE, "+233 24 816 0391")
        self.assertEqual(settings.EDEN_SYS_EMAIL, "berkojohnson@gmail.com")
        self.assertEqual(settings.EDEN_SYS_DOMAIN, "ecreativesys.com")
        self.assertEqual(
            settings.EDEN_SYS_ATTRIBUTION,
            "Developed by Eden Creative Systems (Eden Sys)",
        )

    def test_demo_request_throttling_is_configured(self):
        self.assertGreater(settings.DEMO_REQUEST_RATE_LIMIT, 0)
        self.assertGreater(settings.DEMO_REQUEST_RATE_WINDOW, 0)
