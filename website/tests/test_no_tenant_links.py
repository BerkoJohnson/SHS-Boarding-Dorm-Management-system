"""The marketing site must never link to a school's hostname.

Every school is served from its own hostname. Anything the public site publishes
— an anchor, an image, a canonical link, a social preview, a sitemap entry or
the ``Sitemap:`` line in robots.txt — must be either relative, one of the
sanctioned contact channels, or the main Eden Creative Systems domain. A tenant
hostname anywhere in the output would hand visitors and crawlers a school's
address, so these tests enumerate every URL in the rendered pages and reject
anything else.

The same rule is enforced in production by ``website/addressing.py`` (which
drops any link that is not on the site's own origin) and reported by
``website/checks.py``.
"""
from __future__ import annotations

import re
from html import unescape
from urllib.parse import urlsplit

from django.test import SimpleTestCase, TestCase, override_settings

from website import addressing, checks
from website.tests.test_public_pages import PUBLIC_PATHS

MAIN_DOMAIN = "ecreativesys.com"
MAIN_ORIGIN = f"https://{MAIN_DOMAIN}"

# The one external host the site may reference: the WhatsApp click-to-chat
# endpoint for the published contact number. It names the business, never a
# school.
ALLOWED_EXTERNAL = re.compile(r"^https://wa\.me/\d+$")

URL_ATTRIBUTES = re.compile(r'(?:href|src)="([^"]*)"')
CONTENT_ATTRIBUTES = re.compile(r'content="([^"]*)"')


def _looks_like_a_url(value: str) -> bool:
    """Skip prose in ``content`` attributes (``name="description"``)."""
    return value.startswith(("http://", "https://", "//", "/", "#"))


@override_settings(ROOT_URLCONF="schoolsystem.urls_public")
class NoTenantLinksTests(TestCase):
    """Audit every URL that the public pages emit."""

    def _urls_in(self, body: str) -> list[str]:
        values = [unescape(value) for value in URL_ATTRIBUTES.findall(body)]
        values += [
            unescape(value)
            for value in CONTENT_ATTRIBUTES.findall(body)
            if _looks_like_a_url(unescape(value))
        ]
        return values

    def _assert_allowed(self, path: str, url: str) -> None:
        if not url or url.startswith("#"):
            return
        if url.startswith(("tel:", "mailto:")):
            return
        if url.startswith("/") and not url.startswith("//"):
            return  # stays on whichever host served the page
        if ALLOWED_EXTERNAL.match(url):
            return
        parts = urlsplit(url)
        if (
            parts.scheme in {"http", "https"}
            and (parts.hostname or "").lower() == MAIN_DOMAIN
        ):
            return
        self.fail(f"{path} publishes a URL that is not on {MAIN_DOMAIN}: {url}")

    def test_no_page_links_to_another_hostname(self):
        for path in PUBLIC_PATHS:
            with self.subTest(path=path):
                body = self.client.get(path).content.decode("utf-8")
                urls = self._urls_in(body)
                self.assertTrue(urls, f"{path} rendered no URLs at all")
                for url in urls:
                    self._assert_allowed(path, url)

    def test_no_subdomain_of_the_main_domain_appears(self):
        """Atebubu SHS is a pilot school, never an address on this site."""
        for path in PUBLIC_PATHS:
            with self.subTest(path=path):
                body = self.client.get(path).content.decode("utf-8").lower()
                self.assertNotIn("atebubu", body)
                # Nothing may look like `<something>.ecreativesys.com`.
                self.assertNotRegex(body, r"[\w-]+\.ecreativesys\.com")

    def test_every_canonical_url_names_the_main_domain(self):
        for path in PUBLIC_PATHS:
            with self.subTest(path=path):
                body = self.client.get(path).content.decode("utf-8")
                self.assertIn(
                    f'<link rel="canonical" href="{MAIN_ORIGIN}{path}">', body
                )
                self.assertIn(
                    f'property="og:url" content="{MAIN_ORIGIN}{path}"', body
                )

    def test_sitemap_and_robots_stay_on_the_main_domain(self):
        sitemap = self.client.get("/sitemap.xml").content.decode("utf-8")
        located = re.findall(r"<loc>([^<]+)</loc>", sitemap)
        self.assertEqual(len(located), len(PUBLIC_PATHS))
        for url in located:
            self.assertTrue(url.startswith(f"{MAIN_ORIGIN}/"), url)

        robots = self.client.get("/robots.txt").content.decode("utf-8")
        self.assertIn(f"Sitemap: {MAIN_ORIGIN}/sitemap.xml", robots)

    @override_settings(SITE_URL="", EDEN_SYS_DOMAIN=MAIN_DOMAIN)
    def test_the_main_domain_is_used_even_when_site_url_is_unset(self):
        """EDEN_SYS_DOMAIN alone keeps every published URL on the main domain."""
        body = self.client.get("/pricing/").content.decode("utf-8")
        self.assertIn(f'<link rel="canonical" href="{MAIN_ORIGIN}/pricing/">', body)

    def test_structured_data_names_the_main_domain(self):
        """JSON-LD is not a `content` attribute, so it is checked directly."""
        body = self.client.get("/").content.decode("utf-8")
        match = re.search(r'"url":\s*"([^"]+)"', body)
        self.assertIsNotNone(match, "the home page publishes no Organization url")
        self.assertEqual(match.group(1), MAIN_ORIGIN)

    @override_settings(EDEN_PORTAL_LOGIN_URL=f"https://atebubu.{MAIN_DOMAIN}/login/")
    def test_a_school_hostname_is_never_published_as_the_login_link(self):
        for path in PUBLIC_PATHS:
            with self.subTest(path=path):
                body = self.client.get(path).content.decode("utf-8")
                self.assertNotIn(f"atebubu.{MAIN_DOMAIN}", body)

    @override_settings(EDEN_PORTAL_LOGIN_URL=f"https://www.{MAIN_DOMAIN}/login/")
    def test_a_subdomain_of_the_main_domain_is_not_used_either(self):
        body = self.client.get("/").content.decode("utf-8")
        self.assertNotIn(f"www.{MAIN_DOMAIN}", body)

    @override_settings(EDEN_PORTAL_LOGIN_URL=f"{MAIN_ORIGIN}/login/")
    def test_a_link_on_the_main_domain_is_published(self):
        """The guard hides off-domain links, it does not disable the feature."""
        body = self.client.get("/").content.decode("utf-8")
        self.assertIn(f'href="{MAIN_ORIGIN}/login/"', body)


