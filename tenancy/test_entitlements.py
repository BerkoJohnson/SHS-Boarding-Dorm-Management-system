from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.client import RequestFactory

from tenancy.entitlements import (
    entitlement_value,
    ensure_quota_available,
    feature_enabled,
    quota_guard,
    quota_limit,
    quota_lock_key,
    reserve_quota,
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


class QuotaLockKeyTests(SimpleTestCase):
    """The advisory-lock key must be deterministic and tenant/quota specific."""

    def test_lock_key_is_stable_and_distinct(self):
        first = quota_lock_key("school_a", "max_students")
        self.assertEqual(first, quota_lock_key("school_a", "max_students"))
        self.assertNotEqual(first, quota_lock_key("school_b", "max_students"))
        self.assertNotEqual(first, quota_lock_key("school_a", "max_staff"))
        self.assertIsInstance(first, int)
        self.assertTrue(-(2**63) <= first < 2**63)


class QuotaGuardTransactionTests(TestCase):
    """Behaviour of the transaction-scoped quota guard helpers."""

    @override_settings(TENANCY_ENABLED=True)
    def test_reserve_quota_raises_when_limit_reached(self):
        with patch("tenancy.entitlements.quota_limit", return_value=1):
            with self.assertRaises(ValidationError):
                with reserve_quota("max_students", lambda: 1):
                    pass

    @override_settings(TENANCY_ENABLED=True)
    def test_reserve_quota_allows_under_limit(self):
        entered = False
        with patch("tenancy.entitlements.quota_limit", return_value=5):
            with reserve_quota("max_students", lambda: 1):
                entered = True
        self.assertTrue(entered)

    @override_settings(TENANCY_ENABLED=False)
    def test_quota_guard_runs_block_when_tenancy_disabled(self):
        entered = False
        with quota_guard("max_students"):
            entered = True
        self.assertTrue(entered)
