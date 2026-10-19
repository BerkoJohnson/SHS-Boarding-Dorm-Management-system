from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.forms import ExeatRequestForm
from core.models import (
    AcademicYear,
    ClassGroup,
    Dormitory,
    Exeat,
    ExeatMovement,
    House,
    HouseMaster,
    Programme,
    RollCallSession,
    Student,
    StudentProgressionRecord,
    StaffRole,
    UserProfile,
)
from core.services.roll_call import create_roll_call_session
from core.services.student_progression import promote_student_cohort
from core.tasks import _phase_out_shs3_students_for_current_year


class StudentProgressionTests(TestCase):
    def setUp(self):
        self.year = AcademicYear.objects.create(
            start_date=timezone.datetime(2026, 1, 1).date(),
            end_date=timezone.datetime(2026, 12, 31).date(),
            is_current=True,
        )
        self.programme = Programme.objects.create(
            name="Progression Science",
            code="PRSCI",
        )
        self.house = House.objects.create(name="Progression House")
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name="Progression Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=20,
        )
        self.shs1 = ClassGroup.objects.create(name="1A1", programme=self.programme)
        self.shs2 = ClassGroup.objects.create(name="2A1", programme=self.programme)
        self.shs3 = ClassGroup.objects.create(name="3A1", programme=self.programme)
        self.admin = get_user_model().objects.create_user(
            username="progression-admin",
            password="password123",
        )
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={"role": StaffRole.ADMIN},
        )

    def create_student(self, admission_no, class_group, **overrides):
        values = {
            "admission_no": admission_no,
            "index_number": f"IDX-{admission_no}",
            "full_name": f"Student {admission_no}",
            "gender": Student.Gender.MALE,
            "programme": self.programme,
            "house": self.house,
            "dormitory": self.dormitory,
            "class_group": class_group,
        }
        values.update(overrides)
        return Student.objects.create(**values)

    def test_promotion_preview_and_confirmation_record_class_history(self):
        student = self.create_student("PROMO-001", self.shs1)
        self.client.force_login(self.admin)
        url = reverse("core:student_promotion")

        preview = self.client.get(
            url,
            {
                "preview": "1",
                "academic_year": self.year.pk,
                "source_class": self.shs1.pk,
                "target_class": self.shs2.pk,
            },
        )

        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, student.admission_no)
        self.assertContains(preview, "1 enrolled student(s)")

        response = self.client.post(
            url,
            {
                "academic_year": self.year.pk,
                "source_class": self.shs1.pk,
                "target_class": self.shs2.pk,
            },
        )

        self.assertRedirects(response, url)
        student.refresh_from_db()
        self.assertEqual(student.class_group, self.shs2)
        record = StudentProgressionRecord.objects.get(student=student)
        self.assertEqual(record.event, StudentProgressionRecord.Event.PROMOTED)
        self.assertEqual(record.from_class_group, self.shs1)
        self.assertEqual(record.to_class_group, self.shs2)
        self.assertEqual(record.actor, self.admin)

    def test_promotion_service_rejects_skipping_a_form(self):
        self.create_student("PROMO-002", self.shs1)

        with self.assertRaisesMessage(ValueError, "consecutive SHS classes"):
            promote_student_cohort(self.year, self.shs1, self.shs3, self.admin)

    def test_promotion_into_shs3_sets_the_exact_completion_year(self):
        shs3_year = AcademicYear.objects.create(
            start_date=timezone.datetime(2027, 1, 1).date(),
            end_date=timezone.datetime(2027, 12, 31).date(),
        )
        student = self.create_student("PROMO-SHS3", self.shs2)

        promote_student_cohort(shs3_year, self.shs2, self.shs3, self.admin)

        student.refresh_from_db()
        self.assertEqual(student.class_group, self.shs3)
        self.assertEqual(student.expected_completion_year, shs3_year)

    def test_promotion_page_is_limited_to_student_management_roles(self):
        tutor = get_user_model().objects.create_user(
            username="progression-tutor",
            password="password123",
        )
        UserProfile.objects.update_or_create(
            user=tutor,
            defaults={"role": StaffRole.HOUSE_TUTOR},
        )
        self.client.force_login(tutor)

        response = self.client.get(reverse("core:student_promotion"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:dashboard"))

    def test_shs3_students_are_completed_after_year_end_only_once(self):
        graduating_student = self.create_student("GRAD-001", self.shs3)
        self.assertEqual(graduating_student.expected_completion_year, self.year)
        continuing_student = self.create_student("GRAD-002", self.shs2)
        approver = HouseMaster.objects.create(
            full_name="Progression Approver",
            staff_id="PROGRESSION-HM",
            phone_number="+233200000020",
            house=self.house,
        )
        now = timezone.make_aware(timezone.datetime(2026, 12, 20, 12, 0))
        exeat = Exeat.objects.create(
            student=graduating_student,
            exeat_type=Exeat.ExeatType.TOWN,
            reason="Approved visit",
            destination="Town",
            expected_departure=now,
            expected_return=now + timezone.timedelta(days=1),
            status=Exeat.Status.APPROVED,
            approved_by=approver,
        )
        movement = ExeatMovement.objects.create(
            exeat=exeat,
            actual_departure=now,
            departure_recorded_by="Gate staff",
        )
        self.year.is_current = False
        self.year.save(update_fields=["is_current"])

        with patch(
            "core.tasks.timezone.localdate",
            return_value=self.year.end_date - timezone.timedelta(days=1),
        ):
            self.assertEqual(_phase_out_shs3_students_for_current_year(), 0)
        self.assertEqual(
            graduating_student.status,
            Student.EnrollmentStatus.ENROLLED,
        )

        with patch(
            "core.tasks.timezone.localdate",
            return_value=self.year.end_date,
        ):
            self.assertEqual(_phase_out_shs3_students_for_current_year(), 1)
            self.assertEqual(_phase_out_shs3_students_for_current_year(), 0)

        graduating_student.refresh_from_db()
        movement.refresh_from_db()
        self.assertEqual(
            graduating_student.status,
            Student.EnrollmentStatus.COMPLETED,
        )
        self.assertEqual(graduating_student.completed_on, self.year.end_date)
        self.assertEqual(graduating_student.completion_academic_year, self.year)
        self.assertEqual(graduating_student.expected_completion_year, self.year)
        self.assertIsNone(movement.actual_return)
        self.assertEqual(movement.departure_recorded_by, "Gate staff")
        self.assertEqual(
            StudentProgressionRecord.objects.filter(
                student=graduating_student,
                event=StudentProgressionRecord.Event.PHASED_OUT,
            ).count(),
            1,
        )
        self.assertEqual(
            continuing_student.status,
            Student.EnrollmentStatus.ENROLLED,
        )

        self.client.force_login(self.admin)
        dashboard = self.client.get(reverse("core:dashboard"))
        self.assertEqual(dashboard.context["total_boarders"], 1)
        registry = self.client.get(reverse("core:student_registry"))
        self.assertContains(registry, graduating_student.full_name)

        session = create_roll_call_session(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
        )
        self.assertQuerySetEqual(
            session.entries.values_list("student_id", flat=True),
            [continuing_student.pk],
        )

        self.client.force_login(self.admin)
        record_page = self.client.get(
            reverse("core:student_record", args=[graduating_student.pk])
        )
        self.assertEqual(record_page.status_code, 200)
        self.assertContains(record_page, "Class progression")
        self.assertContains(record_page, "Completed SHS 3")

    def test_completed_students_do_not_consume_room_capacity_or_request_exeats(self):
        room = Dormitory.objects.get(pk=self.dormitory.pk).rooms.create(
            name="One Bed Room",
            capacity=1,
        )
        completed_student = self.create_student("COMPLETED-001", self.shs3, room=room)
        StudentProgressionRecord.objects.create(
            student=completed_student,
            academic_year=self.year,
            event=StudentProgressionRecord.Event.PHASED_OUT,
            from_class_group=self.shs3,
            effective_on=self.year.end_date,
        )
        completed_student.status = Student.EnrollmentStatus.COMPLETED
        completed_student.completed_on = self.year.end_date
        completed_student.completion_academic_year = self.year
        completed_student.save()

        next_student = self.create_student(
            "CURRENT-001",
            self.shs1,
            room=room,
        )
        form = ExeatRequestForm()

        self.assertEqual(next_student.room, room)
        self.assertNotIn(
            completed_student,
            form.fields["student"].queryset,
        )
        departure = timezone.now()
        with self.assertRaisesMessage(
            ValidationError,
            "Completed students cannot receive new exeat passes.",
        ):
            Exeat.objects.create(
                student=completed_student,
                exeat_type=Exeat.ExeatType.TOWN,
                reason="New request",
                destination="Town",
                expected_departure=departure,
                expected_return=departure + timezone.timedelta(hours=2),
            )

    def test_completed_status_requires_an_audited_phase_out_record(self):
        with self.assertRaisesMessage(
            ValidationError,
            "Students can only be completed through the audited phase-out process.",
        ):
            self.create_student(
                "UNAUDITED-COMPLETION",
                self.shs3,
                status=Student.EnrollmentStatus.COMPLETED,
                completed_on=self.year.end_date,
                completion_academic_year=self.year,
                expected_completion_year=self.year,
            )
