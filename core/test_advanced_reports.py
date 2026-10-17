from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    AcademicTerm,
    AcademicYear,
    DisciplinaryRecord,
    Dormitory,
    DormitoryRoom,
    Exeat,
    ExeatMovement,
    House,
    HouseMaster,
    Programme,
    RollCallEntry,
    RollCallSession,
    SMSDelivery,
    StaffRole,
    Student,
    UserProfile,
)
from core.services.reports import (
    get_boarding_demographics,
    get_boarding_kpi_pack,
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
            "ADV-003", "Other House Report Student", cls.other_house, cls.other_dormitory
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
        Student.objects.filter(pk=day_student.pk).update(
            dormitory=self.dormitory
        )
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
        self.assertEqual(dict(report["summary"])["Currently overdue"], 1)
        self.assertEqual(dict(report["summary"])["Returned late in period"], 1)

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
            updated_at=timezone.now(),
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
        self.assertIn("updated_at", " ".join(report["highlights"]))

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
        self.assertEqual(empty_report["distribution"][0]["rows"], [("Male", 0), ("Female", 0)])

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
            **{key: value for key, value in shared_fields.items() if key != "dedupe_key"},
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
        self.assertTemplateUsed(
            print_page, "core/reports/boarding_kpi_print.html"
        )
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
        tiles = {label: value for label, value, _tone in page.context["report"]["kpi_tiles"]}

        self.assertEqual(page.status_code, 200)
        self.assertEqual(tiles["Boarders"], 3)
        self.assertContains(page, self.house.name)
        self.assertNotContains(page, self.other_house.name)
        self.assertEqual(export.status_code, 200)
        self.assertIn(self.house.name.encode(), export.content)
        self.assertNotIn(self.other_house.name.encode(), export.content)
