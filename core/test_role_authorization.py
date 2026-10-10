import io
import tempfile
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import (
    Dormitory,
    Exeat,
    House,
    HouseMaster,
    Programme,
    StaffRole,
    Student,
    UserProfile,
)


User = get_user_model()


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

    def test_public_photo_requires_the_students_opaque_qr_token(self):
        response = self.client.get(
            reverse("core:public_student_photo", args=[self.student.public_token])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        unknown_token_response = self.client.get(
            reverse("core:public_student_photo", args=[uuid.uuid4()])
        )
        self.assertEqual(unknown_token_response.status_code, 404)
        response.close()

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
