from django.contrib.auth import get_user_model
from django.test import TestCase
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
from core.services.gate_movements import record_gate_departure, record_gate_return


class GateMovementConcurrencyGuardTests(TestCase):
    def setUp(self):
        programme = Programme.objects.create(name="Gate Movement Science")
        house = House.objects.create(name="Gate Movement House")
        dormitory = Dormitory.objects.create(
            house=house,
            name="Gate Movement Block",
            gender=Dormitory.GenderBlock.MALE,
        )
        student = Student.objects.create(
            admission_no="GATE-001",
            index_number="GATE-IDX-001",
            full_name="Gate Movement Student",
            gender=Student.Gender.MALE,
            programme=programme,
            house=house,
            dormitory=dormitory,
        )
        approver = HouseMaster.objects.create(
            full_name="Gate Movement Approver",
            staff_id="GATE-SENIOR",
            phone_number="+233200000030",
            is_senior_housemaster=True,
        )
        now = timezone.now()
        self.exeat = Exeat.objects.create(
            student=student,
            exeat_type=Exeat.ExeatType.TOWN,
            reason="Approved visit",
            destination="Town",
            expected_departure=now - timezone.timedelta(minutes=5),
            expected_return=now + timezone.timedelta(hours=2),
            status=Exeat.Status.APPROVED,
            approved_by=approver,
        )
        user_model = get_user_model()
        self.first_actor = user_model.objects.create_user(username="gate-first")
        self.second_actor = user_model.objects.create_user(username="gate-second")

    def test_duplicate_departure_does_not_replace_timestamp_or_recorder(self):
        first_time = timezone.now()
        later_time = first_time + timezone.timedelta(minutes=1)

        _, first_recorded, _ = record_gate_departure(
            self.exeat,
            actor=self.first_actor,
            recorded_by="First Officer",
            at=first_time,
        )
        _, second_recorded, _ = record_gate_departure(
            self.exeat,
            actor=self.second_actor,
            recorded_by="Second Officer",
            at=later_time,
        )

        movement = ExeatMovement.objects.get(exeat=self.exeat)
        self.assertTrue(first_recorded)
        self.assertFalse(second_recorded)
        self.assertEqual(movement.actual_departure, first_time)
        self.assertEqual(movement.departure_recorded_by, "First Officer")
        self.assertEqual(movement.departure_recorded_by_user, self.first_actor)

    def test_duplicate_return_does_not_replace_timestamp_or_recorder(self):
        departure_time = timezone.now()
        first_return_time = departure_time + timezone.timedelta(hours=1)
        later_return_time = first_return_time + timezone.timedelta(minutes=1)
        record_gate_departure(
            self.exeat,
            actor=self.first_actor,
            recorded_by="First Officer",
            at=departure_time,
        )

        _, first_recorded = record_gate_return(
            self.exeat,
            actor=self.first_actor,
            recorded_by="First Officer",
            at=first_return_time,
        )
        _, second_recorded = record_gate_return(
            self.exeat,
            actor=self.second_actor,
            recorded_by="Second Officer",
            at=later_return_time,
        )

        movement = ExeatMovement.objects.get(exeat=self.exeat)
        self.assertTrue(first_recorded)
        self.assertFalse(second_recorded)
        self.assertEqual(movement.actual_return, first_return_time)
        self.assertEqual(movement.return_recorded_by, "First Officer")
        self.assertEqual(movement.return_recorded_by_user, self.first_actor)
