"""Rendering tests for every page of the public website.

These run against the *real* public URLconf (``schoolsystem.urls_public``) via
``ROOT_URLCONF`` override, so the production routing is what gets exercised
rather than a test-only mount point.
"""
from __future__ import annotations

import re

from django.test import TestCase, override_settings
from django.urls import reverse

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
)

# Marker text that must appear on each page. Kept deliberately loose so the
# tests fail on structural breakage rather than on copy edits.
PAGE_MARKERS = {
    "/": "Smarter Boarding. Safer Students. Simpler School Operations.",
    "/features/": "What the boarding system actually does",
    "/services/": "Support before, during and after go-live",
    "/how-it-works/": "From first conversation to a working boarding system",
    "/pricing/": "How the cost is put together",
    "/faqs/": "Frequently asked questions",
    "/contact/": "Talk to Eden Creative Systems",
    "/request-a-demo/": "See the system against your own boarding processes",
    "/privacy-policy/": "Privacy Policy",
    "/terms-of-service/": "Terms of Service",
}

CONTACT_PHONE = "+233 24 816 0391"
CONTACT_EMAIL = "berkojohnson@gmail.com"
ATTRIBUTION = "Developed by Eden Creative Systems (Edensys)"


@override_settings(ROOT_URLCONF="schoolsystem.urls_public")
class PublicPageTests(TestCase):
    def test_every_page_renders_with_its_marker(self):
        for path, marker in PAGE_MARKERS.items():
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(
                    response.status_code,
                    200,
                    f"{path} returned {response.status_code}",
                )
                body = response.content.decode("utf-8")
                self.assertIn(marker, body)

    def test_pages_are_indexable_and_carry_metadata(self):
        for path in PAGE_MARKERS:
            with self.subTest(path=path):
                response = self.client.get(path)
                body = response.content.decode("utf-8")
                self.assertIn('content="index, follow"', body)
                self.assertIn('<link rel="canonical"', body)
                self.assertIn('<meta name="description"', body)
                self.assertIn('property="og:title"', body)
                self.assertIn('property="og:image"', body)
                self.assertNotIn("<title></title>", body)

    def test_headers_advertise_indexing(self):
        response = self.client.get("/")
        self.assertEqual(response["X-Robots-Tag"], "index, follow")

    def test_open_graph_metadata_is_absolute(self):
        """Social crawlers fetch og:image and og:url without a page context.

        A relative path is silently dropped, so both tags must always be
        absolute — and on the site's own domain.
        """
        body = self.client.get("/").content.decode("utf-8")
        self.assertIn('property="og:image" content="https://', body)
        self.assertIn('property="og:url" content="https://', body)
        self.assertIn('name="twitter:image" content="https://', body)

    @override_settings(SITE_URL="", EDEN_SYS_DOMAIN="")
    def test_the_request_host_is_the_last_resort_origin(self):
        """With no configured address the site falls back to the request host.

        This is the local-development and ``/site/`` preview path; it is only
        reached when neither SITE_URL nor EDEN_SYS_DOMAIN is set.
        """
        body = self.client.get("/").content.decode("utf-8")
        self.assertIn('property="og:url" content="http://testserver/"', body)
        self.assertIn('property="og:image" content="http://testserver/', body)

    @override_settings(SITE_URL="https://ecreativesys.com")
    def test_canonical_and_open_graph_prefer_the_configured_origin(self):
        body = self.client.get("/features/").content.decode("utf-8")
        self.assertIn(
            '<link rel="canonical" href="https://ecreativesys.com/features/">',
            body,
        )
        self.assertIn(
            'property="og:url" content="https://ecreativesys.com/features/"',
            body,
        )
        # The exact file name is hashed by ManifestStaticFilesStorage in
        # production, so only the origin and the asset name are asserted.
        self.assertIn(
            'property="og:image" content="https://ecreativesys.com/static/',
            body,
        )
        self.assertIn("og-image", body)

    @override_settings(SITE_URL="https://ecreativesys.com/")
    def test_a_trailing_slash_in_site_url_does_not_double_up(self):
        body = self.client.get("/contact/").content.decode("utf-8")
        self.assertIn(
            '<link rel="canonical" href="https://ecreativesys.com/contact/">', body
        )
        self.assertNotIn("ecreativesys.com//", body)

    def test_home_states_the_headline_and_both_primary_calls_to_action(self):
        response = self.client.get("/")
        self.assertContains(
            response,
            "Smarter Boarding. Safer Students. Simpler School Operations.",
        )
        self.assertContains(response, "Request a Demo")
        self.assertContains(response, "Contact Us")

    def test_no_page_promotes_a_school_as_owner_or_endorsement(self):
        """"Atebubu" must never appear on the public site.

        The pilot school is a customer, not a brand, owner or endorsement.
        """
        for path in PAGE_MARKERS:
            with self.subTest(path=path):
                body = self.client.get(path).content.decode("utf-8")
                self.assertNotIn("Atebubu", body)
                self.assertNotIn("ATSEC", body)
                self.assertNotIn("Pilot (", body)

    def test_contact_details_are_published(self):
        for path in ("/", "/contact/", "/request-a-demo/"):
            with self.subTest(path=path):
                body = self.client.get(path).content.decode("utf-8")
                self.assertIn(CONTACT_PHONE, body)
                self.assertIn(CONTACT_EMAIL, body)

    def test_footer_attribution_is_verbatim(self):
        for path in PAGE_MARKERS:
            with self.subTest(path=path):
                body = self.client.get(path).content.decode("utf-8")
                self.assertIn(ATTRIBUTION, body)

    def test_price_figures_are_absent_while_unapproved(self):
        """No figure may be published before pricing is approved."""
        response = self.client.get("/pricing/")
        self.assertContains(response, "Contact us for pricing")
        body = response.content.decode("utf-8")
        for amount in ("GH₵", "GHS", "GH¢", "$", "₵"):
            with self.subTest(amount=amount):
                self.assertNotIn(amount, body)

    def test_no_external_resources_are_referenced(self):
        """The pages must work under the project's restrictive CSP.

        ``default-src 'self'`` means no CDN scripts, no remote fonts, no
        third-party analytics and no externally hosted images.
        """
        forbidden = (
            "fonts.googleapis.com",
            "fonts.gstatic.com",
            "cdn.jsdelivr.net",
            "unpkg.com",
            "cdnjs.cloudflare.com",
            "google-analytics.com",
            "googletagmanager.com",
            "facebook.net",
            "<script src=\"http",
            "<link rel=\"stylesheet\" href=\"http",
        )
        for path in PAGE_MARKERS:
            body = self.client.get(path).content.decode("utf-8")
            for needle in forbidden:
                with self.subTest(path=path, needle=needle):
                    self.assertNotIn(needle, body)

    def test_accessibility_basics(self):
        body = self.client.get("/").content.decode("utf-8")
        self.assertIn('lang="en"', body)
        self.assertIn('href="#main"', body)
        self.assertIn('id="main"', body)
        # The mobile navigation must be reachable without JavaScript.
        self.assertIn('id="site-nav-toggle"', body)
        # Every image must either be decorative or described.
        self.assertNotIn('alt="undefined"', body)

    def test_headings_do_not_skip_a_level(self):
        """Accessibility checkers flag a jump from ``<h1>`` straight to ``<h3>``."""
        for path in PAGE_MARKERS:
            with self.subTest(path=path):
                body = self.client.get(path).content.decode("utf-8")
                levels = [int(level) for level in re.findall(r"<h([1-6])[ >]", body)]
                self.assertTrue(levels, "page has no headings")
                self.assertEqual(levels[0], 1, "the first heading must be an h1")
                self.assertEqual(levels.count(1), 1, "each page needs exactly one h1")
                for previous, current in zip(levels, levels[1:]):
                    self.assertLessEqual(
                        current,
                        previous + 1,
                        f"heading order jumps from h{previous} to h{current}",
                    )

    def test_login_call_to_action_is_hidden_when_unconfigured(self):
        """No school login URL is knowable, so none is invented."""
        body = self.client.get("/").content.decode("utf-8")
        self.assertNotIn(">Login</a>", body)

    @override_settings(EDEN_PORTAL_LOGIN_URL="https://ecreativesys.com/portal/")
    def test_login_call_to_action_appears_when_configured(self):
        body = self.client.get("/").content.decode("utf-8")
        self.assertIn("https://ecreativesys.com/portal/", body)

    @override_settings(EDEN_PORTAL_LOGIN_URL="https://portal.example.test/login/")
    def test_login_call_to_action_is_hidden_on_another_domain(self):
        """A link to any other hostname is suppressed, not merely discouraged."""
        body = self.client.get("/").content.decode("utf-8")
        self.assertNotIn("portal.example.test", body)
        self.assertNotIn(">Login</a>", body)

    def test_features_page_states_what_is_not_included(self):
        response = self.client.get("/features/")
        self.assertContains(response, "What is not included")
        self.assertContains(response, "No parent, guardian or student login")

    def test_every_page_lists_the_same_navigation(self):
        body = self.client.get("/").content.decode("utf-8")
        for label in (
            "Features",
            "Services",
            "How it works",
            "Pricing",
            "FAQs",
            "Contact",
        ):
            with self.subTest(label=label):
                self.assertIn(label, body)


