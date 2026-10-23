import json

from django.core.cache import cache
from django.test import TestCase

from core.api.tests.factories import (
    auth,
    create_house,
    create_school,
    create_staff,
    create_student,
    login,
    token_from,
)
from core.models import RollCallEntry, RollCallSession, StaffRole


class RollCallApiTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.house = create_house("Unity House")
        self.other_house = create_house("Other House")
        self.student_one = create_student(
            "RC-001", full_name="Akosua Danso", house=self.house, gender="female"
        )
        self.student_two = create_student(
            "RC-002", full_name="Kwame Asare", house=self.house
        )
        self.other_student = create_student(
            "RC-003", full_name="Yaa Owusu", house=self.other_house, gender="female"
        )
        self.admin = create_staff("admin", role=StaffRole.ADMIN)
        self.senior = create_staff(
            "senior", role=StaffRole.SENIOR_HOUSEMASTER, senior=True
        )
        self.hm = create_staff("hm", role=StaffRole.HOUSEMASTER, house=self.house)
        self.other_hm = create_staff(
            "other-hm", role=StaffRole.HOUSEMASTER, house=self.other_house
        )
        self.tutor = create_staff(
            "tutor", role=StaffRole.HOUSE_TUTOR, house=self.house
        )
        self.gate = create_staff("gate", role=StaffRole.GATE_OFFICER)

    def _post(self, username, path, payload=None):
        token = token_from(login(self.client, username))
        return self.client.post(
            path,
            data=json.dumps(payload or {}),
            content_type="application/json",
            **auth(token),
        )

    def _get(self, username, path):
        token = token_from(login(self.client, username))
        return self.client.get(path, **auth(token))

    def _start(self, username="hm", **overrides):
        payload = {"session_type": RollCallSession.SessionType.EVENING}
        payload.update(overrides)
        response = self._post(username, "/api/v1/roll-call/sessions/", payload)
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]

    def test_housemaster_starts_session_with_house_roster(self):
        data = self._start()
        self.assertEqual(data["house"]["id"], str(self.house.pk))
        self.assertEqual(data["status"], RollCallSession.Status.IN_PROGRESS)
        self.assertEqual(len(data["entries"]), 2)
        self.assertEqual(
            {entry["student"]["admission_no"] for entry in data["entries"]},
            {"RC-001", "RC-002"},
        )
        self.assertTrue(
            all(
                entry["status"] == RollCallEntry.Status.UNMARKED
                for entry in data["entries"]
            )
        )
        self.assertEqual(data["counts"]["unmarked"], 2)

    def test_starting_same_slot_reuses_existing_session(self):
        first = self._start()
        response = self._post(
            "hm", "/api/v1/roll-call/sessions/", {"session_type": "Evening"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["meta"]["reused_existing"])
        self.assertEqual(response.json()["data"]["id"], first["id"])

    def test_admin_must_specify_house(self):
        missing = self._post(
            "admin", "/api/v1/roll-call/sessions/", {"session_type": "Evening"}
        )
        self.assertEqual(missing.status_code, 400)
        self.assertIn("house_id", missing.json()["error"]["fields"])

        response = self._post(
            "admin",
            "/api/v1/roll-call/sessions/",
            {"house_id": str(self.other_house.pk), "session_type": "Evening"},
        )
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["house"]["id"], str(self.other_house.pk))
        self.assertEqual(len(data["entries"]), 1)

    def test_tutor_can_start_for_own_house_only(self):
        response = self._post(
            "tutor",
            "/api/v1/roll-call/sessions/",
            {"house_id": str(self.other_house.pk), "session_type": "Morning"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json()["error"]["code"], "house_scope_denied"
        )

        started = self._post(
            "tutor", "/api/v1/roll-call/sessions/", {"session_type": "Morning"}
        )
        self.assertEqual(started.status_code, 201, started.content)

    def test_gate_officer_cannot_use_roll_call(self):
        response = self._post(
            "gate", "/api/v1/roll-call/sessions/", {"session_type": "Evening"}
        )
        self.assertEqual(response.status_code, 403)

    def test_session_list_is_house_scoped(self):
        own = self._start()
        self._post(
            "other-hm", "/api/v1/roll-call/sessions/", {"session_type": "Evening"}
        )

        response = self._get("hm", "/api/v1/roll-call/sessions/")
        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]
        self.assertEqual([row["id"] for row in rows], [own["id"]])

        admin_rows = self._get("admin", "/api/v1/roll-call/sessions/").json()["data"]
        self.assertEqual(len(admin_rows), 2)

    def test_housemaster_cannot_open_another_house_session(self):
        other = self._start("other-hm")
        response = self._get("hm", f"/api/v1/roll-call/sessions/{other['id']}/")
        self.assertEqual(response.status_code, 404)

    def test_update_entry_status_and_remarks(self):
        session = self._start()
        entry = next(
            row for row in session["entries"] if row["student"]["admission_no"] == "RC-001"
        )
        response = self._post(
            "hm",
            f"/api/v1/roll-call/sessions/{session['id']}/entries/{entry['id']}/",
            {"status": RollCallEntry.Status.PRESENT, "remarks": "Seen at muster."},
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["entry"]["status"], RollCallEntry.Status.PRESENT)
        self.assertEqual(data["entry"]["remarks"], "Seen at muster.")
        self.assertEqual(data["session"]["counts"]["present"], 1)
        self.assertEqual(data["session"]["counts"]["unmarked"], 1)

    def test_update_entry_rejects_unknown_status(self):
        session = self._start()
        entry = session["entries"][0]
        response = self._post(
            "hm",
            f"/api/v1/roll-call/sessions/{session['id']}/entries/{entry['id']}/",
            {"status": "Sleeping"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("status", response.json()["error"]["fields"])

    def test_mark_all_present_corrects_absent_entries_only(self):
        session = self._start()
        first, second = session["entries"]
        self._post(
            "hm",
            f"/api/v1/roll-call/sessions/{session['id']}/entries/{first['id']}/",
            {"status": RollCallEntry.Status.ABSENT},
        )
        self._post(
            "hm",
            f"/api/v1/roll-call/sessions/{session['id']}/entries/{second['id']}/",
            {"status": RollCallEntry.Status.INFIRMARY},
        )

        response = self._post(
            "hm",
            f"/api/v1/roll-call/sessions/{session['id']}/mark-all-present/",
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["updated"], 1)
        self.assertEqual(data["session"]["counts"]["present"], 1)
        self.assertEqual(data["session"]["counts"]["infirmary"], 1)

    def test_complete_requires_every_entry_marked(self):
        session = self._start()
        blocked = self._post(
            "hm", f"/api/v1/roll-call/sessions/{session['id']}/complete/"
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(
            blocked.json()["error"]["code"], "roll_call_state_error"
        )

        for entry in session["entries"]:
            self._post(
                "hm",
                f"/api/v1/roll-call/sessions/{session['id']}/entries/{entry['id']}/",
                {"status": RollCallEntry.Status.PRESENT},
            )

        completed = self._post(
            "hm", f"/api/v1/roll-call/sessions/{session['id']}/complete/"
        )
        self.assertEqual(completed.status_code, 200, completed.content)
        data = completed.json()["data"]
        self.assertTrue(data["completed"])
        self.assertEqual(
            data["session"]["status"], RollCallSession.Status.COMPLETED
        )

        locked = self._post(
            "hm",
            f"/api/v1/roll-call/sessions/{session['id']}/entries/{session['entries'][0]['id']}/",
            {"status": RollCallEntry.Status.ABSENT},
        )
        self.assertEqual(locked.status_code, 409)

    def test_detail_includes_gate_aware_entry_payload(self):
        session = self._start()
        response = self._get(
            "hm", f"/api/v1/roll-call/sessions/{session['id']}/"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(len(data["entries"]), 2)
        self.assertIn("gate_conflict", data["entries"][0])
        self.assertEqual(data["counts"]["total_students"], 2)
