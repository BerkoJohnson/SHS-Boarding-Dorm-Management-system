"""Regression tests for the Full P1 boarding workflows (D2-D5, F-07, F-12, F-25/F-30).

The tests intentionally exercise the service layer and the small number of
view endpoints that enforce policy, rather than only testing model fields.
Run with:

    python manage.py test core.test_full_p1 \
        --settings=schoolsystem.settings.test --keepdb
"""

from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AcademicTerm,
    AcademicYear,
    AuditEvent,
    ConfigurationAuditLog,
    DataExportLog,
    DisputeCase,
    Dormitory,
    DormitoryCapacityException,
    Exeat,
    ExeatMovement,
    ExeatReview,
    ExeatScheduleChange,
    GateDutyAssignment,
    House,
    HouseMaster,
    LateReturnIncident,
    OperationalSettings,
    Programme,
    RollCallCorrection,
    RollCallEntry,
    RollCallSession,
    StaffNotification,
    StaffRole,
    Student,
    get_approvable_pending_for_user,
    get_user_profile,
)
from core.permissions import can_log_gate_check
from core.services.disputes import decide_dispute, open_dispute
from core.services.exeat_workflow import (
    approve_with_override,
    decide_exeat_review,
    reschedule_exeat,
)
from core.services.late_returns import open_late_return_incident
from core.services.roll_call_corrections import (
    request_roll_call_correction,
    review_roll_call_correction,
)
from core.tasks import enqueue_overdue_exeat_reminders

User = get_user_model()


def _make_fixtures():
    today = timezone.localdate()
    year = AcademicYear.objects.create(
        start_date=today,
        end_date=today + timedelta(days=300),
        is_current=True,
    )
    term = AcademicTerm.objects.create(
        academic_year=year,
        term=AcademicTerm.TermChoice.TERM_1,
        level=AcademicTerm.LevelChoice.ALL,
        start_date=today,
        end_date=today + timedelta(days=90),
        is_current=True,
    )
    programme = Programme.objects.create(name="P1 Science", code="P1SCI")
    house = House.objects.create(name="P1 House")
    dormitory = Dormitory.objects.create(
        house=house,
        name="P1 Block",
        gender=Dormitory.GenderBlock.MALE,
        capacity=10,
    )
    student = Student.objects.create(
        admission_no="P1-001",
        index_number="P1-IDX-001",
        full_name="P1 Student",
        gender=Student.Gender.MALE,
        programme=programme,
        house=house,
        dormitory=dormitory,
        residency=Student.ResidencyStatus.BOARDING,
    )

    hm_user = User.objects.create_user(username="p1_hm", password="password123")
    hm = HouseMaster.objects.create(
        user=hm_user,
        full_name="P1 Housemaster",
        staff_id="P1-HM",
        phone_number="0240000201",
        house=house,
    )
    hm_profile = get_user_profile(hm_user)
    hm_profile.role = StaffRole.HOUSEMASTER
    hm_profile.save(update_fields=["role"])

    senior_user = User.objects.create_user(
        username="p1_senior", password="password123"
    )
    senior_hm = HouseMaster.objects.create(
        user=senior_user,
        full_name="P1 Senior Housemaster",
        staff_id="P1-SENIOR",
        phone_number="0240000202",
        is_senior_housemaster=True,
    )
    senior_profile = get_user_profile(senior_user)
    senior_profile.role = StaffRole.SENIOR_HOUSEMASTER
    senior_profile.save(update_fields=["role"])

    admin_user = User.objects.create_superuser(
        username="p1_admin",
        email="admin@example.com",
        password="password123",
    )
    medical_user = User.objects.create_user(
        username="p1_medical", password="password123"
    )
    medical_profile = get_user_profile(medical_user)
    medical_profile.role = StaffRole.MEDICAL
    medical_profile.save(update_fields=["role"])

    return SimpleNamespace(
        year=year,
        term=term,
        programme=programme,
        house=house,
        dormitory=dormitory,
        student=student,
        hm_user=hm_user,
        hm=hm,
        senior_user=senior_user,
        senior_hm=senior_hm,
        admin_user=admin_user,
        medical_user=medical_user,
    )


