from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from core.models import (
    Student,
    Programme,
    House,
    Dormitory,
    Exeat,
    ExeatMovement,
    ExeatReconciliation,
    DisciplinaryRecord,
    AcademicYear,
    AcademicTerm,
    HouseMaster,
    StaffRole,
    UserProfile,
    get_user_profile,
    RollCallSession,
    RollCallEntry,
    RollCallAbsenceFollowUp,
)
from core.services.roll_call import (
    create_roll_call_session,
    update_entry_status,
    complete_roll_call_session,
    mark_all_present,
    resolve_absence_follow_up,
)
from core.services.reports import get_detailed_report

User = get_user_model()


class DashboardViewTests(TestCase):
    """Integration and unit tests for the enhanced executive dashboard and workspace grid."""

    def setUp(self):
        self.user = User.objects.create_superuser(
            username='test_housemaster',
            password='password123',
        )
        
        # Setup academic year & term
        self.year = AcademicYear.objects.create(
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=300),
            is_current=True
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=90),
            is_current=True
        )

        # Programme & House
        self.programme = Programme.objects.create(name='General Science', code='GSCI')
        self.house = House.objects.create(name='Red House')
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name='House 1 Block A',
            gender=Dormitory.GenderBlock.MALE,
            capacity=80
        )

        # Students
        self.boarder = Student.objects.create(
            admission_no='ADM001',
            index_number='IDX001',
            full_name='Kwame Mensah',
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
            residency=Student.ResidencyStatus.BOARDING
        )
        self.day_student = Student.objects.create(
            admission_no='ADM002',
            index_number='IDX002',
            full_name='Ama Osei',
            gender=Student.Gender.FEMALE,
            programme=self.programme,
            residency=Student.ResidencyStatus.DAY
        )

    def test_dashboard_login_required(self):
        """Unauthenticated requests should redirect to login."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 302)

    def test_dashboard_metrics_context(self):
        """Authenticated dashboard view provides the full 10-metric workspace context."""
        self.client.force_login(self.user)
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

        # Verify Workspace Grid Metrics in response context
        context = response.context
        self.assertEqual(context['total_boarders'], 1)
        self.assertEqual(context['day_students_count'], 1)
        self.assertEqual(context['total_students'], 2)
        self.assertEqual(context['total_capacity'], 80)
        self.assertEqual(context['occupancy_rate'], 1.2)
        self.assertEqual(context['pending_exeats'], 0)
        self.assertEqual(context['active_out'], 0)
        self.assertEqual(context['overdue_count'], 0)

    def test_dashboard_htmx_partial_render(self):
        """HTMX requests with table_only=1 should return the dashboard_table partial."""
        self.client.force_login(self.user)
        response = self.client.get('/?table_only=1&q=Kwame&status=all', HTTP_HX_REQUEST='true')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Pass</th>', response.content)

    def test_dashboard_exeat_table_shows_latest_five(self):
        """Dashboard uses the shared exeat table and limits it to the latest five records."""
        self.client.force_login(self.user)
        now = timezone.now()
        created_exeats = [
            Exeat.objects.create(
                exeat_number=f'DASHBOARD-{index:02d}',
                student=self.boarder,
                exeat_type=Exeat.ExeatType.TOWN,
                expected_departure=now + timezone.timedelta(hours=1),
                expected_return=now + timezone.timedelta(hours=3),
                destination='Town',
                reason='Errand',
                status=Exeat.Status.PENDING,
            )
            for index in range(6)
        ]

        response = self.client.get('/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['recent_exeats']), 5)
        self.assertEqual(response.context['total_matching_exeats'], 6)
        self.assertNotIn(created_exeats[0].exeat_number.encode(), response.content)
        self.assertIn(created_exeats[-1].exeat_number.encode(), response.content)
        self.assertIn(b'Pass</th>', response.content)
        self.assertIn(b'View Complete Exeat Register', response.content)
        self.assertIn(b'<strong>5</strong> of <strong>6</strong> matching records', response.content)
        partial_response = self.client.get(
            '/?table_only=1&q=DASHBOARD',
            HTTP_HX_REQUEST='true',
        )
        self.assertContains(
            partial_response,
            'id="dashboard-exeat-total-count" hx-swap-oob="true"',
        )
        self.assertContains(
            partial_response,
            '<strong>5</strong> of <strong>6</strong> matching records',
        )


class HouseOperationsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='housing_admin',
            password='password123',
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.user)
        self.programme = Programme.objects.create(name='Agriculture', code='AGRIC')
        self.house = House.objects.create(
            name='Kwame Nkrumah House',
            display_order=1,
        )
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name='Block A',
            gender=Dormitory.GenderBlock.MALE,
            capacity=2,
        )
        self.housemaster = HouseMaster.objects.create(
            full_name='Master Mensah',
            staff_id='HOUSING-001',
            phone_number='+233200000001',
            house=self.house,
        )

    def create_boarder(self, suffix):
        return Student.objects.create(
            admission_no=f'HOUSING-{suffix}',
            index_number=f'IDX-HOUSING-{suffix}',
            full_name=f'Boarder {suffix}',
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
            residency=Student.ResidencyStatus.BOARDING,
        )

    def test_house_operations_shows_occupancy_and_staff_coverage(self):
        self.create_boarder('001')

        response = self.client.get('/houses/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_boarders'], 1)
        self.assertEqual(response.context['total_capacity'], 2)
        self.assertEqual(response.context['overall_occupancy_rate'], 50)
        house = response.context['houses'][0]
        self.assertEqual(house.boarder_count, 1)
        self.assertEqual(house.occupancy_rate, 50)
        self.assertTrue(house.has_housemaster)
        self.assertEqual(house.health_label, 'Healthy')
        self.assertContains(response, 'House health')
        self.assertContains(response, 'Inspect dormitories and staff')

    def test_house_overview_supports_named_houses_without_numeric_identifiers(self):
        self.house.display_order = 2
        self.house.save()
        second_house = House.objects.create(
            name='Akwapim House',
            display_order=1,
        )

        response = self.client.get('/houses/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [house.name for house in response.context['houses']],
            ['Akwapim House', 'Kwame Nkrumah House'],
        )
        self.assertContains(response, 'Kwame Nkrumah House')
        self.assertEqual(str(second_house), 'Akwapim House')

    def test_house_operations_flags_over_capacity_and_missing_placements(self):
        self.create_boarder('002')
        self.create_boarder('003')
        self.create_boarder('004')
        self.create_boarder('005')
        Student.objects.filter(admission_no='HOUSING-005').update(dormitory=None)

        response = self.client.get('/houses/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['unassigned_boarders'], 1)
        house = response.context['houses'][0]
        dormitory = house.dormitories.all()[0]
        self.assertEqual(dormitory.over_capacity_count, 1)
        self.assertEqual(house.unplaced_boarders, 1)
        self.assertEqual(house.health_label, 'Critical')
        self.assertIn('Over capacity: Block A', house.attention_items)
        self.assertContains(response, 'Placement gaps')
        self.assertContains(response, 'over capacity')


class ExeatCreateViewTests(TestCase):
    """Integration tests for the new exeat creation workflow and student autocomplete."""

    def setUp(self):
        self.user = User.objects.create_user(username='housemaster_user', password='password123')
        self.client.force_login(self.user)

        self.year = AcademicYear.objects.create(
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=300),
            is_current=True
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=90),
            is_current=True
        )

        self.programme = Programme.objects.create(name='General Arts', code='GART')
        self.house = House.objects.create(name='Blue House')
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name='Blue Block 1',
            gender=Dormitory.GenderBlock.MALE,
            capacity=100
        )
        self.housemaster = HouseMaster.objects.create(
            user=self.user,
            full_name='Mr. J. Mensah',
            staff_id='STAFF-001',
            phone_number='+233200000001',
            house=self.house
        )

        self.boarder = Student.objects.create(
            admission_no='SHS-2026-001',
            index_number='10293847',
            full_name='Kofi Badu',
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
            residency=Student.ResidencyStatus.BOARDING
        )
        self.day_student = Student.objects.create(
            admission_no='SHS-2026-002',
            index_number='10293848',
            full_name='Yaa Asantewaa',
            gender=Student.Gender.FEMALE,
            programme=self.programme,
            residency=Student.ResidencyStatus.DAY
        )

    def test_exeat_create_get(self):
        """GET /exeats/new/ renders the exeat creation form successfully."""
        response = self.client.get('/exeats/new/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Issue Official Exeat Pass', response.content)

    def test_student_search_autocomplete(self):
        """GET /exeats/search-students/?q=Kofi returns Kofi Badu with select action."""
        response = self.client.get('/exeats/search-students/?q=Kofi')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Kofi Badu', response.content)
        self.assertIn(b'Boarder', response.content)

    def test_exeat_create_post_success(self):
        """A valid exeat request is submitted pending review without creating gate movement."""
        now = timezone.now()
        data = {
            'student': str(self.boarder.id),
            'exeat_type': Exeat.ExeatType.MEDICAL,
            'expected_departure': (now + timezone.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'expected_return': (now + timezone.timedelta(hours=5)).strftime('%Y-%m-%dT%H:%M'),
            'destination': 'Ridge Regional Hospital',
            'reason': 'Scheduled dental appointment with dentist.',
            'status': Exeat.Status.APPROVED,
            'approved_by': str(self.housemaster.id),
            'approval_remarks': 'Report to clinic master upon return.'
        }
        response = self.client.post('/exeats/new/', data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/exeats/')

        # Verify record in database
        exeat = Exeat.objects.get(student=self.boarder)
        self.assertEqual(exeat.exeat_type, Exeat.ExeatType.MEDICAL)
        self.assertEqual(exeat.status, Exeat.Status.PENDING)
        self.assertFalse(ExeatMovement.objects.filter(exeat=exeat).exists())

    def test_exeat_create_day_student_rejected(self):
        """POST /exeats/new/ for a Day student must fail validation."""
        now = timezone.now()
        data = {
            'student': str(self.day_student.id),
            'exeat_type': Exeat.ExeatType.TOWN,
            'expected_departure': (now + timezone.timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
            'expected_return': (now + timezone.timedelta(hours=4)).strftime('%Y-%m-%dT%H:%M'),
            'destination': 'Town Center',
            'reason': 'Personal errands',
            'status': Exeat.Status.APPROVED,
            'approved_by': str(self.housemaster.id),
        }
        response = self.client.post('/exeats/new/', data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Exeat.objects.filter(student=self.day_student).exists())

    def test_exeat_detail_view(self):
        """GET /exeats/<pk>/ renders the printable exeat pass slip."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.WEEKEND,
            expected_departure=now,
            expected_return=now + timezone.timedelta(days=2),
            destination='Kumasi',
            reason='Family wedding',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster
        )
        response = self.client.get(f'/exeats/{exeat.pk}/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Official Boarding Student Leave Authorization', response.content)
        self.assertIn(exeat.exeat_number.encode(), response.content)
        self.assertIn(b'Unknown', response.content)

    def test_exeat_quick_actions(self):
        """POST /exeats/<pk>/action/<action>/ correctly transitions gate movement and status."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=4),
            destination='Town Clinic',
            reason='Checkup',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster
        )

        # 1. Log Gate Departure
        dep_resp = self.client.post(f'/exeats/{exeat.pk}/action/departure/')
        self.assertEqual(dep_resp.status_code, 302)
        exeat.refresh_from_db()
        self.assertIsNotNone(exeat.movement.actual_departure)
        self.assertIsNone(exeat.movement.actual_return)

        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        cancel_resp = self.client.post(f'/exeats/{exeat.pk}/action/cancel/')
        self.assertEqual(cancel_resp.status_code, 302)
        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.APPROVED)
        self.assertIsNone(exeat.movement.actual_return)
        list_resp = self.client.get('/exeats/')
        self.assertNotIn(b'<span>Cancel Pass</span>', list_resp.content)

        # 2. Log Gate Return
        ret_resp = self.client.post(f'/exeats/{exeat.pk}/action/return/')
        self.assertEqual(ret_resp.status_code, 302)
        exeat.refresh_from_db()
        self.assertIsNotNone(exeat.movement.actual_return)
        self.assertEqual(exeat.movement.state_display, "Returned")

    def test_gate_departure_is_blocked_until_scheduled_time(self):
        """Gate departure is unavailable before its scheduled time, then succeeds."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now + timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=3),
            destination='Town',
            reason='Errand',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster,
        )

        response = self.client.get('/exeats/')
        self.assertNotIn(b'Log Gate Departure', response.content)
        self.assertIn(b'Departure available', response.content)

        early_response = self.client.post(f'/exeats/{exeat.pk}/action/departure/')

        self.assertEqual(early_response.status_code, 302)
        self.assertFalse(ExeatMovement.objects.filter(exeat=exeat).exists())

        exeat.expected_departure = now - timezone.timedelta(minutes=1)
        exeat.save(update_fields=['expected_departure'])
        departure_response = self.client.post(f'/exeats/{exeat.pk}/action/departure/')

        self.assertEqual(departure_response.status_code, 302)
        movement = ExeatMovement.objects.get(exeat=exeat)
        self.assertIsNotNone(movement.actual_departure)

    def test_exeat_tables_recalculate_overdue_status_on_each_request(self):
        """Overdue status and table filters use the current time on every request."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(minutes=30),
            destination='Town',
            reason='Errand',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster,
        )
        ExeatMovement.objects.create(exeat=exeat, actual_departure=now)

        dashboard_before = self.client.get('/?table_only=1&status=overdue', HTTP_HX_REQUEST='true')
        list_before = self.client.get('/exeats/?status=overdue', HTTP_HX_REQUEST='true')
        self.assertNotIn(exeat.exeat_number.encode(), dashboard_before.content)
        self.assertNotIn(exeat.exeat_number.encode(), list_before.content)

        exeat.expected_departure = now - timezone.timedelta(hours=1)
        exeat.expected_return = now - timezone.timedelta(minutes=15)
        exeat.save(update_fields=['expected_departure', 'expected_return'])
        dashboard_after = self.client.get('/?table_only=1&status=overdue', HTTP_HX_REQUEST='true')
        list_after = self.client.get('/exeats/?status=overdue', HTTP_HX_REQUEST='true')
        self.assertIn(exeat.exeat_number.encode(), dashboard_after.content)
        self.assertIn(exeat.exeat_number.encode(), list_after.content)
        self.assertIn(b'Overdue', dashboard_after.content)
        self.assertIn(b'Overdue', list_after.content)

        dashboard_all = self.client.get('/?table_only=1&status=all', HTTP_HX_REQUEST='true')
        list_all = self.client.get('/exeats/')
        self.assertIn(exeat.exeat_number.encode(), dashboard_all.content)
        self.assertIn(exeat.exeat_number.encode(), list_all.content)
        self.assertIn(b'Overdue', dashboard_all.content)
        self.assertIn(b'Overdue', list_all.content)

    def test_pass_past_return_without_departure_is_unresolved_not_overdue(self):
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timezone.timedelta(hours=3),
            expected_return=now - timezone.timedelta(hours=1),
            destination='Town',
            reason='Errand',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster,
        )

        register = self.client.get('/exeats/?status=unresolved')
        self.assertContains(register, exeat.exeat_number)
        self.assertContains(register, 'Pass Unresolved')
        self.assertEqual(exeat.current_state, 'Pass Unresolved')
        self.assertContains(register, 'Reconcile Missing Movement')

    def test_reconciliation_records_unused_pass_without_gate_events(self):
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timezone.timedelta(hours=3),
            expected_return=now - timezone.timedelta(hours=1),
            destination='Town',
            reason='Errand',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster,
        )

        response = self.client.post(
            f'/exeats/{exeat.pk}/reconcile/',
            {'outcome': 'unused', 'reason': 'Student confirmed they did not leave campus.'},
        )

        self.assertEqual(response.status_code, 302)
        reconciliation = ExeatReconciliation.objects.get(exeat=exeat)
        self.assertEqual(reconciliation.reconciled_by, self.user)
        self.assertEqual(exeat.current_state, 'Pass Unused')
        self.assertFalse(ExeatMovement.objects.filter(exeat=exeat, actual_departure__isnull=False).exists())
        detail = self.client.get(f'/exeats/{exeat.pk}/')
        self.assertContains(detail, 'Movement Reconciliation')
        self.assertContains(detail, 'Student confirmed they did not leave campus.')

    def test_reconciled_student_out_can_be_logged_back_in_without_fake_departure(self):
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timezone.timedelta(hours=3),
            expected_return=now - timezone.timedelta(hours=1),
            destination='Town',
            reason='Errand',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster,
        )
        response = self.client.post(
            f'/exeats/{exeat.pk}/reconcile/',
            {
                'outcome': 'student_out',
                'actual_departure': (now - timezone.timedelta(hours=2)).strftime('%Y-%m-%dT%H:%M'),
                'reason': 'Housemaster confirmed the student is still away by telephone.',
            },
        )
        self.assertEqual(response.status_code, 302)
        reconciliation = ExeatReconciliation.objects.get(exeat=exeat)
        self.assertEqual(exeat.current_state, 'OVERDUE (Reconciled)')
        overdue_list = self.client.get('/exeats/?status=overdue')
        self.assertContains(overdue_list, exeat.exeat_number)
        gate_terminal = self.client.get('/operations/gate/')
        self.assertContains(gate_terminal, 'Staff-reported')

        returned = self.client.post(f'/exeats/{exeat.pk}/action/return/')

        self.assertEqual(returned.status_code, 302)
        reconciliation.refresh_from_db()
        self.assertEqual(reconciliation.outcome, ExeatReconciliation.Outcome.RETURNED)
        self.assertEqual(reconciliation.return_recorded_by, self.user)
        self.assertIsNotNone(reconciliation.actual_return)
        exeat.refresh_from_db()
        self.assertEqual(exeat.current_state, 'Returned (Reconciled)')
        returned_list = self.client.get('/exeats/?status=returned')
        self.assertContains(returned_list, exeat.exeat_number)
        self.assertFalse(ExeatMovement.objects.filter(exeat=exeat, actual_departure__isnull=False).exists())

    def test_return_cannot_infer_a_missing_departure(self):
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=2),
            destination='Town',
            reason='Errand',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster,
        )

        response = self.client.post(f'/exeats/{exeat.pk}/action/return/')

        self.assertEqual(response.status_code, 302)
        self.assertFalse(ExeatMovement.objects.filter(exeat=exeat).exists())

    def test_rejected_exeat_cannot_be_cancelled(self):
        """Rejected passes have no cancel action and cannot be cancelled directly."""
        self.user.is_superuser = True
        self.user.save(update_fields=['is_superuser'])
        exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=timezone.now(),
            expected_return=timezone.now() + timezone.timedelta(hours=2),
            destination='Town',
            reason='Errand',
            status=Exeat.Status.REJECTED,
        )

        list_response = self.client.get('/exeats/?status=Rejected')
        self.assertNotIn(b'<span>Cancel Pass</span>', list_response.content)

        cancel_response = self.client.post(f'/exeats/{exeat.pk}/action/cancel/')

        self.assertEqual(cancel_response.status_code, 302)
        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.REJECTED)

    def test_exeat_list_filtering(self):
        """GET /exeats/ filters properly by type and status."""
        now = timezone.now()
        medical_exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.MEDICAL,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=2),
            destination='Hospital',
            reason='Care',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster
        )
        town_exeat = Exeat.objects.create(
            student=self.boarder,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=3),
            destination='Town',
            reason='Errand',
            status=Exeat.Status.PENDING
        )

        # Filter by type=Medical
        resp_med = self.client.get('/exeats/?type=Medical')
        self.assertEqual(resp_med.status_code, 200)
        self.assertIn(medical_exeat.exeat_number.encode(), resp_med.content)
        self.assertNotIn(town_exeat.exeat_number.encode(), resp_med.content)
        self.assertContains(resp_med, 'Showing <strong>1</strong> of <strong>1</strong> records')

        # Filter by status=Pending
        resp_pending = self.client.get('/exeats/?status=Pending')
        self.assertEqual(resp_pending.status_code, 200)
        self.assertIn(town_exeat.exeat_number.encode(), resp_pending.content)
        self.assertNotIn(medical_exeat.exeat_number.encode(), resp_pending.content)
        self.assertContains(resp_pending, 'Showing <strong>1</strong> of <strong>1</strong> records')

        resp_htmx = self.client.get('/exeats/?type=Medical', HTTP_HX_REQUEST='true')
        self.assertContains(resp_htmx, 'id="exeat-total-count" hx-swap-oob="true"')
        self.assertContains(resp_htmx, 'Showing <strong>1</strong> of <strong>1</strong> records')

    def test_exeat_register_paginates_all_matching_records(self):
        """Exeat Register paginates the full matching queryset and keeps accurate totals."""
        now = timezone.now()
        for index in range(101):
            Exeat.objects.create(
                exeat_number=f'REGISTER-{index:03d}',
                student=self.boarder,
                exeat_type=Exeat.ExeatType.TOWN,
                expected_departure=now + timezone.timedelta(hours=1),
                expected_return=now + timezone.timedelta(hours=3),
                destination='Town',
                reason='Errand',
                status=Exeat.Status.PENDING,
            )

        response = self.client.get('/exeats/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['exeats'].paginator.count, 101)
        self.assertEqual(len(response.context['exeats']), 10)
        self.assertContains(response, 'Showing <strong>10</strong> of <strong>101</strong> records')
        self.assertContains(response, 'aria-label="Table pagination"')
        self.assertContains(response, 'hx-get="?page=2"')
        self.assertContains(response, 'hx-target="#exeat-table"')

        last_page = self.client.get('/exeats/?page=11')
        self.assertEqual(last_page.status_code, 200)
        self.assertEqual(len(last_page.context['exeats']), 1)
        self.assertContains(last_page, 'Showing <strong>1</strong> of <strong>101</strong> records')
        self.assertIn(b'REGISTER-000', last_page.content)

        htmx_page = self.client.get(
            '/exeats/?type=Town&page=2',
            HTTP_HX_REQUEST='true',
            HTTP_HX_TARGET='exeat-table',
        )
        self.assertEqual(htmx_page.status_code, 200)
        self.assertTemplateUsed(htmx_page, 'core/exeats/partials/exeat_table.html')
        self.assertNotContains(htmx_page, '<!DOCTYPE html>')
        self.assertContains(htmx_page, 'hx-get="?type=Town&page=1"')


class ConductViewTests(TestCase):
    """Integration tests for Disciplinary Incident recording and Conduct Register views."""

    def setUp(self):
        self.user = User.objects.create_user(username='conduct_master', password='password123')
        self.client.force_login(self.user)

        self.year = AcademicYear.objects.create(
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=300),
            is_current=True
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=90),
            is_current=True
        )

        self.programme = Programme.objects.create(name='General Arts', code='GART')
        self.house = House.objects.create(name='Blue House')
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name='Blue Block 1',
            gender=Dormitory.GenderBlock.MALE,
            capacity=100
        )
        self.housemaster = HouseMaster.objects.create(
            user=self.user,
            full_name='Mr. J. Mensah',
            staff_id='STAFF-001',
            phone_number='+233200000001',
            house=self.house
        )

        self.student = Student.objects.create(
            admission_no='SHS-2026-001',
            index_number='10293847',
            full_name='Kofi Badu',
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
            residency=Student.ResidencyStatus.BOARDING
        )

    def test_conduct_list_view_get(self):
        """GET /conduct/ renders the disciplinary register with top metrics and records."""
        DisciplinaryRecord.objects.create(
            student=self.student,
            offense='Overstayed Exeat',
            severity=DisciplinaryRecord.Severity.MAJOR,
            action_taken='Campus grounding for 2 weeks',
            logged_by=self.housemaster
        )
        response = self.client.get('/conduct/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Disciplinary Register', response.content)
        self.assertEqual(response.context['total_incidents'], 1)
        self.assertEqual(response.context['major_count'], 1)
        self.assertEqual(response.context['critical_count'], 0)
        self.assertContains(response, 'Showing <strong>1</strong> of <strong>1</strong> incident logs')

    def test_conduct_list_filtering_and_htmx(self):
        """GET /conduct/ filters correctly by severity and query, and supports HTMX partials."""
        DisciplinaryRecord.objects.create(
            student=self.student,
            offense='Sneaking Out of Compound',
            severity=DisciplinaryRecord.Severity.CRITICAL,
            action_taken='Internal suspension for 1 week',
            logged_by=self.housemaster
        )
        DisciplinaryRecord.objects.create(
            student=self.student,
            offense='Missing Roll Call',
            severity=DisciplinaryRecord.Severity.MINOR,
            action_taken='Compound sweeping duty',
            logged_by=self.housemaster
        )

        # Filter by severity=Critical
        resp = self.client.get('/conduct/?severity=Critical')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Sneaking Out of Compound', resp.content)
        self.assertNotIn(b'Missing Roll Call', resp.content)

        # Filter by query search (table partial reload)
        resp_q = self.client.get('/conduct/?q=Sweeping', HTTP_HX_REQUEST='true')
        self.assertEqual(resp_q.status_code, 200)
        self.assertIn(b'Missing Roll Call', resp_q.content)
        self.assertNotIn(b'Sneaking Out of Compound', resp_q.content)
        self.assertContains(resp_q, 'id="conduct-total-count" hx-swap-oob="true"')
        self.assertContains(resp_q, 'Showing <strong>1</strong> of <strong>1</strong> incident logs')

        # HTMX search by student name
        resp_htmx = self.client.get('/conduct/?q=Kofi', HTTP_HX_REQUEST='true')
        self.assertEqual(resp_htmx.status_code, 200)
        self.assertIn(b'Kofi Badu', resp_htmx.content)

    def test_conduct_register_paginates_filtered_records(self):
        for index in range(16):
            DisciplinaryRecord.objects.create(
                student=self.student,
                offense=f'Pagination incident {index:02d}',
                severity=DisciplinaryRecord.Severity.MAJOR,
                action_taken='Follow-up',
                logged_by=self.housemaster,
            )

        first_page = self.client.get('/conduct/?severity=Major')
        self.assertEqual(first_page.status_code, 200)
        self.assertEqual(first_page.context['records'].paginator.count, 16)
        self.assertEqual(len(first_page.context['records']), 10)
        self.assertContains(first_page, 'Showing <strong>10</strong> of <strong>16</strong> incident logs')

        second_page = self.client.get('/conduct/?severity=Major&page=2')
        self.assertEqual(second_page.status_code, 200)
        self.assertEqual(len(second_page.context['records']), 6)
        self.assertContains(second_page, 'Pagination incident 00')
        self.assertContains(second_page, 'severity=Major&page=1')

    def test_incident_create_view_get(self):
        """GET /conduct/new/ renders the incident recording form."""
        response = self.client.get(f'/conduct/new/?student_id={self.student.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Record Student Conduct Infraction', response.content)
        self.assertIn(b'Kofi Badu', response.content)
        self.assertNotIn(b'Reporting Authority', response.content)
        self.assertNotIn('logged_by', response.context['form'].fields)

    def test_incident_create_view_hides_reporting_authority_for_house_tutor(self):
        """House tutors do not see or choose the reporting Housemaster field."""
        tutor = User.objects.create_user(username='conduct_tutor', password='password123')
        UserProfile.objects.filter(user=tutor).update(role=StaffRole.HOUSE_TUTOR)
        self.client.force_login(tutor)

        response = self.client.get(f'/conduct/new/?student_id={self.student.id}')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'Reporting Authority', response.content)
        self.assertNotIn(b'Logging Housemaster', response.content)

    def test_incident_create_view_defaults_reporting_authority_for_senior_housemaster(self):
        """Senior Housemasters also default the reporting authority to their own profile."""
        senior_user = User.objects.create_user(username='senior_master', password='password123')
        senior_housemaster = HouseMaster.objects.create(
            user=senior_user,
            full_name='Senior Housemaster',
            staff_id='STAFF-SENIOR',
            phone_number='+233200000002',
            is_senior_housemaster=True,
        )
        self.client.force_login(senior_user)

        response = self.client.get(f'/conduct/new/?student_id={self.student.id}')

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'Reporting Authority', response.content)
        self.assertNotIn('logged_by', response.context['form'].fields)

    def test_incident_create_view_post_sets_logged_by_from_creator(self):
        """The creator's Housemaster record is used regardless of submitted authority."""
        other_housemaster = HouseMaster.objects.create(
            full_name='Other Housemaster',
            staff_id='STAFF-OTHER',
            phone_number='+233200000003',
            house=self.house,
        )
        data = {
            'student': str(self.student.id),
            'offense': 'Missing Roll Call',
            'severity': DisciplinaryRecord.Severity.MINOR,
            'action_taken': 'Warning',
            'description': '',
            'logged_by': str(other_housemaster.id),
        }

        response = self.client.post('/conduct/new/', data)

        self.assertRedirects(response, '/conduct/')
        record = DisciplinaryRecord.objects.get(student=self.student, offense='Missing Roll Call')
        self.assertEqual(record.logged_by, self.housemaster)

    def test_incident_create_view_post_success(self):
        """POST /conduct/new/ creates a DisciplinaryRecord and redirects to conduct register."""
        data = {
            'student': str(self.student.id),
            'offense': 'Possession of Unauthorized Electronics',
            'severity': DisciplinaryRecord.Severity.MAJOR,
            'action_taken': 'Confiscation till term end and parental notification',
            'description': 'Smartphone found in locker during unannounced inspection.',
            'logged_by': str(self.housemaster.id),
        }
        response = self.client.post('/conduct/new/', data)
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, '/conduct/')

        record = DisciplinaryRecord.objects.get(student=self.student)
        self.assertEqual(record.offense, 'Possession of Unauthorized Electronics')
        self.assertEqual(record.severity, DisciplinaryRecord.Severity.MAJOR)
        self.assertEqual(record.academic_term, self.term)


