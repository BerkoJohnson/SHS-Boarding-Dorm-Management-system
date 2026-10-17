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
    Student,
)


class PublicStudentExeatPriorityTests(TestCase):
    def setUp(self):
        programme = Programme.objects.create(name="Active Exeat Science")
        house = House.objects.create(name="Active Exeat House")
        dormitory = Dormitory.objects.create(
            house=house,
            name="Active Exeat Block",
            gender=Dormitory.GenderBlock.MALE,
        )
        self.student = Student.objects.create(
            admission_no="ACTIVE-001",
            index_number="ACTIVE-IDX-001",
            full_name="Active Exeat Student",
            gender=Student.Gender.MALE,
            programme=programme,
            house=house,
            dormitory=dormitory,
        )
        self.approver = HouseMaster.objects.create(
            full_name="Active Exeat Approver",
            staff_id="ACTIVE-SENIOR",
            phone_number="+233200000020",
            is_senior_housemaster=True,
        )

    def create_exeat(self, **overrides):
        now = timezone.now()
        values = {
            "student": self.student,
            "exeat_type": Exeat.ExeatType.TOWN,
            "expected_departure": now + timezone.timedelta(hours=1),
            "expected_return": now + timezone.timedelta(hours=4),
            "destination": "Private destination",
            "reason": "Private reason",
        }
        values.update(overrides)
        return Exeat.objects.create(**values)

    def get_public_response(self):
        return self.client.get(
            reverse(
                "core:public_student_exeat",
                args=[self.student.public_token],
            )
        )

    def test_active_approved_exeat_takes_priority_over_a_newer_request(self):
        now = timezone.now()
        self.create_exeat(
            status=Exeat.Status.APPROVED,
            approved_by=self.approver,
            expected_departure=now - timezone.timedelta(minutes=30),
            expected_return=now + timezone.timedelta(hours=2),
        )
        self.create_exeat(
            expected_departure=now + timezone.timedelta(hours=3),
            expected_return=now + timezone.timedelta(hours=5),
        )

        response = self.get_public_response()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["approved_exeat_active"])
        self.assertFalse(response.context["movement_authorized"])
        self.assertContains(response, "Departure not verified")
        self.assertNotIn("exeat", response.context)
        self.assertNotIn("movement", response.context)
        self.assertNotContains(response, "Private destination")
        self.assertNotContains(response, "Private reason")

    def test_returned_or_expired_approval_is_not_prioritized_as_active(self):
        now = timezone.now()
        returned_exeat = self.create_exeat(
            status=Exeat.Status.APPROVED,
            approved_by=self.approver,
            expected_departure=now - timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=1),
        )
        ExeatMovement.objects.create(
            exeat=returned_exeat,
            actual_departure=now - timezone.timedelta(minutes=45),
            actual_return=now - timezone.timedelta(minutes=15),
        )
        self.create_exeat(
            status=Exeat.Status.APPROVED,
            approved_by=self.approver,
            expected_departure=now - timezone.timedelta(hours=5),
            expected_return=now - timezone.timedelta(hours=1),
        )
        self.create_exeat(
            expected_departure=now + timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=3),
        )

        response = self.get_public_response()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["approved_exeat_active"])
        self.assertFalse(response.context["movement_authorized"])
        self.assertContains(response, "No active authorization found")
        self.assertNotIn("exeat", response.context)
        self.assertNotIn("movement", response.context)
        self.assertNotContains(response, "Private destination")
        self.assertNotContains(response, "Private reason")

    def test_overdue_departed_exeat_remains_active_until_return(self):
        now = timezone.now()
        overdue_exeat = self.create_exeat(
            status=Exeat.Status.APPROVED,
            approved_by=self.approver,
            expected_departure=now - timezone.timedelta(hours=5),
            expected_return=now - timezone.timedelta(hours=1),
        )
        ExeatMovement.objects.create(
            exeat=overdue_exeat,
            actual_departure=now - timezone.timedelta(hours=4, minutes=30),
        )
        self.create_exeat(
            expected_departure=now + timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=3),
        )

        response = self.get_public_response()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["approved_exeat_active"])
        self.assertTrue(response.context["departure_recorded"])
        self.assertTrue(response.context["movement_authorized"])
        self.assertContains(response, "Authorized movement")
        self.assertContains(response, "has not yet been recorded as returned")
        self.assertNotIn("exeat", response.context)
        self.assertNotIn("movement", response.context)
        self.assertNotContains(response, "Private destination")
        self.assertNotContains(response, "Private reason")
