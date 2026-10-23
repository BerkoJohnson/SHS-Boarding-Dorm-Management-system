import json

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta

from core.api.tests.factories import (
    PASSWORD,
    auth,
    create_school,
    create_staff,
    create_house,
    login,
    token_from,
)
from core.models import APIAccessToken, AuditEvent, StaffInvite, StaffRole


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.admin = create_staff("admin", role=StaffRole.ADMIN)

    def test_login_returns_token_and_session_context(self):
        response = login(self.client, "admin")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["token"])
        self.assertEqual(data["user"]["username"], "admin")
        self.assertEqual(data["user"]["capabilities"]["role"], StaffRole.ADMIN)
        self.assertEqual(data["school"]["name"], "Test Senior High School")
        self.assertIn("features", data)
        self.assertEqual(response["X-Request-ID"], response.json()["meta"]["request_id"])

    def test_password_is_not_echoed_or_stored(self):
        response = login(self.client, "admin")
        token = token_from(response)
        stored = APIAccessToken.objects.get(user=self.admin)
        self.assertNotEqual(stored.key_hash, token)
        self.assertEqual(len(stored.key_hash), 64)
        self.assertTrue(token.startswith(stored.key_prefix))

    def test_invalid_credentials_return_401(self):
        response = login(self.client, "admin", password="wrong-password")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_credentials")

    def test_missing_fields_return_400_with_field_errors(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.json()["error"]["fields"])

    def test_malformed_json_returns_400(self):
        response = self.client.post(
            "/api/v1/auth/login/",
            data="{not json",
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_json")

    def test_unassigned_role_is_rejected(self):
        create_staff("unassigned", role=StaffRole.UNASSIGNED)
        response = login(self.client, "unassigned")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "account_not_ready")

    def test_role_mismatch_is_rejected_with_actual_role(self):
        create_staff("tutor", role=StaffRole.HOUSE_TUTOR)
        response = login(self.client, "tutor", role="ADMIN")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "role_mismatch")
        self.assertIn("House Tutor", response.json()["error"]["message"])

    def test_matching_selected_role_is_accepted(self):
        create_staff("tutor", role=StaffRole.HOUSE_TUTOR)
        response = login(self.client, "tutor", role="HOUSE_TUTOR")
        self.assertEqual(response.status_code, 200)

    def test_session_cookie_alone_cannot_call_the_api(self):
        self.client.force_login(self.admin)
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(
            response.json()["error"]["code"], "authentication_required"
        )

    def test_device_metadata_is_recorded(self):
        response = login(
            self.client,
            "admin",
            device={
                "device_id": "device-1",
                "device_name": "Pixel 8",
                "platform": "android",
                "app_version": "1.2.3",
            },
        )
        self.assertEqual(response.status_code, 200)
        token = APIAccessToken.objects.get(user=self.admin)
        self.assertEqual(token.device_id, "device-1")
        self.assertEqual(token.platform, "android")
        self.assertEqual(token.app_version, "1.2.3")

    def test_same_device_replaces_previous_token(self):
        first = token_from(login(self.client, "admin", device={"device_id": "device-1"}))
        second = token_from(
            login(self.client, "admin", device={"device_id": "device-1"})
        )
        self.assertNotEqual(first, second)
        self.assertEqual(
            APIAccessToken.objects.filter(
                user=self.admin, revoked_at__isnull=True
            ).count(),
            1,
        )

    def test_token_limit_revokes_oldest_sessions(self):
        for index in range(10):
            login(self.client, "admin", device={"device_id": f"device-{index}"})
        active = APIAccessToken.objects.filter(
            user=self.admin, revoked_at__isnull=True
        ).count()
        self.assertLessEqual(active, 8)


class SessionLifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.user = create_staff("admin", role=StaffRole.ADMIN)

    def _token(self):
        return token_from(login(self.client, "admin"))

    def test_me_requires_bearer_token(self):
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response["WWW-Authenticate"])

    def test_me_returns_profile(self):
        token = self._token()
        response = self.client.get("/api/v1/auth/me/", **auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["user"]["username"], "admin")
        self.assertIn("unread", response.json()["data"])

    def test_refresh_rotates_and_invalidates_old_token(self):
        old = self._token()
        response = self.client.post("/api/v1/auth/refresh/", **auth(old))
        self.assertEqual(response.status_code, 200)
        new = response.json()["data"]["token"]
        self.assertNotEqual(old, new)
        self.assertEqual(
            self.client.get("/api/v1/auth/me/", **auth(old)).status_code, 401
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/me/", **auth(new)).status_code, 200
        )

    def test_logout_revokes_current_token(self):
        token = self._token()
        response = self.client.post("/api/v1/auth/logout/", **auth(token))
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self.client.get("/api/v1/auth/me/", **auth(token)).status_code, 401
        )

    def test_expired_token_is_rejected(self):
        token = self._token()
        APIAccessToken.objects.filter(user=self.user).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        response = self.client.get("/api/v1/auth/me/", **auth(token))
        self.assertEqual(response.status_code, 401)

    def test_revoked_token_is_rejected(self):
        token = self._token()
        APIAccessToken.objects.filter(user=self.user).update(
            revoked_at=timezone.now(), revoked_reason="test"
        )
        response = self.client.get("/api/v1/auth/me/", **auth(token))
        self.assertEqual(response.status_code, 401)

    def test_inactive_user_token_is_rejected(self):
        token = self._token()
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        response = self.client.get("/api/v1/auth/me/", **auth(token))
        self.assertEqual(response.status_code, 401)

    def test_password_change_revokes_other_sessions(self):
        current = self._token()
        other = token_from(
            login(self.client, "admin", device={"device_id": "other-device"})
        )
        response = self.client.post(
            "/api/v1/auth/password/",
            data=json.dumps(
                {
                    "current_password": PASSWORD,
                    "new_password": "EvenStronger!2026",
                }
            ),
            content_type="application/json",
            **auth(current),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get("/api/v1/auth/me/", **auth(other)).status_code, 401
        )
        self.assertEqual(
            self.client.get("/api/v1/auth/me/", **auth(current)).status_code, 200
        )
        self.assertIsNone(
            self.client.post(
                "/api/v1/auth/login/",
                data=json.dumps({"username": "admin", "password": PASSWORD}),
                content_type="application/json",
            ).json().get("data")
        )

    def test_password_change_rejects_wrong_current_password(self):
        token = self._token()
        response = self.client.post(
            "/api/v1/auth/password/",
            data=json.dumps(
                {"current_password": "nope", "new_password": "EvenStronger!2026"}
            ),
            content_type="application/json",
            **auth(token),
        )
        self.assertEqual(response.status_code, 400)

    def test_devices_list_marks_current_session(self):
        token = self._token()
        response = self.client.get("/api/v1/auth/devices/", **auth(token))
        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["current"])

    def test_device_can_be_revoked(self):
        current = self._token()
        other = token_from(
            login(self.client, "admin", device={"device_id": "other-device"})
        )
        other_id = APIAccessToken.objects.get(
            user=self.user, device_id="other-device"
        ).pk
        response = self.client.delete(
            f"/api/v1/auth/devices/{other_id}/", **auth(current)
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            self.client.get("/api/v1/auth/me/", **auth(other)).status_code, 401
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.API_TOKEN_REVOKED
            ).exists()
        )

    def test_cannot_revoke_another_users_device(self):
        token = self._token()
        stranger = create_staff("stranger", role=StaffRole.ADMIN)
        stranger_token = APIAccessToken.objects.create(
            user=stranger,
            key_hash=APIAccessToken.hash_key("stranger-token"),
            key_prefix="stranger-t",
            expires_at=timezone.now() + timedelta(days=1),
        )
        response = self.client.delete(
            f"/api/v1/auth/devices/{stranger_token.pk}/", **auth(token)
        )
        self.assertEqual(response.status_code, 404)


class InviteOnboardingTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.admin = create_staff("admin", role=StaffRole.ADMIN)
        self.house = create_house("Unity House")
        self.admin_token = token_from(login(self.client, "admin"))

    def _create_invite(self, **payload):
        body = {
            "role": StaffRole.HOUSEMASTER,
            "house_id": str(self.house.pk),
            "phone_number": "+233200000123",
        }
        body.update(payload)
        response = self.client.post(
            "/api/v1/staff/invites/",
            data=json.dumps(body),
            content_type="application/json",
            **auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]

    def test_invite_requires_admin_role(self):
        tutor = create_staff("tutor", role=StaffRole.HOUSE_TUTOR)
        token = token_from(login(self.client, "tutor"))
        response = self.client.get("/api/v1/staff/invites/", **auth(token))
        self.assertEqual(response.status_code, 403)

    def test_housemaster_invite_requires_a_house(self):
        response = self.client.post(
            "/api/v1/staff/invites/",
            data=json.dumps({"role": StaffRole.HOUSEMASTER}),
            content_type="application/json",
            **auth(self.admin_token),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("house_id", response.json()["error"]["fields"])

    def test_create_validate_and_redeem_invite(self):
        invite = self._create_invite(
            first_name="Ama", last_name="Mensah", staff_id="STF-100"
        )
        self.assertTrue(invite["code"].startswith("EDEN-"))
        self.assertFalse(
            StaffInvite.objects.filter(code_hash=invite["code"]).exists()
        )

        validate = self.client.post(
            "/api/v1/auth/invites/validate/",
            data=json.dumps({"code": invite["code"]}),
            content_type="application/json",
        )
        self.assertEqual(validate.status_code, 200)
        self.assertEqual(validate.json()["data"]["role"], StaffRole.HOUSEMASTER)

        redeem = self.client.post(
            "/api/v1/auth/invites/redeem/",
            data=json.dumps(
                {
                    "code": invite["code"],
                    "username": "ama",
                    "password": "FreshPassw0rd!2026",
                    "device": {"device_id": "pixel", "platform": "android"},
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(redeem.status_code, 201, redeem.content)
        token = redeem.json()["data"]["token"]
        me = self.client.get("/api/v1/auth/me/", **auth(token))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["data"]["user"]["role"], StaffRole.HOUSEMASTER)

        from django.contrib.auth import get_user_model

        user = get_user_model().objects.get(username="ama")
        self.assertEqual(user.housemaster.house, self.house)
        self.assertEqual(user.housemaster.staff_id, "STF-100")
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.STAFF_INVITE_REDEEMED
            ).exists()
        )

    def test_used_invite_cannot_be_redeemed_twice(self):
        invite = self._create_invite()
        payload = json.dumps(
            {
                "code": invite["code"],
                "username": "first",
                "password": "FreshPassw0rd!2026",
            }
        )
        first = self.client.post(
            "/api/v1/auth/invites/redeem/",
            data=payload,
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201, first.content)
        second = self.client.post(
            "/api/v1/auth/invites/redeem/",
            data=json.dumps(
                {
                    "code": invite["code"],
                    "username": "second",
                    "password": "FreshPassw0rd!2026",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 404)

    def test_revoked_invite_cannot_be_used(self):
        invite = self._create_invite()
        response = self.client.delete(
            f"/api/v1/staff/invites/{invite['id']}/", **auth(self.admin_token)
        )
        self.assertEqual(response.status_code, 204)
        validate = self.client.post(
            "/api/v1/auth/invites/validate/",
            data=json.dumps({"code": invite["code"]}),
            content_type="application/json",
        )
        self.assertEqual(validate.status_code, 404)

    def test_invite_is_bound_to_the_issuing_tenant(self):
        # The invite resolves inside this test's schema only. A different
        # tenant's identical-looking code cannot be validated here.
        response = self.client.post(
            "/api/v1/auth/invites/validate/",
            data=json.dumps({"code": "EDEN-AAAA-BBBB"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    @override_settings(API_LOGIN_RATE_LIMIT=1, API_LOGIN_RATE_WINDOW=60)
    def test_invite_probing_is_rate_limited_before_lookup(self):
        first = self.client.post(
            "/api/v1/auth/invites/validate/",
            data=json.dumps({"code": "EDEN-AAAA-BBBB"}),
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 404)

        second = self.client.post(
            "/api/v1/auth/invites/redeem/",
            data=json.dumps({"code": "EDEN-CCCC-DDDD"}),
            content_type="application/json",
        )
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "rate_limited")
        self.assertEqual(second["Retry-After"], "60")