class HeaderComponentsTests(TestCase):
    """Integration and unit tests for top command bar notifications and logged-in user options."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='snr_admin',
            first_name='Kwame',
            last_name='Mensah',
            password='password123'
        )
        self.client.force_login(self.user)

        self.year = AcademicYear.objects.create(
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=300),
            is_current=True
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=90),
            is_current=True
        )

        self.programme = Programme.objects.create(name='General Arts', code='GART')
        self.house = House.objects.create(name='Green House')
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name='Green Block 1',
            gender=Dormitory.GenderBlock.MALE,
            capacity=100
        )
        self.housemaster = HouseMaster.objects.create(
            user=self.user,
            full_name='Mr. Kwame Mensah',
            staff_id='snr_admin',
            phone_number='+233200000002',
            house=self.house
        )

        self.student = Student.objects.create(
            admission_no='SHS-2026-003',
            index_number='10293849',
            full_name='Kojo Blankson',
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
            residency=Student.ResidencyStatus.BOARDING
        )

    def test_user_initials_generator(self):
        """User initials correctly compute first+last monogram or fallback."""
        from core.context_processors import get_user_initials

        # Kwame Mensah -> KM
        self.assertEqual(get_user_initials(self.user), 'KM')

        # Single username 'admin' -> AD
        admin_user = User.objects.create_user(username='admin', password='password123')
        self.assertEqual(get_user_initials(admin_user), 'AD')

    def test_header_context_operational_alerts(self):
        """Context processor calculates overdue, pending, and critical alerts dynamically."""
        from core.context_processors import header_context
        from django.test import RequestFactory

        now = timezone.now()

        # 1. Create an Overdue Exeat
        exeat_overdue = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timezone.timedelta(hours=5),
            expected_return=now - timezone.timedelta(hours=2),
            destination='Market',
            reason='Errand',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster
        )
        movement, _ = ExeatMovement.objects.get_or_create(exeat=exeat_overdue)
        movement.actual_departure = now - timezone.timedelta(hours=5)
        movement.actual_return = None
        movement.save()

        # 2. Create a Pending Exeat
        Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.MEDICAL,
            expected_departure=now + timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=6),
            destination='Clinic',
            reason='Medical checkup',
            status=Exeat.Status.PENDING
        )

        # 3. Create a Critical Disciplinary Record
        DisciplinaryRecord.objects.create(
            student=self.student,
            offense='Unapproved Off-Campus Exit',
            severity=DisciplinaryRecord.Severity.CRITICAL,
            action_taken='Disciplinary Committee referral',
            logged_by=self.housemaster
        )

        factory = RequestFactory()
        request = factory.get('/')
        request.user = self.user

        context = header_context(request)
        self.assertEqual(context['overdue_count'], 1)
        self.assertEqual(context['pending_exeat_count'], 1)
        self.assertEqual(context['critical_incident_count'], 1)
        self.assertEqual(context['notification_count'], 3)
        self.assertEqual(context['user_initials'], 'KM')
        self.assertEqual(len(context['notifications']), 3)

    def test_dashboard_renders_header_components(self):
        """Dashboard HTML response contains notification bell, count badge, and user menu."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

        # User avatar and monogram initials
        self.assertIn(b'KM', response.content)
        self.assertIn(b'Sign Out', response.content)
        self.assertIn(b'Active Alerts', response.content)

    def test_logout_view(self):
        """POST /logout/ terminates user session and redirects to dashboard (which redirects to login)."""
        response = self.client.post('/logout/')
        self.assertEqual(response.status_code, 302)
        # Verify user is logged out: next request to / should redirect to login
        dash_resp = self.client.get('/')
        self.assertEqual(dash_resp.status_code, 302)
        self.assertIn('/login/', dash_resp.url)

    def test_toast_notification_rendering(self):
        """Flash messages are rendered responsibly as floating toasts in base.html and cleared."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=2),
            destination='Town',
            reason='Errand',
            status=Exeat.Status.PENDING
        )
        # Cancel the pass -> triggers messages.info(...) and redirects
        action_resp = self.client.post(f'/exeats/{exeat.pk}/action/cancel/', follow=True)
        self.assertEqual(action_resp.status_code, 200)

        # Verify toast container and message rendered in response HTML
        self.assertIn(b'id="toast-container"', action_resp.content)
        self.assertIn(b'was cancelled', action_resp.content)
        self.assertIn(b'Update', action_resp.content)

        # Subsequent request should have consumed the messages
        next_resp = self.client.get('/')
        self.assertEqual(next_resp.status_code, 200)
        self.assertNotIn(b'was cancelled', next_resp.content)


class UserRolesAndPermissionsTests(TestCase):
    """
    Test suite for Eden High School user types, role profiles,
    and jurisdictional authorization boundaries.
    """

    def setUp(self):
        # Academic Setup
        self.year = AcademicYear.objects.create(
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=300),
            is_current=True
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=90),
            is_current=True
        )

        # Houses & Dormitories
        self.house1 = House.objects.create(name='Red House')
        self.dorm1 = Dormitory.objects.create(
            house=self.house1,
            name='Block A',
            gender=Dormitory.GenderBlock.MALE,
            capacity=60
        )
        self.house2 = House.objects.create(name='Blue House')
        self.dorm2 = Dormitory.objects.create(
            house=self.house2,
            name='Block B',
            gender=Dormitory.GenderBlock.MALE,
            capacity=60
        )

        # Programme
        self.programme = Programme.objects.create(name='General Arts', code='GART')

        # Student in House 1
        self.student = Student.objects.create(
            admission_no='SHS-2026-H1',
            index_number='IDX-2026-H1',
            full_name='Kofi Boateng',
            gender=Student.Gender.MALE,
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 16),
            programme=self.programme,
            residency=Student.ResidencyStatus.BOARDING,
            house=self.house1,
            dormitory=self.dorm1
        )

        # Users and Housemasters
        # 1. House 1 Master
        self.user_h1 = User.objects.create_user(username='hm_house1', password='password123')
        self.hm1 = HouseMaster.objects.create(
            user=self.user_h1,
            full_name='Master Kwesi',
            staff_id='STF-H1',
            phone_number='0241000001',
            house=self.house1
        )

        # 2. House 2 Master
        self.user_h2 = User.objects.create_user(username='hm_house2', password='password123')
        self.hm2 = HouseMaster.objects.create(
            user=self.user_h2,
            full_name='Master Mensah',
            staff_id='STF-H2',
            phone_number='0241000002',
            house=self.house2
        )

        # 3. Senior Housemaster
        self.user_shm = User.objects.create_user(username='senior_hm', password='password123')
        self.shm = HouseMaster.objects.create(
            user=self.user_shm,
            full_name='Chief Master Baah',
            staff_id='STF-SHM',
            phone_number='0241000003',
            is_senior_housemaster=True
        )

        # 4. Gate / Security Officer
        self.user_gate = User.objects.create_user(username='gate_officer', password='password123')
        self.hm_gate = HouseMaster.objects.create(
            user=self.user_gate,
            full_name='Gate Officer',
            staff_id='STF-GATE',
            phone_number='0241000004',
            house=self.house1,
        )
        gate_profile = get_user_profile(self.user_gate)
        gate_profile.role = StaffRole.GATE_OFFICER
        gate_profile.save()

    def test_user_profile_creation_signal(self):
        """Registering a User automatically provisions a linked UserProfile."""
        user = User.objects.create_user(username='new_staff', password='password123')
        profile = get_user_profile(user)
        self.assertIsNotNone(profile)
        self.assertEqual(profile.role, StaffRole.HOUSEMASTER)

        admin_user = User.objects.create_superuser(username='admin_boss', password='password123', email='admin@eden.edu')
        admin_profile = get_user_profile(admin_user)
        self.assertEqual(admin_profile.role, StaffRole.ADMIN)

    def test_housemaster_linked_to_user_sync(self):
        """Saving a HouseMaster synchronized the associated UserProfile role."""
        profile_h1 = get_user_profile(self.user_h1)
        self.assertEqual(profile_h1.role, StaffRole.HOUSEMASTER)

        profile_shm = get_user_profile(self.user_shm)
        self.assertEqual(profile_shm.role, StaffRole.SENIOR_HOUSEMASTER)

    def test_exeat_approval_jurisdiction(self):
        """
        House 1 Master can approve House 1 student's exeat.
        House 2 Master cannot approve House 1 student's exeat.
        Senior Housemaster can approve any house's exeat.
        """
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=4),
            destination='Town Center',
            reason='Medical checkup',
            status=Exeat.Status.PENDING
        )

        # 1. House 2 Master attempts to approve House 1 exeat -> Should fail
        self.client.login(username='hm_house2', password='password123')
        resp_h2 = self.client.post(f'/exeats/{exeat.pk}/action/approve/', follow=True)
        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.PENDING)
        self.assertEqual(resp_h2.status_code, 404)

        # 2. House 1 Master approves House 1 exeat -> Should succeed
        self.client.login(username='hm_house1', password='password123')
        resp_h1 = self.client.post(f'/exeats/{exeat.pk}/action/approve/', follow=True)
        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.APPROVED)
        self.assertEqual(exeat.approved_by, self.hm1)
        self.assertIn(b'marked as Approved', resp_h1.content)

    def test_senior_housemaster_campus_wide_approval(self):
        """Senior Housemaster can approve exeats across any house."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.WEEKEND,
            expected_departure=now,
            expected_return=now + timezone.timedelta(days=2),
            destination='Home',
            reason='Family event',
            status=Exeat.Status.PENDING
        )

        self.client.login(username='senior_hm', password='password123')
        resp = self.client.post(f'/exeats/{exeat.pk}/action/approve/', follow=True)
        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.APPROVED)
        self.assertEqual(exeat.approved_by, self.shm)

    def test_gate_officer_permissions(self):
        """
        Gate Officer can log gate departure and return on approved exeats,
        but cannot approve pending exeats.
        """
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=2),
            destination='Market',
            reason='Supplies',
            status=Exeat.Status.PENDING
        )

        self.client.login(username='gate_officer', password='password123')

        # Gate officer attempts to approve -> Denied
        resp_approve = self.client.post(f'/exeats/{exeat.pk}/action/approve/', follow=True)
        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.PENDING)
        self.assertIn(b'Permission denied', resp_approve.content)

        # Housemaster approves first
        exeat.status = Exeat.Status.APPROVED
        exeat.approved_by = self.hm1
        exeat.save()

        # Gate officer logs departure -> Allowed
        resp_dep = self.client.post(f'/exeats/{exeat.pk}/action/departure/', follow=True)
        self.assertEqual(resp_dep.status_code, 200)
        self.assertIn(b'Gate departure recorded', resp_dep.content)
        movement = ExeatMovement.objects.get(exeat=exeat)
        self.assertIsNotNone(movement.actual_departure)

    def test_context_processor_direct_role_display(self):
        """Header context processor accurately reflects housemaster and user role."""
        self.client.login(username='hm_house1', password='password123')
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Housemaster (Red House)', resp.content)


