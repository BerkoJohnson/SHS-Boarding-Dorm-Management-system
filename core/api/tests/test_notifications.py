import json

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from core.api.tests.factories import (
    auth,
    create_school,
    create_staff,
    login,
    token_from,
)
from core.models import (
    RollCallNotification,
    StaffNotification,
    StaffRole,
)


class NotificationApiTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.hm = create_staff("hm", role=StaffRole.HOUSEMASTER)
        self.other = create_staff("other", role=StaffRole.HOUSEMASTER)
        self.staff_notification = StaffNotification.objects.create(
            user=self.hm,
            kind=StaffNotification.Kind.LATE_RETURN,
            title="Late return",
            message="A student returned after curfew.",
        )
        self.roll_call_notification = RollCallNotification.objects.create(
            user=self.hm,
            title="Roll call expired",
            message="Evening muster was not completed.",
        )
        StaffNotification.objects.create(
            user=self.other,
            kind=StaffNotification.Kind.GENERAL,
            title="Someone else's alert",
            message="This must never leak.",
        )

    def _get(self, username, path):
        token = token_from(login(self.client, username))
        return self.client.get(path, **auth(token))

    def _post(self, username, path, payload=None):
        token = token_from(login(self.client, username))
        return self.client.post(
            path,
            data=json.dumps(payload or {}),
            content_type="application/json",
            **auth(token),
        )

    def test_feed_merges_staff_and_roll_call_notifications(self):
        response = self._get("hm", "/api/v1/notifications/")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(data["pagination"]["total"], 2)
        self.assertEqual(data["meta"]["unread"]["total"], 2)
        self.assertEqual(
            {row["source"] for row in data["data"]}, {"staff", "roll_call"}
        )
        self.assertTrue(
            all(row["id"].startswith(("staff:", "roll_call:")) for row in data["data"])
        )
        self.assertTrue(all(not row["is_read"] for row in data["data"]))
        titles = {row["title"] for row in data["data"]}
        self.assertNotIn("Someone else's alert", titles)

    def test_kind_filter_and_unread_filter(self):
        only_staff = self._get(
            "hm", "/api/v1/notifications/?kind=late_return"
        ).json()
        self.assertEqual(only_staff["pagination"]["total"], 1)
        self.assertEqual(only_staff["data"][0]["source"], "staff")

        only_roll_call = self._get(
            "hm", "/api/v1/notifications/?kind=roll_call"
        ).json()
        self.assertEqual(only_roll_call["pagination"]["total"], 1)
        self.assertEqual(only_roll_call["data"][0]["source"], "roll_call")

        self._post(
            "hm",
            f"/api/v1/notifications/staff:{self.staff_notification.pk}/read/",
        )
        unread = self._get("hm", "/api/v1/notifications/?unread=true").json()
        self.assertEqual(unread["pagination"]["total"], 1)
        self.assertEqual(unread["data"][0]["source"], "roll_call")

        invalid = self._get("hm", "/api/v1/notifications/?kind=nonsense")
        self.assertEqual(invalid.status_code, 400)

    def test_mark_one_notification_read(self):
        response = self._post(
            "hm",
            f"/api/v1/notifications/staff:{self.staff_notification.pk}/read/",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertTrue(data["notification"]["is_read"])
        self.assertEqual(data["unread"]["staff"], 0)
        self.assertEqual(data["unread"]["total"], 1)

        self.staff_notification.refresh_from_db()
        self.assertIsNotNone(self.staff_notification.read_at)

        # Reading it again is a harmless no-op rather than an error.
        again = self._post(
            "hm",
            f"/api/v1/notifications/staff:{self.staff_notification.pk}/read/",
        )
        self.assertEqual(again.status_code, 200)

    def test_mark_all_read(self):
        response = self._post("hm", "/api/v1/notifications/read-all/")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["marked_read"]["total"], 2)
        self.assertEqual(data["unread"]["total"], 0)

        self.staff_notification.refresh_from_db()
        self.roll_call_notification.refresh_from_db()
        self.assertIsNotNone(self.staff_notification.read_at)
        self.assertIsNotNone(self.roll_call_notification.read_at)

        second = self._post("hm", "/api/v1/notifications/read-all/")
        self.assertEqual(second.json()["data"]["marked_read"]["total"], 0)

    def test_cannot_read_another_users_notification(self):
        other_notification = StaffNotification.objects.get(user=self.other)
        response = self._post(
            "hm",
            f"/api/v1/notifications/staff:{other_notification.pk}/read/",
        )
        self.assertEqual(response.status_code, 404)

    def test_invalid_notification_id_is_rejected(self):
        malformed = self._post("hm", "/api/v1/notifications/nope/read/")
        self.assertEqual(malformed.status_code, 400)

        missing = self._post(
            "hm",
            "/api/v1/notifications/staff:00000000-0000-0000-0000-000000000000/read/",
        )
        self.assertEqual(missing.status_code, 404)

    def test_pagination_and_is_read_flag(self):
        self.staff_notification.read_at = timezone.now()
        self.staff_notification.save(update_fields=["read_at", "updated_at"])
        response = self._get("hm", "/api/v1/notifications/?page_size=1")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["pagination"]["page_size"], 1)
        self.assertEqual(len(body["data"]), 1)
        self.assertTrue(body["pagination"]["has_next"])

        invalid = self._get("hm", "/api/v1/notifications/?page_size=500")
        self.assertEqual(invalid.status_code, 400)
