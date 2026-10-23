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
from core.models import AuditEvent, Exeat, ExeatMovement, StaffRole


class GateVerifyTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.house = create_house("Gate House")
        self.student = create_student(
            "GATE-001", full_name="Kojo Antwi", house=self.house
        )
        self.admin = create_staff("admin", role=StaffRole.ADMIN)
        self.gate = create_staff("gate", role=StaffRole.GATE_OFFICER)
        self.housemaster = create_staff(
            "hm", role=StaffRole.HOUSEMASTER, house=self.house
        )

        now = timezone.now()
        self.exeat = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timedelta(hours=1),
            expected_return=now + timedelta(hours=3),
            destination="Town centre",
            reason="Dental appointment",
        )
        self.exeat.status = Exeat.Status.APPROVED
        self.exeat.approved_by_user = self.admin
        self.exeat.save()

    def _post(self, username, code):
        token = token_from(login(self.client, username))
        return self.client.post(
            "/api/v1/gate/verify/",
            data=json.dumps({"code": code}),
            content_type="application/json",
            **auth(token),
        )

    def test_qr_scan_returns_student_and_active_pass(self):
        response = self._post("gate", str(self.student.public_token))
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertEqual(data["match"], "student")
        self.assertEqual(data["student"]["admission_no"], "GATE-001")
        self.assertEqual(data["exeat"]["exeat_number"], self.exeat.exeat_number)
        self.assertEqual(data["movement_state"], "awaiting_departure")
        self.assertTrue(data["can_depart"])
        self.assertFalse(data["can_return"])
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.STUDENT_SCAN,
                target_id=str(self.student.pk),
            ).exists()
        )

    def test_scanned_public_url_is_resolved_locally(self):
        code = (
            f"https://school.example/verify/student/{self.student.public_token}/"
        )
        response = self._post("gate", code)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["student"]["id"], str(self.student.pk))

    def test_exeat_number_scan(self):
        response = self._post("gate", self.exeat.exeat_number)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["match"], "exeat_number")
        self.assertEqual(data["exeat"]["id"], str(self.exeat.pk))
        self.assertTrue(data["can_depart"])

    def test_admission_number_scan(self):
        response = self._post("gate", "GATE-001")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["match"], "student")
        self.assertEqual(data["student"]["full_name"], "Kojo Antwi")

    def test_index_number_scan(self):
        response = self._post("gate", self.student.index_number)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["student"]["id"], str(self.student.pk))

    def test_student_out_can_return(self):
        ExeatMovement.objects.create(
            exeat=self.exeat,
            actual_departure=timezone.now() - timedelta(minutes=20),
            departure_recorded_by="Gate Officer",
        )
        response = self._post("gate", str(self.student.public_token))
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["movement_state"], "out")
        self.assertFalse(data["can_depart"])
        self.assertTrue(data["can_return"])

    def test_unknown_code_returns_no_match(self):
        response = self._post("gate", "ZZZ-000")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["match"], "none")
        self.assertIsNone(response.json()["data"]["student"])

    def test_housemaster_cannot_use_gate_endpoint(self):
        response = self._post("hm", "GATE-001")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "role_forbidden")

    def test_expired_qr_code_is_rejected(self):
        self.student.public_token_expires_at = timezone.now() - timedelta(days=1)
        self.student.save(update_fields=["public_token_expires_at"])
        response = self._post("gate", str(self.student.public_token))
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"]["code"], "qr_expired")

    def test_pending_pass_is_shown_but_cannot_depart(self):
        pending = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.WEEKEND,
            expected_departure=timezone.now() + timedelta(hours=2),
            expected_return=timezone.now() + timedelta(hours=8),
            destination="Home",
            reason="Family weekend",
        )
        response = self._post("gate", pending.exeat_number)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertFalse(data["can_depart"])
        self.assertTrue(any("pending" in w.lower() for w in data["warnings"]))

    def test_missing_code_returns_validation_error(self):
        token = token_from(login(self.client, "gate"))
        response = self.client.post(
            "/api/v1/gate/verify/",
            data=json.dumps({}),
            content_type="application/json",
            **auth(token),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("code", response.json()["error"]["fields"])