def _make_exeat(
    f,
    *,
    status=Exeat.Status.PENDING,
    exeat_type=Exeat.ExeatType.TOWN,
    expected_departure=None,
    expected_return=None,
    approved_by=None,
):
    now = timezone.now()
    return Exeat.objects.create(
        student=f.student,
        academic_term=f.term,
        exeat_type=exeat_type,
        reason="Full P1 regression fixture",
        destination="Town",
        expected_departure=expected_departure or now - timedelta(minutes=30),
        expected_return=expected_return or now + timedelta(hours=2),
        status=status,
        approved_by=approved_by,
    )


class EmergencyOverrideTests(TestCase):
    def setUp(self):
        self.f = _make_fixtures()

    def test_senior_housemaster_can_authorize_and_review_is_due_within_24_hours(self):
        exeat = _make_exeat(
            self.f,
            exeat_type=Exeat.ExeatType.EMERGENCY,
        )
        approved, review = approve_with_override(
            exeat,
            actor=self.f.senior_user,
            reason="Student required urgent medical assessment.",
        )

        self.assertEqual(approved.status, Exeat.Status.APPROVED)
        self.assertTrue(approved.is_emergency_override)
        self.assertEqual(approved.approved_by_user, self.f.senior_user)
        self.assertTrue(ExeatMovement.objects.filter(exeat=approved).exists())
        self.assertEqual(review.status, ExeatReview.Status.PENDING)
        self.assertGreaterEqual(review.due_at, review.authorized_at + timedelta(hours=23))
        self.assertLessEqual(review.due_at, review.authorized_at + timedelta(hours=25))
        self.assertTrue(
            StaffNotification.objects.filter(
                user=self.f.admin_user,
                kind=StaffNotification.Kind.EXEAT_REVIEW,
            ).exists()
        )

    def test_override_cannot_be_used_for_ordinary_leave(self):
        town = _make_exeat(self.f, exeat_type=Exeat.ExeatType.TOWN)
        with self.assertRaises(ValueError):
            approve_with_override(
                town,
                actor=self.f.senior_user,
                reason="Ordinary leave must follow the normal approval path.",
            )
        town.refresh_from_db()
        self.assertEqual(town.status, Exeat.Status.PENDING)

    def test_quick_approve_cannot_bypass_emergency_review(self):
        exeat = _make_exeat(
            self.f,
            exeat_type=Exeat.ExeatType.EMERGENCY,
        )
        self.client.force_login(self.f.senior_user)

        response = self.client.post(
            reverse("core:exeat_action", args=[exeat.pk, "approve"]),
        )

        self.assertRedirects(
            response,
            reverse("core:exeat_override", args=[exeat.pk]),
            fetch_redirect_response=False,
        )
        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.PENDING)

    def test_housemaster_cannot_authorize_a_general_emergency_override(self):
        exeat = _make_exeat(
            self.f,
            exeat_type=Exeat.ExeatType.EMERGENCY,
        )
        with self.assertRaises(ValueError):
            approve_with_override(
                exeat,
                actor=self.f.hm_user,
                reason="Housemaster is initiating but cannot authorize.",
            )
        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.PENDING)

    def test_medical_lead_can_authorize_only_medical_passes(self):
        medical = _make_exeat(
            self.f,
            exeat_type=Exeat.ExeatType.MEDICAL,
        )
        approved, _review = approve_with_override(
            medical,
            actor=self.f.medical_user,
            reason="Infirmary referral requires immediate movement.",
        )
        self.assertEqual(approved.status, Exeat.Status.APPROVED)

        town = _make_exeat(self.f, exeat_type=Exeat.ExeatType.TOWN)
        with self.assertRaises(ValueError):
            approve_with_override(
                town,
                actor=self.f.medical_user,
                reason="Medical lead cannot authorize ordinary town leave.",
            )

        emergency = _make_exeat(self.f, exeat_type=Exeat.ExeatType.EMERGENCY)
        with self.assertRaises(ValueError):
            approve_with_override(
                emergency,
                actor=self.f.medical_user,
                reason="General emergencies remain with the Headmaster/Senior Housemaster.",
            )

    def test_authorizer_cannot_complete_own_post_hoc_review(self):
        exeat = _make_exeat(
            self.f,
            exeat_type=Exeat.ExeatType.EMERGENCY,
        )
        _approved, review = approve_with_override(
            exeat,
            actor=self.f.senior_user,
            reason="Emergency movement required after hours.",
        )
        with self.assertRaises(ValueError):
            decide_exeat_review(
                review,
                reviewer=self.f.senior_user,
                outcome=ExeatReview.Outcome.UPHELD,
                notes="Self-review is not permitted.",
            )

        decided = decide_exeat_review(
            review,
            reviewer=self.f.admin_user,
            outcome=ExeatReview.Outcome.UPHELD,
            notes="Reviewed the emergency record and evidence.",
        )
        self.assertEqual(decided.status, ExeatReview.Status.REVIEWED)
        self.assertEqual(decided.outcome, ExeatReview.Outcome.UPHELD)


