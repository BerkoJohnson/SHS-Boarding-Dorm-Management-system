"""Assert that every piece of published copy actually reaches a page.

Django templates fail silently on a missing key or a misspelled attribute, so a
renamed dictionary key renders an empty element and nothing complains. These
tests close that hole: each page is checked against the content module it
renders from, entry by entry.

If one of these fails, either the template references a key that no longer
exists, or the copy was changed without the template being updated.
"""
from __future__ import annotations

import re

from django.test import TestCase, override_settings
from django.utils.html import escape
from django.utils.text import slugify

from website import content, legal, pricing

CONTACTS = (
    "Eden Creative Systems",
    "ecreativesys.com",
    "+233 24 816 0391",
    "berkojohnson@gmail.com",
    "Developed by Eden Creative Systems (Edensys)",
)


@override_settings(ROOT_URLCONF="schoolsystem.urls_public")
class ContentRenderingTests(TestCase):
    def render(self, path: str) -> str:
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, path)
        return response.content.decode("utf-8")

    def assert_rendered(self, body: str, text: str, where: str = "") -> None:
        """Compare against the HTML-escaped form of ``text``.

        Content is authored with ordinary punctuation (including apostrophes),
        which autoescaping rewrites on output; escaping the expectation keeps
        the comparison honest instead of forcing the copy to avoid apostrophes.
        """
        self.assertIn(escape(text), body, f"{where}: {text!r} not rendered")

    # ------------------------------------------------------------- home -----
    def test_home_renders_every_section_it_imports(self):
        body = self.render("/")

        for text in (content.HEADLINE, content.SUBHEADLINE, content.PRODUCT_SUMMARY):
            self.assert_rendered(body, text, "home")

        for item in content.CHALLENGES:
            self.assert_rendered(body, item["title"], "home/challenges")
            self.assert_rendered(body, item["body"], "home/challenges")

        for item in content.BENEFITS:
            self.assert_rendered(body, item["title"], "home/benefits")
            self.assert_rendered(body, item["body"], "home/benefits")

        for feature in content.FEATURES:
            self.assert_rendered(body, feature["number"], "home/features")
            self.assert_rendered(body, feature["title"], "home/features")
            self.assert_rendered(body, feature["summary"], "home/features")
            self.assert_rendered(body, feature["benefit"], "home/features")

        for step in content.STEPS:
            self.assert_rendered(body, step["title"], "home/steps")
            self.assert_rendered(body, step["body"], "home/steps")

        for item in content.FAQS[:4]:
            self.assert_rendered(body, item["question"], "home/faq-preview")

        for fact in content.TRUST_FACTS[:6]:
            self.assert_rendered(body, fact["title"], "home/trust")

        for link in content.NAV_LINKS:
            self.assert_rendered(body, link["label"], "home/nav")

        for marker in CONTACTS:
            self.assert_rendered(body, marker, "home/footer")

    def test_home_renders_the_product_preview(self):
        body = self.render("/")
        preview = content.PRODUCT_PREVIEW

        self.assert_rendered(body, preview["roll_call"]["title"], "preview")
        self.assert_rendered(body, preview["roll_call"]["meta"], "preview")
        for stat in preview["roll_call"]["stats"]:
            self.assert_rendered(body, stat["label"], "preview/stat")
            self.assert_rendered(body, stat["value"], "preview/stat")
        for row in preview["roll_call"]["rows"]:
            self.assert_rendered(body, row["name"], "preview/row")
            self.assert_rendered(body, row["room"], "preview/row")
            self.assert_rendered(body, row["status"], "preview/row")

        self.assert_rendered(body, preview["exeat"]["title"], "preview/exeat")
        self.assert_rendered(body, preview["exeat"]["meta"], "preview/exeat")
        for row in preview["exeat"]["rows"]:
            self.assert_rendered(body, row["reference"], "preview/exeat")
            self.assert_rendered(body, row["detail"], "preview/exeat")
            self.assert_rendered(body, row["window"], "preview/exeat")
            self.assert_rendered(body, row["status"], "preview/exeat")

        for note in preview["notifications"]:
            self.assert_rendered(body, note, "preview/notifications")

        # The mock interface must say that it is a mock.
        self.assert_rendered(body, preview["disclaimer"], "preview/disclaimer")

    # --------------------------------------------------------- features -----
    def test_features_renders_every_capability_and_point(self):
        body = self.render("/features/")

        for feature in content.FEATURES:
            self.assert_rendered(body, feature["key"], "features/anchor")
            self.assert_rendered(body, feature["number"], "features")
            self.assert_rendered(body, feature["title"], "features")
            self.assert_rendered(body, feature["summary"], "features")
            self.assert_rendered(body, feature["benefit"], "features")
            for point in feature["points"]:
                self.assert_rendered(body, point, "features/points")

        for limit in content.NOT_INCLUDED:
            self.assert_rendered(body, limit, "features/limits")

    # --------------------------------------------------------- services -----
    def test_services_renders_every_item_and_the_provisional_note(self):
        body = self.render("/services/")

        self.assert_rendered(body, content.SERVICES_INTRO, "services")
        self.assert_rendered(body, content.SERVICES_PROVISIONAL_NOTE, "services")

        for item in content.SERVICES:
            self.assert_rendered(body, item["title"], "services")
            self.assert_rendered(body, item["body"], "services")

    # ---------------------------------------------------- how it works -----
    def test_how_it_works_renders_stages_preparation_and_notes(self):
        body = self.render("/how-it-works/")

        for step in content.STEPS:
            self.assert_rendered(body, step["title"], "how-it-works")
            self.assert_rendered(body, step["body"], "how-it-works")

        for item in content.ROLLOUT_PREPARATION:
            self.assert_rendered(body, item["title"], "how-it-works")
            self.assert_rendered(body, item["body"], "how-it-works")

        for item in content.ROLLOUT_NOTES:
            self.assert_rendered(body, item["title"], "how-it-works")
            self.assert_rendered(body, item["body"], "how-it-works")

    # ------------------------------------------------------------ faq ------
    def test_faq_renders_every_question_and_answer(self):
        body = self.render("/faqs/")
        for item in content.FAQS:
            self.assert_rendered(body, item["question"], "faq")
            self.assert_rendered(body, item["answer"], "faq")

    # -------------------------------------------------------- contact ------
    def test_contact_renders_every_channel_and_guidance_item(self):
        body = self.render("/contact/")

        for text in (content.CONTACT_INTRO, content.CONTACT_RESPONSE_NOTE):
            self.assert_rendered(body, text, "contact")

        for item in content.CONTACT_HELPFUL:
            self.assert_rendered(body, item, "contact")

        for marker in CONTACTS:
            self.assert_rendered(body, marker, "contact")

    # -------------------------------------------------------- pricing ------
    def test_pricing_renders_structure_and_separate_charges(self):
        body = self.render("/pricing/")

        for text in (
            pricing.PUBLIC_DISCLAIMER,
            pricing.CONTACT_FOR_PRICING_HEADLINE,
            pricing.CONTACT_FOR_PRICING_BODY,
        ):
            self.assert_rendered(body, text, "pricing")

        for item in pricing.COMMERCIAL_STRUCTURE:
            self.assert_rendered(body, item["title"], "pricing")
            self.assert_rendered(body, item["body"], "pricing")

        for item in pricing.BILLING_QUESTIONS:
            self.assert_rendered(body, item, "pricing")

        for item in pricing.NOT_INCLUDED_IN_SUBSCRIPTION:
            self.assert_rendered(body, item, "pricing")

    # -------------------------------------------------------- privacy ------
    def test_privacy_renders_every_section_and_trust_statement(self):
        body = self.render("/privacy-policy/")

        self.assert_rendered(body, legal.PRIVACY_INTRO, "privacy")
        self.assert_rendered(body, legal.LEGAL_REVIEW_NOTICE, "privacy")
        self.assert_rendered(body, legal.LAST_UPDATED, "privacy")

        for section in legal.PRIVACY_SECTIONS:
            self.assert_rendered(body, section["heading"], "privacy")
            self.assert_rendered(body, section["body"], "privacy")

        for fact in content.TRUST_FACTS:
            self.assert_rendered(body, fact["title"], "privacy")
            self.assert_rendered(body, fact["body"], "privacy")

        self.assert_rendered(body, content.TRUST_LIMITS, "privacy")

    # ---------------------------------------------------------- terms ------
    def test_terms_renders_every_section_and_withheld_commitment(self):
        body = self.render("/terms-of-service/")

        self.assert_rendered(body, legal.TERMS_INTRO, "terms")
        self.assert_rendered(body, legal.LEGAL_REVIEW_NOTICE, "terms")
        self.assert_rendered(body, legal.LAST_UPDATED, "terms")

        for section in legal.TERMS_SECTIONS:
            self.assert_rendered(body, section["heading"], "terms")
            self.assert_rendered(body, section["body"], "terms")

        for item in legal.TERMS_WITHHELD:
            self.assert_rendered(body, item, "terms")

    # ---------------------------------------------------- legal anchors -----
    LEGAL_PAGES = (
        ("/privacy-policy/", legal.PRIVACY_SECTIONS),
        ("/terms-of-service/", legal.TERMS_SECTIONS),
    )

    def test_legal_contents_links_every_heading_and_nothing_else(self):
        """Each contents list must mirror the article, with no dead links.

        ``slugify`` is applied to display headings at render time, so a copy
        edit can silently produce a duplicate or empty ``id``. This asserts the
        rendered contract instead of trusting the filter: every article heading
        has one unique id, and the sidebar's in-page links match that set
        exactly (all headings, nothing missing, no targets that do not exist).
        """
        for path, sections in self.LEGAL_PAGES:
            with self.subTest(path=path):
                body = self.render(path)

                article = re.search(r"<article\b.*?</article>", body, re.S)
                aside = re.search(r"<aside\b.*?</aside>", body, re.S)
                self.assertIsNotNone(article, f"{path}: no article block")
                self.assertIsNotNone(aside, f"{path}: no sidebar block")

                heading_ids = re.findall(r'<h2 id="([^"]+)"', article.group(0))
                heading_count = len(re.findall(r"<h2\b", article.group(0)))
                toc_targets = re.findall(r'href="#([^"]+)"', aside.group(0))

                self.assertEqual(
                    len(heading_ids),
                    heading_count,
                    f"{path}: every article heading needs a non-empty id",
                )
                self.assertEqual(
                    len(set(heading_ids)),
                    len(heading_ids),
                    f"{path}: duplicate heading ids",
                )
                self.assertEqual(
                    sorted(toc_targets),
                    sorted(heading_ids),
                    f"{path}: contents links do not match the article headings",
                )

                for section in sections:
                    slug = slugify(section["heading"])
                    self.assertIn(
                        slug,
                        heading_ids,
                        f"{path}: no id for {section['heading']!r}",
                    )
                    self.assertIn(
                        slug,
                        toc_targets,
                        f"{path}: no contents link for {section['heading']!r}",
                    )

    # ----------------------------------------------------------- meta ------
    def test_every_page_has_a_meta_entry(self):
        for key in (
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
            with self.subTest(page=key):
                meta = content.PAGE_META.get(key)
                self.assertIsNotNone(meta, f"PAGE_META is missing {key!r}")
                self.assertTrue(meta.get("title"))
                self.assertTrue(meta.get("description"))
                # Long descriptions are truncated by search engines.
                self.assertLessEqual(len(meta["description"]), 320)
                # An em dash in a <title> mixes two topics badly; a pipe is fine.
                self.assertNotIn("—", meta["title"])

    def test_pages_do_not_reuse_a_title_or_description(self):
        titles = [meta["title"] for meta in content.PAGE_META.values()]
        descriptions = [meta["description"] for meta in content.PAGE_META.values()]
        self.assertEqual(len(titles), len(set(titles)))
        self.assertEqual(len(descriptions), len(set(descriptions)))
