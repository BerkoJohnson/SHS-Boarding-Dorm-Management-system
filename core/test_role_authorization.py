import io
import tempfile
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import (
    RequestFactory,
    TestCase,
    TransactionTestCase,
    override_settings,
)
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import (
    AcademicTerm,
    AcademicYear,
    ConfigurationAuditLog,
    DisciplinaryRecord,
    Dormitory,
    Exeat,
    House,
    HouseMaster,
    Programme,
    SMSConfiguration,
    StaffRole,
    Student,
    UserProfile,
)


User = get_user_model()


class PermissionDeniedPageTests(TestCase):
    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.entitlements.feature_enabled", return_value=False)
    def test_disabled_tenant_feature_explains_how_to_get_access(
        self, _feature_enabled
    ):
        admin_user = User.objects.create_superuser(
            username="feature-disabled-admin",
            password="password123",
        )
        self.client.force_login(admin_user)

        response = self.client.get(reverse("core:exeat_list"))

        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            "This school’s current plan does not include exeat gate",
            status_code=403,
        )
        self.assertContains(
            response,
            "contact a school administrator",
            status_code=403,
        )

    def test_other_permission_denials_have_a_clear_generic_explanation(self):
        from core.views import permission_denied_view

        response = permission_denied_view(
            RequestFactory().get("/operations/gate/"),
            PermissionDenied("Internal implementation detail"),
        )

        self.assertEqual(response.status_code, 403)
        self.assertContains(
            response,
            "Your account is not allowed to open this page",
            status_code=403,
        )
        self.assertNotContains(
            response,
            "Internal implementation detail",
            status_code=403,
        )


