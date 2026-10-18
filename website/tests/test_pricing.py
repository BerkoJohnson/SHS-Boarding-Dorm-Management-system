"""Guard tests for the published pricing rules.

These are the tests that must keep passing after pricing is eventually
approved. Their purpose is to make it impossible to publish a figure by
accident, and impossible for internal commercial data to reach the public site.
"""
from __future__ import annotations

import inspect
from unittest import mock

from django.test import TestCase, override_settings

from website import pricing

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

# Identifiers and values that exist only in the internal commercial model
# (tenancy/models.py and tenancy/management/commands/tenant_subscription.py).
# None of them may ever appear in public HTML.
INTERNAL_MARKERS = (
    "infra_cost_basis",
    "infra_margin_percent",
    "sms_cost_basis",
    "sms_margin_percent",
    "rate_per_sms",
    "margin_percent",
    "cost_basis",
    "TenantSubscription",
    "TenantInvoice",
    "PLAN_PRESETS",
    "PLAN_DEFAULTS",
    # SMSOnlineGH credit unit costs and package sizes.
    "0.03257",
    "0.03082",
    "0.02952",
    "0.02851",
    "0.02760",
    "0.02620",
    "0.02560",
    "0.02460",
    "0.02400",
    "208333",
    "39062",
    "81300",
)


@override_settings(ROOT_URLCONF="schoolsystem.urls_public")
class PricingPublishGateTests(TestCase):
    def test_pricing_is_unpublished_by_default(self):
        self.assertFalse(pricing.pricing_is_published())

    def test_pricing_page_offers_contact_instead_of_figures(self):
        response = self.client.get("/pricing/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Contact us for pricing")
        body = response.content.decode("utf-8")
        for symbol in ("₵", "GH₵", "GHS", "$"):
            with self.subTest(symbol=symbol):
                self.assertNotIn(symbol, body)

    @override_settings(PRICING_PUBLIC_ENABLED=True)
    def test_the_flag_alone_is_not_sufficient_to_publish(self):
        """Approval requires both the switch and an approved price list."""
        self.assertFalse(pricing.pricing_is_published())
        response = self.client.get("/pricing/")
        self.assertContains(response, "Contact us for pricing")
        self.assertNotContains(response, "GH₵")

    @override_settings(PRICING_PUBLIC_ENABLED=False)
    def test_an_approved_price_list_is_not_published_without_the_switch(self):
        with mock.patch.object(
            pricing,
            "APPROVED_PRICE_LIST",
            (
                {
                    "name": "Test Tier",
                    "summary": "Example",
                    "base_fee": "GH₵1.00 per term",
                    "per_student": None,
                    "limits": None,
                    "includes": (),
                    "excludes": (),
                },
            ),
        ):
            self.assertFalse(pricing.pricing_is_published())
            self.assertNotContains(self.client.get("/pricing/"), "Test Tier")

    @override_settings(PRICING_PUBLIC_ENABLED=True)
    def test_an_approved_price_list_is_rendered_when_both_are_present(self):
        with mock.patch.object(
            pricing,
            "APPROVED_PRICE_LIST",
            (
                {
                    "name": "Test Tier",
                    "summary": "Example tier",
                    "base_fee": "GH₵1.00 per term",
                    "per_student": "GH₵2.00 per student per month",
                    "limits": "Up to 100 students",
                    "includes": ("Roll call", "Exeats and gate"),
                    "excludes": ("Guardian text messages",),
                },
            ),
        ):
            self.assertTrue(pricing.pricing_is_published())
            response = self.client.get("/pricing/")
            self.assertContains(response, "Test Tier")
            self.assertContains(response, "GH₵1.00 per term")
            self.assertContains(response, "GH₵2.00 per student per month")
            self.assertContains(response, "Up to 100 students")
            self.assertContains(response, "Roll call")
            self.assertContains(response, "Guardian text messages")
            # The "contact us" fallback must not appear once figures are live.
            self.assertNotContains(response, "Contact us for pricing")

    def test_plans_are_only_offered_when_published(self):
        context = pricing.get_pricing_context()
        self.assertEqual(context["plans"], [])
        self.assertEqual(context["status"], pricing.PRICING_STATUS_AWAITING_APPROVAL)
        self.assertFalse(context["published"])

    def test_pilot_arrangement_is_described_without_publishing_its_terms(self):
        body = self.client.get("/pricing/").content.decode("utf-8")
        self.assertIn("pilot", body.lower())
        self.assertNotIn("2,500", body)
        self.assertNotIn("2500", body)


@override_settings(ROOT_URLCONF="schoolsystem.urls_public")
class InternalCommercialDataLeakTests(TestCase):
    def test_no_public_page_exposes_internal_commercial_identifiers(self):
        for path in PUBLIC_PATHS:
            body = self.client.get(path).content.decode("utf-8")
            for marker in INTERNAL_MARKERS:
                with self.subTest(path=path, marker=marker):
                    self.assertNotIn(marker, body)

    def test_pricing_module_does_not_import_the_tenant_commercial_models(self):
        """The published price list must never be derived from live contracts.

        ``TenantSubscription`` carries each school's cost basis and margin, so
        reading it from public code would risk exposing it.
        """
        source = inspect.getsource(pricing)
        code_lines = [
            line
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        joined = "\n".join(code_lines)
        self.assertNotIn("tenancy", joined)
        self.assertNotIn("TenantSubscription", joined)

    def test_public_views_do_not_read_tenant_commercial_models(self):
        from website import views

        source = inspect.getsource(views)
        self.assertNotIn("TenantSubscription", source)
        self.assertNotIn("TenantInvoice", source)