class AddressingTests(SimpleTestCase):
    """The rule itself, independent of any rendered page."""

    @override_settings(SITE_URL=MAIN_ORIGIN, EDEN_SYS_DOMAIN=MAIN_DOMAIN)
    def test_links_that_are_not_the_site_own_origin_are_dropped(self):
        for url in (
            f"https://atebubu.{MAIN_DOMAIN}/",
            f"https://www.{MAIN_DOMAIN}/",
            "https://evil.test/",
            f"//atebubu.{MAIN_DOMAIN}/",
            f"http://{MAIN_DOMAIN}.evil.test/",
            "javascript:alert(1)",
            "data:text/html,<script>alert(1)</script>",
        ):
            with self.subTest(url=url):
                self.assertEqual(addressing.own_origin_link(url), "")

    @override_settings(SITE_URL=MAIN_ORIGIN, EDEN_SYS_DOMAIN=MAIN_DOMAIN)
    def test_own_origin_links_are_kept_unchanged(self):
        for url in (f"{MAIN_ORIGIN}/login/", f"https://{MAIN_DOMAIN}/login/", "/login/"):
            with self.subTest(url=url):
                self.assertEqual(addressing.own_origin_link(url), url)

    @override_settings(SITE_URL="", EDEN_SYS_DOMAIN=MAIN_DOMAIN)
    def test_the_canonical_origin_falls_back_to_the_main_domain(self):
        self.assertEqual(addressing.canonical_origin(), MAIN_ORIGIN)
        self.assertEqual(addressing.absolute(None, "/features/"), f"{MAIN_ORIGIN}/features/")

    @override_settings(SITE_URL="", EDEN_SYS_DOMAIN="")
    def test_with_no_configured_address_there_is_no_origin(self):
        self.assertEqual(addressing.canonical_origin(), "")
        self.assertEqual(addressing.own_origin_link("https://evil.test/"), "")

    @override_settings(SITE_URL=f"{MAIN_ORIGIN}/", EDEN_SYS_DOMAIN=MAIN_DOMAIN)
    def test_a_trailing_slash_in_site_url_is_ignored(self):
        self.assertEqual(addressing.canonical_origin(), MAIN_ORIGIN)


class DomainHygieneCheckTests(SimpleTestCase):
    """Misconfiguration is reported, not silently shipped."""

    @override_settings(EDEN_PORTAL_LOGIN_URL=f"https://atebubu.{MAIN_DOMAIN}/login/")
    def test_an_off_domain_login_url_is_reported(self):
        messages = checks.portal_login_url_is_on_the_site_own_domain(None)
        self.assertEqual(
            [message.id for message in messages], [checks.PORTAL_LOGIN_OFF_DOMAIN]
        )

    @override_settings(EDEN_PORTAL_LOGIN_URL=f"{MAIN_ORIGIN}/login/")
    def test_an_own_domain_login_url_is_not_reported(self):
        self.assertEqual(
            checks.portal_login_url_is_on_the_site_own_domain(None), []
        )

    @override_settings(EDEN_PORTAL_LOGIN_URL="")
    def test_an_unset_login_url_is_not_reported(self):
        self.assertEqual(
            checks.portal_login_url_is_on_the_site_own_domain(None), []
        )

    @override_settings(SITE_URL=f"https://www.{MAIN_DOMAIN}", EDEN_SYS_DOMAIN=MAIN_DOMAIN)
    def test_a_site_url_on_another_hostname_is_reported(self):
        messages = checks.site_url_names_the_main_domain(None)
        self.assertEqual(
            [message.id for message in messages], [checks.SITE_URL_OFF_MAIN_DOMAIN]
        )

    @override_settings(SITE_URL=MAIN_ORIGIN, EDEN_SYS_DOMAIN=MAIN_DOMAIN)
    def test_a_matching_site_url_is_not_reported(self):
        self.assertEqual(checks.site_url_names_the_main_domain(None), [])
