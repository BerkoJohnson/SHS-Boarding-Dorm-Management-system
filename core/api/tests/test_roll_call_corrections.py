"""API coverage for post-closure roll-call corrections."""

import json

from django.core.cache import cache
from django.test import TestCase, override_settings

from core.api.tests.factories import (
    auth,
    create_house,
    create_school,
    create_staff,
    create_student,
    login,
    token_from,
)
from core.models import (
    AuditEvent,
    RollCallCorrection,
    RollCallEntry,
    RollCallSession,
    StaffNotification,
    StaffRole,
)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"]
)
class RollCallCorrectionApiTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.house = create_house("Unity House")
        self.other_house = create_house("Other House")
        self.student = create_student(
            "RC-001", full_name="Akosua Danso", house=self.house, gender="female"
        )
        create_student("RC-002", full_name="Kwame Asare", house=self.house)
        create_student(
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
        self._tokens = {}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _token(self, username):
        if username not in self._tokens:
            self._tokens[username] = token_from(login(self.client, username))
        return self._tokens[username]

    def _post(self, username, path, payload=None, **extra):
        return self.client.post(
            path,
            data=json.dumps(payload or {}),
            content_type="application/json",
            **auth(self._token(username)),
            **extra,
        )

    def _get(self, username, path):
        return self.client.get(path, **auth(self._token(username)))

    def _start(self, username="hm", session_type=RollCallSession.SessionType.EVENING):
        response = self._post(
            username,
            "/api/v1/roll-call/sessions/",
            {"session_type": session_type},
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]

    def _closed_session(self, username="hm"):
        session = self._start(username)
        for entry in session["entries"]:
            response = self._post(
                username,
                f"/api/v1/roll-call/sessions/{session['id']}/entries/"
                f"{entry['id']}/",
                {"status": RollCallEntry.Status.PRESENT},
            )
            self.assertEqual(response.status_code, 200, response.content)
        response = self._post(
            username, f"/api/v1/roll-call/sessions/{session['id']}/complete/"
        )
        self.assertEqual(response.status_code, 200, response.content)
        return session

    def _entry(self, session, admission_no="RC-001"):
        return next(
            entry
            for entry in session["entries"]
            if entry["student"]["admission_no"] == admission_no
        )

    def _payload(self, **overrides):
        payload = {
            "requested_status": RollCallEntry.Status.ABSENT,
            "requested_remarks": "Absent from the dormitory at muster.",
            "reason": "The student was in the dormitory at muster time.",
            "evidence": "Dormitory register signed by the tutor.",
        }
        payload.update(overrides)
        return payload

    def _create_correction(self, username, session, entry=None, **overrides):
        entry = entry or session["entries"][0]
        return self._post(
            username,
            f"/api/v1/roll-call/sessions/{session['id']}/entries/"
            f"{entry['id']}/corrections/",
            self._payload(**overrides),
        )

    # ------------------------------------------------------------------
    # Request
    # ------------------------------------------------------------------
    def test_request_creates_pending_correction_and_notifies_approvers(self):
        session = self._closed_session()
        response = self._create_correction("hm", session)

        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], RollCallCorrection.Status.PENDING)
        self.assertEqual(data["previous_status"], RollCallEntry.Status.PRESENT)
        self.assertEqual(data["requested_status"], RollCallEntry.Status.ABSENT)
        self.assertTrue(data["requires_second_approval"])
        self.assertEqual(data["requested_by"]["id"], str(self.hm.pk))
        self.assertEqual(
            data["evidence"], "Dormitory register signed by the tutor."
        )
        self.assertEqual(data["entry"]["student"]["admission_no"], "RC-001")
        self.assertEqual(data["session"]["id"], session["id"])

        correction = RollCallCorrection.objects.get(pk=data["id"])
        self.assertEqual(correction.requested_by, self.hm)
        self.assertEqual(str(correction.entry_id), self._entry(session)["id"])

        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.ROLL_CALL_CORRECTION_REQUESTED,
                target_id=correction.pk,
            ).exists()
        )
        self.assertTrue(
            StaffNotification.objects.filter(
                user=self.senior,
                kind=StaffNotification.Kind.ROLL_CALL_CORRECTION,
            ).exists()
        )
        self.assertTrue(
            StaffNotification.objects.filter(
                user=self.admin,
                kind=StaffNotification.Kind.ROLL_CALL_CORRECTION,
            ).exists()
        )
        self.assertFalse(
            StaffNotification.objects.filter(
                user=self.hm,
                kind=StaffNotification.Kind.ROLL_CALL_CORRECTION,
            ).exists()
        )

    def test_open_session_rejects_correction(self):
        session = self._start()
        entry = session["entries"][0]
        response = self._post(
            "hm",
            f"/api/v1/roll-call/sessions/{session['id']}/entries/"
            f"{entry['id']}/corrections/",
            self._payload(requested_status=RollCallEntry.Status.PRESENT),
        )

        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(
            response.json()["error"]["code"], "roll_call_state_error"
        )
        self.assertFalse(RollCallCorrection.objects.exists())

    def test_sensitive_correction_requires_evidence(self):
        session = self._closed_session()
        response = self._create_correction("hm", session, evidence="")

        self.assertEqual(response.status_code, 400, response.content)
        body = response.json()["error"]
        self.assertEqual(body["code"], "validation_error")
        self.assertIn("evidence", body["fields"])
        self.assertFalse(RollCallCorrection.objects.exists())

    def test_duplicate_pending_correction_conflicts(self):
        session = self._closed_session()
        first = self._create_correction("hm", session)
        self.assertEqual(first.status_code, 201, first.content)

        second = self._create_correction("hm", session)
        self.assertEqual(second.status_code, 409, second.content)
        self.assertEqual(
            second.json()["error"]["code"], "correction_pending"
        )
        self.assertEqual(RollCallCorrection.objects.count(), 1)

    def test_request_rejects_same_and_unknown_status(self):
        session = self._closed_session()
        entry = self._entry(session)

        same = self._create_correction(
            "hm", session, requested_status=RollCallEntry.Status.PRESENT
        )
        self.assertEqual(same.status_code, 400, same.content)
        self.assertIn("requested_status", same.json()["error"]["fields"])

        unknown = self._create_correction(
            "hm", session, requested_status="Sleeping"
        )
        self.assertEqual(unknown.status_code, 400, unknown.content)
        self.assertIn("requested_status", unknown.json()["error"]["fields"])
        self.assertEqual(entry["id"], self._entry(session)["id"])

    def test_tutor_can_request_for_own_house_only(self):
        own = self._closed_session()
        response = self._create_correction("tutor", own)
        self.assertEqual(response.status_code, 201, response.content)

        other = self._closed_session("other-hm")
        denied = self._create_correction("tutor", other)
        self.assertEqual(denied.status_code, 404, denied.content)

        gate = self._create_correction("gate", own)
        self.assertEqual(gate.status_code, 403, gate.content)

    def test_request_is_idempotent_with_a_key(self):
        session = self._closed_session()
        entry = self._entry(session)
        path = (
            f"/api/v1/roll-call/sessions/{session['id']}/entries/"
            f"{entry['id']}/corrections/"
        )
        token = self._token("hm")
        first = self.client.post(
            path,
            data=json.dumps(self._payload()),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="correction-key-1",
            **auth(token),
        )
        self.assertEqual(first.status_code, 201, first.content)

        replay = self.client.post(
            path,
            data=json.dumps(self._payload()),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="correction-key-1",
            **auth(token),
        )
        self.assertEqual(replay.status_code, 201, replay.content)
        self.assertEqual(replay["Idempotent-Replayed"], "true")
        self.assertEqual(replay.json()["data"]["id"], first.json()["data"]["id"])
        self.assertEqual(RollCallCorrection.objects.count(), 1)

    # ------------------------------------------------------------------
    # List and detail
    # ------------------------------------------------------------------
    def test_list_is_house_scoped_and_filterable(self):
        own = self._closed_session()
        other = self._closed_session("other-hm")
        own_correction = self._create_correction("hm", own).json()["data"]
        other_correction = self._create_correction(
            "other-hm", other
        ).json()["data"]

        own_rows = self._get("hm", "/api/v1/roll-call/corrections/").json()["data"]
        self.assertEqual([row["id"] for row in own_rows], [own_correction["id"]])

        other_rows = self._get(
            "other-hm", "/api/v1/roll-call/corrections/"
        ).json()["data"]
        self.assertEqual(
            [row["id"] for row in other_rows], [other_correction["id"]]
        )

        admin_rows = self._get(
            "admin", "/api/v1/roll-call/corrections/"
        ).json()["data"]
        self.assertEqual(len(admin_rows), 2)

        filtered = self._get(
            "admin",
            f"/api/v1/roll-call/corrections/?session_id={own['id']}",
        ).json()["data"]
        self.assertEqual([row["id"] for row in filtered], [own_correction["id"]])

        filtered = self._get(
            "admin",
            f"/api/v1/roll-call/corrections/?student_id={self.student.pk}",
        ).json()["data"]
        self.assertEqual([row["id"] for row in filtered], [own_correction["id"]])

        filtered = self._get(
            "admin", "/api/v1/roll-call/corrections/?status=approved"
        ).json()["data"]
        self.assertEqual(filtered, [])

        self.assertEqual(
            self._get("tutor", "/api/v1/roll-call/corrections/").status_code,
            403,
        )

    def test_list_rejects_invalid_filters(self):
        bad_status = self._get(
            "admin", "/api/v1/roll-call/corrections/?status=sleeping"
        )
        self.assertEqual(bad_status.status_code, 400)
        self.assertEqual(
            bad_status.json()["error"]["code"], "invalid_filter"
        )

        bad_uuid = self._get(
            "admin", "/api/v1/roll-call/corrections/?session_id=not-a-uuid"
        )
        self.assertEqual(bad_uuid.status_code, 400)
        self.assertEqual(bad_uuid.json()["error"]["code"], "invalid_filter")

    def test_detail_is_scoped_and_includes_evidence(self):
        own = self._closed_session()
        created = self._create_correction("hm", own).json()["data"]

        response = self._get(
            "hm", f"/api/v1/roll-call/corrections/{created['id']}/"
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["reason"], self._payload()["reason"])
        self.assertEqual(data["evidence"], self._payload()["evidence"])

        denied = self._get(
            "other-hm", f"/api/v1/roll-call/corrections/{created['id']}/"
        )
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(denied.json()["error"]["code"], "not_found")

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------
    def test_senior_approves_and_applies_correction(self):
        session = self._closed_session()
        created = self._create_correction("hm", session).json()["data"]

        response = self._post(
            "senior",
            f"/api/v1/roll-call/corrections/{created['id']}/decision/",
            {"decision": "approve", "review_note": "Register checked."},
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], RollCallCorrection.Status.APPROVED)
        self.assertEqual(data["reviewed_by"]["id"], str(self.senior.pk))
        self.assertEqual(data["review_note"], "Register checked.")
        self.assertIsNotNone(data["applied_at"])
        self.assertEqual(data["entry"]["status"], RollCallEntry.Status.ABSENT)

        entry = RollCallEntry.objects.get(pk=self._entry(session)["id"])
        self.assertEqual(entry.status, RollCallEntry.Status.ABSENT)

        correction = RollCallCorrection.objects.get(pk=created["id"])
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.ROLL_CALL_CORRECTION_DECIDED,
                target_id=correction.pk,
            ).exists()
        )
        self.assertTrue(
            StaffNotification.objects.filter(
                user=self.hm,
                kind=StaffNotification.Kind.ROLL_CALL_CORRECTION,
                title="Roll-call correction decided",
            ).exists()
        )

    def test_admin_rejection_leaves_entry_unchanged(self):
        session = self._closed_session()
        created = self._create_correction("hm", session).json()["data"]

        response = self._post(
            "admin",
            f"/api/v1/roll-call/corrections/{created['id']}/decision/",
            {"decision": "reject", "review_note": "Evidence does not match."},
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], RollCallCorrection.Status.REJECTED)
        self.assertIsNone(data["applied_at"])
        self.assertEqual(data["entry"]["status"], RollCallEntry.Status.PRESENT)

        entry = RollCallEntry.objects.get(pk=self._entry(session)["id"])
        self.assertEqual(entry.status, RollCallEntry.Status.PRESENT)

    def test_housemaster_cannot_decide(self):
        session = self._closed_session()
        created = self._create_correction("hm", session).json()["data"]

        response = self._post(
            "hm",
            f"/api/v1/roll-call/corrections/{created['id']}/decision/",
            {"decision": "approve"},
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(
            response.json()["error"]["code"], "role_forbidden"
        )

    def test_requester_cannot_self_approve(self):
        session = self._closed_session()
        created = self._create_correction("senior", session)
        self.assertEqual(created.status_code, 201, created.content)
        correction_id = created.json()["data"]["id"]

        response = self._post(
            "senior",
            f"/api/v1/roll-call/corrections/{correction_id}/decision/",
            {"decision": "approve"},
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(
            response.json()["error"]["code"], "approval_forbidden"
        )
        self.assertEqual(
            RollCallCorrection.objects.get(pk=correction_id).status,
            RollCallCorrection.Status.PENDING,
        )

    def test_stale_entry_cannot_be_decided(self):
        session = self._closed_session()
        entry = self._entry(session)
        created = self._create_correction("hm", session).json()["data"]

        RollCallEntry.objects.filter(pk=entry["id"]).update(
            status=RollCallEntry.Status.ON_EXEAT
        )

        response = self._post(
            "senior",
            f"/api/v1/roll-call/corrections/{created['id']}/decision/",
            {"decision": "approve"},
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "correction_stale")

    def test_decision_validation_and_already_decided(self):
        session = self._closed_session()
        created = self._create_correction("hm", session).json()["data"]

        invalid = self._post(
            "senior",
            f"/api/v1/roll-call/corrections/{created['id']}/decision/",
            {"decision": "maybe"},
        )
        self.assertEqual(invalid.status_code, 400, invalid.content)
        self.assertIn("decision", invalid.json()["error"]["fields"])

        first = self._post(
            "senior",
            f"/api/v1/roll-call/corrections/{created['id']}/decision/",
            {"decision": "approve"},
        )
        self.assertEqual(first.status_code, 200, first.content)

        second = self._post(
            "admin",
            f"/api/v1/roll-call/corrections/{created['id']}/decision/",
            {"decision": "reject"},
        )
        self.assertEqual(second.status_code, 409, second.content)
        self.assertEqual(
            second.json()["error"]["code"], "correction_decided"
        )
