from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    DisciplinaryRecord,
    Dormitory,
    Exeat,
    House,
    HouseMaster,
    Programme,
    RollCallAbsenceFollowUp,
    RollCallEntry,
    RollCallSession,
    StaffRole,
    Student,
    UserProfile,
)


User = get_user_model()


class StudentRecordViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.programme = Programme.objects.create(name="Record Science", code="RSCI")
        cls.house = House.objects.create(name="Record House")
        cls.other_house = House.objects.create(name="Other Record House")
        cls.dormitory = Dormitory.objects.create(
            house=cls.house,
            name="Record Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=20,
        )
        cls.student = Student.objects.create(
            admission_no="RECORD-001",
            index_number="RECORD-IDX-001",
            full_name="Student Record One",
            gender=Student.Gender.MALE,
            programme=cls.programme,
            house=cls.house,
            dormitory=cls.dormitory,
            residency=Student.ResidencyStatus.BOARDING,
        )
        cls.other_student = Student.objects.create(
            admission_no="RECORD-002",
            index_number="RECORD-IDX-002",
            full_name="Student Record Two",
            gender=Student.Gender.MALE,
            programme=cls.programme,
            house=cls.other_house,
            dormitory=Dormitory.objects.create(
                house=cls.other_house,
                name="Other Record Block",
                gender=Dormitory.GenderBlock.MALE,
                capacity=20,
            ),
            residency=Student.ResidencyStatus.BOARDING,
        )
        cls.admin = cls.create_role_user("record-admin", StaffRole.ADMIN)
        cls.senior = cls.create_role_user(
            "record-senior",
            StaffRole.SENIOR_HOUSEMASTER,
        )
        cls.housemaster = cls.create_role_user(
            "record-housemaster",
            StaffRole.HOUSEMASTER,
        )
        HouseMaster.objects.create(
            user=cls.housemaster,
            full_name="Record Housemaster",
            staff_id="RECORD-HM-1",
            phone_number="+233200000001",
            house=cls.house,
        )
        cls.tutor = cls.create_role_user("record-tutor", StaffRole.HOUSE_TUTOR)

    @classmethod
    def create_role_user(cls, username, role):
        user = User.objects.create_user(username=username, password="password123")
        UserProfile.objects.update_or_create(user=user, defaults={"role": role})
        return user

    def test_requested_staff_roles_can_open_student_record(self):
        for user in (self.admin, self.senior, self.housemaster):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse("core:student_record", args=[self.student.pk])
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["student"], self.student)
                self.assertContains(response, "View full record")

    def test_housemaster_cannot_open_student_from_another_house(self):
        self.client.force_login(self.housemaster)

        response = self.client.get(
            reverse("core:student_record", args=[self.other_student.pk])
        )

        self.assertEqual(response.status_code, 404)

    def test_other_roles_cannot_open_student_record(self):
        self.client.force_login(self.tutor)

        response = self.client.get(
            reverse("core:student_record", args=[self.student.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:dashboard"))

    def test_summary_is_initially_collapsed_and_shows_record_counts(self):
        now = timezone.now()
        Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now + timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=3),
            destination="Record destination",
            reason="Record reason",
        )
        DisciplinaryRecord.objects.create(
            student=self.student,
            offense="Record infraction",
            description="Record description",
            severity=DisciplinaryRecord.Severity.MAJOR,
            action_taken="Record action",
        )
        session = RollCallSession.objects.create(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
            date=timezone.localdate(),
            status=RollCallSession.Status.IN_PROGRESS,
        )
        entry = RollCallEntry.objects.create(
            session=session,
            student=self.student,
            status=RollCallEntry.Status.ABSENT,
            remarks="Record attendance note",
        )
        RollCallAbsenceFollowUp.objects.create(
            entry=entry,
            status=RollCallAbsenceFollowUp.Status.PENDING,
            resolution_note="Follow-up note",
        )
        self.client.force_login(self.housemaster)

        response = self.client.get(
            reverse("core:student_record", args=[self.student.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["exeat_count"], 1)
        self.assertEqual(response.context["disciplinary_record_count"], 1)
        self.assertEqual(response.context["roll_call_entry_count"], 1)
        self.assertEqual(response.context["pending_absence_follow_up_count"], 1)
        self.assertIn(b"<details", response.content)
        self.assertNotIn(b"<details open", response.content)
        self.assertContains(response, "Record destination")
        self.assertContains(response, "Record infraction")
        self.assertContains(response, "Record attendance note")
        self.assertContains(response, "Follow-up note")

    def test_record_links_appear_in_student_search_for_authorized_roles(self):
        self.client.force_login(self.housemaster)

        response = self.client.get(reverse("core:global_search"), {"q": "RECORD-001"})

        self.assertContains(
            response,
            reverse("core:student_record", args=[self.student.pk]),
        )

    def test_empty_history_has_explicit_empty_states(self):
        self.client.force_login(self.housemaster)

        response = self.client.get(
            reverse("core:student_record", args=[self.student.pk])
        )

        self.assertEqual(response.context["exeat_count"], 0)
        self.assertEqual(response.context["disciplinary_record_count"], 0)
        self.assertEqual(response.context["roll_call_entry_count"], 0)
        self.assertContains(response, "No exeat records have been recorded")
        self.assertContains(response, "No disciplinary records have been recorded")
        self.assertContains(response, "No roll-call records have been recorded")
