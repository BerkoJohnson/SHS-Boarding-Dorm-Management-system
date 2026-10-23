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
    StaffNotification,
    StaffRole,
)


class DashboardApiTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.house = create_house("Unity House")
        self.other_house = create_house("Other House")
        self.own_student = create_student(
            "DB-001", full_name="Akosua Danso", house=self.house, gender="female"
        )
        self.own_student_two = create_student(
            "DB-002", full_name="Kwame Asare", house=self.house
        )
        self.other_student = create_student(
            "DB-003", full_name="Yaa Owusu", house=self.other_house, gender="female"
        )
        self.admin = create_staff("admin", role=StaffRole.ADMIN)
        self.hm = create_staff("hm", role=StaffRole.HOUSEMASTER, house=self.house)
        self.gate = create_staff("gate", role=StaffRole.GATE_OFFICER)
        self.medical = create_staff("medical", role=StaffRole.MEDICAL)

    def _get(self, username, path):
        token = token_from(login(self.client, username))
        return self.client.get(path, **auth(token))

    def _exeat(
        self,
        student,
        *,
        exeat_type=Exeat.ExeatType.TOWN,
        approved=False,
        departure_offset=1,
        return_offset=4,
        departure_actual=None,
    ):
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=student,
            exeat_type=exeat_type,
            expected_departure=now + timedelta(hours=departure_offset),
            expected_return=now + timedelta(hours=return_offset),
            destination="Town",
            reason="Appointment",
        )
        if approved:
            exeat.status = Exeat.Status.APPROVED
            exeat.approved_by_user = self.admin
            exeat.save()
            ExeatMovement.objects.create(
                exeat=exeat,
                actual_departure=(
                    now + timedelta(hours=departure_actual)
                    if departure_actual is not None
                    else None
                ),
            )
        return exeat

    def test_admin_dashboard_aggregates_school_wide(self):
        pending = self._exeat(self.own_student, departure_offset=2)
        self._exeat(
            self.other_student,
            approved=True,
            departure_offset=-2,
            return_offset=2,
            departure_actual=-1,
        )
        StaffNotification.objects.create(
            user=self.admin,
            kind=StaffNotification.Kind.EXEAT_REVIEW,
            title="Review needed",
            message="An emergency override needs review.",
        )

        response = self._get("admin", "/api/v1/dashboard/")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertTrue(data["scope"]["school_wide"])
        self.assertEqual(data["students"]["total"], 3)
        self.assertEqual(data["exeats"]["pending_approvals"], 1)
        self.assertEqual(data["exeats"]["active"], 1)
        self.assertEqual(data["exeats"]["on_pass"], 1)
        self.assertEqual(data["gate"]["on_pass"], 1)
        self.assertIn("roll_call", data)
        self.assertEqual(data["notifications"]["unread"]["total"], 1)
        self.assertEqual(data["exeats"]["latest_pending"][0]["id"], str(pending.pk))

    def test_housemaster_dashboard_is_house_scoped(self):
        own_pending = self._exeat(self.own_student, departure_offset=2)
        self._exeat(self.other_student, departure_offset=2)
        own_out = self._exeat(
            self.own_student_two,
            approved=True,
            departure_offset=-1,
            return_offset=2,
            departure_actual=-1,
        )
        other_out = self._exeat(
            self.other_student,
            approved=True,
            departure_offset=-1,
            return_offset=2,
            departure_actual=-1,
        )

        response = self._get("hm", "/api/v1/dashboard/")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertFalse(data["scope"]["school_wide"])
        self.assertEqual(data["scope"]["house"]["id"], str(self.house.pk))
        self.assertEqual(data["students"]["total"], 2)
        self.assertEqual(data["exeats"]["active"], 1)
        self.assertEqual(data["exeats"]["pending_approvals"], 1)
        self.assertEqual(
            [row["id"] for row in data["exeats"]["latest_pending"]],
            [str(own_pending.pk)],
        )
        # Sanity: the other house's passes exist but are excluded.
        self.assertTrue(Exeat.objects.filter(pk=other_out.pk).exists())
        self.assertTrue(Exeat.objects.filter(pk=own_out.pk).exists())

    def test_gate_officer_gets_gate_summary_but_no_student_registry(self):
        self._exeat(
            self.own_student,
            approved=True,
            departure_offset=-1,
            return_offset=2,
            departure_actual=-1,
        )
        self._exeat(self.other_student, departure_offset=3)

        response = self._get("gate", "/api/v1/dashboard/")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertNotIn("students", data)
        self.assertNotIn("pending_approvals", data.get("exeats", {}))
        self.assertEqual(data["gate"]["on_pass"], 1)
        self.assertEqual(data["exeats"]["on_pass"], 1)

    def test_medical_lead_only_sees_medical_exeats(self):
        medical_exeat = self._exeat(
            self.own_student,
            exeat_type=Exeat.ExeatType.MEDICAL,
            approved=True,
            departure_offset=-1,
            return_offset=2,
            departure_actual=-1,
        )
        self._exeat(
            self.other_student,
            exeat_type=Exeat.ExeatType.TOWN,
            approved=True,
            departure_offset=-1,
            return_offset=2,
            departure_actual=-1,
        )

        response = self._get("medical", "/api/v1/dashboard/")
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["data"]
        self.assertNotIn("students", data)
        self.assertEqual(data["exeats"]["active"], 1)
        self.assertEqual(data["exeats"]["pending_approvals"], 0)
        self.assertEqual(medical_exeat.exeat_type, Exeat.ExeatType.MEDICAL)

    def test_overdue_pass_is_counted(self):
        self._exeat(
            self.own_student,
            approved=True,
            departure_offset=-3,
            return_offset=-1,
            departure_actual=-3,
        )
        data = self._get("admin", "/api/v1/dashboard/").json()["data"]
        self.assertEqual(data["exeats"]["overdue"], 1)
        self.assertEqual(data["gate"]["overdue"], 1)
