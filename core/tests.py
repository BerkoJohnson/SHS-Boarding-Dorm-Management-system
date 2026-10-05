from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from core.models import (
    Student,
    Programme,
    House,
    Dormitory,
    Exeat,
    ExeatMovement,
    DisciplinaryRecord,
    AcademicYear,
    AcademicTerm,
    HouseMaster,
    StaffRole,
    UserProfile,
    get_user_profile,
    RollCallSession,
    RollCallEntry,
)
from core.services.roll_call import (
    create_roll_call_session,
    update_entry_status,
    mark_all_present,
    convert_absence_to_disciplinary_record,
)

User = get_user_model()


class DashboardViewTests(TestCase):
    """Integration and unit tests for the enhanced executive dashboard and workspace grid."""

    def setUp(self):
        self.user = User.objects.create_user(username='test_housemaster', password='password123')
        
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
        self.house = House.objects.create(name='Red House', number=House.HouseNumber.ONE)
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
        self.assertIn(b'Student & Details', response.content)


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
        self.house = House.objects.create(name='Blue House', number=House.HouseNumber.TWO)
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name='Blue Block 1',
            gender=Dormitory.GenderBlock.MALE,
            capacity=100
        )
        self.housemaster = HouseMaster.objects.create(
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
        """POST /exeats/new/ with valid boarding student data creates Exeat & Movement."""
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
        self.assertRedirects(response, '/')

        # Verify record in database
        exeat = Exeat.objects.get(student=self.boarder)
        self.assertEqual(exeat.exeat_type, Exeat.ExeatType.MEDICAL)
        self.assertEqual(exeat.status, Exeat.Status.APPROVED)
        self.assertIsNotNone(exeat.movement)

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

        # 2. Log Gate Return
        ret_resp = self.client.post(f'/exeats/{exeat.pk}/action/return/')
        self.assertEqual(ret_resp.status_code, 302)
        exeat.refresh_from_db()
        self.assertIsNotNone(exeat.movement.actual_return)
        self.assertEqual(exeat.movement.state_display, "Returned")

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

        # Filter by status=Pending
        resp_pending = self.client.get('/exeats/?status=Pending')
        self.assertEqual(resp_pending.status_code, 200)
        self.assertIn(town_exeat.exeat_number.encode(), resp_pending.content)
        self.assertNotIn(medical_exeat.exeat_number.encode(), resp_pending.content)


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
        self.house = House.objects.create(name='Blue House', number=House.HouseNumber.TWO)
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name='Blue Block 1',
            gender=Dormitory.GenderBlock.MALE,
            capacity=100
        )
        self.housemaster = HouseMaster.objects.create(
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

        # HTMX search by student name
        resp_htmx = self.client.get('/conduct/?q=Kofi', HTTP_HX_REQUEST='true')
        self.assertEqual(resp_htmx.status_code, 200)
        self.assertIn(b'Kofi Badu', resp_htmx.content)

    def test_incident_create_view_get(self):
        """GET /conduct/new/ renders the incident recording form."""
        response = self.client.get(f'/conduct/new/?student_id={self.student.id}')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Record Student Conduct Infraction', response.content)
        self.assertIn(b'Kofi Badu', response.content)

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
        self.house = House.objects.create(name='Green House', number=House.HouseNumber.THREE)
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
        self.house1 = House.objects.create(name='Red House', number=House.HouseNumber.ONE)
        self.dorm1 = Dormitory.objects.create(
            house=self.house1,
            name='Block A',
            gender=Dormitory.GenderBlock.MALE,
            capacity=60
        )
        self.house2 = House.objects.create(name='Blue House', number=House.HouseNumber.TWO)
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
        self.assertIn(b'Permission denied', resp_h2.content)

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
        self.house = House.objects.create(name='Aggrey House', number=House.HouseNumber.ONE)
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
        self.house = House.objects.create(name='Volta House', number=House.HouseNumber.TWO)
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
        profile_gate = get_user_profile(self.user_gate)
        profile_gate.role = StaffRole.GATE_OFFICER
        profile_gate.save()

        self.user_regular = User.objects.create_user(username='unauthorized_user', password='password123')
        profile_reg = get_user_profile(self.user_regular)
        profile_reg.role = StaffRole.HOUSE_TUTOR
        profile_reg.save()

    def test_operations_hub_requires_login(self):
        """Unauthenticated requests are redirected to login."""
        resp = self.client.get('/operations/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

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
        self.assertIn('Active Gate Exeat', entry_boarder1.remarks)
        self.assertEqual(entry_boarder2.status, RollCallEntry.Status.PRESENT)

    def test_roll_call_update_entry_and_flag_infraction(self):
        """Housemaster can mark student absent and optionally log disciplinary infraction."""
        session = create_roll_call_session(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
            conducted_by=self.hm
        )
        entry = session.entries.get(student=self.boarder1)

        self.client.login(username='hm_volta', password='password123')
        resp = self.client.post(
            f'/operations/roll-call/entry/{entry.id}/',
            {'status': 'Absent', 'flag_infraction': 'true'},
            follow=True
        )
        self.assertEqual(resp.status_code, 200)

        entry.refresh_from_db()
        self.assertEqual(entry.status, RollCallEntry.Status.ABSENT)

        # Infraction created
        infraction = DisciplinaryRecord.objects.filter(student=self.boarder1).first()
        self.assertIsNotNone(infraction)
        self.assertIn('Evening Roll Call', infraction.offense)

    def test_roll_call_mark_all_present_bulk(self):
        """Bulk action resets unexcused absent entries to Present while preserving Exeat status."""
        session = create_roll_call_session(
            house=self.house,
            session_type=RollCallSession.SessionType.EVENING,
            conducted_by=self.hm
        )
        entry1 = session.entries.get(student=self.boarder1)
        entry2 = session.entries.get(student=self.boarder2)

        entry1.status = RollCallEntry.Status.ABSENT
        entry1.save()
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
        self.assertEqual(entry1.status, RollCallEntry.Status.PRESENT)
        self.assertEqual(entry2.status, RollCallEntry.Status.ON_EXEAT)

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