class RescheduleTests(TestCase):
    def setUp(self):
        self.f = _make_fixtures()

    def test_reschedule_preserves_previous_window_and_audits_change(self):
        exeat = _make_exeat(
            self.f,
            status=Exeat.Status.APPROVED,
            approved_by=self.f.hm,
        )
        old_departure = exeat.expected_departure
        old_return = exeat.expected_return
        new_departure = old_departure + timedelta(hours=1)
        new_return = old_return + timedelta(hours=3)

        change = reschedule_exeat(
            exeat,
            new_departure=new_departure,
            new_return=new_return,
            reason="Approved travel time changed by the family.",
            actor=self.f.hm_user,
        )

        exeat.refresh_from_db()
        self.assertEqual(exeat.expected_departure, new_departure)
        self.assertEqual(exeat.expected_return, new_return)
        self.assertEqual(change.previous_departure, old_departure)
        self.assertEqual(change.previous_return, old_return)
        self.assertTrue(
            ExeatScheduleChange.objects.filter(exeat=exeat).exists()
        )
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.EXEAT_RESCHEDULED,
                target_id=str(exeat.pk),
            ).exists()
        )

    def test_reschedule_is_blocked_after_gate_departure(self):
        exeat = _make_exeat(
            self.f,
            status=Exeat.Status.APPROVED,
            approved_by=self.f.hm,
        )
        ExeatMovement.objects.create(
            exeat=exeat,
            actual_departure=timezone.now() - timedelta(minutes=5),
        )
        with self.assertRaises(ValueError):
            reschedule_exeat(
                exeat,
                new_departure=timezone.now(),
                new_return=timezone.now() + timedelta(hours=1),
                reason="Late schedule change.",
                actor=self.f.hm_user,
            )


