from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AcademicTerm,
    AcademicYear,
    AuditEvent,
    Dormitory,
    Exeat,
    ExeatMovement,
    House,
    HouseMaster,
    Programme,
    Student,
)
from core.services.exeat_workflow import cancel_exeat
from core.throttling import RateLimitUnavailable, is_rate_limited


User = get_user_model()


class ExeatCancellationGuardTests(TestCase):
    def setUp(self):
        self.house = House.objects.create(name="Cancellation Guard House")
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name="Cancellation Guard Block",
            gender=Dormitory.GenderBlock.MALE,
        )
        programme = Programme.objects.create(name="Cancellation Guard Programme")
        self.student = Student.objects.create(
            admission_no="CANCEL-001",
            index_number="CANCEL-IDX-001",
            full_name="Cancellation Guard Student",
            gender=Student.Gender.MALE,
            programme=programme,
            house=self.house,
            dormitory=self.dormitory,
        )
        self.approver = HouseMaster.objects.create(
            full_name="Cancellation Guard Approver",
            staff_id="CANCEL-HM",
            phone_number="+233200000040",
            is_senior_housemaster=True,
        )
        self.actor = User.objects.create_user(username="cancel-actor")

    def create_exeat(self):
        now = timezone.now()
        return Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            reason="Approved visit",
            destination="Town",
            expected_departure=now - timedelta(minutes=5),
            expected_return=now + timedelta(hours=2),
            status=Exeat.Status.APPROVED,
            approved_by=self.approver,
        )

    def test_cancellation_succeeds_before_departure_and_is_audited(self):
        exeat = self.create_exeat()

        cancelled = cancel_exeat(exeat, actor=self.actor)

        self.assertEqual(cancelled.status, Exeat.Status.CANCELLED)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.EXEAT_CANCELLED,
                target_id=str(exeat.pk),
                after__status=Exeat.Status.CANCELLED,
            ).exists()
        )

    def test_cancellation_is_rejected_after_departure(self):
        exeat = self.create_exeat()
        ExeatMovement.objects.create(
            exeat=exeat,
            actual_departure=timezone.now() - timedelta(minutes=1),
        )

        with self.assertRaisesRegex(ValueError, "after gate departure"):
            cancel_exeat(exeat, actor=self.actor)

        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.APPROVED)

    @patch(
        "core.views.is_rate_limited",
        side_effect=RateLimitUnavailable("cache unavailable"),
    )
    def test_public_report_returns_service_unavailable_when_limiter_fails(self, _limiter):
        response = self.client.post(
            reverse("core:public_student_exeat", args=[self.student.public_token]),
            {"details": "A concern to report."},
        )

        self.assertEqual(response.status_code, 503)


class ActiveAcademicTermConstraintTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        self.year = AcademicYear.objects.create(
            start_date=today,
            end_date=today + timedelta(days=300),
            is_current=True,
        )
        self.first_term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=today,
            end_date=today + timedelta(days=90),
            is_current=True,
        )
        self.second_term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_2,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=today + timedelta(days=91),
            end_date=today + timedelta(days=180),
        )

    def test_database_rejects_two_current_terms_for_one_level(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AcademicTerm.objects.filter(pk=self.second_term.pk).update(
                    is_current=True
                )

        self.first_term.refresh_from_db()
        self.second_term.refresh_from_db()
        self.assertTrue(self.first_term.is_current)
        self.assertFalse(self.second_term.is_current)


class RateLimitFailureTests(SimpleTestCase):
    @patch("core.throttling.cache.add", side_effect=OSError("cache unavailable"))
    def test_cache_failure_is_reported_instead_of_disabling_the_limit(self, _add):
        with self.assertRaises(RateLimitUnavailable):
            is_rate_limited("login", "192.0.2.1", 10, 300)

    @patch(
        "core.views.is_rate_limited",
        side_effect=RateLimitUnavailable("cache unavailable"),
    )
    def test_login_returns_service_unavailable_when_limiter_fails(self, _limiter):
        response = self.client.post(reverse("core:login"), {})

        self.assertEqual(response.status_code, 503)
