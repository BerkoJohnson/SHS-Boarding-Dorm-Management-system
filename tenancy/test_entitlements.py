from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, override_settings
from django.test.client import RequestFactory

from tenancy.entitlements import (
    entitlement_value,
    ensure_quota_available,
    feature_enabled,
    quota_limit,
    tenant_feature_required,
)


class TenantEntitlementTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.entitlements._current_schema_name", return_value="school")
    @patch(
        "tenancy.entitlements._subscription",
        return_value=SimpleNamespace(features={}, plan_tier="pilot"),
    )
    def test_missing_override_uses_plan_defaults(self, _subscription, _schema):
        self.assertTrue(feature_enabled("sms_dispatch"))
        self.assertEqual(entitlement_value("max_students"), 2000)

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.entitlements._current_schema_name", return_value="school")
    @patch(
        "tenancy.entitlements._subscription",
        return_value=SimpleNamespace(
            features={"sms_dispatch": False},
            plan_tier="pilot",
        ),
    )
    def test_explicit_feature_disable_overrides_plan_default(
        self, _subscription, _schema
    ):
        self.assertFalse(feature_enabled("sms_dispatch"))

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.entitlements._current_schema_name", return_value="school")
    @patch(
        "tenancy.entitlements._subscription",
        return_value=SimpleNamespace(
            features={"max_students": 2, "max_sms_monthly": 0},
            plan_tier="free",
        ),
    )
    def test_quotas_block_overage_and_treat_zero_sms_as_unlimited(
        self, _subscription, _schema
    ):
        self.assertEqual(quota_limit("max_students"), 2)
        ensure_quota_available("max_students", current=1, additional=1)
        with self.assertRaisesMessage(
            ValidationError,
            "The tenant students limit of 2 has been reached.",
        ):
            ensure_quota_available("max_students", current=2)
        self.assertIsNone(quota_limit("max_sms_monthly"))

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.entitlements._current_schema_name", return_value="school")
    @patch(
        "tenancy.entitlements._subscription",
        return_value=SimpleNamespace(
            features={"advanced_reports": False},
            plan_tier="enterprise",
        ),
    )
    def test_feature_decorator_denies_disabled_entitlement(
        self, _subscription, _schema
    ):
        @tenant_feature_required("advanced_reports")
        def report_view(_request):
            return "available"

        with self.assertRaises(PermissionDenied):
            report_view(RequestFactory().get("/reports/"))