class LateReturnTests(TestCase):
    def setUp(self):
        self.f = _make_fixtures()

    def _overdue_exeat(self, *, hours_late=3):
        now = timezone.now()
        exeat = _make_exeat(
            self.f,
            status=Exeat.Status.APPROVED,
            approved_by=self.f.hm,
            expected_departure=now - timedelta(hours=5),
            expected_return=now - timedelta(hours=hours_late),
        )
        ExeatMovement.objects.create(
            exeat=exeat,
            actual_departure=now - timedelta(hours=5),
        )
        return exeat

    def test_scheduled_task_creates_one_incident_and_escalates_to_senior(self):
        exeat = self._overdue_exeat(hours_late=3)
        enqueue_overdue_exeat_reminders()

        incident = LateReturnIncident.objects.get(exeat=exeat)
        self.assertEqual(
            incident.escalation_level,
            LateReturnIncident.Escalation.SENIOR_HOUSEMASTER,
        )
        self.assertTrue(
            StaffNotification.objects.filter(
                user=self.f.hm_user,
                kind=StaffNotification.Kind.LATE_RETURN,
            ).exists()
        )
        self.assertTrue(
            StaffNotification.objects.filter(
                user=self.f.senior_user,
                kind=StaffNotification.Kind.LATE_RETURN,
            ).exists()
        )

        enqueue_overdue_exeat_reminders()
        self.assertEqual(LateReturnIncident.objects.filter(exeat=exeat).count(), 1)

    def test_missed_return_notifies_housemaster_before_incident_threshold(self):
        exeat = self._overdue_exeat(hours_late=0.5)

        enqueue_overdue_exeat_reminders()
        enqueue_overdue_exeat_reminders()

        self.assertFalse(LateReturnIncident.objects.filter(exeat=exeat).exists())
        notifications = StaffNotification.objects.filter(
            user=self.f.hm_user,
            kind=StaffNotification.Kind.LATE_RETURN,
            title="Student missed expected return",
        )
        self.assertEqual(notifications.count(), 1)

    def test_incident_escalates_to_headmaster_after_six_hours(self):
        exeat = self._overdue_exeat(hours_late=3)
        open_late_return_incident(exeat, detected_at=timezone.now())

        open_late_return_incident(
            exeat,
            detected_at=timezone.now() + timedelta(hours=4),
        )
        incident = LateReturnIncident.objects.get(exeat=exeat)
        self.assertEqual(
            incident.escalation_level,
            LateReturnIncident.Escalation.HEADMASTER,
        )
        self.assertTrue(
            StaffNotification.objects.filter(
                user=self.f.admin_user,
                kind=StaffNotification.Kind.LATE_RETURN,
            ).exists()
        )

    def test_actual_late_return_creates_a_single_incident(self):
        now = timezone.now()
        exeat = self._overdue_exeat(hours_late=1)
        first = open_late_return_incident(
            exeat,
            actual_return=now - timedelta(minutes=15),
            source="gate test",
        )
        second = open_late_return_incident(
            exeat,
            actual_return=now - timedelta(minutes=10),
            source="duplicate gate save",
        )
        self.assertIsNotNone(first)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(LateReturnIncident.objects.filter(exeat=exeat).count(), 1)


class RollCallCorrectionTests(TestCase):
    def setUp(self):
        self.f = _make_fixtures()
        self.session = RollCallSession.objects.create(
            house=self.f.house,
            academic_term=self.f.term,
            session_type=RollCallSession.SessionType.EVENING,
            date=timezone.localdate(),
            status=RollCallSession.Status.IN_PROGRESS,
        )
        self.entry = RollCallEntry.objects.create(
            session=self.session,
            student=self.f.student,
            status=RollCallEntry.Status.ABSENT,
        )
        self.session.status = RollCallSession.Status.COMPLETED
        self.session.save(update_fields=["status"])

    def test_sensitive_correction_requires_evidence_and_different_approver(self):
        with self.assertRaises(ValueError):
            request_roll_call_correction(
                self.entry,
                requested_status=RollCallEntry.Status.PRESENT,
                reason="Student was actually present.",
                actor=self.f.hm_user,
            )

        correction = request_roll_call_correction(
            self.entry,
            requested_status=RollCallEntry.Status.PRESENT,
            reason="Student was actually present at the dormitory.",
            evidence="Dormitory register and tutor statement.",
            actor=self.f.hm_user,
        )
        self.assertTrue(correction.requires_second_approval)

        with self.assertRaises(ValueError):
            review_roll_call_correction(
                correction,
                reviewer=self.f.hm_user,
                approve=True,
                review_note="Self approval is not allowed.",
            )

        reviewed = review_roll_call_correction(
            correction,
            reviewer=self.f.senior_user,
            approve=True,
            review_note="Evidence checked against the dormitory register.",
        )
        self.entry.refresh_from_db()
        self.assertEqual(reviewed.status, "approved")
        self.assertEqual(self.entry.status, RollCallEntry.Status.PRESENT)

    def test_housemaster_can_view_but_not_decide_a_correction(self):
        correction = request_roll_call_correction(
            self.entry,
            requested_status=RollCallEntry.Status.PRESENT,
            reason="Student was present at the dormitory.",
            evidence="Dormitory register checked by the housemaster.",
            actor=self.f.hm_user,
        )
        self.client.force_login(self.f.hm_user)
        url = reverse("core:roll_call_correction_review", args=[correction.pk])

        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(
            url,
            {"decision": "approve", "review_note": "Attempted self approval."},
        )

        self.assertEqual(response.status_code, 403)
        correction.refresh_from_db()
        self.assertEqual(correction.status, RollCallCorrection.Status.PENDING)

    def test_non_sensitive_correction_still_requires_reviewer_authority(self):
        # Use a previously unmarked entry: Present/Absent are the sensitive
        # statuses that trigger the second-approval/evidence rule.
        RollCallEntry.objects.filter(pk=self.entry.pk).update(
            status=RollCallEntry.Status.UNMARKED
        )
        self.entry.refresh_from_db()
        correction = request_roll_call_correction(
            self.entry,
            requested_status=RollCallEntry.Status.ON_EXEAT,
            reason="Approved exeat was not reflected in the muster.",
            actor=self.f.hm_user,
        )
        self.assertFalse(correction.requires_second_approval)
        with self.assertRaises(ValueError):
            review_roll_call_correction(
                correction,
                reviewer=self.f.hm_user,
                approve=True,
                review_note="A second senior reviewer is required.",
            )


