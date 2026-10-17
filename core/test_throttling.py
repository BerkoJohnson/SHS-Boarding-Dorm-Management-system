from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.throttling import client_ip, is_rate_limited


class RateLimitTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_blocks_after_the_limit_within_the_window(self):
        for _ in range(3):
            self.assertFalse(is_rate_limited("scope", "identity", 3, 60))
        self.assertTrue(is_rate_limited("scope", "identity", 3, 60))

    def test_scopes_and_identities_are_independent(self):
        self.assertFalse(is_rate_limited("a", "ip", 1, 60))
        self.assertTrue(is_rate_limited("a", "ip", 1, 60))
        self.assertFalse(is_rate_limited("a", "other-ip", 1, 60))
        self.assertFalse(is_rate_limited("b", "ip", 1, 60))

    def test_non_positive_limit_or_window_disables_the_check(self):
        for _ in range(50):
            self.assertFalse(is_rate_limited("scope", "ip", 0, 60))
            self.assertFalse(is_rate_limited("scope", "ip", 3, 0))

    def test_client_ip_ignores_a_spoofed_forwarded_prefix(self):
        factory = RequestFactory()

        # A client can prepend arbitrary X-Forwarded-For entries, so the value
        # our own proxy appended (the rightmost one) is the trustworthy address.
        forwarded = factory.get(
            "/",
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.7",
        )
        self.assertEqual(client_ip(forwarded), "198.51.100.7")

        direct = factory.get("/", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(client_ip(direct), "10.0.0.1")

    @override_settings(TRUSTED_PROXY_COUNT=2)
    def test_client_ip_honours_the_trusted_proxy_count(self):
        factory = RequestFactory()
        forwarded = factory.get(
            "/",
            REMOTE_ADDR="10.0.0.1",
            HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.7, 10.0.0.1",
        )
        self.assertEqual(client_ip(forwarded), "198.51.100.7")
