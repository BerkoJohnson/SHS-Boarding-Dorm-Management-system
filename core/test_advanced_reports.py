import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AcademicTerm,
    AcademicYear,
    ConfigurationAuditLog,
    DisciplinaryRecord,
    Dormitory,
    DormitoryRoom,
    Exeat,
    ExeatMovement,
    House,
    HouseMaster,
    MovementConcernReport,
    Programme,
    RollCallAbsenceFollowUp,
    RollCallEntry,
    RollCallSession,
    SMSDelivery,
    StaffRole,
    Student,
    UserProfile,
)
from core.forms import REPORTS_WITHOUT_PERIOD_FILTERS, ReportFilterForm
from core.services.reports import (
    get_boarding_demographics,
    get_boarding_kpi_pack,
    get_configuration_audit,
    get_detailed_report,
    get_sms_usage_summary,
)


class AdvancedReportsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()
        cls.start_date = today - timedelta(days=30)
        cls.end_date = today + timedelta(days=30)
        cls.academic_year = AcademicYear.objects.create(
            start_date=cls.start_date,
            end_date=cls.end_date,
        )
        cls.term = AcademicTerm.objects.create(
            academic_year=cls.academic_year,
            term=AcademicTerm.TermChoice.TERM_1,
            start_date=cls.start_date,
            end_date=cls.end_date,
            is_current=True,
        )
        cls.house = House.objects.create(name="Advanced Report House")
        cls.other_house = House.objects.create(name="Other Report House")
        cls.dormitory = Dormitory.objects.create(
            house=cls.house,
            name="Advanced Report Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=4,
        )
        cls.other_dormitory = Dormitory.objects.create(
            house=cls.other_house,
            name="Other Report Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=4,
        )
        cls.programme = Programme.objects.create(name="Advanced Report Science")
        cls.student = cls.create_student(
            "ADV-001", "Advanced Report Student", cls.house, cls.dormitory
        )
        cls.second_student = cls.create_student(
            "ADV-002", "Second Report Student", cls.house, cls.dormitory
        )
        cls.other_student = cls.create_student(
            "ADV-003",
            "Other House Report Student",
            cls.other_house,
            cls.other_dormitory,
        )
        cls.unplaced_student = cls.create_student(
            "ADV-004", "Unplaced Report Student", cls.house, cls.dormitory
        )
        Student.objects.filter(pk=cls.unplaced_student.pk).update(dormitory=None)
        cls.unplaced_student.refresh_from_db()
        cls.approver = HouseMaster.objects.create(
            full_name="Advanced Report Approver",
            staff_id="ADV-SENIOR",
            phone_number="+233200000090",
            is_senior_housemaster=True,
        )

    @classmethod
    def create_student(cls, admission_no, name, house, dormitory):
        return Student.objects.create(
            admission_no=admission_no,
            index_number=f"IDX-{admission_no}",
            full_name=name,
            gender=Student.Gender.MALE,
            programme=cls.programme,
            house=house,
            dormitory=dormitory,
        )

    def create_exeat(self, student, *, expected_return, actual_return=None):
        expected_departure = expected_return - timedelta(hours=4)
        exeat = Exeat.objects.create(
            student=student,
            academic_term=self.term,
            exeat_type=Exeat.ExeatType.TOWN,
            reason="Report test",
            destination="Report test",
            expected_departure=expected_departure,
            expected_return=expected_return,
            status=Exeat.Status.APPROVED,
            approved_by=self.approver,
        )
        ExeatMovement.objects.create(
            exeat=exeat,
            actual_departure=expected_departure + timedelta(minutes=5),
            actual_return=actual_return,
        )
        return exeat

    def create_roll_call(self, house, students, statuses):
        session = RollCallSession.objects.create(
            house=house,
            academic_term=self.term,
            date=timezone.localdate(),
            status=RollCallSession.Status.IN_PROGRESS,
        )
        for student, status in zip(students, statuses):
            RollCallEntry.objects.create(
                session=session,
                student=student,
                status=status,
            )
        RollCallSession.objects.filter(pk=session.pk).update(
            status=RollCallSession.Status.COMPLETED
        )
        return session

    def test_placement_exception_report_is_house_scoped(self):
        day_student = Student.objects.create(
            admission_no="ADV-DAY",
            index_number="IDX-ADV-DAY",
            full_name="Misassigned Day Student",
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            residency=Student.ResidencyStatus.DAY,
        )
        self.create_student(
            "ADV-005", "Over-capacity Report Student", self.house, self.dormitory
        )
        Student.objects.filter(pk=day_student.pk).update(dormitory=self.dormitory)
        Student.objects.filter(pk=self.second_student.pk).update(
            dormitory=self.other_dormitory
        )
        Dormitory.objects.filter(pk=self.dormitory.pk).update(capacity=1)

        report = get_detailed_report(
            "placement_exceptions",
            house_id=self.house.pk,
        )

        labels = [row[0] for row in report["rows"]]
        details = [row[3] for row in report["rows"]]
        self.assertIn("Unplaced boarder", labels)
        self.assertIn("House/dormitory mismatch", labels)
        self.assertIn("Day student in dormitory", labels)
        self.assertIn("Over-capacity dormitory", labels)
        self.assertIn(self.unplaced_student.full_name, details)
        self.assertNotIn(self.other_student.full_name, details)
        self.assertEqual(dict(report["summary"])["Unplaced boarders"], 1)
        self.assertEqual(dict(report["summary"])["Day students in dormitory"], 1)

    def test_attendance_league_ranks_houses_and_applies_filters(self):
        self.create_roll_call(
            self.house,
            [self.student, self.second_student],
            [RollCallEntry.Status.PRESENT, RollCallEntry.Status.ABSENT],
        )
        self.create_roll_call(
            self.other_house,
            [self.other_student],
            [RollCallEntry.Status.PRESENT],
        )

        report = get_detailed_report(
            "attendance_league",
            date_from=timezone.localdate(),
            date_to=timezone.localdate(),
        )
        self.assertEqual(report["rows"][0][1], self.other_house.name)
        self.assertEqual(report["rows"][0][-1], "100.0%")
        self.assertEqual(report["rows"][1][1], self.house.name)
        self.assertEqual(report["rows"][1][-1], "50.0%")
        self.assertIn("Activity", report["headers"])

        scoped_report = get_detailed_report(
            "attendance_league",
            house_id=self.house.pk,
            term_id=self.term.pk,
        )
        self.assertEqual(len(scoped_report["rows"]), 1)
        self.assertEqual(scoped_report["rows"][0][1], self.house.name)

    def test_overdue_register_includes_currently_out_and_late_returns(self):
        now = timezone.now()
        overdue = self.create_exeat(
            self.student,
            expected_return=now - timedelta(hours=3),
        )
        late = self.create_exeat(
            self.second_student,
            expected_return=now - timedelta(hours=4),
            actual_return=now - timedelta(hours=2),
        )
        report = get_detailed_report(
            "overdue_register",
            house_id=self.house.pk,
            date_from=timezone.localdate() - timedelta(days=2),
            date_to=timezone.localdate(),
            term_id=self.term.pk,
        )

        self.assertEqual(len(report["rows"]), 2)
        self.assertEqual(report["rows"][0][0], late.exeat_number)
        self.assertEqual(report["rows"][1][0], overdue.exeat_number)
        self.assertEqual(dict(report["summary"])["Currently overdue (still out)"], 1)
        self.assertEqual(dict(report["summary"])["Returned late in period"], 1)

    def test_overdue_register_keeps_currently_out_passes_outside_date_window(self):
        overdue = self.create_exeat(
            self.student,
            expected_return=timezone.now() - timedelta(days=10),
        )

        report = get_detailed_report(
            "overdue_register",
            house_id=self.house.pk,
            date_from=timezone.localdate() - timedelta(days=2),
            date_to=timezone.localdate(),
            term_id=self.term.pk,
        )

        self.assertEqual([row[0] for row in report["rows"]], [overdue.exeat_number])
        self.assertEqual(dict(report["summary"])["Currently overdue (still out)"], 1)

    def test_kpi_pack_aggregates_metrics_for_selected_house_and_term(self):
        now = timezone.now()
        self.create_exeat(
            self.student,
            expected_return=now - timedelta(hours=3),
        )
        self.create_roll_call(
            self.house,
            [self.student, self.second_student],
            [RollCallEntry.Status.PRESENT, RollCallEntry.Status.ABSENT],
        )
        DisciplinaryRecord.objects.create(
            student=self.student,
            academic_term=self.term,
            offense="Repeated lateness",
            severity=DisciplinaryRecord.Severity.MAJOR,
            action_taken="Warning",
        )
        DisciplinaryRecord.objects.create(
            student=self.student,
            academic_term=self.term,
            offense="Repeated lateness",
            severity=DisciplinaryRecord.Severity.MINOR,
            action_taken="Counselling",
        )

        report = get_boarding_kpi_pack(
            house_id=self.house.pk,
            term=self.term,
        )
        tiles = {label: value for label, value, _tone in report["kpi_tiles"]}

        self.assertEqual(tiles["Boarders"], 3)
        self.assertEqual(tiles["Configured beds"], 4)
        self.assertEqual(tiles["Awaiting dorm placement"], 1)
        self.assertEqual(tiles["Currently out"], 1)
        self.assertEqual(tiles["Overdue returns"], 1)
        self.assertEqual(tiles["Attendance"], "50.0%")
        self.assertEqual(tiles["Active incidents"], 2)
        self.assertEqual(tiles["Students with incidents"], 1)
        self.assertEqual(tiles["Repeat students (2+)"], 1)

    def test_kpi_date_range_intersects_the_selected_term(self):
        now = timezone.now()
        self.create_exeat(
            self.student,
            expected_return=now - timedelta(hours=3),
        )

        report = get_boarding_kpi_pack(
            house_id=self.house.pk,
            term=self.term,
            date_from=timezone.localdate() + timedelta(days=1),
        )
        tiles = {label: value for label, value, _tone in report["kpi_tiles"]}

        self.assertEqual(tiles["Exeats in period"], 0)
        self.assertEqual(tiles["Attendance"], "0%")

    def test_gate_conflict_report_filters_by_house_and_term(self):
        session = self.create_roll_call(
            self.house,
            [self.student],
            [RollCallEntry.Status.PRESENT],
        )
        entry = session.entries.get(student=self.student)
        RollCallEntry.objects.filter(pk=entry.pk).update(
            gate_conflict_at=timezone.now(),
            gate_conflict_note="Gate departure recorded",
        )
        other_session = self.create_roll_call(
            self.other_house,
            [self.other_student],
            [RollCallEntry.Status.ABSENT],
        )
        RollCallEntry.objects.filter(
            session=other_session, student=self.other_student
        ).update(gate_conflict_at=timezone.now())

        report = get_detailed_report(
            "gate_conflicts",
            house_id=self.house.pk,
            term_id=self.term.pk,
        )

        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0][3], self.student.full_name)
        self.assertEqual(dict(report["summary"])["Pending review"], 1)
        self.assertEqual(report["sections"][0]["rows"], report["rows"])

    def test_approval_turnaround_report_counts_decisions_and_scope(self):
        approved = self.create_exeat(
            self.student,
            expected_return=timezone.now() + timedelta(hours=12),
        )
        rejected = self.create_exeat(
            self.second_student,
            expected_return=timezone.now() + timedelta(hours=12),
        )
        Exeat.objects.filter(pk=rejected.pk).update(
            status=Exeat.Status.REJECTED,
            decided_at=timezone.now(),
        )
        outside_house = self.create_exeat(
            self.other_student,
            expected_return=timezone.now() + timedelta(hours=12),
        )

        report = get_detailed_report(
            "approval_turnaround",
            house_id=self.house.pk,
            term_id=self.term.pk,
        )

        self.assertEqual(len(report["rows"]), 2)
        summary = dict(report["summary"])
        self.assertEqual(summary["Approved"], 1)
        self.assertEqual(summary["Rejected"], 1)
        self.assertNotIn(outside_house.exeat_number, [row[0] for row in report["rows"]])
        self.assertIn(approved.exeat_number, [row[0] for row in report["rows"]])
        self.assertIn("immutable decision timestamp", " ".join(report["highlights"]))

    def test_exeat_analytics_and_room_register_report_expected_aggregates(self):
        self.create_exeat(
            self.student,
            expected_return=timezone.now() + timedelta(hours=8),
        )
        room = DormitoryRoom.objects.create(
            dormitory=self.dormitory,
            name="Room 1",
            capacity=2,
        )
        Student.objects.filter(pk=self.student.pk).update(room=room)

        analytics = get_detailed_report(
            "exeat_analytics",
            house_id=self.house.pk,
            term_id=self.term.pk,
        )
        room_report = get_detailed_report(
            "room_register",
            house_id=self.house.pk,
        )

        self.assertEqual(dict(analytics["summary"])["Passes in period"], 1)
        self.assertEqual(analytics["sections"][1]["rows"][0][1], 1)
        self.assertEqual(dict(room_report["summary"])["Rooms"], 1)
        self.assertEqual(room_report["rows"][0][2], room.name)
        self.assertEqual(room_report["rows"][0][5], 1)

    def test_demographics_are_house_scoped_and_handle_empty_population(self):
        report = get_boarding_demographics(house_id=self.house.pk)

        self.assertEqual(dict(report["summary"])["Boarders"], 3)
        self.assertNotIn(
            self.other_house.name,
            [row[0] for row in report["sections"][1]["rows"]],
        )

        empty_house = House.objects.create(name="Empty Report House")
        empty_report = get_boarding_demographics(house_id=empty_house.pk)
        self.assertEqual(dict(empty_report["summary"])["Boarders"], 0)
        self.assertEqual(
            empty_report["distribution"][0]["rows"], [("Male", 0), ("Female", 0)]
        )

    def test_sms_usage_filters_delivery_data_and_keeps_quota_tenant_wide(self):
        now = timezone.now()
        shared_fields = {
            "event_type": SMSDelivery.EventType.APPROVED,
            "recipient_name": "Parent Name",
            "phone_number": "+233200000099",
            "message": "Private notification body",
            "dedupe_key": "advanced-report-sms-house",
            "scheduled_for": now,
        }
        SMSDelivery.objects.create(
            exeat=self.create_exeat(
                self.student,
                expected_return=now + timedelta(hours=12),
            ),
            status=SMSDelivery.Status.SENT,
            sent_at=now,
            **shared_fields,
        )
        SMSDelivery.objects.create(
            exeat=self.create_exeat(
                self.other_student,
                expected_return=now + timedelta(hours=12),
            ),
            status=SMSDelivery.Status.FAILED,
            last_error="Provider rejected destination",
            dedupe_key="advanced-report-sms-other-house",
            **{
                key: value
                for key, value in shared_fields.items()
                if key != "dedupe_key"
            },
        )

        from unittest.mock import patch

        with (
            patch("tenancy.entitlements.sms_monthly_usage", return_value=3),
            patch("tenancy.entitlements.quota_limit", return_value=10),
        ):
            report = get_sms_usage_summary(
                house_id=self.house.pk,
                date_from=timezone.localdate() - timedelta(days=1),
                date_to=timezone.localdate() + timedelta(days=1),
                term_id=self.term.pk,
            )

        self.assertEqual(report["record_count"], 1)
        self.assertEqual(report["gauge"], {"used": 3, "limit": 10, "percent": 30.0})
        self.assertEqual(report["sections"][0]["rows"], [("Exeat approved", 1)])
        rendered_summary = repr(report)
        self.assertNotIn("+233200000099", rendered_summary)
        self.assertNotIn("Private notification body", rendered_summary)

    def test_demographics_and_sms_pages_render_for_reports_role(self):
        admin = get_user_model().objects.create_superuser(
            username="advanced-report-pages-admin",
            email="advanced-report-pages@example.test",
            password="test-password",
        )
        self.client.force_login(admin)

        demographics = self.client.get(reverse("core:boarding_demographics"))
        demographics_print = self.client.get(
            reverse("core:boarding_demographics"),
            {"print": "1"},
        )
        sms = self.client.get(
            reverse("core:sms_usage"),
            {"date_from": "not-a-date"},
        )
        sms_print = self.client.get(
            reverse("core:sms_usage"),
            {"print": "1", "date_from": "not-a-date"},
        )
        sms_valid = self.client.get(reverse("core:sms_usage"))
        sms_valid_print = self.client.get(
            reverse("core:sms_usage"),
            {"print": "1", "term": str(self.term.pk)},
        )

        self.assertEqual(demographics.status_code, 200)
        self.assertTemplateUsed(
            demographics,
            "core/reports/boarding_demographics.html",
        )
        self.assertEqual(demographics_print.status_code, 200)
        self.assertTemplateUsed(
            demographics_print,
            "core/reports/boarding_demographics_print.html",
        )
        self.assertEqual(sms.status_code, 200)
        self.assertFalse(sms.context["report_filters_valid"])
        self.assertContains(sms, "Correct the filters")
        self.assertEqual(sms_print.status_code, 400)
        self.assertEqual(sms_valid.status_code, 200)
        self.assertTrue(sms_valid.context["report_filters_valid"])
        self.assertTemplateUsed(sms_valid, "core/reports/sms_usage.html")
        self.assertEqual(sms_valid_print.status_code, 200)
        self.assertTemplateUsed(
            sms_valid_print,
            "core/reports/sms_usage_print.html",
        )

    def test_housemaster_demographics_view_is_scoped_to_assigned_house(self):
        user = get_user_model().objects.create_user(
            username="advanced-demographics-housemaster",
            password="test-password",
        )
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": StaffRole.HOUSEMASTER},
        )
        HouseMaster.objects.create(
            user=user,
            full_name="Demographics Housemaster",
            staff_id="ADV-DEMO-HM",
            phone_number="+233200000092",
            house=self.house,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("core:boarding_demographics"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["user_house_scope"], self.house)
        self.assertEqual(
            dict(response.context["report"]["summary"])["Boarders"],
            3,
        )
        self.assertContains(response, self.house.name)
        self.assertNotContains(response, self.other_house.name)

    def test_staff_activity_counts_actions_and_unattributed_records_in_scope(self):
        approver_user = get_user_model().objects.create_user(
            username="advanced-activity-approver",
            password="test-password",
        )
        activity_housemaster = HouseMaster.objects.create(
            user=approver_user,
            full_name="Activity Report Housemaster",
            staff_id="ADV-ACTIVITY-HM",
            phone_number="+233200000094",
            house=self.house,
        )
        session = self.create_roll_call(
            self.house,
            [self.student],
            [RollCallEntry.Status.PRESENT],
        )
        RollCallSession.objects.filter(pk=session.pk).update(
            conducted_by=activity_housemaster
        )
        approved_exeat = self.create_exeat(
            self.student,
            expected_return=timezone.now() + timedelta(hours=2),
        )
        Exeat.objects.filter(pk=approved_exeat.pk).update(
            approved_by=activity_housemaster
        )
        DisciplinaryRecord.objects.create(
            student=self.student,
            academic_term=self.term,
            offense="Report activity test",
            severity=DisciplinaryRecord.Severity.MINOR,
            action_taken="Warning",
            logged_by=activity_housemaster,
        )
        entry = session.entries.get(student=self.student)
        RollCallAbsenceFollowUp.objects.create(
            entry=entry,
            status=RollCallAbsenceFollowUp.Status.EXCUSED,
            resolved_by=approver_user,
            resolved_at=timezone.now(),
        )
        self.create_roll_call(
            self.house,
            [self.second_student],
            [RollCallEntry.Status.PRESENT],
        )

        report = get_detailed_report(
            "staff_activity",
            house_id=self.house.pk,
            term_id=self.term.pk,
        )

        self.assertEqual(len(report["rows"]), 1)
        row = report["rows"][0]
        self.assertEqual(row[0], activity_housemaster.full_name)
        self.assertEqual(row[2:8], (1, 1, 1, 1, 0, 4))
        unattributed = dict(report["sections"][0]["rows"])
        self.assertEqual(unattributed["Roll calls without a conductor"], 1)
        self.assertEqual(unattributed["Incidents without a logger"], 0)

    def test_staff_activity_includes_admin_approver_and_gate_recorder(self):
        admin = get_user_model().objects.create_superuser(
            username="advanced-activity-admin",
            email="advanced-activity-admin@example.test",
            password="test-password",
        )
        gate_user = get_user_model().objects.create_user(
            username="advanced-activity-gate",
            first_name="Gate",
            last_name="Recorder",
            password="test-password",
        )
        exeat = self.create_exeat(
            self.student,
            expected_return=timezone.now() + timedelta(hours=2),
        )
        Exeat.objects.filter(pk=exeat.pk).update(approved_by_user=admin)
        ExeatMovement.objects.filter(exeat=exeat).update(
            departure_recorded_by_user=gate_user
        )

        report = get_detailed_report(
            "staff_activity",
            house_id=self.house.pk,
            term_id=self.term.pk,
        )
        rows = {row[0]: row for row in report["rows"]}

        self.assertEqual(rows["advanced-activity-admin"][3], 1)
        self.assertEqual(rows["Gate Recorder"][6], 1)
        self.assertEqual(rows["Gate Recorder"][7], 1)

    def test_welfare_report_combines_medical_exeats_and_infirmary_entries(self):
        session = self.create_roll_call(
            self.house,
            [self.student, self.second_student],
            [RollCallEntry.Status.INFIRMARY, RollCallEntry.Status.PRESENT],
        )
        # Anchor the departure to midday so the computed expected_departure
        # (expected_return - 4h, see create_exeat) always lands on today's
        # date. Using now() + 8h rolls into tomorrow when the suite runs after
        # 20:00 local time, making this assertion time-of-day dependent.
        expected_return = timezone.now().replace(
            hour=12, minute=0, second=0, microsecond=0
        )
        medical_exeat = self.create_exeat(
            self.student,
            expected_return=expected_return,
        )
        Exeat.objects.filter(pk=medical_exeat.pk).update(
            exeat_type=Exeat.ExeatType.MEDICAL
        )
        self.create_exeat(
            self.other_student,
            expected_return=expected_return,
        )

        report = get_detailed_report(
            "welfare_infirmary",
            house_id=self.house.pk,
            date_from=timezone.localdate(),
            date_to=timezone.localdate(),
            term_id=self.term.pk,
        )

        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0][0], self.student.full_name)
        self.assertEqual(report["rows"][0][3:6], (1, 1, 2))
        self.assertEqual(report["sections"][0]["rows"], [])
        self.assertEqual(session.house_id, self.house.pk)

    def test_data_completeness_applies_house_scope_and_excludes_day_placement_gaps(
        self,
    ):
        day_student = Student.objects.create(
            admission_no="ADV-DAY-COMPLETE",
            index_number="IDX-ADV-DAY-COMPLETE",
            full_name="Day Student Completeness",
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            residency=Student.ResidencyStatus.DAY,
        )

        report = get_detailed_report(
            "data_completeness",
            house_id=self.house.pk,
        )
        summary = dict(report["summary"])
        names = [row[0] for row in report["rows"]]
        checklist = dict(report["sections"][0]["rows"])
        day_row = next(row for row in report["rows"] if row[0] == day_student.full_name)

        self.assertEqual(summary["Students"], 4)
        self.assertIn(self.unplaced_student.full_name, names)
        self.assertNotIn(self.other_student.full_name, names)
        self.assertEqual(checklist["Boarders without dormitory"], 1)
        self.assertEqual(checklist["Boarders without room"], 3)
        self.assertNotIn("No dormitory", day_row[5])
        self.assertNotIn("No room", day_row[5])

    def test_movement_concern_report_filters_by_house_and_date(self):
        concern = MovementConcernReport.objects.create(
            student=self.student,
            exeat=None,
            details="Please verify this movement.",
        )
        MovementConcernReport.objects.create(
            student=self.other_student,
            details="Other house concern.",
        )

        report = get_detailed_report(
            "movement_concerns",
            house_id=self.house.pk,
            date_from=timezone.localdate(),
            date_to=timezone.localdate(),
        )

        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0][1], self.student.full_name)
        self.assertEqual(report["rows"][0][4], "Open")
        self.assertIn(concern.details, report["rows"][0][5])

    def test_conduct_normalized_uses_only_scoped_boarder_denominator(self):
        DisciplinaryRecord.objects.create(
            student=self.student,
            academic_term=self.term,
            offense="Major report test",
            severity=DisciplinaryRecord.Severity.MAJOR,
            action_taken="Warning",
        )
        DisciplinaryRecord.objects.create(
            student=self.student,
            academic_term=self.term,
            offense="Critical report test",
            severity=DisciplinaryRecord.Severity.CRITICAL,
            action_taken="Counselling",
        )

        report = get_detailed_report(
            "conduct_normalized",
            house_id=self.house.pk,
            term_id=self.term.pk,
        )

        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0], (self.house.name, 3, 2, 66.7, 1, 1))
        self.assertEqual(dict(report["summary"])["Boarders"], 3)

    def test_conduct_normalized_excludes_day_student_incidents(self):
        day_student = Student.objects.create(
            admission_no="ADV-DAY-CONDUCT",
            index_number="IDX-ADV-DAY-CONDUCT",
            full_name="Day Student Conduct Record",
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            residency=Student.ResidencyStatus.DAY,
        )
        DisciplinaryRecord.objects.create(
            student=day_student,
            academic_term=self.term,
            offense="Day student incident",
            severity=DisciplinaryRecord.Severity.MAJOR,
            action_taken="Warning",
        )

        report = get_detailed_report(
            "conduct_normalized",
            house_id=self.house.pk,
            term_id=self.term.pk,
        )

        self.assertEqual(report["rows"][0][1], 3)
        self.assertEqual(report["rows"][0][2], 0)
        self.assertEqual(report["rows"][0][3], 0)

    def test_intervention_effectiveness_detects_recurrence_after_correction(self):
        corrected = DisciplinaryRecord.objects.create(
            student=self.student,
            academic_term=self.term,
            offense="Earlier record",
            severity=DisciplinaryRecord.Severity.MINOR,
            action_taken="Warning",
        )
        active = DisciplinaryRecord.objects.create(
            student=self.student,
            academic_term=self.term,
            offense="Later record",
            severity=DisciplinaryRecord.Severity.MINOR,
            action_taken="Counselling",
        )
        correction_time = timezone.now() - timedelta(days=1)
        DisciplinaryRecord.objects.filter(pk=corrected.pk).update(
            created_at=correction_time - timedelta(days=2),
            corrected_at=correction_time,
        )
        DisciplinaryRecord.objects.filter(pk=active.pk).update(
            created_at=correction_time + timedelta(hours=2),
        )

        report = get_detailed_report(
            "intervention_effectiveness",
            house_id=self.house.pk,
            term_id=self.term.pk,
        )

        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["rows"][0][2:5], (2, 1, 1))
        self.assertEqual(report["rows"][0][6], "Yes")
        self.assertEqual(report["sections"][0]["rows"], report["rows"])

    def test_configuration_audit_is_date_filtered_and_omits_private_json_details(self):
        user = get_user_model().objects.create_user(
            username="audit-report-actor",
            first_name="Audit",
            last_name="Actor",
        )
        event = ConfigurationAuditLog.objects.create(
            actor=user,
            event=ConfigurationAuditLog.Event.SMS_STATE_CHANGED,
            description="SMS setting changed.",
            details={"private_token": "must-not-appear"},
        )
        ConfigurationAuditLog.objects.create(
            event=ConfigurationAuditLog.Event.SCHOOL_IDENTITY_CHANGED,
            description="Outside selected period.",
        )
        ConfigurationAuditLog.objects.filter(
            description="Outside selected period."
        ).update(created_at=event.created_at - timedelta(days=1))

        report = get_configuration_audit(
            date_from=event.created_at.date(),
            date_to=event.created_at.date(),
        )

        self.assertEqual(report["record_count"], 1)
        self.assertEqual(report["rows"][0][1], "Audit Actor")
        self.assertEqual(report["rows"][0][2], "SMS notification state changed")
        self.assertEqual(report["rows"][0][3], event.description)
        self.assertNotIn("must-not-appear", repr(report))

    def test_configuration_audit_page_is_admin_only_and_printable(self):
        admin = get_user_model().objects.create_superuser(
            username="config-audit-admin",
            email="config-audit-admin@example.test",
            password="test-password",
        )
        ConfigurationAuditLog.objects.create(
            actor=admin,
            event=ConfigurationAuditLog.Event.ACTIVE_TERM_CHANGED,
            description="Active term changed.",
        )
        self.client.force_login(admin)

        page = self.client.get(reverse("core:config_audit"))
        print_page = self.client.get(
            reverse("core:config_audit"),
            {"print": "1", "date_from": str(self.start_date)},
        )
        invalid_print = self.client.get(
            reverse("core:config_audit"),
            {"print": "1", "date_from": "not-a-date"},
        )

        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, "core/reports/config_audit.html")
        self.assertContains(page, "Configuration Audit Trail")
        self.assertEqual(print_page.status_code, 200)
        self.assertTemplateUsed(print_page, "core/reports/print.html")
        self.assertEqual(invalid_print.status_code, 400)

        housemaster = get_user_model().objects.create_user(
            username="config-audit-housemaster",
            password="test-password",
        )
        UserProfile.objects.update_or_create(
            user=housemaster,
            defaults={"role": StaffRole.HOUSEMASTER},
        )
        HouseMaster.objects.create(
            user=housemaster,
            full_name="Config Audit Housemaster",
            staff_id="ADV-CONFIG-HM",
            phone_number="+233200000093",
            house=self.house,
        )
        self.client.force_login(housemaster)
        denied = self.client.get(reverse("core:config_audit"))
        self.assertEqual(denied.status_code, 302)

    def test_kpi_page_print_and_csv_routes_render(self):
        admin = get_user_model().objects.create_superuser(
            username="advanced-reports-admin",
            email="advanced-reports@example.test",
            password="Test-only-password-123!",
        )
        self.client.force_login(admin)

        page = self.client.get(reverse("core:boarding_kpi"))
        print_page = self.client.get(
            reverse("core:boarding_kpi"),
            {"print": "1", "term": str(self.term.pk)},
        )
        export = self.client.get(
            reverse("core:boarding_kpi_export"),
            {"term": str(self.term.pk)},
        )

        self.assertEqual(page.status_code, 200)
        self.assertTemplateUsed(page, "core/reports/boarding_kpi.html")
        self.assertContains(page, "Boarding Termly KPI Pack")
        self.assertEqual(print_page.status_code, 200)
        self.assertTemplateUsed(print_page, "core/reports/boarding_kpi_print.html")
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn(b"Boarding Termly KPI Pack", export.content)
        self.assertIn(b"Configured beds", export.content)

        invalid_export = self.client.get(
            reverse("core:boarding_kpi_export"),
            {"date_from": "not-a-date"},
        )
        self.assertEqual(invalid_export.status_code, 400)

    def test_detail_reports_are_available_in_dashboard_and_print_views(self):
        admin = get_user_model().objects.create_superuser(
            username="advanced-detail-admin",
            email="advanced-detail@example.test",
            password="Test-only-password-123!",
        )
        self.client.force_login(admin)
        titles = {
            "placement_exceptions": "Placement Exception Queue",
            "attendance_league": "House Attendance League",
            "overdue_register": "Overdue & Late-Return Register",
            "gate_conflicts": "Gate / Roll-call Conflict Report",
            "approval_turnaround": "Exeat Approval Turnaround & Rejection",
            "exeat_analytics": "Exeat Type, Destination & Duration Analytics",
            "room_register": "Room Bed-Allocation & Vacancy Register",
            "staff_activity": "Staff Activity & Workload",
            "welfare_infirmary": "Welfare & Infirmary Referrals",
            "data_completeness": "Data Completeness / Register Hygiene",
            "movement_concerns": "Movement Concern Reports",
            "conduct_normalized": "Conduct Normalized by House Size",
            "intervention_effectiveness": "Intervention / Correction Effectiveness",
        }

        for report_type, title in titles.items():
            with self.subTest(report_type=report_type):
                response = self.client.get(
                    reverse("core:reports"),
                    {"report": report_type},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["report"]["title"], title)
                self.assertEqual(
                    response.context["report"]["record_count"],
                    len(response.context["report"]["rows"]),
                )
                print_response = self.client.get(
                    reverse("core:reports_print"),
                    {"report": report_type},
                )
                self.assertEqual(print_response.status_code, 200)
                self.assertEqual(print_response.context["report"]["title"], title)

    def test_housemaster_kpi_page_and_export_stay_in_assigned_house(self):
        user = get_user_model().objects.create_user(
            username="advanced-housemaster",
            password="Test-only-password-123!",
        )
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": StaffRole.HOUSEMASTER},
        )
        HouseMaster.objects.create(
            user=user,
            full_name="Advanced Report Housemaster",
            staff_id="ADV-HM",
            phone_number="+233200000091",
            house=self.house,
        )
        self.create_roll_call(
            self.house,
            [self.student],
            [RollCallEntry.Status.PRESENT],
        )
        self.create_roll_call(
            self.other_house,
            [self.other_student],
            [RollCallEntry.Status.PRESENT],
        )
        self.client.force_login(user)

        page = self.client.get(reverse("core:boarding_kpi"))
        export = self.client.get(
            reverse("core:boarding_kpi_export"),
            {"term": str(self.term.pk)},
        )
        tiles = {
            label: value for label, value, _tone in page.context["report"]["kpi_tiles"]
        }

        self.assertEqual(page.status_code, 200)
        self.assertEqual(tiles["Boarders"], 3)
        self.assertContains(page, self.house.name)
        self.assertNotContains(page, self.other_house.name)
        self.assertEqual(export.status_code, 200)
        self.assertIn(self.house.name.encode(), export.content)
        self.assertNotIn(self.other_house.name.encode(), export.content)


