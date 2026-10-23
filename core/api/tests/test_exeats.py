import json
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

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
    Exeat,
    ExeatMovement,
    ExeatReconciliation,
    ExeatReview,
    StaffRole,
)


class ExeatLifecycleTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.house = create_house("Unity House")
        self.student = create_student(
            "EX-001", full_name="Akosua Danso", house=self.house, gender="female"
        )
        self.admin = create_staff("admin", role=StaffRole.ADMIN)
        self.senior = create_staff("senior", role=StaffRole.SENIOR_HOUSEMASTER, senior=True)
        self.hm = create_staff("hm", role=StaffRole.HOUSEMASTER, house=self.house)
        self.other_hm = create_staff(
            "other-hm",
            role=StaffRole.HOUSEMASTER,
            house=create_house("Other House"),
        )
        self.tutor = create_staff("tutor", role=StaffRole.HOUSE_TUTOR, house=self.house)
        self.gate = create_staff("gate", role=StaffRole.GATE_OFFICER)

    def _token(self, username):
        return token_from(login(self.client, username))

    def _iso(self, value):
        return value.isoformat()

    def _create_payload(self, **overrides):
        now = timezone.now()
        payload = {
            "student_id": str(self.student.pk),
            "exeat_type": Exeat.ExeatType.TOWN,
            "expected_departure": self._iso(now + timedelta(hours=1)),
            "expected_return": self._iso(now + timedelta(hours=4)),
            "destination": "Town centre",
            "reason": "Dental appointment",
        }
        payload.update(overrides)
        return payload

    def _post(self, username, path, payload=None):
        return self.client.post(
            path,
            data=json.dumps(payload or {}),
            content_type="application/json",
            **auth(self._token(username)),
        )

    def _create_exeat(self, username="hm", **overrides):
        response = self._post(username, "/api/v1/exeats/", self._create_payload(**overrides))
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()["data"]

    def _approved_exeat(self, *, departure_offset_hours=-1, return_offset_hours=3):
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now + timedelta(hours=departure_offset_hours),
            expected_return=now + timedelta(hours=return_offset_hours),
            destination="Town",
            reason="Appointment",
        )
        exeat.status = Exeat.Status.APPROVED
        exeat.approved_by_user = self.admin
        exeat.save()
        ExeatMovement.objects.get_or_create(exeat=exeat)
        return exeat

    def test_house_tutor_can_create_request(self):
        data = self._create_exeat(username="tutor")
        self.assertEqual(data["status"], Exeat.Status.PENDING)
        self.assertTrue(data["exeat_number"].startswith("EXT-"))
        self.assertEqual(data["student"]["id"], str(self.student.pk))

    def test_gate_officer_cannot_create_request(self):
        response = self._post("gate", "/api/v1/exeats/", self._create_payload())
        self.assertEqual(response.status_code, 403)

    def test_create_validates_required_fields(self):
        payload = self._create_payload()
        payload.pop("destination")
        response = self._post("hm", "/api/v1/exeats/", payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("destination", response.json()["error"]["fields"])

    def test_housemaster_can_approve_own_house_request(self):
        exeat = self._create_exeat()
        response = self._post(
            "hm",
            f"/api/v1/exeats/{exeat['id']}/approve/",
            {"approval_remarks": "Approved, report back by 18:00."},
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], Exeat.Status.APPROVED)
        self.assertEqual(data["approved_by_name"], "hm")
        self.assertTrue(ExeatMovement.objects.filter(exeat_id=exeat["id"]).exists())

    def test_other_house_housemaster_cannot_approve(self):
        exeat = self._create_exeat()
        response = self._post(
            "other-hm", f"/api/v1/exeats/{exeat['id']}/approve/"
        )
        self.assertEqual(response.status_code, 404)

    def test_approval_twice_is_conflict(self):
        exeat = self._create_exeat()
        self._post("hm", f"/api/v1/exeats/{exeat['id']}/approve/")
        response = self._post("hm", f"/api/v1/exeats/{exeat['id']}/approve/")
        self.assertEqual(response.status_code, 409)

    def test_reject_requires_reason(self):
        exeat = self._create_exeat()
        missing = self._post("hm", f"/api/v1/exeats/{exeat['id']}/reject/")
        self.assertEqual(missing.status_code, 400)
        response = self._post(
            "hm",
            f"/api/v1/exeats/{exeat['id']}/reject/",
            {"approval_remarks": "No guardian confirmation."},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], Exeat.Status.REJECTED)
        self.assertEqual(
            response.json()["data"]["approval_remarks"], "No guardian confirmation."
        )

    def test_cancel_approved_pass(self):
        exeat = self._approved_exeat()
        response = self._post("hm", f"/api/v1/exeats/{exeat.pk}/cancel/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["status"], Exeat.Status.CANCELLED)

    def test_reschedule_preserves_previous_window(self):
        exeat = self._approved_exeat()
        now = timezone.now()
        response = self._post(
            "hm",
            f"/api/v1/exeats/{exeat.pk}/reschedule/",
            {
                "expected_departure": self._iso(now + timedelta(hours=2)),
                "expected_return": self._iso(now + timedelta(hours=6)),
                "reason": "Clinic appointment moved.",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(len(data["schedule_changes"]), 1)
        exeat.refresh_from_db()
        self.assertEqual(
            data["expected_departure"], exeat.expected_departure.isoformat()
        )

    def test_reschedule_requires_reason(self):
        exeat = self._approved_exeat()
        now = timezone.now()
        response = self._post(
            "hm",
            f"/api/v1/exeats/{exeat.pk}/reschedule/",
            {
                "expected_departure": self._iso(now + timedelta(hours=2)),
                "expected_return": self._iso(now + timedelta(hours=6)),
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_reconcile_returned_pass(self):
        exeat = self._approved_exeat()
        now = timezone.now()
        response = self._post(
            "hm",
            f"/api/v1/exeats/{exeat.pk}/reconcile/",
            {
                "outcome": ExeatReconciliation.Outcome.RETURNED,
                "actual_departure": self._iso(now - timedelta(hours=2)),
                "actual_return": self._iso(now - timedelta(minutes=30)),
                "reason": "Gate log was not recorded; confirmed with guardian.",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(
            data["reconciliation"]["outcome"], ExeatReconciliation.Outcome.RETURNED
        )
        self.assertTrue(
            ExeatReconciliation.objects.filter(exeat=exeat).exists()
        )

    def test_reconcile_rejects_movement_conflict(self):
        exeat = self._approved_exeat()
        ExeatMovement.objects.filter(exeat=exeat).update(
            actual_departure=timezone.now() - timedelta(hours=2)
        )
        response = self._post(
            "hm",
            f"/api/v1/exeats/{exeat.pk}/reconcile/",
            {
                "outcome": ExeatReconciliation.Outcome.UNUSED,
                "reason": "Should not be allowed.",
            },
        )
        self.assertEqual(response.status_code, 409)

    def test_emergency_override_creates_review_and_senior_can_decide(self):
        exeat = self._create_exeat(
            username="hm",
            exeat_type=Exeat.ExeatType.EMERGENCY,
            expected_departure=self._iso(timezone.now() + timedelta(hours=2)),
        )
        # General emergency passes cannot take the ordinary approval path.
        blocked = self._post("hm", f"/api/v1/exeats/{exeat['id']}/approve/")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"]["code"], "override_required")

        response = self._post(
            "admin",
            f"/api/v1/exeats/{exeat['id']}/override/",
            {"reason": "Bereavement in the family."},
        )
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["status"], Exeat.Status.APPROVED)
        self.assertTrue(ExeatReview.objects.filter(exeat_id=exeat["id"]).exists())

        decided = self._post(
            "senior",
            f"/api/v1/exeats/{exeat['id']}/review/",
            {"outcome": ExeatReview.Outcome.UPHELD, "notes": "Evidence verified."},
        )
        self.assertEqual(decided.status_code, 200, decided.content)
        self.assertEqual(decided.json()["data"]["outcome"], ExeatReview.Outcome.UPHELD)

    def test_medical_lead_only_sees_medical_passes(self):
        medical_student = create_student(
            "EX-002", full_name="Medical Student", house=self.house, gender="female"
        )
        medical_exeat = Exeat.objects.create(
            student=medical_student,
            exeat_type=Exeat.ExeatType.MEDICAL,
            expected_departure=timezone.now() + timedelta(hours=1),
            expected_return=timezone.now() + timedelta(hours=4),
            destination="Hospital",
            reason="Clinic",
        )
        Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=timezone.now() + timedelta(hours=1),
            expected_return=timezone.now() + timedelta(hours=4),
            destination="Town",
            reason="Errand",
        )
        create_staff("medical", role=StaffRole.MEDICAL)
        token = self._token("medical")
        response = self.client.get("/api/v1/exeats/", **auth(token))
        self.assertEqual(response.status_code, 200)
        rows = response.json()["data"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], str(medical_exeat.pk))


class GateMovementApiTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.house = create_house("Gate House")
        self.student = create_student(
            "GM-001", full_name="Kwame Asare", house=self.house
        )
        self.admin = create_staff("admin", role=StaffRole.ADMIN)
        self.hm = create_staff("hm", role=StaffRole.HOUSEMASTER, house=self.house)
        self.gate = create_staff("gate", role=StaffRole.GATE_OFFICER)

        now = timezone.now()
        self.exeat = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timedelta(hours=1),
            expected_return=now + timedelta(hours=3),
            destination="Town",
            reason="Errand",
        )
        self.exeat.status = Exeat.Status.APPROVED
        self.exeat.approved_by_user = self.admin
        self.exeat.save()
        ExeatMovement.objects.get_or_create(exeat=self.exeat)

    def _post(self, username, path, payload):
        token = token_from(login(self.client, username))
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **auth(token),
        )

    def test_gate_departure_then_return(self):
        depart = self._post(
            "gate", "/api/v1/gate/depart/", {"exeat_id": str(self.exeat.pk)}
        )
        self.assertEqual(depart.status_code, 200, depart.content)
        self.assertTrue(depart.json()["data"]["recorded"])
        self.assertIsNotNone(depart.json()["data"]["movement"]["actual_departure"])

        duplicate = self._post(
            "gate", "/api/v1/gate/depart/", {"exeat_id": str(self.exeat.pk)}
        )
        self.assertEqual(duplicate.status_code, 409)

        returned = self._post(
            "gate", "/api/v1/gate/return/", {"exeat_id": str(self.exeat.pk)}
        )
        self.assertEqual(returned.status_code, 200, returned.content)
        self.assertIsNotNone(returned.json()["data"]["movement"]["actual_return"])

        duplicate_return = self._post(
            "gate", "/api/v1/gate/return/", {"exeat_id": str(self.exeat.pk)}
        )
        self.assertEqual(duplicate_return.status_code, 409)

    def test_return_requires_confirmed_departure(self):
        response = self._post(
            "gate", "/api/v1/gate/return/", {"exeat_id": str(self.exeat.pk)}
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["code"], "return_without_departure"
        )

    def test_housemaster_cannot_record_gate_movement(self):
        response = self._post(
            "hm", "/api/v1/gate/depart/", {"exeat_id": str(self.exeat.pk)}
        )
        self.assertEqual(response.status_code, 403)

    def test_departure_before_window_is_conflict(self):
        self.exeat.expected_departure = timezone.now() + timedelta(hours=2)
        self.exeat.save()
        response = self._post(
            "gate", "/api/v1/gate/depart/", {"exeat_id": str(self.exeat.pk)}
        )
        self.assertEqual(response.status_code, 409)

    def test_idempotency_key_replays_response(self):
        token = token_from(login(self.client, "gate"))
        payload = json.dumps({"exeat_id": str(self.exeat.pk)})
        first = self.client.post(
            "/api/v1/gate/depart/",
            data=payload,
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="gate-depart-1",
            **auth(token),
        )
        self.assertEqual(first.status_code, 200)
        replay = self.client.post(
            "/api/v1/gate/depart/",
            data=payload,
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY="gate-depart-1",
            **auth(token),
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay["Idempotent-Replayed"], "true")
