"""Regression tests for the second audit remediation batch.

These tests exercise the production paths identified in
``AUDIT_REPORT_SHS_2026-10-19.md``:

* F-02 - multiple active (approved and unreturned) exeats are allowed for one
  student, with no application check and no database constraint.
* F-09 - duplicate in-progress roll-call sessions can be created, including
  house-wide sessions with a NULL dormitory.
* F-11 - expiry deletes unfinished roll calls and their attendance evidence.

Run in isolation with:

    python manage.py test core.test_audit_batch2 \
        --settings=schoolsystem.settings.test
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.checks import background_jobs_check
from core.models import (
    ROLL_CALL_SESSION_TIMEOUT,
    AcademicTerm,
    AcademicYear,
    Dormitory,
    Exeat,
    ExeatMovement,
    ExeatReconciliation,
    House,
    HouseMaster,
    Programme,
    RollCallAbsenceFollowUp,
    RollCallEntry,
    RollCallNotification,
    RollCallSession,
    StaffRole,
    Student,
    get_user_profile,
)
from core.services.roll_call import (
    complete_roll_call_session,
    create_roll_call_session,
    resolve_absence_follow_up,
    update_entry_status,
)
from core.tasks import expire_unfinished_roll_calls

User = get_user_model()


def _create_school_fixtures():
    """Create the smallest real school structure needed by the batch tests."""
    today = timezone.localdate()
    year = AcademicYear.objects.create(
        start_date=today,
        end_date=today + timezone.timedelta(days=300),
        is_current=True,
    )
    term = AcademicTerm.objects.create(
        academic_year=year,
        term=AcademicTerm.TermChoice.TERM_1,
        level=AcademicTerm.LevelChoice.ALL,
        start_date=today,
        end_date=today + timezone.timedelta(days=90),
        is_current=True,
    )
    programme = Programme.objects.create(name="Batch Two Science", code="B2SCI")
    house = House.objects.create(name="Batch Two House")
    dormitory = Dormitory.objects.create(
        house=house,
        name="Batch Two Block",
        gender=Dormitory.GenderBlock.MALE,
        capacity=20,
    )

    boarder = Student.objects.create(
        admission_no="BATCH2-001",
        index_number="BATCH2-IDX-001",
        full_name="Batch Two Student",
        gender=Student.Gender.MALE,
        programme=programme,
        house=house,
        dormitory=dormitory,
        residency=Student.ResidencyStatus.BOARDING,
    )

    hm_user = User.objects.create_user(
        username="batch2_hm",
        password="password123",
    )
    housemaster = HouseMaster.objects.create(
        user=hm_user,
        full_name="Batch Two Housemaster",
        staff_id="BATCH2-HM",
        phone_number="0240000101",
        house=house,
    )
    hm_profile = get_user_profile(hm_user)
    hm_profile.role = StaffRole.HOUSEMASTER
    hm_profile.save(update_fields=["role"])

    return SimpleNamespace(
        year=year,
        term=term,
        programme=programme,
        house=house,
        dormitory=dormitory,
        boarder=boarder,
        hm_user=hm_user,
        housemaster=housemaster,
    )


class OneActiveExeatBatch2Tests(TestCase):
    """F-02: only one approved and unreturned pass per student."""

    def setUp(self):
        self.f = _create_school_fixtures()
        self.now = timezone.now()

    def _make_exeat(
        self,
        number,
        *,
        status=Exeat.Status.PENDING,
        departure=None,
        return_at=None,
        approved_by=None,
    ):
        return Exeat.objects.create(
            exeat_number=number,
            student=self.f.boarder,
            academic_term=self.f.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=departure
            or (self.now - timezone.timedelta(minutes=30)),
            expected_return=return_at
            or (self.now + timezone.timedelta(hours=2)),
            destination="Town",
            reason="Batch 2 regression fixture",
            status=status,
            approved_by=approved_by,
        )

    def _approve(self, exeat):
        exeat.status = Exeat.Status.APPROVED
        exeat.approved_by = self.f.housemaster
        exeat.save()
        exeat.refresh_from_db()
        return exeat

    def test_first_approved_pass_is_marked_active(self):
        exeat = self._approve(self._make_exeat("EXT-BATCH2-ACTIVE"))

        self.assertTrue(exeat.is_active)

    def test_second_approval_is_rejected_while_first_is_unreturned(self):
        first = self._approve(self._make_exeat("EXT-BATCH2-FIRST"))
        second = self._make_exeat("EXT-BATCH2-SECOND")

        with self.assertRaises(ValidationError):
            self._approve(second)

        second.refresh_from_db()
        self.assertEqual(second.status, Exeat.Status.PENDING)
        self.assertFalse(second.is_active)
        self.assertEqual(
            Exeat.objects.filter(student=self.f.boarder, is_active=True).count(),
            1,
        )
        self.assertTrue(Exeat.objects.get(pk=first.pk).is_active)

    def test_pending_requests_do_not_block_the_first_approval(self):
        first = self._make_exeat("EXT-BATCH2-PENDING-ONE")
        second = self._make_exeat("EXT-BATCH2-PENDING-TWO")

        self._approve(first)

        with self.assertRaises(ValidationError):
            self._approve(second)

    def test_approval_is_allowed_after_gate_return_is_recorded(self):
        first = self._approve(self._make_exeat("EXT-BATCH2-RETURNED"))
        movement, _ = ExeatMovement.objects.get_or_create(exeat=first)
        movement.actual_departure = self.now - timezone.timedelta(hours=3)
        movement.save()
        movement.actual_return = self.now - timezone.timedelta(minutes=5)
        movement.save()

        first.refresh_from_db()
        self.assertFalse(first.is_active)

        second = self._approve(self._make_exeat("EXT-BATCH2-AFTER-RETURN"))
        self.assertTrue(second.is_active)

    def test_overdue_unreturned_pass_still_blocks_approval(self):
        first = self._approve(
            self._make_exeat(
                "EXT-BATCH2-OVERDUE",
                departure=self.now - timezone.timedelta(hours=5),
                return_at=self.now - timezone.timedelta(hours=1),
            )
        )
        movement, _ = ExeatMovement.objects.get_or_create(exeat=first)
        movement.actual_departure = self.now - timezone.timedelta(hours=5)
        movement.save()

        first.refresh_from_db()
        self.assertTrue(first.is_active)

        with self.assertRaises(ValidationError):
            self._approve(self._make_exeat("EXT-BATCH2-OVERDUE-SECOND"))

    def test_unused_reconciliation_releases_the_student(self):
        first = self._approve(self._make_exeat("EXT-BATCH2-UNUSED"))
        ExeatReconciliation.objects.create(
            exeat=first,
            outcome=ExeatReconciliation.Outcome.UNUSED,
            reason="The student did not travel and the pass was unused.",
            reconciled_by=self.f.hm_user,
        )

        first.refresh_from_db()
        self.assertFalse(first.is_active)

        second = self._approve(self._make_exeat("EXT-BATCH2-AFTER-UNUSED"))
        self.assertTrue(second.is_active)

    def test_student_out_reconciliation_blocks_until_return_is_recorded(self):
        first = self._approve(self._make_exeat("EXT-BATCH2-STILL-OUT"))
        reconciliation = ExeatReconciliation.objects.create(
            exeat=first,
            outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
            actual_departure=self.now - timezone.timedelta(hours=2),
            reason="Gate records were missed; the student is confirmed out.",
            reconciled_by=self.f.hm_user,
        )

        first.refresh_from_db()
        self.assertTrue(first.is_active)

        with self.assertRaises(ValidationError):
            self._approve(self._make_exeat("EXT-BATCH2-STILL-OUT-SECOND"))

        reconciliation.outcome = ExeatReconciliation.Outcome.RETURNED
        reconciliation.actual_return = self.now - timezone.timedelta(minutes=10)
        reconciliation.save()

        first.refresh_from_db()
        self.assertFalse(first.is_active)

        second = self._approve(self._make_exeat("EXT-BATCH2-AFTER-RETURN-RECON"))
        self.assertTrue(second.is_active)

    def test_cancelling_an_active_pass_releases_the_student(self):
        first = self._approve(self._make_exeat("EXT-BATCH2-CANCELLED"))
        first.status = Exeat.Status.CANCELLED
        first.save()

        first.refresh_from_db()
        self.assertFalse(first.is_active)

        second = self._approve(self._make_exeat("EXT-BATCH2-AFTER-CANCEL"))
        self.assertTrue(second.is_active)

    def test_database_constraint_rejects_a_second_active_row(self):
        first = self._approve(self._make_exeat("EXT-BATCH2-DB-FIRST"))
        second = self._make_exeat("EXT-BATCH2-DB-SECOND")
        self.assertTrue(first.is_active)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Exeat.objects.filter(pk=second.pk).update(
                status=Exeat.Status.APPROVED,
                is_active=True,
            )

    def test_approve_action_view_shows_a_friendly_conflict_message(self):
        first = self._approve(self._make_exeat("EXT-BATCH2-VIEW-FIRST"))
        second = self._make_exeat("EXT-BATCH2-VIEW-SECOND")
        self.client.force_login(self.f.hm_user)

        response = self.client.post(
            reverse("core:exeat_action", args=[second.pk, "approve"]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already has an active approved pass")
        second.refresh_from_db()
        self.assertEqual(second.status, Exeat.Status.PENDING)
        self.assertTrue(Exeat.objects.get(pk=first.pk).is_active)


class OneActiveExeatConcurrencyBatch2Tests(TransactionTestCase):
    """F-02: concurrent approvals must leave exactly one active pass."""

    def setUp(self):
        self.f = _create_school_fixtures()
        self.now = timezone.now()
        self.first = self._make_exeat("EXT-BATCH2-CONCURRENT-ONE")
        self.second = self._make_exeat("EXT-BATCH2-CONCURRENT-TWO")

    def _make_exeat(self, number):
        return Exeat.objects.create(
            exeat_number=number,
            student=self.f.boarder,
            academic_term=self.f.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=self.now - timezone.timedelta(minutes=15),
            expected_return=self.now + timezone.timedelta(hours=2),
            destination="Town",
            reason="Batch 2 concurrency fixture",
        )

    def test_concurrent_approvals_leave_one_active_pass(self):
        barrier = threading.Barrier(2)
        hm_pk = self.f.housemaster.pk

        def approve(exeat_pk):
            try:
                connections.close_all()
                barrier.wait(timeout=10)
                exeat = Exeat.objects.get(pk=exeat_pk)
                exeat.status = Exeat.Status.APPROVED
                exeat.approved_by_id = hm_pk
                exeat.save()
                return ("ok", exeat.pk)
            except Exception as error:  # pragma: no cover - surfaced in assertion
                return ("error", repr(error))
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(approve, self.first.pk),
                executor.submit(approve, self.second.pk),
            ]
            results = [future.result(timeout=30) for future in futures]

        successful = [pk for status, pk in results if status == "ok"]
        self.assertEqual(len(successful), 1, results)
        self.assertEqual(
            Exeat.objects.filter(student=self.f.boarder, is_active=True).count(),
            1,
            results,
        )


class RollCallUniqueConstraintBatch2Tests(TestCase):
    """F-09: a partial unique constraint blocks duplicate in-progress sessions."""

    def setUp(self):
        self.f = _create_school_fixtures()

    def _create(self, **overrides):
        values = {
            "house": self.f.house,
            "conducted_by": self.f.housemaster,
            "created_by": self.f.hm_user,
        }
        values.update(overrides)
        return create_roll_call_session(**values)

    def _duplicate_values(self, session):
        return {
            "house": self.f.house,
            "dormitory": session.dormitory,
            "session_type": session.session_type,
            "date": session.date,
            "status": RollCallSession.Status.IN_PROGRESS,
        }

    def test_house_wide_duplicate_is_rejected_at_database_level(self):
        first = self._create()
        self.assertIsNone(first.dormitory)

        with self.assertRaises(IntegrityError), transaction.atomic():
            RollCallSession.objects.bulk_create(
                [RollCallSession(**self._duplicate_values(first))]
            )

    def test_dormitory_duplicate_is_rejected_at_database_level(self):
        first = self._create(dormitory=self.f.dormitory)

        with self.assertRaises(IntegrityError), transaction.atomic():
            RollCallSession.objects.bulk_create(
                [RollCallSession(**self._duplicate_values(first))]
            )

    def test_new_session_is_allowed_after_the_previous_is_completed(self):
        first = self._create()
        first.entries.update(status=RollCallEntry.Status.PRESENT)
        complete_roll_call_session(first)

        second = self._create()

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(RollCallSession.objects.count(), 2)

    def test_house_and_dormitory_sessions_remain_distinct(self):
        house_session = self._create()
        dormitory_session = self._create(dormitory=self.f.dormitory)

        self.assertNotEqual(house_session.pk, dormitory_session.pk)
        self.assertEqual(RollCallSession.objects.count(), 2)


class RollCallExpiryPreservationBatch2Tests(TestCase):
    """F-11: expired sessions keep their status and entries instead of deletion."""

    def setUp(self):
        self.f = _create_school_fixtures()
        self.session = create_roll_call_session(
            house=self.f.house,
            conducted_by=self.f.housemaster,
            created_by=self.f.hm_user,
        )
        self.absent_entry = self.session.entries.get(student=self.f.boarder)
        update_entry_status(
            self.absent_entry,
            RollCallEntry.Status.ABSENT,
            updated_by=self.f.hm_user,
        )
        self.follow_up = self.absent_entry.absence_follow_ups.get()
        self._make_stale(self.session)

    def _make_stale(self, session):
        RollCallSession.objects.filter(pk=session.pk).update(
            created_at=timezone.now() - ROLL_CALL_SESSION_TIMEOUT - timezone.timedelta(minutes=1)
        )
        session.refresh_from_db()

    def test_expiry_task_preserves_the_session_and_its_entries(self):
        expired_count = expire_unfinished_roll_calls()

        self.assertEqual(expired_count, 1)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, RollCallSession.Status.EXPIRED)
        self.assertEqual(self.session.entries.count(), 1)
        self.assertEqual(
            self.session.entries.get().status,
            RollCallEntry.Status.ABSENT,
        )
        notification = RollCallNotification.objects.get(user=self.f.hm_user)
        self.assertIn("preserved", notification.message.lower())
        self.assertNotIn("deleted", notification.message.lower())

    def test_starting_a_new_muster_expires_the_stale_session(self):
        second = create_roll_call_session(
            house=self.f.house,
            conducted_by=self.f.housemaster,
            created_by=self.f.hm_user,
        )

        self.assertNotEqual(self.session.pk, second.pk)
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, RollCallSession.Status.EXPIRED)
        self.assertEqual(self.session.entries.count(), 1)
        self.assertGreaterEqual(second.entries.count(), 1)

    def test_expired_session_rejects_entry_updates_and_follow_ups(self):
        expire_unfinished_roll_calls()
        self.session.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "expired"):
            update_entry_status(
                self.session.entries.get(),
                RollCallEntry.Status.PRESENT,
                updated_by=self.f.hm_user,
            )
        with self.assertRaisesMessage(ValueError, "expired"):
            resolve_absence_follow_up(
                self.follow_up,
                RollCallAbsenceFollowUp.Status.CORRECTED,
                self.f.hm_user,
                "Late correction after expiry.",
                resulting_status=RollCallEntry.Status.PRESENT,
            )

    def test_expired_session_cannot_be_completed(self):
        expire_unfinished_roll_calls()
        self.session.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "expired"):
            complete_roll_call_session(self.session)


class BackgroundJobsConfigurationBatch2Tests(SimpleTestCase):
    """F-32: a web-only deployment must say so and must not imply SMS works."""

    @override_settings(BACKGROUND_JOBS_ENABLED=False, SMS_ENABLED=False)
    def test_disabled_background_jobs_emit_a_warning(self):
        messages = background_jobs_check(None)

        self.assertEqual([message.id for message in messages], ["core.W003"])
        self.assertIn("SHS 3", messages[0].msg)

    @override_settings(BACKGROUND_JOBS_ENABLED=False, SMS_ENABLED=True)
    def test_sms_enabled_without_workers_is_an_error(self):
        messages = background_jobs_check(None)

        self.assertEqual(
            {message.id for message in messages},
            {"core.W003", "core.E004"},
        )

    @override_settings(BACKGROUND_JOBS_ENABLED=True, SMS_ENABLED=True)
    def test_enabled_background_jobs_emit_no_warnings(self):
        self.assertEqual(background_jobs_check(None), [])