class GlobalSearchTests(TestCase):
    """Integration tests for the system-wide search / command palette endpoint."""

    def setUp(self):
        # 1. Academic Structure
        self.year = AcademicYear.objects.create(
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=300),
            is_current=True
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=90),
            is_current=True
        )

        # 2. Housing & Programme
        self.house = House.objects.create(name='Aggrey House')
        self.dorm = Dormitory.objects.create(
            house=self.house,
            name='Alpha Dormitory',
            gender=Dormitory.GenderBlock.MALE,
            capacity=50
        )
        self.programme = Programme.objects.create(name='General Science', code='SCI')

        # 3. Student
        self.student = Student.objects.create(
            admission_no='SHS-2026-999',
            index_number='IDX-2026-999',
            full_name='Kwame Nkrumah Mensah',
            gender=Student.Gender.MALE,
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 16),
            programme=self.programme,
            residency=Student.ResidencyStatus.BOARDING,
            house=self.house,
            dormitory=self.dorm
        )

        # 4. User & Housemaster
        self.user = User.objects.create_user(username='search_user', password='password123')
        self.user_housemaster = HouseMaster.objects.create(
            user=self.user,
            full_name='Search User Housemaster',
            staff_id='STF-SRCH-USER',
            phone_number='0241000998',
            house=self.house,
        )
        self.superuser = User.objects.create_superuser(username='search_admin', password='password123', email='admin@eden.edu')
        self.user_hm = User.objects.create_user(username='search_hm', password='password123')
        self.housemaster = HouseMaster.objects.create(
            user=self.user_hm,
            full_name='Master Mensah',
            staff_id='STF-SRCH-01',
            phone_number='0241000999',
            house=self.house
        )

    def test_search_requires_login(self):
        """Unauthenticated requests are redirected to login."""
        response = self.client.get('/search/?q=Kwame')
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_search_short_query_returns_prompt(self):
        """Query with less than 2 characters asks user to type at least 2 chars."""
        self.client.login(username='search_user', password='password123')
        response = self.client.get('/search/?q=k')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['is_short'])
        self.assertIn(b'Type at least 2 characters to search', response.content)

    def test_search_by_student_name_and_admission_no(self):
        """Search matches student by full name and admission number."""
        self.client.login(username='search_user', password='password123')

        # Search by name
        resp_name = self.client.get('/search/?q=Nkrumah')
        self.assertEqual(resp_name.status_code, 200)
        self.assertIn(b'Kwame Nkrumah Mensah', resp_name.content)
        self.assertIn(b'SHS-2026-999', resp_name.content)

        # Search by admission number
        resp_adm = self.client.get('/search/?q=SHS-2026-999')
        self.assertEqual(resp_adm.status_code, 200)
        self.assertIn(b'Kwame Nkrumah Mensah', resp_adm.content)

    def test_search_by_exeat_details(self):
        """Search finds active or past exeat passes by exeat number or destination."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.student,
            academic_term=self.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=4),
            destination='Cape Coast Central Hospital',
            reason='Medical checkup',
            status=Exeat.Status.APPROVED,
            approved_by=self.housemaster
        )

        self.client.login(username='search_user', password='password123')

        # Search by destination
        resp_dest = self.client.get('/search/?q=Hospital')
        self.assertEqual(resp_dest.status_code, 200)
        self.assertIn(exeat.exeat_number.encode(), resp_dest.content)
        self.assertIn(b'Cape Coast Central Hospital', resp_dest.content)

        # Search by exeat code
        resp_code = self.client.get(f'/search/?q={exeat.exeat_number[:8]}')
        self.assertEqual(resp_code.status_code, 200)
        self.assertIn(exeat.exeat_number.encode(), resp_code.content)

    def test_search_by_house_and_dormitory(self):
        """Search locates houses and dormitories by name."""
        self.client.login(username='search_user', password='password123')

        resp_house = self.client.get('/search/?q=Aggrey')
        self.assertEqual(resp_house.status_code, 200)
        self.assertIn(b'Aggrey House', resp_house.content)

        resp_dorm = self.client.get('/search/?q=Alpha')
        self.assertEqual(resp_dorm.status_code, 200)
        self.assertIn(b'Alpha Dormitory', resp_dorm.content)

    def test_search_by_conduct_offense(self):
        """Search finds student disciplinary and conduct logs."""
        record = DisciplinaryRecord.objects.create(
            student=self.student,
            academic_term=self.term,
            offense='Sneaking Out After Hours',
            description='Caught near the northern perimeter fence',
            severity=DisciplinaryRecord.Severity.MAJOR,
            action_taken='Campus gating for 2 weekends'
        )

        self.client.login(username='search_user', password='password123')
        resp = self.client.get('/search/?q=Sneaking')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Sneaking Out After Hours', resp.content)
        self.assertIn(b'Campus gating for 2 weekends', resp.content)

    def test_search_quick_actions_and_superuser_admin_link(self):
        """Search surfaces quick actions and admin navigation for superusers."""
        # Regular user search
        self.client.login(username='search_user', password='password123')
        resp_reg = self.client.get('/search/?q=exeat')
        self.assertEqual(resp_reg.status_code, 200)
        self.assertIn(b'New Exeat Request', resp_reg.content)
        self.assertNotIn(b'Django Administration', resp_reg.content)

        # Superuser search
        self.client.login(username='search_admin', password='password123')
        resp_admin = self.client.get('/search/?q=admin')
        self.assertEqual(resp_admin.status_code, 200)
        self.assertIn(b'Django Administration', resp_admin.content)

    def test_search_no_results_found(self):
        """Unmatched queries render clean empty state."""
        self.client.login(username='search_user', password='password123')
        resp = self.client.get('/search/?q=nonexistentqueryxyz')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['total_results'], 0)
        self.assertIn(b'No results found for', resp.content)


class RollCallAndGateTests(TestCase):
    """
    Test suite for Roll Call Muster and Gate Security Terminal operations.
    Validates auto-roster population, exeat reconciliation, gate departures/returns,
    and jurisdictional authorization.
    """

    def setUp(self):
        # 1. Academic Structure
        self.year = AcademicYear.objects.create(
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=300),
            is_current=True
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=timezone.now().date(),
            end_date=timezone.now().date() + timezone.timedelta(days=90),
            is_current=True
        )

        # 2. House & Dormitory
        self.house = House.objects.create(name='Volta House')
        self.dorm = Dormitory.objects.create(
            house=self.house,
            name='Volta Block A',
            gender=Dormitory.GenderBlock.MALE,
            capacity=60
        )
        self.programme = Programme.objects.create(name='Business', code='BUS')

        # 3. Students: 2 Boarders, 1 Day student
        self.boarder1 = Student.objects.create(
            admission_no='SHS-2026-B1',
            index_number='IDX-2026-B1',
            full_name='Agyemang Badu',
            gender=Student.Gender.MALE,
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 16),
            programme=self.programme,
            residency=Student.ResidencyStatus.BOARDING,
            house=self.house,
            dormitory=self.dorm
        )
        self.boarder2 = Student.objects.create(
            admission_no='SHS-2026-B2',
            index_number='IDX-2026-B2',
            full_name='Emmanuel Frimpong',
            gender=Student.Gender.MALE,
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 16),
            programme=self.programme,
            residency=Student.ResidencyStatus.BOARDING,
            house=self.house,
            dormitory=self.dorm
        )
        self.day_student = Student.objects.create(
            admission_no='SHS-2026-D1',
            index_number='IDX-2026-D1',
            full_name='Yaw Mensah',
            gender=Student.Gender.MALE,
            date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 16),
            programme=self.programme,
            residency=Student.ResidencyStatus.DAY,
            house=self.house
        )

        # 4. Users: Housemaster, Gate Officer, Regular Staff
        self.user_hm = User.objects.create_user(username='hm_volta', password='password123')
        self.hm = HouseMaster.objects.create(
            user=self.user_hm,
            full_name='Master Volta',
            staff_id='STF-VLT-01',
            phone_number='0241999888',
            house=self.house
        )

        self.user_gate = User.objects.create_user(username='gate_officer_bob', password='password123')
        self.gate_hm = HouseMaster.objects.create(
            user=self.user_gate,
            full_name='Gate Officer Bob',
            staff_id='STF-GATE-01',
            phone_number='0241999777',
            house=self.house,
        )
        profile_gate = get_user_profile(self.user_gate)
        profile_gate.role = StaffRole.GATE_OFFICER
        profile_gate.save()

        self.user_regular = User.objects.create_user(username='unauthorized_user', password='password123')
        profile_reg = get_user_profile(self.user_regular)
        profile_reg.role = StaffRole.HOUSE_TUTOR
        profile_reg.save()
        self.regular_hm = HouseMaster.objects.create(
            user=self.user_regular,
            full_name='Assigned House Tutor',
            staff_id='STF-TUTOR-01',
            phone_number='0241999666',
            house=self.house,
        )

    def test_separate_operations_pages_require_login_and_combined_page_is_retired(self):
        """Dedicated pages require login, and the retired combined URL is gone."""
        for path in ('/operations/roll-call/', '/operations/gate/'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertIn('/login/', response.url)
        self.assertEqual(self.client.get('/operations/').status_code, 404)

    def test_roll_call_and_gate_permissions_are_separate_in_sidebar_and_pages(self):
        """House staff see only their authorized workspace and sidebar link."""
        self.client.force_login(self.user_hm)
        home_response = self.client.get('/')
        self.assertTrue(home_response.context['can_access_roll_call'])
        self.assertFalse(home_response.context['can_access_gate'])
        self.assertContains(home_response, 'href="/operations/roll-call/"')
        self.assertNotContains(home_response, 'href="/operations/gate/"')

        response = self.client.get('/operations/roll-call/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Roll Call Sessions')
        self.assertNotContains(response, 'Gate Security & Movement Terminal')
        self.assertNotContains(response, 'Currently Out')
        self.assertNotContains(response, 'Overdue at Gate')

        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder1,
            academic_term=self.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timezone.timedelta(minutes=1),
            expected_return=now + timezone.timedelta(hours=2),
            destination='Town',
            reason='Test restricted gate access',
            status=Exeat.Status.APPROVED,
            approved_by=self.hm,
        )
        response = self.client.post(f'/operations/gate/{exeat.pk}/departure/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ExeatMovement.objects.filter(exeat=exeat).exists())

        response = self.client.get('/operations/gate/')
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ExeatMovement.objects.filter(exeat=exeat).exists())

        housemaster_profile = get_user_profile(self.user_hm)
        housemaster_profile.role = StaffRole.SENIOR_HOUSEMASTER
        housemaster_profile.save(update_fields=['role'])
        senior_home_response = self.client.get('/')
        self.assertTrue(senior_home_response.context['can_access_roll_call'])
        self.assertFalse(senior_home_response.context['can_access_gate'])
        self.assertNotContains(senior_home_response, 'href="/operations/gate/"')
        self.assertEqual(self.client.get('/operations/gate/').status_code, 302)

        self.client.force_login(self.user_gate)
        home_response = self.client.get('/')
        self.assertRedirects(
            home_response,
            '/operations/gate/',
            fetch_redirect_response=False,
        )

        response = self.client.get('/operations/roll-call/')
        self.assertEqual(response.status_code, 302)
        response = self.client.get('/operations/gate/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_access_roll_call'])
        self.assertTrue(response.context['can_access_gate'])
        self.assertContains(response, 'Gate Security & Movement Terminal')
        self.assertNotContains(response, 'Dormitory Roster')
        self.assertNotContains(response, 'id="modal-title"')

        response = self.client.post('/operations/roll-call/new/', {
            'house': str(self.house.pk),
            'session_type': RollCallSession.SessionType.EVENING,
        })
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.user_regular)
        home_response = self.client.get('/')
        self.assertFalse(home_response.context['can_access_roll_call'])
        self.assertFalse(home_response.context['can_access_gate'])
        self.assertNotContains(home_response, 'href="/operations/roll-call/"')
        self.assertNotContains(
            home_response,
            'href="/operations/gate/"',
        )
        response = self.client.get('/operations/roll-call/')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get('/operations/gate/').status_code, 302)

    def test_admin_can_access_both_separate_workspaces(self):
        admin = User.objects.create_user(
            username='operations_admin',
            email='operations-admin@example.com',
            password='password123',
        )
        admin_profile = get_user_profile(admin)
        admin_profile.role = StaffRole.ADMIN
        admin_profile.save(update_fields=['role'])
        self.client.force_login(admin)

        home_response = self.client.get('/')
        self.assertTrue(home_response.context['can_access_roll_call'])
        self.assertTrue(home_response.context['can_access_gate'])
        self.assertContains(home_response, 'Roll Call Sessions')
        self.assertContains(home_response, 'Gate Security')

        roll_call_response = self.client.get('/operations/roll-call/')
        gate_response = self.client.get('/operations/gate/')
        self.assertEqual(roll_call_response.status_code, 200)
        self.assertEqual(gate_response.status_code, 200)
        self.assertContains(home_response, 'href="/operations/roll-call/"')
        self.assertContains(home_response, 'href="/operations/gate/"')
        self.assertNotContains(roll_call_response, 'Gate Security & Movement Terminal')
        self.assertNotContains(gate_response, 'Dormitory Roster')

    def test_roll_call_session_creation_auto_populates_boarders(self):
        """Roll call auto-populates only boarding students of the target house."""
        session = create_roll_call_session(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
            conducted_by=self.hm
        )
        self.assertEqual(session.entries.count(), 2)
        student_ids = list(session.entries.values_list('student_id', flat=True))
        self.assertIn(self.boarder1.id, student_ids)
        self.assertIn(self.boarder2.id, student_ids)
        self.assertNotIn(self.day_student.id, student_ids)

    def test_roll_call_roster_is_paginated_and_keeps_session_selected(self):
        for index in range(15):
            Student.objects.create(
                admission_no=f'SHS-PAGE-{index:02d}',
                index_number=f'IDX-PAGE-{index:02d}',
                full_name=f'Pagination Student {index:02d}',
                gender=Student.Gender.MALE,
                date_of_birth=timezone.now().date() - timezone.timedelta(days=365 * 16),
                programme=self.programme,
                residency=Student.ResidencyStatus.BOARDING,
                house=self.house,
                dormitory=self.dorm,
            )
        session = create_roll_call_session(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
            conducted_by=self.hm,
        )

        self.client.force_login(self.user_hm)
        response = self.client.get(
            f'/operations/roll-call/?session_id={session.pk}&entry_page=2'
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['session_entries_page'].paginator.count, 17)
        self.assertEqual(len(response.context['session_entries_page']), 7)
        self.assertContains(response, f'session_id={session.pk}&entry_page=1')

        htmx_response = self.client.get(
            f'/operations/roll-call/?session_id={session.pk}&entry_page=2',
            HTTP_HX_REQUEST='true',
            HTTP_HX_TARGET='roll-call-roster',
        )
        self.assertTemplateUsed(
            htmx_response, 'core/operations/partials/roll_call_roster.html'
        )
        self.assertNotContains(htmx_response, '<!DOCTYPE html>')

    def test_gate_currently_out_table_paginates_movement_and_reconciliation_rows(self):
        now = timezone.now()
        for index in range(16):
            exeat = Exeat.objects.create(
                student=self.boarder1,
                academic_term=self.term,
                exeat_type=Exeat.ExeatType.TOWN,
                expected_departure=now - timezone.timedelta(hours=2),
                expected_return=now + timezone.timedelta(hours=2),
                destination='Town',
                reason=f'Gate pagination {index}',
                status=Exeat.Status.APPROVED,
                approved_by=self.hm,
            )
            ExeatMovement.objects.create(
                exeat=exeat,
                actual_departure=now - timezone.timedelta(minutes=index + 1),
                departure_recorded_by='Gate Officer Bob',
            )

        reconciled_exeat = Exeat.objects.create(
            student=self.boarder2,
            academic_term=self.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now - timezone.timedelta(hours=2),
            expected_return=now + timezone.timedelta(hours=2),
            destination='Town',
            reason='Reconciled gate pagination',
            status=Exeat.Status.APPROVED,
            approved_by=self.hm,
        )
        ExeatReconciliation.objects.create(
            exeat=reconciled_exeat,
            outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
            actual_departure=now - timezone.timedelta(hours=1),
            reason='Confirmed departure',
            reconciled_by=self.user_gate,
        )

        self.client.force_login(self.user_gate)
        response = self.client.get('/operations/gate/?out_page=2&movement_page=2')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_out_page'].paginator.count, 17)
        self.assertEqual(len(response.context['active_out_page']), 7)
        self.assertEqual(len(response.context['recent_movements_page']), 6)
        self.assertContains(response, 'movement_page=2&out_page=1')

        htmx_response = self.client.get(
            '/operations/gate/?out_page=2',
            HTTP_HX_REQUEST='true',
            HTTP_HX_TARGET='gate-terminal-body',
        )
        self.assertTemplateUsed(
            htmx_response, 'core/operations/partials/gate_terminal_body.html'
        )
        self.assertNotContains(htmx_response, '<!DOCTYPE html>')

    def test_roll_call_auto_detects_active_exeat(self):
        """Students currently departed on approved exeat are automatically marked ON_EXEAT."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder1,
            academic_term=self.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=3),
            destination='Town Clinic',
            reason='Dentist',
            status=Exeat.Status.APPROVED,
            approved_by=self.hm
        )
        movement = ExeatMovement.objects.create(
            exeat=exeat,
            actual_departure=now,
            departure_recorded_by='Gate Officer Bob'
        )

        session = create_roll_call_session(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
            conducted_by=self.hm
        )

        entry_boarder1 = session.entries.get(student=self.boarder1)
        entry_boarder2 = session.entries.get(student=self.boarder2)

        self.assertEqual(entry_boarder1.status, RollCallEntry.Status.ON_EXEAT)
        self.assertEqual(entry_boarder1.status_source, RollCallEntry.StatusSource.GATE)
        self.assertIn('Active Gate Exeat', entry_boarder1.remarks)
        self.assertEqual(entry_boarder2.status, RollCallEntry.Status.UNMARKED)

    def test_roll_call_entries_start_unmarked_and_unmarked_is_not_present(self):
        session = create_roll_call_session(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
            conducted_by=self.hm,
        )

        self.assertEqual(session.unmarked_count, 2)
        self.assertEqual(session.accounted_count, 0)
        self.assertEqual(session.present_count, 0)
        self.assertEqual(session.attendance_rate, 0)

    def test_roll_call_absence_creates_pending_follow_up_not_a_conduct_record(self):
        """An absence is reviewed before it can be recorded as a conduct incident."""
        session = create_roll_call_session(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
            conducted_by=self.hm
        )
        entry = session.entries.get(student=self.boarder1)

        self.client.login(username='hm_volta', password='password123')
        resp = self.client.post(
            f'/operations/roll-call/entry/{entry.id}/',
            {'status': RollCallEntry.Status.ABSENT},
            follow=True
        )
        self.assertEqual(resp.status_code, 200)

        entry.refresh_from_db()
        self.assertEqual(entry.status, RollCallEntry.Status.ABSENT)

        follow_up = RollCallAbsenceFollowUp.objects.get(entry=entry)
        self.assertEqual(follow_up.status, RollCallAbsenceFollowUp.Status.PENDING)
        self.assertEqual(follow_up.reported_by, self.user_hm)
        self.assertFalse(DisciplinaryRecord.objects.filter(roll_call_entry=entry).exists())
        self.assertContains(resp, 'Absence Follow-Up Queue')
        self.assertContains(resp, 'Open session to review')

    def test_absence_follow_up_confirmation_creates_audited_conduct_record(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        entry = session.entries.get(student=self.boarder1)
        self.client.login(username='hm_volta', password='password123')
        page_response = self.client.get(reverse('core:roll_call_sessions'))
        self.assertContains(
            page_response,
            'document.body.addEventListener("showToast"',
        )

        response = self.client.post(
            f'/operations/roll-call/entry/{entry.id}/',
            {'status': RollCallEntry.Status.ABSENT},
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('showToast', response['HX-Trigger'])
        self.assertIn('follow-up', response['HX-Trigger'])
        follow_up = RollCallAbsenceFollowUp.objects.get(entry=entry)
        self.assertEqual(follow_up.status, RollCallAbsenceFollowUp.Status.PENDING)
        self.assertFalse(DisciplinaryRecord.objects.filter(roll_call_entry=entry).exists())

        duplicate_response = self.client.post(
            f'/operations/roll-call/entry/{entry.id}/',
            {'status': RollCallEntry.Status.ABSENT},
            HTTP_HX_REQUEST='true',
        )
        self.assertIn('already recorded for follow-up', duplicate_response['HX-Trigger'])
        self.assertEqual(RollCallAbsenceFollowUp.objects.filter(entry=entry).count(), 1)

        response = self.client.post(
            reverse('core:roll_call_resolve_absence_follow_up', args=[follow_up.id]),
            {
                'resolution': RollCallAbsenceFollowUp.Status.CONFIRMED,
                'resolution_note': 'House staff checked and confirmed the student was absent.',
            },
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn('conduct incident was recorded', response['HX-Trigger'])
        follow_up.refresh_from_db()
        self.assertEqual(follow_up.status, RollCallAbsenceFollowUp.Status.CONFIRMED)
        self.assertEqual(follow_up.resolved_by, self.user_hm)
        record = DisciplinaryRecord.objects.get(roll_call_follow_up=follow_up)
        self.assertIsNone(record.corrected_at)
        self.assertIn('confirmed after review', record.description.lower())

        response = self.client.post(
            f'/operations/roll-call/entry/{entry.id}/',
            {'status': RollCallEntry.Status.PRESENT},
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('showToast', response['HX-Trigger'])
        self.assertIn('corrected', response['HX-Trigger'])
        entry.refresh_from_db()
        follow_up.refresh_from_db()
        record.refresh_from_db()
        self.assertEqual(entry.status, RollCallEntry.Status.PRESENT)
        self.assertEqual(follow_up.status, RollCallAbsenceFollowUp.Status.CONFIRMED)
        self.assertIsNotNone(record.corrected_at)
        self.assertEqual(record.corrected_by, self.user_hm)
        self.assertIn('changed from Absent to Present', record.correction_note)
        conduct_response = self.client.get('/conduct/')
        self.assertEqual(conduct_response.status_code, 200)
        self.assertContains(conduct_response, 'Corrected')
        self.assertContains(conduct_response, 'retained for audit')

        report = get_detailed_report("discipline", house_id=self.house.pk)
        self.assertIn(("Active incidents", 0), report["summary"])
        self.assertIn(("Corrected records", 1), report["summary"])
        self.assertTrue(report["rows"][0][-1].startswith("Corrected "))

    def test_authorized_absence_and_recording_error_resolutions_do_not_create_incidents(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        entry = session.entries.get(student=self.boarder1)
        self.client.force_login(self.user_hm)
        self.client.post(
            f'/operations/roll-call/entry/{entry.id}/',
            {'status': RollCallEntry.Status.ABSENT},
        )
        follow_up = RollCallAbsenceFollowUp.objects.get(entry=entry)

        response = self.client.post(
            reverse('core:roll_call_resolve_absence_follow_up', args=[follow_up.id]),
            {
                'resolution': RollCallAbsenceFollowUp.Status.EXCUSED,
                'resulting_status': RollCallEntry.Status.OFFICIAL_ACTIVITY,
                'resolution_note': 'Assigned to an authorized school activity.',
            },
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        follow_up.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(follow_up.status, RollCallAbsenceFollowUp.Status.EXCUSED)
        self.assertEqual(entry.status, RollCallEntry.Status.OFFICIAL_ACTIVITY)
        self.assertEqual(entry.remarks, follow_up.resolution_note)
        self.assertFalse(DisciplinaryRecord.objects.filter(roll_call_entry=entry).exists())

        other_entry = session.entries.get(student=self.boarder2)
        update_entry_status(
            other_entry,
            RollCallEntry.Status.ABSENT,
            updated_by=self.user_hm,
        )
        correction = other_entry.absence_follow_ups.get()
        update_entry_status(
            entry,
            RollCallEntry.Status.PRESENT,
            remarks=entry.remarks,
            updated_by=self.user_hm,
        )
        complete_roll_call_session(session)
        response = self.client.post(
            reverse('core:roll_call_resolve_absence_follow_up', args=[correction.id]),
            {
                'resolution': RollCallAbsenceFollowUp.Status.CORRECTED,
                'resulting_status': RollCallEntry.Status.PRESENT,
                'resolution_note': 'A second staff count confirmed the student was present.',
            },
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(response.status_code, 200)
        correction.refresh_from_db()
        other_entry.refresh_from_db()
        self.assertEqual(correction.status, RollCallAbsenceFollowUp.Status.CORRECTED)
        self.assertEqual(other_entry.status, RollCallEntry.Status.PRESENT)
        self.assertFalse(
            DisciplinaryRecord.objects.filter(roll_call_entry=other_entry).exists()
        )

    def test_changing_pending_absence_to_authorized_status_resolves_as_excused(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        entry = session.entries.get(student=self.boarder1)
        self.client.force_login(self.user_hm)
        self.client.post(
            f'/operations/roll-call/entry/{entry.id}/',
            {'status': RollCallEntry.Status.ABSENT},
        )
        follow_up = RollCallAbsenceFollowUp.objects.get(entry=entry)

        response = self.client.post(
            f'/operations/roll-call/entry/{entry.id}/',
            {
                'status': RollCallEntry.Status.INFIRMARY,
            },
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('authorized exception', response['HX-Trigger'])
        follow_up.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(follow_up.status, RollCallAbsenceFollowUp.Status.EXCUSED)
        self.assertEqual(follow_up.resolution_note, '')
        self.assertEqual(entry.status, RollCallEntry.Status.INFIRMARY)
        self.assertFalse(
            DisciplinaryRecord.objects.filter(roll_call_entry=entry).exists()
        )

    def test_excused_roll_call_statuses_do_not_require_a_staff_note(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        entry = session.entries.get(student=self.boarder1)
        self.client.force_login(self.user_hm)

        for status in (
            RollCallEntry.Status.INFIRMARY,
            RollCallEntry.Status.ON_EXEAT,
            RollCallEntry.Status.OFFICIAL_ACTIVITY,
        ):
            response = self.client.post(
                f'/operations/roll-call/entry/{entry.id}/',
                {'status': status},
                HTTP_HX_REQUEST='true',
            )
            self.assertEqual(response.status_code, 200)
            entry.refresh_from_db()
            self.assertEqual(entry.status, status)
            self.assertEqual(entry.remarks, '')

    def test_roll_call_roster_shows_identity_and_status_in_two_columns(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        self.client.force_login(self.user_hm)

        response = self.client.get(
            reverse('core:roll_call_sessions'),
            {'session_id': session.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Student / Dorm / Class')
        self.assertContains(response, 'Attendance Status')
        self.assertContains(response, 'col-span-7')
        self.assertContains(response, 'col-span-5')
        self.assertNotContains(response, 'Reason for exeat')
        self.assertNotContains(response, 'entry-remarks-')

    def test_legacy_flag_action_only_queues_absence_for_review(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        entry = session.entries.get(student=self.boarder1)
        self.client.login(username='hm_volta', password='password123')

        response = self.client.post(
            f'/operations/roll-call/entry/{entry.id}/',
            {
                'status': RollCallEntry.Status.ABSENT,
                'flag_infraction': 'true',
            },
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('showToast', response['HX-Trigger'])
        self.assertIn('follow-up', response['HX-Trigger'])
        entry.refresh_from_db()
        self.assertEqual(entry.status, RollCallEntry.Status.ABSENT)
        self.assertTrue(
            entry.absence_follow_ups.filter(
                status=RollCallAbsenceFollowUp.Status.PENDING
            ).exists()
        )
        self.assertContains(response, 'Absent')
        self.assertFalse(DisciplinaryRecord.objects.filter(roll_call_entry=entry).exists())

    def test_roll_call_htmx_update_refreshes_metrics_and_finalize_state(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        entries = list(session.entries.order_by('student_id'))
        self.client.login(username='hm_volta', password='password123')

        response = self.client.post(
            f'/operations/roll-call/entry/{entries[0].id}/',
            {'status': RollCallEntry.Status.PRESENT},
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'hx-swap-oob="outerHTML"')
        self.assertContains(response, 'disabled title="Mark all students before finalizing"')
        self.assertContains(response, 'col-span-5 min-w-0 w-full')
        self.assertContains(response, 'grid-cols-12')
        self.assertContains(response, 'break-words')
        self.assertContains(response, 'name="status"')
        self.assertNotContains(response, 'entry-remarks-')

        response = self.client.post(
            f'/operations/roll-call/entry/{entries[1].id}/',
            {'status': RollCallEntry.Status.ABSENT},
            HTTP_HX_REQUEST='true',
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'disabled title="Mark all students before finalizing"')
        self.assertContains(response, 'Absence follow-up pending')

    def test_roll_call_mark_all_present_bulk_preserves_unmarked_and_exeat(self):
        """Bulk correction does not convert unverified or excused entries to present."""
        session = create_roll_call_session(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
            conducted_by=self.hm
        )
        entry1 = session.entries.get(student=self.boarder1)
        entry2 = session.entries.get(student=self.boarder2)
        student3 = Student.objects.create(
            admission_no='SHS-2026-B3',
            index_number='IDX-2026-B3',
            full_name='Third Boarder',
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dorm,
            residency=Student.ResidencyStatus.BOARDING,
        )
        entry3 = RollCallEntry.objects.create(
            session=session,
            student=student3,
            status=RollCallEntry.Status.UNMARKED,
        )

        update_entry_status(
            entry1,
            RollCallEntry.Status.ABSENT,
            updated_by=self.user_hm,
        )
        follow_up = entry1.absence_follow_ups.get()
        infraction = resolve_absence_follow_up(
            follow_up,
            RollCallAbsenceFollowUp.Status.CONFIRMED,
            self.user_hm,
            'House staff verified the student was unexcused.',
            logged_by=self.hm,
        )
        self.assertIsNotNone(infraction)
        entry2.status = RollCallEntry.Status.ON_EXEAT
        entry2.save()

        self.client.login(username='hm_volta', password='password123')
        resp = self.client.post(
            f'/operations/roll-call/{session.id}/mark-all-present/',
            follow=True
        )
        self.assertEqual(resp.status_code, 200)

        entry1.refresh_from_db()
        entry2.refresh_from_db()
        entry3.refresh_from_db()
        infraction.refresh_from_db()
        self.assertEqual(entry1.status, RollCallEntry.Status.PRESENT)
        self.assertEqual(entry2.status, RollCallEntry.Status.ON_EXEAT)
        self.assertEqual(entry3.status, RollCallEntry.Status.UNMARKED)
        self.assertIsNotNone(infraction.corrected_at)
        self.assertEqual(infraction.corrected_by, self.user_hm)

    def test_attendance_report_counts_unmarked_entries(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        session.entries.filter(student=self.boarder1).update(
            status=RollCallEntry.Status.PRESENT
        )

        report = get_detailed_report(
            "attendance",
            date_from=session.date,
            date_to=session.date,
        )
        row = report["rows"][0]

        self.assertEqual(report["headers"][10], "Unmarked")
        self.assertEqual(row[5], 2)
        self.assertEqual(row[6], 1)
        self.assertEqual(row[10], 1)

    def test_roll_call_cannot_finalize_with_unmarked_entries(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )

        with self.assertRaisesMessage(ValueError, "2 student(s) are still unmarked"):
            complete_roll_call_session(session)

        session.refresh_from_db()
        self.assertEqual(session.status, RollCallSession.Status.IN_PROGRESS)

    def test_roll_call_finalize_view_blocks_unmarked_and_get_requests(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        self.client.login(username='hm_volta', password='password123')

        get_response = self.client.get(f'/operations/roll-call/{session.id}/complete/')
        self.assertEqual(get_response.status_code, 405)

        response = self.client.post(
            f'/operations/roll-call/{session.id}/complete/',
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.status, RollCallSession.Status.IN_PROGRESS)
        self.assertContains(response, '2 student(s) are still unmarked')

    def test_roll_call_finalizes_after_all_entries_are_marked(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        session.entries.update(status=RollCallEntry.Status.PRESENT)

        completed_session = complete_roll_call_session(session)

        self.assertEqual(completed_session.status, RollCallSession.Status.COMPLETED)

    def test_gate_terminal_action_departure_and_return(self):
        """Gate Officer can log departure and arrival return through terminal action."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder1,
            academic_term=self.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=2),
            destination='Post Office',
            reason='Package',
            status=Exeat.Status.APPROVED,
            approved_by=self.hm
        )

        self.client.login(username='gate_officer_bob', password='password123')

        # 1. Log departure
        resp_dep = self.client.post(f'/operations/gate/{exeat.id}/departure/', follow=True)
        self.assertEqual(resp_dep.status_code, 200)
        movement = ExeatMovement.objects.get(exeat=exeat)
        self.assertIsNotNone(movement.actual_departure)
        self.assertTrue(movement.is_currently_out)

        # 2. Log return
        resp_ret = self.client.post(f'/operations/gate/{exeat.id}/return/', follow=True)
        self.assertEqual(resp_ret.status_code, 200)
        movement.refresh_from_db()
        self.assertIsNotNone(movement.actual_return)
        self.assertEqual(movement.state_display, "Returned")

    def test_gate_departures_reconcile_only_unmarked_entries_and_surface_conflicts(self):
        session = create_roll_call_session(
            house=self.house,
            conducted_by=self.hm,
        )
        marked_entry = session.entries.get(student=self.boarder1)
        unmarked_entry = session.entries.get(student=self.boarder2)
        marked_entry.status = RollCallEntry.Status.PRESENT
        marked_entry.save()

        now = timezone.now()
        exeats = []
        for student in (self.boarder1, self.boarder2):
            exeats.append(
                Exeat.objects.create(
                    student=student,
                    academic_term=self.term,
                    exeat_type=Exeat.ExeatType.TOWN,
                    expected_departure=now - timezone.timedelta(minutes=1),
                    expected_return=now + timezone.timedelta(hours=2),
                    destination='Town',
                    reason='Authorized visit',
                    status=Exeat.Status.APPROVED,
                    approved_by=self.hm,
                )
            )

        self.client.force_login(self.user_gate)
        first_response = self.client.post(
            f'/operations/gate/{exeats[0].id}/departure/',
            follow=True,
        )
        self.assertEqual(first_response.status_code, 200)
        marked_entry.refresh_from_db()
        self.assertEqual(marked_entry.status, RollCallEntry.Status.PRESENT)
        self.assertIsNotNone(marked_entry.gate_conflict_at)
        self.assertIsNone(marked_entry.gate_conflict_reviewed_at)

        second_response = self.client.post(
            f'/operations/gate/{exeats[1].id}/departure/',
            follow=True,
        )
        self.assertEqual(second_response.status_code, 200)
        unmarked_entry.refresh_from_db()
        self.assertEqual(unmarked_entry.status, RollCallEntry.Status.ON_EXEAT)
        self.assertEqual(unmarked_entry.status_source, RollCallEntry.StatusSource.GATE)

        with self.assertRaisesMessage(ValueError, 'gate/roll-call discrepancy'):
            complete_roll_call_session(session)

        self.client.force_login(self.user_hm)
        review_response = self.client.post(
            f'/operations/roll-call/entry/{marked_entry.id}/',
            {
                'status': RollCallEntry.Status.PRESENT,
                'remarks': 'Staff confirmed the student attended before departing.',
            },
            HTTP_HX_REQUEST='true',
        )
        self.assertEqual(review_response.status_code, 200)
        marked_entry.refresh_from_db()
        self.assertIsNotNone(marked_entry.gate_conflict_reviewed_at)
        self.assertEqual(marked_entry.gate_conflict_reviewed_by, self.user_hm)
        complete_roll_call_session(session)

        self.client.force_login(self.user_gate)
        self.client.post(f'/operations/gate/{exeats[1].id}/return/', follow=True)
        unmarked_entry.refresh_from_db()
        self.assertEqual(unmarked_entry.status, RollCallEntry.Status.ON_EXEAT)

    def test_gate_verify_pass_lookup(self):
        """Gate terminal barcode/pass lookup locates approved exeat."""
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=self.boarder1,
            academic_term=self.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=2),
            destination='Supermarket',
            reason='Provisions',
            status=Exeat.Status.APPROVED,
            approved_by=self.hm
        )

        self.client.login(username='gate_officer_bob', password='password123')
        resp = self.client.get(f'/operations/gate/verify/?q={exeat.exeat_number}')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.boarder1.full_name.encode(), resp.content)
        self.assertIn(exeat.exeat_number.encode(), resp.content)


class HouseScopedAccessTests(TestCase):
    def setUp(self):
        today = timezone.now().date()
        self.year = AcademicYear.objects.create(
            start_date=today,
            end_date=today + timezone.timedelta(days=300),
            is_current=True,
        )
        self.term = AcademicTerm.objects.create(
            academic_year=self.year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=today,
            end_date=today + timezone.timedelta(days=90),
            is_current=True,
        )
        self.programme = Programme.objects.create(name='Science', code='SCI')
        self.house_a = House.objects.create(name='Amber House')
        self.dorm_a = Dormitory.objects.create(
            house=self.house_a,
            name='Amber Block',
            gender=Dormitory.GenderBlock.MALE,
            capacity=10,
        )
        self.house_b = House.objects.create(name='Cobalt House')
        self.dorm_b = Dormitory.objects.create(
            house=self.house_b,
            name='Cobalt Block',
            gender=Dormitory.GenderBlock.MALE,
            capacity=10,
        )

        self.hm_user = User.objects.create_user(username='amber_hm', password='password123')
        self.hm_a = HouseMaster.objects.create(
            user=self.hm_user,
            full_name='Amber Housemaster',
            staff_id='SCOPE-HM-A',
            phone_number='+233200000011',
            house=self.house_a,
        )
        self.hm_b = HouseMaster.objects.create(
            full_name='Cobalt Housemaster',
            staff_id='SCOPE-HM-B',
            phone_number='+233200000012',
            house=self.house_b,
        )

        self.gate_user = User.objects.create_user(username='amber_gate', password='password123')
        self.gate_hm = HouseMaster.objects.create(
            user=self.gate_user,
            full_name='Amber Gate Officer',
            staff_id='SCOPE-GATE-A',
            phone_number='+233200000013',
            house=self.house_a,
        )
        gate_profile = get_user_profile(self.gate_user)
        gate_profile.role = StaffRole.GATE_OFFICER
        gate_profile.save(update_fields=['role'])

        self.tutor_user = User.objects.create_user(username='amber_tutor', password='password123')
        self.tutor_hm = HouseMaster.objects.create(
            user=self.tutor_user,
            full_name='Amber House Tutor',
            staff_id='SCOPE-TUTOR-A',
            phone_number='+233200000014',
            house=self.house_a,
        )
        tutor_profile = get_user_profile(self.tutor_user)
        tutor_profile.role = StaffRole.HOUSE_TUTOR
        tutor_profile.save(update_fields=['role'])

        self.student_a = self.create_boarder(
            'SCOPE-A', 'Amber Student', self.house_a, self.dorm_a
        )
        self.student_b = self.create_boarder(
            'SCOPE-B', 'Cobalt Student', self.house_b, self.dorm_b
        )
        now = timezone.now()
        self.exeat_a = self.create_exeat('SCOPE-EX-A', self.student_a, now)
        self.exeat_b = self.create_exeat('SCOPE-EX-B', self.student_b, now)
        self.session_a = create_roll_call_session(
            house=self.house_a,
            conducted_by=self.hm_a,
        )
        self.session_b = create_roll_call_session(
            house=self.house_b,
            conducted_by=self.hm_b,
        )

    def create_boarder(self, suffix, name, house, dormitory):
        return Student.objects.create(
            admission_no=suffix,
            index_number=f'IDX-{suffix}',
            full_name=name,
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=house,
            dormitory=dormitory,
            residency=Student.ResidencyStatus.BOARDING,
        )

    def create_exeat(self, number, student, now):
        return Exeat.objects.create(
            exeat_number=number,
            student=student,
            academic_term=self.term,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now,
            expected_return=now + timezone.timedelta(hours=3),
            destination='Town',
            reason='House-scoped test',
            status=Exeat.Status.PENDING,
        )

    def test_housemaster_exeat_list_search_and_object_actions_are_scoped(self):
        self.client.force_login(self.hm_user)

        response = self.client.get('/exeats/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'SCOPE-EX-A')
        self.assertNotContains(response, 'SCOPE-EX-B')
        self.assertEqual(response.context['total_count'], 1)

        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_boarders'], 1)
        self.assertEqual(response.context['total_capacity'], 10)
        self.assertNotContains(response, 'Cobalt Student')

        response = self.client.get('/exeats/search-students/?q=Student')
        self.assertContains(response, 'Amber Student')
        self.assertNotContains(response, 'Cobalt Student')

        response = self.client.get(
            f'/exeats/new/?student_id={self.student_b.pk}'
        )
        self.assertIsNone(response.context['selected_student'])
        before_create = Exeat.objects.count()
        response = self.client.post('/exeats/new/', {
            'student': str(self.student_b.pk),
            'exeat_type': Exeat.ExeatType.TOWN,
            'expected_departure': (timezone.now() + timezone.timedelta(hours=1)).strftime(
                '%Y-%m-%dT%H:%M'
            ),
            'expected_return': (timezone.now() + timezone.timedelta(hours=3)).strftime(
                '%Y-%m-%dT%H:%M'
            ),
            'destination': 'Town',
            'reason': 'Out-of-house application attempt',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Exeat.objects.count(), before_create)

        response = self.client.get(f'/exeats/{self.exeat_b.pk}/')
        self.assertEqual(response.status_code, 404)
        response = self.client.post(f'/exeats/{self.exeat_b.pk}/action/cancel/')
        self.assertEqual(response.status_code, 404)
        self.exeat_b.refresh_from_db()
        self.assertEqual(self.exeat_b.status, Exeat.Status.PENDING)

    def test_housemaster_cannot_read_or_change_another_house_roll_call(self):
        self.client.force_login(self.hm_user)

        response = self.client.get(
            f'/operations/roll-call/?session_id={self.session_b.pk}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [session.pk for session in response.context['recent_sessions']],
            [self.session_a.pk],
        )
        self.assertIsNone(response.context['current_session'])

        response = self.client.post(
            f'/operations/roll-call/{self.session_b.pk}/complete/'
        )
        self.assertEqual(response.status_code, 404)
        self.session_b.refresh_from_db()
        self.assertEqual(self.session_b.status, RollCallSession.Status.IN_PROGRESS)

        response = self.client.post(
            '/operations/roll-call/new/',
            {'house': str(self.house_b.pk), 'session_type': RollCallSession.SessionType.EVENING},
        )
        self.assertEqual(response.status_code, 404)

    def test_gate_operations_cover_all_houses(self):
        now = timezone.now()
        self.exeat_a.status = Exeat.Status.APPROVED
        self.exeat_a.approved_by = self.hm_a
        self.exeat_a.expected_departure = now - timezone.timedelta(minutes=1)
        self.exeat_a.save()
        self.exeat_b.status = Exeat.Status.APPROVED
        self.exeat_b.approved_by = self.hm_b
        self.exeat_b.expected_departure = now - timezone.timedelta(minutes=1)
        self.exeat_b.save()
        self.client.force_login(self.gate_user)

        response = self.client.get(
            f'/operations/gate/verify/?q={self.exeat_b.exeat_number}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Cobalt Student')

        response = self.client.post(
            f'/operations/gate/{self.exeat_b.pk}/departure/'
        )
        self.assertEqual(response.status_code, 302)
        movement = ExeatMovement.objects.get(exeat=self.exeat_b)
        self.assertIsNotNone(movement.actual_departure)

        response = self.client.post(
            f'/operations/gate/{self.exeat_b.pk}/return/'
        )
        self.assertEqual(response.status_code, 302)
        movement.refresh_from_db()
        self.assertIsNotNone(movement.actual_return)

    def test_unassigned_gate_officer_gets_school_wide_gate_landing_only(self):
        now = timezone.now()
        for exeat, housemaster in (
            (self.exeat_a, self.hm_a),
            (self.exeat_b, self.hm_b),
        ):
            exeat.status = Exeat.Status.APPROVED
            exeat.approved_by = housemaster
            exeat.expected_departure = now - timezone.timedelta(minutes=1)
            exeat.save()

        gate_user = User.objects.create_user(
            username='central_gate',
            password='password123',
        )
        profile = get_user_profile(gate_user)
        profile.role = StaffRole.GATE_OFFICER
        profile.save(update_fields=['role'])
        self.client.force_login(gate_user)

        dashboard_response = self.client.get('/')
        self.assertRedirects(
            dashboard_response,
            '/operations/gate/',
            fetch_redirect_response=False,
        )
        gate_response = self.client.get('/operations/gate/')
        self.assertEqual(gate_response.status_code, 200)
        self.assertEqual(gate_response.context['awaiting_departure_count'], 2)
        self.assertContains(gate_response, 'Amber Student')
        self.assertContains(gate_response, 'Cobalt Student')
        self.assertTrue(gate_response.context['can_access_gate'])
        self.assertFalse(gate_response.context['can_view_houses'])

        verify_response = self.client.get(
            f'/operations/gate/verify/?q={self.exeat_b.exeat_number}'
        )
        self.assertContains(verify_response, 'Cobalt Student')

        response = self.client.post(
            f'/operations/gate/{self.exeat_b.pk}/departure/'
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            ExeatMovement.objects.filter(
                exeat=self.exeat_b,
                actual_departure__isnull=False,
            ).exists()
        )

        response = self.client.get('/operations/gate/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_boarders'], 2)
        self.assertNotIn('houses', response.context)

    def test_report_filter_form_defaults_and_submits_selected_report(self):
        self.client.force_login(self.hm_user)

        response = self.client.get('/reports/')
        self.assertEqual(response.context['report_filters']['report'].value(), 'housing')
        self.assertEqual(response.context['report']['title'], 'Boarding Occupancy & Placement')
        self.assertContains(response, 'action="/reports/"')
        self.assertContains(response, 'method="get"')
        self.assertContains(response, 'name="report"')

        response = self.client.get('/reports/', {'report': 'attendance'})
        self.assertEqual(response.context['report_filters']['report'].value(), 'attendance')
        self.assertEqual(response.context['report']['title'], 'Roll-call Attendance & Follow-up')

    def test_reports_are_denied_to_gate_officer_and_tutor(self):
        self.client.force_login(self.gate_user)
        self.assertEqual(self.client.get('/reports/').status_code, 302)
        self.assertEqual(self.client.get('/reports/print/').status_code, 302)
        self.assertRedirects(
            self.client.get('/'),
            '/operations/gate/',
            fetch_redirect_response=False,
        )

        self.client.force_login(self.tutor_user)
        self.assertEqual(self.client.get('/reports/').status_code, 302)
        self.assertEqual(self.client.get('/reports/print/').status_code, 302)
        self.assertFalse(self.client.get('/').context['can_view_reports'])

    def test_housemaster_reports_and_printable_output_stay_in_assigned_house(self):
        for student, suffix in ((self.student_a, 'A'), (self.student_b, 'B')):
            DisciplinaryRecord.objects.create(
                student=student,
                academic_term=self.term,
                offense=f'Incident {suffix}',
                description='House-scoped report test',
                severity=DisciplinaryRecord.Severity.MINOR,
                action_taken='Follow-up',
            )
        DisciplinaryRecord.objects.create(
            student=self.student_a,
            academic_term=self.term,
            offense='Incident A',
            description='Second occurrence',
            severity=DisciplinaryRecord.Severity.MAJOR,
            action_taken='Committee review',
        )

        self.client.force_login(self.hm_user)
        response = self.client.get(
            f'/reports/?report=discipline&term={self.term.pk}&house={self.house_b.pk}'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['metrics']['total_boarders'], 1)
        self.assertTrue(self.client.get('/').context['can_view_reports'])
        self.assertContains(response, 'Amber House')
        self.assertNotContains(response, 'Cobalt House')
        self.assertContains(response, 'Incident A')
        self.assertNotContains(response, 'Incident B')
        self.assertEqual(response.context['report']['record_count'], 2)
        self.assertIn(
            ('Students with 2+ recorded incidents', 1),
            response.context['report']['summary'],
        )
        self.assertEqual(
            response.context['report']['sections'][1]['rows'][0][0],
            'Amber Student',
        )
        self.assertEqual(
            response.context['report']['sections'][0]['rows'][0][0],
            f'{timezone.now().year}-{timezone.now().month:02d}',
        )
        self.assertContains(response, 'The system records actions taken but not intervention outcomes')

        housing_response = self.client.get('/reports/?report=housing')
        self.assertContains(housing_response, 'Amber Block')
        self.assertNotContains(housing_response, 'Cobalt Block')
        housing_report = housing_response.context['report']
        self.assertIn(('Boarders', 1), housing_report['summary'])
        self.assertIn(('Configured beds', 10), housing_report['summary'])
        self.assertIn(('Occupied capacity', '10.0%'), housing_report['summary'])

        self.dorm_a.capacity = 0
        self.dorm_a.save(update_fields=['capacity'])
        self.create_boarder(
            'SCOPE-A-OVER', 'Additional Amber Student', self.house_a, self.dorm_a
        )
        over_capacity_response = self.client.get('/reports/?report=housing')
        self.assertIn(
            ('Over-capacity dormitories', 1),
            over_capacity_response.context['report']['summary'],
        )
        self.assertIn(
            'Over capacity by 2',
            [row[-1] for row in over_capacity_response.context['report']['rows']],
        )

        report_day = timezone.now().date().isoformat()
        attendance_response = self.client.get(
            f'/reports/?report=attendance&date_from={report_day}&date_to={report_day}'
        )
        self.assertContains(attendance_response, 'Amber House')
        self.assertNotContains(attendance_response, 'Cobalt House')
        self.assertEqual(attendance_response.context['report']['record_count'], 1)
        additional_session = create_roll_call_session(
            house=self.house_a,
            conducted_by=self.hm_a,
        )
        self.session_a.entries.filter(student=self.student_a).update(
            status=RollCallEntry.Status.ABSENT
        )
        additional_session.entries.filter(student=self.student_a).update(
            status=RollCallEntry.Status.ABSENT
        )
        attendance_response = self.client.get(
            f'/reports/?report=attendance&date_from={report_day}&date_to={report_day}'
        )
        self.assertIn(
            ('Students with 2+ absences', 1),
            attendance_response.context['report']['summary'],
        )
        self.assertEqual(
            attendance_response.context['report']['sections'][0]['rows'][0][0],
            'Amber Student',
        )

        exeat_response = self.client.get(
            f'/reports/?report=exeats&date_from={report_day}&date_to={report_day}'
        )
        self.assertContains(exeat_response, 'SCOPE-EX-A')
        self.assertNotContains(exeat_response, 'SCOPE-EX-B')
        self.assertEqual(exeat_response.context['report']['record_count'], 1)

        print_response = self.client.get(
            f'/reports/print/?report=discipline&term={self.term.pk}'
        )
        self.assertEqual(print_response.status_code, 200)
        self.assertContains(print_response, 'Incident A')
        self.assertNotContains(print_response, 'Cobalt House')
        self.assertNotContains(print_response, 'COLLAPSIBLE UTILITY SIDEBAR')
        self.assertContains(print_response, 'Students with multiple recorded incidents')
        self.assertContains(print_response, 'actions taken but not intervention outcomes')
        self.assertTemplateUsed(print_response, 'core/reports/print.html')

    def test_admin_and_senior_housemaster_reports_are_school_wide(self):
        DisciplinaryRecord.objects.create(
            student=self.student_a,
            academic_term=self.term,
            offense='Amber report incident',
            severity=DisciplinaryRecord.Severity.MINOR,
            action_taken='Follow-up',
        )
        DisciplinaryRecord.objects.create(
            student=self.student_b,
            academic_term=self.term,
            offense='Cobalt report incident',
            severity=DisciplinaryRecord.Severity.MAJOR,
            action_taken='Committee review',
        )
        senior = User.objects.create_user(username='school_senior', password='password123')
        senior_profile = get_user_profile(senior)
        senior_profile.role = StaffRole.SENIOR_HOUSEMASTER
        senior_profile.save(update_fields=['role'])
        admin = User.objects.create_superuser(
            username='reports_admin',
            email='reports-admin@example.com',
            password='password123',
        )

        for user in (senior, admin):
            self.client.force_login(user)
            response = self.client.get('/reports/?report=discipline&term=' + str(self.term.pk))
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['metrics']['total_boarders'], 2)
            self.assertContains(response, 'Amber report incident')
            self.assertContains(response, 'Cobalt report incident')
            self.assertContains(response, 'Amber House')
            self.assertContains(response, 'Cobalt House')
            print_response = self.client.get(
                f'/reports/print/?report=discipline&term={self.term.pk}'
            )
            self.assertContains(print_response, 'Cobalt report incident')

    def test_invalid_report_date_range_is_reported_and_cannot_be_printed(self):
        self.client.force_login(self.hm_user)
        response = self.client.get(
            '/reports/?report=attendance&date_from=2026-10-10&date_to=2026-10-01'
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['report_filters_valid'])
        self.assertContains(response, 'End date must be on or after the start date.')
        self.assertContains(response, 'The selected report filters are invalid.')
        self.assertContains(response, 'aria-disabled="true"')

        print_response = self.client.get(
            '/reports/print/?report=attendance&date_from=2026-10-10&date_to=2026-10-01'
        )
        self.assertEqual(print_response.status_code, 400)

    def test_house_tutor_keeps_house_scope_without_gaining_approval_authority(self):
        self.client.force_login(self.gate_user)
        response = self.client.post(
            f'/exeats/{self.exeat_a.pk}/action/approve/'
        )
        self.assertEqual(response.status_code, 302)
        self.exeat_a.refresh_from_db()
        self.assertEqual(self.exeat_a.status, Exeat.Status.PENDING)

        self.client.force_login(self.tutor_user)
        response = self.client.post(
            f'/exeats/{self.exeat_a.pk}/action/approve/'
        )
        self.assertEqual(response.status_code, 302)
        self.exeat_a.refresh_from_db()
        self.assertEqual(self.exeat_a.status, Exeat.Status.PENDING)

        self.assertEqual(get_user_profile(self.gate_user).role, StaffRole.GATE_OFFICER)
        self.assertEqual(get_user_profile(self.tutor_user).role, StaffRole.HOUSE_TUTOR)

    def test_unassigned_gate_officer_has_gate_access_but_not_house_workspaces(self):
        user = User.objects.create_user(username='unassigned_gate', password='password123')
        profile = get_user_profile(user)
        profile.role = StaffRole.GATE_OFFICER
        profile.save(update_fields=['role'])
        self.client.force_login(user)

        gate_response = self.client.get('/operations/gate/')
        self.assertEqual(gate_response.status_code, 200)
        self.assertTrue(gate_response.context['can_access_gate'])
        self.assertFalse(gate_response.context['can_view_houses'])
        self.assertFalse(gate_response.context['can_view_exeats'])
        self.assertEqual(self.client.get('/reports/').status_code, 302)
        self.assertEqual(self.client.get('/reports/print/').status_code, 302)

        unassigned_housemaster = User.objects.create_user(
            username='unassigned_housemaster',
            password='password123',
        )
        housemaster_profile = get_user_profile(unassigned_housemaster)
        housemaster_profile.role = StaffRole.HOUSEMASTER
        housemaster_profile.save(update_fields=['role'])
        self.client.force_login(unassigned_housemaster)
        self.assertEqual(self.client.get('/reports/').status_code, 403)
        self.assertEqual(self.client.get('/reports/print/').status_code, 403)
