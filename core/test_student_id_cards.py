import base64
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    Dormitory,
    Exeat,
    ExeatMovement,
    House,
    HouseMaster,
    Programme,
    SchoolConfiguration,
    StaffRole,
    Student,
    UserProfile,
)


class StudentIdCardTests(TestCase):
    def setUp(self):
        self.programme = Programme.objects.create(name="Id Science")
        self.house = House.objects.create(name="Id House")
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name="Id Block",
            gender=Dormitory.GenderBlock.MALE,
        )
        self.student = Student.objects.create(
            admission_no="ID-001",
            index_number="IDX-ID-001",
            full_name="Kwame Identity",
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
        )
        SchoolConfiguration.objects.create(
            name="Atebubu SHS",
            address="Atebubu, Bono East Region",
            contact_number="+233 20 000 0000",
        )
        self.admin = get_user_model().objects.create_user(
            username="id-admin",
            password="password123",
        )
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={"role": StaffRole.ADMIN},
        )
        self.approver = HouseMaster.objects.create(
            full_name="Senior Approver",
            staff_id="ID-SENIOR",
            phone_number="+233200000010",
            is_senior_housemaster=True,
        )

    def create_exeat(self, **kwargs):
        now = timezone.now()
        values = {
            "student": self.student,
            "exeat_type": Exeat.ExeatType.TOWN,
            "expected_departure": now + timezone.timedelta(hours=1),
            "expected_return": now + timezone.timedelta(hours=4),
            "destination": "Private destination",
            "reason": "Private reason",
        }
        values.update(kwargs)
        return Exeat.objects.create(**values)

    def test_id_card_is_restricted_and_renders_fixed_branded_layouts(self):
        url = reverse("core:student_id_card", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.admin)
        response = self.client.get(url, {"layout": "crest"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_layout"], "crest")
        self.assertEqual(len(response.context["layouts"]), 3)
        self.assertContains(response, "layout-crest")
        self.assertContains(response, "crest-front-header")
        self.assertContains(response, "crest-back-panel")
        self.assertContains(response, "Kwame Identity")
        self.assertContains(response, "Scan to verify")
        self.assertContains(response, "Atebubu, Bono East Region")
        self.assertNotContains(response, "Parent Contact")
        qr_data_uri = response.context["qr_code"]
        self.assertTrue(qr_data_uri.startswith("data:image/svg+xml;base64,"))
        svg = base64.b64decode(qr_data_uri.partition(",")[2])
        self.assertIn(b"<svg", svg)
        self.assertIn(
            reverse(
                "core:public_student_exeat",
                args=[self.student.public_token],
            ),
            response.context["public_url"],
        )

    def test_school_address_is_shown_on_other_card_layouts(self):
        self.client.force_login(self.admin)
        url = reverse("core:student_id_card", args=[self.student.pk])

        for layout in ("classic", "modern"):
            with self.subTest(layout=layout):
                response = self.client.get(url, {"layout": layout})

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Atebubu, Bono East Region")

    def test_id_card_rejects_unknown_layout(self):
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("core:student_id_card", args=[self.student.pk]),
            {"layout": "custom-user-html"},
        )

        self.assertEqual(response.status_code, 400)

    def test_replacing_qr_invalidates_the_previous_public_link(self):
        old_token = self.student.public_token
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse(
                "core:student_rotate_public_token",
                args=[self.student.pk],
            )
        )

        self.assertRedirects(
            response,
            reverse("core:student_id_card", args=[self.student.pk]),
        )
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.public_token, old_token)
        self.assertEqual(
            self.client.get(
                reverse("core:public_student_exeat", args=[old_token])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "core:public_student_exeat",
                    args=[self.student.public_token],
                )
            ).status_code,
            200,
        )

    def test_public_qr_shows_only_latest_exeat_and_gate_movements(self):
        self.create_exeat(
            exeat_type=Exeat.ExeatType.MEDICAL,
            destination="Older private destination",
            reason="Older private reason",
        )
        exeat = self.create_exeat(
            status=Exeat.Status.APPROVED,
            approved_by=self.approver,
        )
        now = timezone.now()
        ExeatMovement.objects.create(
            exeat=exeat,
            actual_departure=now,
            actual_return=now + timezone.timedelta(minutes=25),
            departure_recorded_by="Gate Officer",
            return_recorded_by="Gate Officer",
        )

        response = self.client.get(
            reverse(
                "core:public_student_exeat",
                args=[self.student.public_token],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kwame Identity")
        self.assertContains(response, "Approved departure")
        self.assertContains(response, "Gate departure")
        self.assertContains(response, "Gate return")
        self.assertNotContains(response, "Private reason")
        self.assertNotContains(response, "Private destination")
        self.assertNotContains(response, "Older private")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_public_qr_handles_no_request_and_unknown_token(self):
        response = self.client.get(
            reverse(
                "core:public_student_exeat",
                args=[self.student.public_token],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No exeat request has been recorded")

        response = self.client.get(
            reverse("core:public_student_exeat", args=[uuid.uuid4()])
        )
        self.assertEqual(response.status_code, 404)