class ReportFilterBarAndPaginationTests(TestCase):
    """Report filter chrome: shared partial, disabled irrelevant filters, tabs, paging."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser(
            username="report-chrome-admin",
            email="report-chrome-admin@example.test",
            password="Test-only-password-123!",
        )
        cls.house = House.objects.create(name="Chrome House")
        cls.dormitory = Dormitory.objects.create(
            house=cls.house,
            name="Chrome Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=12,
        )
        cls.programme = Programme.objects.create(name="Chrome Programme")
        for index in range(12):
            Student.objects.create(
                admission_no=f"CHR-{index:03d}",
                index_number=f"IDX-CHR-{index:03d}",
                full_name=f"Chrome Student {index:02d}",
                gender=Student.Gender.MALE,
                programme=cls.programme,
                house=cls.house,
                dormitory=cls.dormitory,
            )

    def setUp(self):
        self.client.force_login(self.admin)

    @staticmethod
    def _input_tag(html, name):
        match = re.search(rf'<input[^>]*name="{name}"[^>]*>', html)
        return match.group(0) if match else ""

    def test_every_report_type_declares_whether_it_uses_period_filters(self):
        choice_names = {name for name, _label in ReportFilterForm.REPORT_CHOICES}
        self.assertTrue(REPORTS_WITHOUT_PERIOD_FILTERS <= choice_names)
        # Exactly these four reports are point-in-time snapshots. Adding a report
        # type should force a conscious decision about this set.
        self.assertEqual(
            REPORTS_WITHOUT_PERIOD_FILTERS,
            {"housing", "placement_exceptions", "room_register", "data_completeness"},
        )

    def test_snapshot_reports_disable_the_period_filters(self):
        response = self.client.get(
            reverse("core:reports"), {"report": "data_completeness"}
        )
        fields = response.context["report_filters"].fields
        self.assertTrue(fields["date_from"].disabled)
        self.assertTrue(fields["date_to"].disabled)
        self.assertTrue(fields["term"].disabled)

        html = response.content.decode()
        self.assertIn("disabled", self._input_tag(html, "date_from"))
        self.assertIn("disabled", self._input_tag(html, "date_to"))

    def test_period_reports_keep_the_filters_enabled(self):
        response = self.client.get(reverse("core:reports"), {"report": "attendance"})
        fields = response.context["report_filters"].fields
        self.assertFalse(fields["date_from"].disabled)
        self.assertFalse(fields["date_to"].disabled)
        self.assertFalse(fields["term"].disabled)

        html = response.content.decode()
        self.assertNotIn("disabled", self._input_tag(html, "date_from"))

    def test_config_audit_disables_only_the_term_filter(self):
        response = self.client.get(reverse("core:config_audit"))
        fields = response.context["report_filters"].fields
        self.assertFalse(fields["date_from"].disabled)
        self.assertTrue(fields["term"].disabled)

    def test_kpi_and_sms_keep_all_period_filters_enabled(self):
        kpi_fields = (
            self.client.get(reverse("core:boarding_kpi"))
            .context["report_filters"]
            .fields
        )
        self.assertFalse(kpi_fields["term"].disabled)
        self.assertFalse(kpi_fields["date_from"].disabled)

        sms_fields = (
            self.client.get(reverse("core:sms_usage")).context["report_filters"].fields
        )
        self.assertFalse(sms_fields["date_from"].disabled)
        self.assertFalse(sms_fields["term"].disabled)

    def test_report_pages_share_a_destination_nav_with_the_active_tab_marked(self):
        response = self.client.get(reverse("core:reports"))
        self.assertContains(response, 'aria-label="Boarding reports"')
        for url_name in (
            "core:reports",
            "core:boarding_kpi",
            "core:boarding_demographics",
            "core:sms_usage",
        ):
            self.assertContains(response, f'href="{reverse(url_name)}"')
        self.assertContains(response, 'aria-current="page"')

    def test_report_select_auto_submits_and_form_is_shared(self):
        response = self.client.get(reverse("core:reports"))
        self.assertContains(response, 'onchange="this.form.submit()"')
        self.assertContains(response, 'action="/reports/#report-results"')

    def test_long_tables_paginate_ten_rows_at_a_time(self):
        response = self.client.get(
            reverse("core:reports"), {"report": "data_completeness"}
        )
        page_obj = response.context["report"]["rows_page"]
        self.assertEqual(page_obj.paginator.count, 12)
        self.assertEqual(len(page_obj.object_list), 10)
        self.assertContains(response, "Showing 1\u201310 of 12")
        # The record count still reflects the whole report, not the visible page.
        self.assertEqual(response.context["report"]["record_count"], 12)

        second_page = self.client.get(
            reverse("core:reports"), {"report": "data_completeness", "page": 2}
        )
        self.assertEqual(len(second_page.context["report"]["rows_page"].object_list), 2)
        self.assertContains(second_page, "Page 2 of 2")

    def test_config_audit_paginates_its_events(self):
        for index in range(12):
            ConfigurationAuditLog.objects.create(
                actor=self.admin,
                event=ConfigurationAuditLog.Event.ACTIVE_TERM_CHANGED,
                description=f"Audit event {index:02d}",
            )
        response = self.client.get(reverse("core:config_audit"))
        page_obj = response.context["report"]["rows_page"]
        self.assertEqual(page_obj.paginator.count, 12)
        self.assertEqual(len(page_obj.object_list), 10)
        self.assertContains(response, "Showing 1\u201310 of 12")

    def test_print_links_drop_pagination_and_print_keeps_every_row(self):
        paged = self.client.get(
            reverse("core:reports"), {"report": "data_completeness", "page": 2}
        )
        self.assertIn(
            f'href="{reverse("core:reports_print")}?report=data_completeness"',
            paged.content.decode(),
        )

        printed = self.client.get(
            reverse("core:reports_print"), {"report": "data_completeness"}
        )
        self.assertEqual(printed.status_code, 200)
        self.assertEqual(len(printed.context["report"]["rows"]), 12)
        self.assertNotIn("rows_page", printed.context["report"])
        html = printed.content.decode()
        self.assertIn("Chrome Student 00", html)
        self.assertIn("Chrome Student 11", html)

    def test_pagination_controls_swap_the_results_region_via_htmx(self):
        response = self.client.get(
            reverse("core:reports"), {"report": "data_completeness"}
        )
        html = response.content.decode()
        self.assertIn('id="report-results-body"', html)
        self.assertIn('hx-target="#report-results-body"', html)
        self.assertIn('hx-push-url="true"', html)

    def test_htmx_pagination_returns_only_the_results_fragment(self):
        response = self.client.get(
            reverse("core:reports"),
            {"report": "data_completeness", "page": 2},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/reports/partials/report_results.html")
        html = response.content.decode()
        self.assertNotIn("<html", html.lower())
        self.assertIn("Showing 11\u201312 of 12", html)
        self.assertEqual(len(response.context["report"]["rows_page"].object_list), 2)

    def test_htmx_requests_render_each_pages_own_fragment(self):
        cases = (
            (
                reverse("core:boarding_kpi"),
                "core/reports/partials/boarding_kpi_results.html",
            ),
            (
                reverse("core:sms_usage"),
                "core/reports/partials/sms_usage_results.html",
            ),
            (
                reverse("core:config_audit"),
                "core/reports/partials/config_audit_results.html",
            ),
            (
                reverse("core:boarding_demographics"),
                "core/reports/partials/boarding_demographics_results.html",
            ),
        )
        for url, template_name in cases:
            with self.subTest(template=template_name):
                response = self.client.get(url, HTTP_HX_REQUEST="true")
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, template_name)
                self.assertNotIn("<html", response.content.decode().lower())

    def test_kpi_htmx_pagination_keeps_the_current_term_default(self):
        current_term = AcademicTerm.objects.create(
            academic_year=AcademicYear.objects.create(
                start_date=timezone.localdate() - timedelta(days=30),
                end_date=timezone.localdate() + timedelta(days=30),
            ),
            term=AcademicTerm.TermChoice.TERM_1,
            start_date=timezone.localdate() - timedelta(days=30),
            end_date=timezone.localdate() + timedelta(days=30),
            is_current=True,
        )
        response = self.client.get(
            reverse("core:boarding_kpi"),
            {"section1_page": "1"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["report_filters"]["term"].value(),
            str(current_term.pk),
        )