class InstitutionalRoleAuthorizationTests(TestCase):
    def test_new_accounts_are_unassigned_and_cannot_open_staff_views(self):
        user = User.objects.create_user(username="unassigned-staff", password="safe-password")
        self.assertEqual(user.profile.role, StaffRole.UNASSIGNED)

        self.client.force_login(user)
        response = self.client.get(reverse("core:student_registry"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("core:dashboard"))

    def test_housemaster_assignment_does_not_downgrade_an_administrator(self):
        user = User.objects.create_user(
            username="assigned-administrator",
            password="safe-password",
            is_staff=True,
        )
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": StaffRole.ADMIN},
        )
        house = House.objects.create(name="Administrator House")
        HouseMaster.objects.create(
            user=user,
            full_name="Administrator Housemaster",
            staff_id="ROLE-ADMIN-HM",
            phone_number="+233200000001",
            house=house,
        )

        user.profile.refresh_from_db()
        self.assertEqual(user.profile.role, StaffRole.ADMIN)

    def test_configuration_is_visible_to_admin_role_accounts(self):
        user = User.objects.create_user(
            username="configuration-admin",
            password="secure-test-password",
            is_staff=True,
        )
        UserProfile.objects.filter(user=user).update(role=StaffRole.ADMIN)
        self.client.force_login(user)

        response = self.client.get(reverse("core:configuration"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Configuration")
        self.assertContains(response, "Configuration Audit Log")
        audit_response = self.client.get(
            reverse("admin:core_configurationauditlog_changelist"),
        )
        self.assertEqual(audit_response.status_code, 200)

    def test_configuration_is_not_available_to_senior_housemasters(self):
        user = User.objects.create_user(
            username="configuration-senior",
            password="secure-test-password",
        )
        UserProfile.objects.filter(user=user).update(
            role=StaffRole.SENIOR_HOUSEMASTER,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("core:configuration"))

        self.assertRedirects(response, reverse("core:dashboard"))

    def test_active_term_change_records_actor_and_previous_term(self):
        admin_user = User.objects.create_user(
            username="term-configuration-admin",
            password="secure-test-password",
        )
        UserProfile.objects.filter(user=admin_user).update(role=StaffRole.ADMIN)
        academic_year = AcademicYear.objects.create(
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
        )
        previous_term = AcademicTerm.objects.create(
            academic_year=academic_year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 15),
            is_current=True,
        )
        next_term = AcademicTerm.objects.create(
            academic_year=academic_year,
            term=AcademicTerm.TermChoice.TERM_2,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=date(2027, 1, 10),
            end_date=date(2027, 4, 15),
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse("core:set_active_term", args=[next_term.pk]),
        )

        self.assertEqual(response.status_code, 204)
        audit_event = ConfigurationAuditLog.objects.get(
            event=ConfigurationAuditLog.Event.ACTIVE_TERM_CHANGED,
        )
        self.assertEqual(audit_event.actor, admin_user)
        self.assertEqual(
            audit_event.details["previous_terms"][0]["id"],
            str(previous_term.pk),
        )
        self.assertEqual(
            audit_event.details["new_term"]["id"],
            str(next_term.pk),
        )


class InstitutionalAdminAuthorizationTests(TestCase):
    def setUp(self):
        self.student_view_permission = Permission.objects.get(
            content_type__app_label="core",
            codename="view_student",
        )

    def test_staff_without_an_administrative_role_cannot_use_django_admin(self):
        user = User.objects.create_user(
            username="non-admin-staff",
            password="safe-password",
            is_staff=True,
        )
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": StaffRole.HOUSE_TUTOR},
        )
        user.user_permissions.add(self.student_view_permission)
        self.client.force_login(user)

        response = self.client.get(reverse("admin:core_student_changelist"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_staff_with_an_administrative_role_and_model_permission_can_use_admin(self):
        user = User.objects.create_user(
            username="admin-staff",
            password="safe-password",
            is_staff=True,
        )
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": StaffRole.ADMIN},
        )
        user.user_permissions.add(self.student_view_permission)
        self.client.force_login(user)

        response = self.client.get(reverse("admin:core_student_changelist"))

        self.assertEqual(response.status_code, 200)

    def test_senior_housemaster_cannot_use_django_admin(self):
        user = User.objects.create_user(
            username="senior-admin-site-user",
            password="secure-test-password",
            is_staff=True,
        )
        UserProfile.objects.filter(user=user).update(
            role=StaffRole.SENIOR_HOUSEMASTER,
        )
        user.user_permissions.add(self.student_view_permission)
        self.client.force_login(user)

        response = self.client.get(reverse("admin:core_student_changelist"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_academic_term_admin_changes_are_audited(self):
        admin_user = User.objects.create_superuser(
            username="academic-term-admin",
            password="secure-test-password",
        )
        academic_year = AcademicYear.objects.create(
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
        )
        term = AcademicTerm.objects.create(
            academic_year=academic_year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 15),
        )
        self.client.force_login(admin_user)

        response = self.client.post(
            reverse("admin:core_academicterm_change", args=[term.pk]),
            {
                "academic_year": str(academic_year.pk),
                "term": AcademicTerm.TermChoice.TERM_1,
                "level": AcademicTerm.LevelChoice.ALL,
                "start_date": "2026-09-01",
                "end_date": "2026-12-15",
                "is_current": "on",
                "_save": "Save",
            },
        )

        self.assertEqual(response.status_code, 302)
        term.refresh_from_db()
        self.assertTrue(term.is_current)
        audit_event = ConfigurationAuditLog.objects.get(
            event=ConfigurationAuditLog.Event.ACADEMIC_TERM_CHANGED,
        )
        self.assertEqual(audit_event.actor, admin_user)
        self.assertTrue(audit_event.details["to"]["is_current"])

    def test_configuration_action_rejects_get_requests(self):
        user = User.objects.create_user(
            username="admin-action-staff",
            password="safe-password",
        )
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": StaffRole.ADMIN},
        )
        self.client.force_login(user)

        response = self.client.get(
            reverse("core:set_active_term", args=[uuid.uuid4()])
        )

        self.assertEqual(response.status_code, 405)


class StudentPhotoAuthorizationTests(TransactionTestCase):
    def setUp(self):
        self.photo_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.photo_directory.cleanup)
        self.public_media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.public_media_directory.cleanup)
        self.settings_override = override_settings(
            MEDIA_ROOT=self.public_media_directory.name,
            PRIVATE_STUDENT_PHOTO_ROOT=self.photo_directory.name,
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.programme = Programme.objects.create(name="Private Photo Programme")
        self.house = House.objects.create(name="Photo House")
        self.other_house = House.objects.create(name="Other Photo House")
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name="Photo Dormitory",
            gender=Dormitory.GenderBlock.MALE,
        )
        self.other_dormitory = Dormitory.objects.create(
            house=self.other_house,
            name="Other Photo Dormitory",
            gender=Dormitory.GenderBlock.MALE,
        )
        self.student = self._create_student(
            "PHOTO-001", "Photo House Student", self.house, self.dormitory, "photo.jpg"
        )
        self.other_student = self._create_student(
            "PHOTO-002",
            "Other House Student",
            self.other_house,
            self.other_dormitory,
            "other-photo.jpg",
        )
        self.housemaster = User.objects.create_user(
            username="photo-housemaster",
            password="safe-password",
        )
        UserProfile.objects.update_or_create(
            user=self.housemaster,
            defaults={"role": StaffRole.HOUSEMASTER},
        )
        HouseMaster.objects.create(
            user=self.housemaster,
            full_name="Photo Housemaster",
            staff_id="ROLE-PHOTO-HM",
            phone_number="+233200000002",
            house=self.house,
        )

    def _create_student(self, admission_no, name, house, dormitory, filename):
        image = io.BytesIO()
        Image.new("RGB", (2, 2), color="navy").save(image, format="JPEG")
        return Student.objects.create(
            admission_no=admission_no,
            index_number=f"IDX-{admission_no}",
            full_name=name,
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=house,
            dormitory=dormitory,
            photo=SimpleUploadedFile(
                filename,
                image.getvalue(),
                content_type="image/jpeg",
            ),
        )

    def test_student_photo_is_private_and_house_scoped(self):
        private_path = self.student.photo.storage.path(self.student.photo.name)
        self.assertTrue(private_path.startswith(self.photo_directory.name))
        self.assertFalse(
            Path(self.public_media_directory.name, self.student.photo.name).exists()
        )
        with self.assertRaises(ValueError):
            self.student.photo.url

        self.client.force_login(self.housemaster)
        allowed = self.client.get(
            reverse("core:student_photo", args=[self.student.pk])
        )
        denied = self.client.get(
            reverse("core:student_photo", args=[self.other_student.pk])
        )

        self.assertEqual(allowed.status_code, 200)
        self.assertIn("no-store", allowed["Cache-Control"])
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(
            self.client.get(f"/media/{self.student.photo.name}").status_code,
            404,
        )
        allowed.close()

    def test_private_student_photo_requires_staff_authentication(self):
        response = self.client.get(
            reverse("core:student_photo", args=[self.student.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("core:login"), response["Location"])

    def test_public_student_photo_endpoint_is_not_exposed(self):
        response = self.client.get(
            f"/verify/student/{self.student.public_token}/photo/"
        )

        self.assertEqual(response.status_code, 404)

    def test_admin_student_change_form_does_not_request_private_photo_url(self):
        administrator = User.objects.create_superuser(
            username="photo-admin",
            email="photo-admin@example.test",
            password="test-password",
        )
        self.client.force_login(administrator)

        response = self.client.get(
            reverse("admin:core_student_change", args=[self.student.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current photo is stored privately.")
        self.assertNotContains(response, self.student.photo.name)


class HouseScopedWriteAuthorizationTests(TestCase):
    def test_housemaster_cannot_approve_an_exeat_for_another_house(self):
        programme = Programme.objects.create(name="Scoped Exeat Programme")
        house = House.objects.create(name="Scoped Exeat House")
        other_house = House.objects.create(name="Other Scoped Exeat House")
        other_dormitory = Dormitory.objects.create(
            house=other_house,
            name="Other House Dormitory",
            gender=Dormitory.GenderBlock.MALE,
        )
        other_student = Student.objects.create(
            admission_no="SCOPE-EXEAT-001",
            index_number="IDX-SCOPE-EXEAT-001",
            full_name="Other House Student",
            gender=Student.Gender.MALE,
            programme=programme,
            house=other_house,
            dormitory=other_dormitory,
        )
        user = User.objects.create_user(
            username="scoped-housemaster",
            password="safe-password",
        )
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": StaffRole.HOUSEMASTER},
        )
        HouseMaster.objects.create(
            user=user,
            full_name="Scoped Housemaster",
            staff_id="ROLE-SCOPE-HM",
            phone_number="+233200000003",
            house=house,
        )
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=other_student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now + timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=3),
            destination="Private destination",
            reason="Private reason",
            status=Exeat.Status.PENDING,
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("core:exeat_action", args=[exeat.pk, "approve"])
        )

        self.assertEqual(response.status_code, 404)
        exeat.refresh_from_db()
        self.assertEqual(exeat.status, Exeat.Status.PENDING)

    def test_exeat_action_rejects_external_referer_redirects(self):
        programme = Programme.objects.create(name="Redirect Programme")
        house = House.objects.create(name="Redirect House")
        dormitory = Dormitory.objects.create(
            house=house,
            name="Redirect Dormitory",
            gender=Dormitory.GenderBlock.MALE,
        )
        student = Student.objects.create(
            admission_no="REDIRECT-001",
            index_number="IDX-REDIRECT-001",
            full_name="Redirect Student",
            gender=Student.Gender.MALE,
            programme=programme,
            house=house,
            dormitory=dormitory,
        )
        user = User.objects.create_user(
            username="redirect-housemaster",
            password="test-password",
        )
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": StaffRole.HOUSEMASTER},
        )
        HouseMaster.objects.create(
            user=user,
            full_name="Redirect Housemaster",
            staff_id="ROLE-REDIRECT-HM",
            phone_number="+233200000005",
            house=house,
        )
        now = timezone.now()
        exeat = Exeat.objects.create(
            student=student,
            exeat_type=Exeat.ExeatType.TOWN,
            expected_departure=now + timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=3),
            destination="Private destination",
            reason="Private reason",
        )
        self.client.force_login(user)

        response = self.client.post(
            reverse("core:exeat_action", args=[exeat.pk, "noop"]),
            HTTP_REFERER="https://attacker.example/phishing",
        )

        self.assertRedirects(
            response,
            reverse("core:dashboard"),
            fetch_redirect_response=False,
        )
        internal_response = self.client.post(
            reverse("core:exeat_action", args=[exeat.pk, "noop"]),
            HTTP_REFERER=reverse("core:exeat_list"),
        )
        self.assertRedirects(
            internal_response,
            reverse("core:exeat_list"),
            fetch_redirect_response=False,
        )


class HousemasterAuthorizationBoundaryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="boundary-housemaster",
            password="test-password",
        )
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"role": StaffRole.HOUSEMASTER},
        )
        self.house = House.objects.create(name="Boundary House")
        self.other_house = House.objects.create(name="Other Boundary House")
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name="Boundary Dormitory",
            gender=Dormitory.GenderBlock.MALE,
        )
        self.other_dormitory = Dormitory.objects.create(
            house=self.other_house,
            name="Other Boundary Dormitory",
            gender=Dormitory.GenderBlock.MALE,
        )
        self.programme = Programme.objects.create(name="Boundary Programme")
        HouseMaster.objects.create(
            user=self.user,
            full_name="Boundary Housemaster",
            staff_id="ROLE-BOUNDARY-HM",
            phone_number="+233200000004",
            house=self.house,
        )
        self.student = Student.objects.create(
            admission_no="BOUNDARY-001",
            index_number="IDX-BOUNDARY-001",
            full_name="Boundary House Student",
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
        )
        self.other_student = Student.objects.create(
            admission_no="BOUNDARY-002",
            index_number="IDX-BOUNDARY-002",
            full_name="Other House Student",
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.other_house,
            dormitory=self.other_dormitory,
        )
        self.local_record = DisciplinaryRecord.objects.create(
            student=self.student,
            offense="Local house incident",
            action_taken="Local follow-up",
        )
        DisciplinaryRecord.objects.create(
            student=self.other_student,
            offense="Private opposing-house incident",
            action_taken="Private follow-up",
        )

    def test_housemaster_cannot_view_or_change_schoolwide_configuration(self):
        academic_year = AcademicYear.objects.create(
            start_date=date(2026, 9, 1),
            end_date=date(2027, 8, 31),
        )
        term = AcademicTerm.objects.create(
            academic_year=academic_year,
            term=AcademicTerm.TermChoice.TERM_1,
            level=AcademicTerm.LevelChoice.ALL,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 12, 15),
        )
        sms_configuration, _created = SMSConfiguration.objects.get_or_create(
            pk=1,
            defaults={"enabled": True},
        )
        sms_configuration.enabled = True
        sms_configuration.save(update_fields=["enabled", "updated_at"])
        self.client.force_login(self.user)

        configuration_response = self.client.get(reverse("core:configuration"))
        active_term_response = self.client.post(
            reverse("core:set_active_term", args=[term.pk])
        )
        sms_response = self.client.post(
            reverse("core:set_sms_enabled"),
            {"enabled": "false"},
        )

        self.assertRedirects(
            configuration_response,
            reverse("core:dashboard"),
        )
        self.assertRedirects(active_term_response, reverse("core:dashboard"))
        self.assertRedirects(sms_response, reverse("core:dashboard"))
        term.refresh_from_db()
        sms_configuration.refresh_from_db()
        self.assertFalse(term.is_current)
        self.assertTrue(sms_configuration.enabled)
        self.assertFalse(
            ConfigurationAuditLog.objects.filter(
                event=ConfigurationAuditLog.Event.ACTIVE_TERM_CHANGED,
            ).exists()
        )

    def test_housemaster_cannot_view_disciplinary_data_from_another_house(self):
        self.client.force_login(self.user)

        conduct_response = self.client.get(reverse("core:conduct_list"))
        requested_other_house_response = self.client.get(
            reverse("core:conduct_list"),
            {"house": str(self.other_house.pk)},
        )
        own_record_response = self.client.get(
            reverse("core:student_record", args=[self.student.pk])
        )
        other_record_response = self.client.get(
            reverse("core:student_record", args=[self.other_student.pk])
        )

        self.assertEqual(conduct_response.status_code, 200)
        self.assertContains(conduct_response, "Local house incident")
        self.assertNotContains(
            conduct_response,
            "Private opposing-house incident",
        )
        self.assertEqual(requested_other_house_response.status_code, 200)
        self.assertNotContains(
            requested_other_house_response,
            "Private opposing-house incident",
        )
        self.assertEqual(
            list(conduct_response.context["houses"]),
            [self.house],
        )
        self.assertEqual(own_record_response.status_code, 200)
        self.assertEqual(
            list(own_record_response.context["disciplinary_records"]),
            [self.local_record],
        )
        self.assertEqual(other_record_response.status_code, 404)