class CapacityExceptionTests(TestCase):
    def setUp(self):
        self.f = _make_fixtures()
        self.dorm = Dormitory.objects.create(
            house=self.f.house,
            name="Capacity Test Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=1,
        )
        self.first = Student.objects.create(
            admission_no="P1-CAP-001",
            index_number="P1-CAP-IDX-001",
            full_name="First Boarder",
            gender=Student.Gender.MALE,
            programme=self.f.programme,
            house=self.f.house,
            dormitory=self.dorm,
            residency=Student.ResidencyStatus.BOARDING,
        )
        other_dorm = Dormitory.objects.create(
            house=self.f.house,
            name="Other Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=5,
        )
        self.second = Student.objects.create(
            admission_no="P1-CAP-002",
            index_number="P1-CAP-IDX-002",
            full_name="Second Boarder",
            gender=Student.Gender.MALE,
            programme=self.f.programme,
            house=self.f.house,
            dormitory=other_dorm,
            residency=Student.ResidencyStatus.BOARDING,
        )

    def test_dormitory_capacity_blocks_an_extra_placement(self):
        self.second.dormitory = self.dorm
        with self.assertRaises(ValidationError):
            self.second.save()

    def test_time_bound_exception_allows_one_extra_placement(self):
        DormitoryCapacityException.objects.create(
            dormitory=self.dorm,
            approved_by=self.f.senior_user,
            reason="Temporary overflow while a room is repaired.",
            starts_at=timezone.now() - timedelta(minutes=5),
            ends_at=timezone.now() + timedelta(days=1),
            additional_capacity=1,
        )
        self.second.dormitory = self.dorm
        self.second.save()
        self.assertEqual(
            Student.objects.filter(dormitory=self.dorm).count(),
            2,
        )

    def test_exception_cannot_exceed_seven_days_or_use_housemaster_approval(self):
        with self.assertRaises(ValidationError):
            DormitoryCapacityException.objects.create(
                dormitory=self.dorm,
                approved_by=self.f.senior_user,
                reason="Too long.",
                starts_at=timezone.now(),
                ends_at=timezone.now() + timedelta(days=8),
                additional_capacity=1,
            )
        with self.assertRaises(ValidationError):
            DormitoryCapacityException.objects.create(
                dormitory=self.dorm,
                approved_by=self.f.hm_user,
                reason="Housemaster cannot approve.",
                starts_at=timezone.now(),
                ends_at=timezone.now() + timedelta(days=1),
                additional_capacity=1,
            )

    def test_lowering_capacity_below_active_occupancy_is_blocked(self):
        self.dorm.capacity = 0
        with self.assertRaises(ValidationError):
            self.dorm.save()


