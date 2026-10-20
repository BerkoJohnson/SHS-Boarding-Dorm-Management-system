"""Regression tests for the first audit remediation batch.

These tests exercise the production paths identified in
``AUDIT_REPORT_SHS_2026-10-19.md``:

* F-01 - gate lookup can select a future or non-actionable pass.
* F-09 - two roll-call starts can create duplicate in-progress sessions.
* F-10 - a completed/expired session can be mutated through absence follow-up.
* F-16 - the configured SMS sender ID is ignored in favour of the school name.

They are kept in a separate module so the batch can be run in isolation:

    python manage.py test core.test_audit_batch1 \
        --settings=schoolsystem.settings.test
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (
    ROLL_CALL_SESSION_TIMEOUT,
    AcademicTerm,
    AcademicYear,
    DisciplinaryRecord,
    Dormitory,
    Exeat,
    ExeatMovement,
    Guardian,
    House,
    HouseMaster,
    Programme,
    RollCallAbsenceFollowUp,
    RollCallEntry,
    RollCallSession,
    SchoolConfiguration,
    SMSConfiguration,
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
from core.services.sms import send_sms

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
    programme = Programme.objects.create(name="General Science", code="GSCI")
    house = House.objects.create(name="Batch One House")
    dormitory = Dormitory.objects.create(
        house=house,
        name="Batch One Block",
        gender=Dormitory.GenderBlock.MALE,
        capacity=20,
    )

    boarder_one = Student.objects.create(
        admission_no="BATCH1-001",
        index_number="BATCH1-IDX-001",
        full_name="Batch One Student",
        gender=Student.Gender.MALE,
        programme=programme,
        house=house,
        dormitory=dormitory,
        residency=Student.ResidencyStatus.BOARDING,
    )
    boarder_two = Student.objects.create(
        admission_no="BATCH1-002",
        index_number="BATCH1-IDX-002",
        full_name="Batch Two Student",
        gender=Student.Gender.MALE,
        programme=programme,
        house=house,
        dormitory=dormitory,
        residency=Student.ResidencyStatus.BOARDING,
    )

    hm_user = User.objects.create_user(
        username="batch1_hm",
        password="password123",
    )
    housemaster = HouseMaster.objects.create(
        user=hm_user,
        full_name="Batch One Housemaster",
        staff_id="BATCH1-HM",
        phone_number="0240000001",
        house=house,
    )

    gate_user = User.objects.create_user(
        username="batch1_gate",
        password="password123",
    )
    gate_housemaster = HouseMaster.objects.create(
        user=gate_user,
        full_name="Batch One Gate Officer",
        staff_id="BATCH1-GATE",
        phone_number="0240000002",
        house=house,
    )
    gate_profile = get_user_profile(gate_user)
    gate_profile.role = StaffRole.GATE_OFFICER
    gate_profile.save(update_fields=["role"])

    return SimpleNamespace(
        year=year,
        term=term,
        programme=programme,
        house=house,
        dormitory=dormitory,
        boarder_one=boarder_one,
        boarder_two=boarder_two,
        hm_user=hm_user,
        housemaster=housemaster,
        gate_user=gate_user,
        gate_housemaster=gate_housemaster,
    )


class GateLookupBatch1Tests(TestCase):
    def setUp(self):
        self.f = _create_school_fixtures()
        self.client.force_login(self.f.gate_user)

    def _make_exeat(
        self,
        student,
        number,
        status,
        departure,
        return_at,
        approved_by=None,
    ):
        return Exeat.objects.create(
            exeat_number=number,
            student=student,
            academic_term=self.f.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=departure,
            expected_return=return_at,
            destination="Town",
            reason="Batch 1 regression fixture",
            status=status,
            approved_by=approved_by,
        )

    def test_admission_lookup_prefers_current_approved_pass(self):
        now = timezone.now()
        current = self._make_exeat(
            self.f.boarder_one,
            "EXT-BATCH1-CURRENT",
            Exeat.Status.APPROVED,
            now - timezone.timedelta(minutes=30),
            now + timezone.timedelta(hours=2),
            approved_by=self.f.housemaster,
        )
        future = self._make_exeat(
            self.f.boarder_one,
            "EXT-BATCH1-FUTURE",
            Exeat.Status.PENDING,
            now + timezone.timedelta(days=1),
            now + timezone.timedelta(days=1, hours=2),
        )

        response = self.client.get(
            reverse("core:gate_verify_pass"),
            {"q": self.f.boarder_one.admission_no},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["exeat"].pk, current.pk)
        self.assertNotContains(response, future.exeat_number)

    def test_admission_lookup_does_not_treat_pending_pass_as_actionable(self):
        now = timezone.now()
        self._make_exeat(
            self.f.boarder_one,
            "EXT-BATCH1-PENDING",
            Exeat.Status.PENDING,
            now + timezone.timedelta(hours=1),
            now + timezone.timedelta(hours=3),
        )

        response = self.client.get(
            reverse("core:gate_verify_pass"),
            {"q": self.f.boarder_one.admission_no},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["exeat"])
        self.assertEqual(response.context["matches"], [])
        self.assertContains(response, "No approved pass found")

    def test_partial_pass_lookup_returns_disambiguation_list(self):
        now = timezone.now()
        first = self._make_exeat(
            self.f.boarder_one,
            "EXT-BATCH1-ALPHA",
            Exeat.Status.APPROVED,
            now - timezone.timedelta(minutes=15),
            now + timezone.timedelta(hours=1),
            approved_by=self.f.housemaster,
        )
        second = self._make_exeat(
            self.f.boarder_two,
            "EXT-BATCH1-BETA",
            Exeat.Status.APPROVED,
            now - timezone.timedelta(minutes=10),
            now + timezone.timedelta(hours=2),
            approved_by=self.f.housemaster,
        )

        response = self.client.get(
            reverse("core:gate_verify_pass"),
            {"q": "EXT-BATCH1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["exeat"])
        self.assertEqual(
            {exeat.pk for exeat in response.context["matches"]},
            {first.pk, second.pk},
        )
        self.assertContains(response, "Multiple approved passes matched")
        self.assertContains(response, first.exeat_number)
        self.assertContains(response, second.exeat_number)

    def test_exact_pass_number_still_shows_non_approved_status(self):
        now = timezone.now()
        pending = self._make_exeat(
            self.f.boarder_one,
            "EXT-BATCH1-EXACT-PENDING",
            Exeat.Status.PENDING,
            now + timezone.timedelta(hours=1),
            now + timezone.timedelta(hours=3),
        )

        response = self.client.get(
            reverse("core:gate_verify_pass"),
            {"q": pending.exeat_number},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["exeat"].pk, pending.pk)
        self.assertContains(response, "Pending")
        self.assertContains(response, "Cannot log movement")

    def test_disambiguation_puts_student_currently_out_first(self):
        now = timezone.now()
        out = self._make_exeat(
            self.f.boarder_one,
            "EXT-BATCH1-RANK-OUT",
            Exeat.Status.APPROVED,
            now - timezone.timedelta(hours=2),
            now + timezone.timedelta(hours=1),
            approved_by=self.f.housemaster,
        )
        ExeatMovement.objects.create(
            exeat=out,
            actual_departure=now - timezone.timedelta(hours=2),
            departure_recorded_by="Gate Officer",
        )
        self._make_exeat(
            self.f.boarder_two,
            "EXT-BATCH1-RANK-NEXT",
            Exeat.Status.APPROVED,
            now + timezone.timedelta(hours=1),
            now + timezone.timedelta(hours=3),
            approved_by=self.f.housemaster,
        )

        response = self.client.get(
            reverse("core:gate_verify_pass"),
            {"q": "EXT-BATCH1-RANK"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["matches"][0].pk, out.pk)


class RollCallDuplicateBatch1Tests(TestCase):
    def setUp(self):
        self.f = _create_school_fixtures()
        self.client.force_login(self.f.hm_user)

    def _create(self, **overrides):
        values = {
            "house": self.f.house,
            "conducted_by": self.f.housemaster,
            "created_by": self.f.hm_user,
        }
        values.update(overrides)
        return create_roll_call_session(**values)

    def test_second_create_reuses_in_progress_session(self):
        first = self._create()
        second = self._create()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(RollCallSession.objects.count(), 1)
        self.assertEqual(second.entries.count(), 2)

    def test_view_reports_reused_session_instead_of_starting_another(self):
        url = reverse("core:roll_call_create")
        payload = {
            "house": str(self.f.house.pk),
            "session_type": RollCallSession.SessionType.EVENING,
        }

        first_response = self.client.post(url, payload)
        self.assertEqual(first_response.status_code, 302)

        second_response = self.client.post(url, payload, follow=True)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(RollCallSession.objects.count(), 1)
        self.assertContains(second_response, "already open")

    def test_new_session_allowed_after_previous_is_completed(self):
        first = self._create()
        first.entries.update(status=RollCallEntry.Status.PRESENT)
        complete_roll_call_session(first)

        second = self._create()

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(RollCallSession.objects.count(), 2)

    def test_house_and_dormitory_sessions_are_distinct(self):
        house_session = self._create()
        dormitory_session = self._create(dormitory=self.f.dormitory)

        self.assertNotEqual(house_session.pk, dormitory_session.pk)
        self.assertEqual(RollCallSession.objects.count(), 2)


class RollCallConcurrencyBatch1Tests(TransactionTestCase):
    def setUp(self):
        self.f = _create_school_fixtures()

    def test_concurrent_creation_yields_one_session(self):
        barrier = threading.Barrier(2)

        def create_session():
            try:
                connections.close_all()
                barrier.wait(timeout=10)
                session = create_roll_call_session(
                    house=self.f.house,
                    conducted_by=self.f.housemaster,
                    created_by=self.f.hm_user,
                )
                return ("ok", session.pk)
            except Exception as error:  # pragma: no cover - surfaced in assertion
                return ("error", repr(error))
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(create_session) for _ in range(2)]
            results = [future.result(timeout=30) for future in futures]

        self.assertTrue(
            all(status == "ok" for status, _value in results),
            results,
        )
        self.assertEqual(len({pk for _status, pk in results}), 1)
        self.assertEqual(RollCallSession.objects.count(), 1)


class ClosedRollCallFollowUpBatch1Tests(TestCase):
    def setUp(self):
        self.f = _create_school_fixtures()
        self.session = create_roll_call_session(
            house=self.f.house,
            conducted_by=self.f.housemaster,
            created_by=self.f.hm_user,
        )
        self.absent_entry = self.session.entries.get(student=self.f.boarder_one)
        self.present_entry = self.session.entries.get(student=self.f.boarder_two)
        update_entry_status(
            self.absent_entry,
            RollCallEntry.Status.ABSENT,
            updated_by=self.f.hm_user,
        )
        update_entry_status(
            self.present_entry,
            RollCallEntry.Status.PRESENT,
            updated_by=self.f.hm_user,
        )
        self.follow_up = self.absent_entry.absence_follow_ups.get()

    def _resolve_correction(self):
        return resolve_absence_follow_up(
            self.follow_up,
            RollCallAbsenceFollowUp.Status.CORRECTED,
            self.f.hm_user,
            "A second staff count confirmed the student was present.",
            resulting_status=RollCallEntry.Status.PRESENT,
        )

    def test_completed_session_follow_up_is_rejected(self):
        complete_roll_call_session(self.session)

        with self.assertRaisesMessage(ValueError, "completed roll call"):
            self._resolve_correction()

        self.absent_entry.refresh_from_db()
        self.follow_up.refresh_from_db()
        self.assertEqual(self.absent_entry.status, RollCallEntry.Status.ABSENT)
        self.assertEqual(
            self.follow_up.status,
            RollCallAbsenceFollowUp.Status.PENDING,
        )
        self.assertFalse(
            DisciplinaryRecord.objects.filter(
                roll_call_follow_up=self.follow_up
            ).exists()
        )

    def test_expired_session_follow_up_is_rejected(self):
        RollCallSession.objects.filter(pk=self.session.pk).update(
            created_at=(
                timezone.now()
                - ROLL_CALL_SESSION_TIMEOUT
                - timezone.timedelta(minutes=1)
            )
        )
        self.session.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "expired roll call"):
            self._resolve_correction()

        self.absent_entry.refresh_from_db()
        self.follow_up.refresh_from_db()
        self.assertEqual(self.absent_entry.status, RollCallEntry.Status.ABSENT)
        self.assertEqual(
            self.follow_up.status,
            RollCallAbsenceFollowUp.Status.PENDING,
        )

    def test_completed_session_view_returns_error_without_mutation(self):
        complete_roll_call_session(self.session)
        self.client.force_login(self.f.hm_user)

        response = self.client.post(
            reverse(
                "core:roll_call_resolve_absence_follow_up",
                args=[self.follow_up.pk],
            ),
            {
                "resolution": RollCallAbsenceFollowUp.Status.CORRECTED,
                "resulting_status": RollCallEntry.Status.PRESENT,
                "resolution_note": (
                    "A second staff count confirmed the student was present."
                ),
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("completed roll call", response["HX-Trigger"])
        self.absent_entry.refresh_from_db()
        self.follow_up.refresh_from_db()
        self.assertEqual(self.absent_entry.status, RollCallEntry.Status.ABSENT)
        self.assertEqual(
            self.follow_up.status,
            RollCallAbsenceFollowUp.Status.PENDING,
        )

    def test_pending_follow_up_can_be_resolved_before_completion(self):
        self._resolve_correction()
        complete_roll_call_session(self.session)

        self.session.refresh_from_db()
        self.absent_entry.refresh_from_db()
        self.assertEqual(self.session.status, RollCallSession.Status.COMPLETED)
        self.assertEqual(self.absent_entry.status, RollCallEntry.Status.PRESENT)


class SMSSenderBatch1Tests(TestCase):
    def setUp(self):
        self.house = House.objects.create(name="SMS Batch House")
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name="SMS Batch Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=10,
        )
        self.programme = Programme.objects.create(
            name="SMS Batch Programme",
            code="SMSB",
        )
        self.guardian = Guardian.objects.create(
            full_name="SMS Batch Guardian",
            phone_number="0240000000",
        )
        self.student = Student.objects.create(
            admission_no="SMS-BATCH-001",
            index_number="SMS-BATCH-IDX-001",
            full_name="SMS Batch Student",
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
            guardian=self.guardian,
            residency=Student.ResidencyStatus.BOARDING,
        )
        self.housemaster = HouseMaster.objects.create(
            full_name="SMS Batch Housemaster",
            staff_id="SMS-BATCH-HM",
            phone_number="0240000001",
            house=self.house,
        )
        now = timezone.now()
        self.exeat = Exeat.objects.create(
            student=self.student,
            academic_term=None,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=2),
            destination="Town",
            reason="SMS sender regression",
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster,
        )
        SMSConfiguration.objects.update_or_create(
            pk=1,
            defaults={"enabled": True},
        )
        SchoolConfiguration.objects.update_or_create(
            pk=1,
            defaults={
                "name": "Atebubu Senior High School",
                "short_name": "Atebubu High",
            },
        )

    @override_settings(
        SMS_ONLINE_GH_API_KEY="batch1-test-key",
        SMS_SENDER_ID="EDEN",
        TENANCY_ENABLED=False,
    )
    def test_configured_sms_sender_id_is_used(self):
        with (
            patch(
                "core.services.sms.sms_quota_allows",
                return_value=True,
            ),
            patch("core.services.sms.requests.post") as post,
        ):
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {
                "handshake": {"label": "HSHK_OK"}
            }

            self.assertTrue(send_sms("+233240000000", "Batch 1 sender test"))

        payload = post.call_args.kwargs["data"]
        self.assertEqual(payload["sender"], "EDEN")

    @override_settings(
        SMS_ONLINE_GH_API_KEY="batch1-test-key",
        SMS_SENDER_ID="Eden Sys GH!",
        TENANCY_ENABLED=False,
    )
    def test_configured_sms_sender_id_is_sanitised(self):
        with (
            patch(
                "core.services.sms.sms_quota_allows",
                return_value=True,
            ),
            patch("core.services.sms.requests.post") as post,
        ):
            post.return_value.raise_for_status.return_value = None
            post.return_value.json.return_value = {
                "handshake": {"label": "HSHK_OK"}
            }

            self.assertTrue(send_sms("+233240000000", "Batch 1 sender test"))

        payload = post.call_args.kwargs["data"]
        self.assertEqual(payload["sender"], "EDENSYSGH")