@override_settings(ROOT_URLCONF="schoolsystem.urls_public")
class PublicUtilityEndpointTests(TestCase):
    def test_robots_txt(self):
        response = self.client.get("/robots.txt")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"], "text/plain; charset=utf-8"
        )
        body = response.content.decode("utf-8")
        self.assertIn("User-agent: *", body)
        self.assertIn("Disallow: /admin/", body)
        self.assertIn("Disallow: /login/", body)
        self.assertIn("Disallow: /verify/", body)
        self.assertIn("Sitemap: https://ecreativesys.com/sitemap.xml", body)
        self.assertIn("/sitemap.xml", body)

    def test_sitemap_lists_every_public_page(self):
        response = self.client.get("/sitemap.xml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"], "application/xml; charset=utf-8"
        )
        body = response.content.decode("utf-8")
        self.assertIn("<urlset", body)
        for path in PUBLIC_PATHS:
            with self.subTest(path=path):
                self.assertIn(f"<loc>https://ecreativesys.com{path}</loc>", body)

    def test_sitemap_excludes_staff_login(self):
        body = self.client.get("/sitemap.xml").content.decode("utf-8")
        self.assertNotIn("/login/", body)


@override_settings(ROOT_URLCONF="schoolsystem.urls_public")
class PublicErrorPageTests(TestCase):
    def test_unknown_path_renders_the_public_404(self):
        response = self.client.get("/definitely-not-a-page/")
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "We could not find that page", status_code=404)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_error_page_offers_working_routes_back(self):
        response = self.client.get("/definitely-not-a-page/")
        body = response.content.decode("utf-8")
        self.assertIn(f'href="{reverse("website:home")}"', body)
        self.assertIn(f'href="{reverse("website:contact")}"', body)


@override_settings(ROOT_URLCONF="schoolsystem.urls_public")
class PublicUrlconfIsolationTests(TestCase):
    """The public URLconf must not expose the school application."""

    def test_admin_is_not_routed(self):
        self.assertEqual(self.client.get("/admin/").status_code, 404)

    def test_staff_only_areas_are_not_routed(self):
        for path in (
            "/login/",
            "/students/",
            "/exeats/",
            "/operations/",
            "/reports/",
            "/configuration/",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_public_verification_routes_are_not_routed(self):
        self.assertEqual(self.client.get("/verify/student/abc/").status_code, 404)

    def test_verification_page_is_excluded_from_the_sitemap(self):
        body = self.client.get("/sitemap.xml").content.decode("utf-8")
        self.assertNotIn("/verify/", body)
