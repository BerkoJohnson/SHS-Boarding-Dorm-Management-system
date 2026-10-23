import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from core.api.tests.factories import (
    auth,
    create_guardian,
    create_house,
    create_school,
    create_staff,
    create_student,
    login,
    token_from,
)
from core.models import (
    ClassGroup,
    Dormitory,
    DormitoryRoom,
    Exeat,
    Programme,
    StaffRole,
)


class StudentAccessTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.house_a = create_house("House A")
        self.house_b = create_house("House B")
        self.programme = Programme.objects.create(name="General Science")
        self.class_group = ClassGroup.objects.create(
            name="2A1", programme=self.programme
        )
        self.dormitory = Dormitory.objects.create(
            house=self.house_a,
            name="A Block",
            gender=Dormitory.GenderBlock.MALE,
        )
        self.room = DormitoryRoom.objects.create(
            dormitory=self.dormitory, name="A1", capacity=20
        )
        self.guardian = create_guardian()
        self.student_a1 = create_student(
            "ADM-001",
            full_name="Kofi Mensah",
            house=self.house_a,
            programme=self.programme,
            dormitory=self.dormitory,
            room=self.room,
            class_group=self.class_group,
            guardian=self.guardian,
        )
        self.student_a2 = create_student(
            "ADM-002",
            full_name="Ama Boateng",
            house=self.house_a,
            programme=self.programme,
            gender="female",
        )
        self.student_b1 = create_student(
            "ADM-003",
            full_name="Yaw Owusu",
            house=self.house_b,
            programme=self.programme,
        )

        self.admin = create_staff("admin", role=StaffRole.ADMIN)
        self.hm_a = create_staff(
            "hm-a", role=StaffRole.HOUSEMASTER, house=self.house_a
        )
        self.hm_b = create_staff(
            "hm-b", role=StaffRole.HOUSEMASTER, house=self.house_b
        )
        self.gate = create_staff("gate", role=StaffRole.GATE_OFFICER)

    def _token(self, username):
        return token_from(login(self.client, username))

    def test_admin_sees_every_student(self):
        token = self._token("admin")
        response = self.client.get("/api/v1/students/", **auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pagination"]["total"], 3)

    def test_housemaster_only_sees_their_house(self):
        token = self._token("hm-a")
        response = self.client.get("/api/v1/students/", **auth(token))
        self.assertEqual(response.status_code, 200)
        names = [row["full_name"] for row in response.json()["data"]]
        self.assertEqual(sorted(names), ["Ama Boateng", "Kofi Mensah"])

    def test_house_filter_cannot_escape_scope(self):
        token = self._token("hm-a")
        response = self.client.get(
            f"/api/v1/students/?house_id={self.house_b.pk}", **auth(token)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [])

    def test_cross_house_detail_returns_404(self):
        token = self._token("hm-a")
        response = self.client.get(
            f"/api/v1/students/{self.student_b1.pk}/", **auth(token)
        )
        self.assertEqual(response.status_code, 404)

    def test_admin_can_view_detail_with_guardian(self):
        token = self._token("admin")
        response = self.client.get(
            f"/api/v1/students/{self.student_a1.pk}/", **auth(token)
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["full_name"], "Kofi Mensah")
        self.assertEqual(data["guardian"]["full_name"], "Test Guardian")
        self.assertEqual(data["house"]["name"], "House A")
        self.assertIn("recent_exeats", data)
        self.assertTrue(data["photo_url"] is None)

    def test_gate_officer_cannot_browse_the_registry(self):
        token = self._token("gate")
        response = self.client.get("/api/v1/students/", **auth(token))
        self.assertEqual(response.status_code, 403)

    def test_search_matches_name_and_admission_number(self):
        token = self._token("admin")
        by_name = self.client.get("/api/v1/students/search/?q=Kofi", **auth(token))
        self.assertEqual(by_name.status_code, 200)
        self.assertEqual(len(by_name.json()["data"]), 1)

        by_admission = self.client.get(
            "/api/v1/students/search/?q=ADM-00", **auth(token)
        )
        self.assertEqual(len(by_admission.json()["data"]), 3)

    def test_search_requires_two_characters(self):
        token = self._token("admin")
        response = self.client.get("/api/v1/students/search/?q=K", **auth(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], [])

    def test_lookup_by_admission_and_qr_token(self):
        token = self._token("admin")
        by_admission = self.client.get(
            "/api/v1/students/lookup/?q=ADM-001", **auth(token)
        )
        self.assertEqual(by_admission.status_code, 200)
        self.assertEqual(by_admission.json()["data"]["full_name"], "Kofi Mensah")

        public_url = (
            f"https://school.example/verify/student/{self.student_a1.public_token}/"
        )
        by_qr = self.client.get(
            f"/api/v1/students/lookup/?q={public_url}", **auth(token)
        )
        self.assertEqual(by_qr.status_code, 200)
        self.assertEqual(by_qr.json()["data"]["id"], str(self.student_a1.pk))

    def test_lookup_unknown_code_returns_404(self):
        token = self._token("admin")
        response = self.client.get("/api/v1/students/lookup/?q=NOPE-1", **auth(token))
        self.assertEqual(response.status_code, 404)

    def test_expired_qr_token_returns_410(self):
        self.student_a1.public_token_expires_at = timezone.now() - timedelta(minutes=1)
        self.student_a1.save(update_fields=["public_token_expires_at"])
        token = self._token("admin")
        response = self.client.get(
            f"/api/v1/students/lookup/?q={self.student_a1.public_token}",
            **auth(token),
        )
        self.assertEqual(response.status_code, 410)
        self.assertEqual(response.json()["error"]["code"], "qr_expired")

    def test_invalid_house_filter_uuid_is_rejected(self):
        token = self._token("admin")
        response = self.client.get(
            "/api/v1/students/?house_id=not-a-uuid", **auth(token)
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_filter")

    def test_invalid_ordering_is_rejected(self):
        token = self._token("admin")
        response = self.client.get(
            "/api/v1/students/?ordering=guardian__phone_number", **auth(token)
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_ordering")

    def test_options_expose_only_management_structure(self):
        token = self._token("admin")
        response = self.client.get("/api/v1/students/options/", **auth(token))
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["houses"][0]["name"], "House A")
        self.assertEqual(data["guardians"][0]["phone_number"], "+233200000222")

    def test_id_card_metadata_and_rotation(self):
        token = self._token("admin")
        original = self.student_a1.public_token
        response = self.client.get(
            f"/api/v1/students/{self.student_a1.pk}/id-card/", **auth(token)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["data"]["public_token"], str(original)
        )
        self.assertIn("/verify/student/", response.json()["data"]["public_qr_url"])

        rotated = self.client.post(
            f"/api/v1/students/{self.student_a1.pk}/rotate-public-token/",
            **auth(token),
        )
        self.assertEqual(rotated.status_code, 200)
        self.student_a1.refresh_from_db()
        self.assertNotEqual(self.student_a1.public_token, original)
        self.assertEqual(
            rotated.json()["data"]["public_token"],
            str(self.student_a1.public_token),
        )

    @override_settings(PRIVATE_STUDENT_PHOTO_ROOT=Path(tempfile.mkdtemp()))
    def test_photo_is_scoped_and_streamed(self):
        buffer = BytesIO()
        Image.new("RGB", (20, 20), "red").save(buffer, "JPEG")
        self.student_a1.photo.save(
            "portrait.jpg",
            SimpleUploadedFile(
                "portrait.jpg", buffer.getvalue(), content_type="image/jpeg"
            ),
            save=True,
        )

        admin_token = self._token("admin")
        response = self.client.get(
            f"/api/v1/students/{self.student_a1.pk}/photo/", **auth(admin_token)
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertTrue(b"".join(response.streaming_content))

        hm_b_token = self._token("hm-b")
        denied = self.client.get(
            f"/api/v1/students/{self.student_a1.pk}/photo/", **auth(hm_b_token)
        )
        self.assertEqual(denied.status_code, 404)

    def test_student_with_exeat_is_flagged_in_list(self):
        token = self._token("admin")
        exeat = Exeat.objects.create(
            student=self.student_a1,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure="2026-10-24T08:00:00Z",
            expected_return="2026-10-24T17:00:00Z",
            destination="Town",
            reason="Family visit",
        )
        exeat.status = Exeat.Status.APPROVED
        exeat.approved_by_user = self.admin
        exeat.save()
        response = self.client.get("/api/v1/students/", **auth(token))
        rows = {row["admission_no"]: row for row in response.json()["data"]}
        self.assertTrue(rows["ADM-001"]["is_active_exeat"])
        self.assertFalse(rows["ADM-002"]["is_active_exeat"])