class DisputeWorkflowTests(TestCase):
    def setUp(self):
        self.f = _make_fixtures()
        self.exeat = _make_exeat(
            self.f,
            status=Exeat.Status.APPROVED,
            approved_by=self.f.hm,
        )

    def test_dispute_is_reviewed_by_a_different_decision_maker_and_audited(self):
        dispute = open_dispute(
            student=self.f.student,
            category=DisputeCase.Category.EXEAT,
            details="The family disputes the recorded departure time.",
            actor=self.f.hm_user,
            exeat=self.exeat,
        )
        self.assertTrue(dispute.reference.startswith("DSP-"))
        self.assertTrue(
            StaffNotification.objects.filter(
                kind=StaffNotification.Kind.DISPUTE,
                user=self.f.admin_user,
            ).exists()
        )
        with self.assertRaises(ValueError):
            decide_dispute(
                dispute,
                actor=self.f.hm_user,
                status=DisputeCase.Status.RESOLVED,
                outcome=DisputeCase.Outcome.UPHELD,
                decision_notes="Cannot decide a case raised by the same user.",
            )

        resolved = decide_dispute(
            dispute,
            actor=self.f.admin_user,
            status=DisputeCase.Status.RESOLVED,
            outcome=DisputeCase.Outcome.CORRECTED,
            decision_notes="The gate record was corrected after checking the CCTV log.",
        )
        self.assertEqual(resolved.status, DisputeCase.Status.RESOLVED)
        self.assertEqual(resolved.resolved_by, self.f.admin_user)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.DISPUTE,
                target_id=str(resolved.pk),
            ).exists()
        )

    def test_dispute_list_scopes_housemaster_and_renders(self):
        open_dispute(
            student=self.f.student,
            category=DisputeCase.Category.EXEAT,
            details="A house-scoped dispute.",
            actor=self.f.hm_user,
            exeat=self.exeat,
        )
        self.client.force_login(self.f.hm_user)
        response = self.client.get(reverse("core:dispute_cases"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Formal dispute cases")


class ReportExportTests(TestCase):
    def setUp(self):
        self.f = _make_fixtures()
        self.client.force_login(self.f.admin_user)

    def test_xlsx_export_writes_export_and_audit_records(self):
        response = self.client.get(
            reverse("core:report_export"),
            {"report": "housing", "format": "xlsx"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        export = DataExportLog.objects.get()
        self.assertEqual(export.actor, self.f.admin_user)
        self.assertEqual(export.file_format, "xlsx")
        self.assertGreaterEqual(export.row_count, 0)
        self.assertTrue(
            AuditEvent.objects.filter(
                action=AuditEvent.Action.EXPORT,
                actor=self.f.admin_user,
            ).exists()
        )

    def test_pdf_export_is_available_and_audited(self):
        response = self.client.get(
            reverse("core:report_export"),
            {"report": "housing", "format": "pdf"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(DataExportLog.objects.get().file_format, "pdf")

    def test_invalid_export_format_is_rejected(self):
        response = self.client.get(
            reverse("core:report_export"),
            {"report": "housing", "format": "docx"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DataExportLog.objects.count(), 0)


class MedicalRoleScopeTests(TestCase):
    def setUp(self):
        self.f = _make_fixtures()
        self.medical = _make_exeat(
            self.f,
            exeat_type=Exeat.ExeatType.MEDICAL,
        )
        self.town = _make_exeat(
            self.f,
            exeat_type=Exeat.ExeatType.TOWN,
        )
        self.emergency = _make_exeat(
            self.f,
            exeat_type=Exeat.ExeatType.EMERGENCY,
        )

    def test_medical_approval_queue_excludes_ordinary_and_general_emergency_leave(self):
        queue = list(get_approvable_pending_for_user(self.f.medical_user))
        self.assertIn(self.medical, queue)
        self.assertNotIn(self.town, queue)
        self.assertNotIn(self.emergency, queue)

    def test_medical_lead_can_open_exeat_list_without_a_house_assignment(self):
        self.client.force_login(self.f.medical_user)
        response = self.client.get(reverse("core:exeat_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.medical.exeat_number)
        self.assertTrue(response.context["can_view_exeats"])

    def test_medical_lead_can_open_pending_approvals_hub(self):
        self.client.force_login(self.f.medical_user)
        response = self.client.get(reverse("core:exeat_approvals"))
        self.assertEqual(response.status_code, 200)

    def test_medical_lead_can_approve_medical_pass_on_review_page(self):
        self.client.force_login(self.f.medical_user)
        response = self.client.get(
            reverse("core:exeat_review", args=[self.medical.pk])
        )
        self.assertEqual(response.status_code, 200)
        response = self.client.post(
            reverse("core:exeat_review", args=[self.medical.pk]),
            {
                "status": Exeat.Status.APPROVED,
                "approval_remarks": "Approved by the medical lead for treatment.",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.medical.refresh_from_db()
        self.assertEqual(self.medical.status, Exeat.Status.APPROVED)
        self.assertIsNone(self.medical.approved_by)
        self.assertEqual(self.medical.approved_by_user, self.f.medical_user)
        self.assertTrue(self.medical.is_active)

    def test_medical_lead_cannot_approve_ordinary_town_pass(self):
        self.client.force_login(self.f.medical_user)
        response = self.client.get(
            reverse("core:exeat_review", args=[self.town.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_medical_lead_cannot_approve_general_emergency_pass(self):
        self.client.force_login(self.f.medical_user)
        response = self.client.get(
            reverse("core:exeat_review", args=[self.emergency.pk])
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            reverse("core:exeat_action", args=[self.emergency.pk, "approve"]),
        )
        self.assertEqual(response.status_code, 404)


class GateDutyAssignmentTests(TestCase):
    def setUp(self):
        self.f = _make_fixtures()
        self.gate_user = User.objects.create_user(
            username="p1_gate", password="password123"
        )
        gate_profile = get_user_profile(self.gate_user)
        gate_profile.role = StaffRole.GATE_OFFICER
        gate_profile.save(update_fields=["role"])
        OperationalSettings.objects.create(gate_duty_enforcement_enabled=True)

    def _assignment(self, *, start=None, end=None):
        now = timezone.now()
        return GateDutyAssignment.objects.create(
            user=self.gate_user,
            approved_by=self.f.admin_user,
            gate_name="Main Gate",
            starts_at=start or now - timedelta(minutes=5),
            ends_at=end or now + timedelta(hours=1),
            reason="Pilot test cover",
        )

    def test_enforcement_requires_current_assignment_but_admins_can_cover(self):
        self.assertFalse(can_log_gate_check(self.gate_user))
        assignment = self._assignment()
        self.assertTrue(assignment.is_active)
        self.assertTrue(can_log_gate_check(self.gate_user))
        # Administrators can cover a missing roster so the school is never
        # locked out of the gate.
        self.assertTrue(can_log_gate_check(self.f.admin_user))

    def test_expired_or_cancelled_assignment_does_not_grant_access(self):
        assignment = self._assignment()
        assignment.cancelled_at = timezone.now()
        assignment.cancelled_by = self.f.senior_user
        assignment.save()
        self.assertFalse(can_log_gate_check(self.gate_user))

        assignment.delete()
        past = timezone.now() - timedelta(hours=2)
        self._assignment(start=past, end=past + timedelta(hours=1))
        self.assertFalse(can_log_gate_check(self.gate_user))

    def test_only_gate_staff_can_be_assigned_and_only_seniors_approve(self):
        with self.assertRaises(ValidationError):
            GateDutyAssignment.objects.create(
                user=self.f.hm_user,
                approved_by=self.f.admin_user,
                starts_at=timezone.now(),
                ends_at=timezone.now() + timedelta(hours=1),
            )
        with self.assertRaises(ValidationError):
            GateDutyAssignment.objects.create(
                user=self.gate_user,
                approved_by=self.f.hm_user,
                starts_at=timezone.now() + timedelta(hours=2),
                ends_at=timezone.now() + timedelta(hours=3),
            )


class OperationalSettingsTests(TestCase):
    def test_settings_singleton_supplies_defaults_and_audits_changes(self):
        settings = OperationalSettings.get_solo()
        self.assertEqual(settings.roll_call_timeout_minutes, 60)

        settings.late_return_incident_after_minutes = 30
        settings.updated_by = None
        settings.save()
        self.assertEqual(
            OperationalSettings.get_solo().late_return_incident_after_minutes,
            30,
        )
        self.assertTrue(
            ConfigurationAuditLog.objects.filter(
                event=ConfigurationAuditLog.Event.OPERATIONAL_SETTINGS_CHANGED
            ).exists()
        )
