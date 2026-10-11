import csv
import io
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook
from PIL import Image

from core.forms import StudentRegistrationForm
from core.models import (
    ClassGroup,
    Dormitory,
    DormitoryRoom,
    Guardian,
    House,
    Programme,
    StaffRole,
    Student,
    UserProfile,
)
from core.services.student_import import import_student_roster


class StudentRegistryTests(TestCase):
    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_directory.cleanup)
        self.media_settings = override_settings(
            MEDIA_ROOT=self.media_directory.name,
            PRIVATE_STUDENT_PHOTO_ROOT=self.media_directory.name,
        )
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)

        self.user = get_user_model().objects.create_user(
            username="student-admin",
            password="password123",
        )
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"role": StaffRole.ADMIN},
        )
        self.client.force_login(self.user)
        self.programme = Programme.objects.create(name="General Science", code="GSCI")
        self.house = House.objects.create(name="Red House")
        self.male_dorm = Dormitory.objects.create(
            house=self.house,
            name="Boys Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=80,
        )
        self.female_dorm = Dormitory.objects.create(
            house=self.house,
            name="Girls Block",
            gender=Dormitory.GenderBlock.FEMALE,
            capacity=80,
        )
        self.class_group = ClassGroup.objects.create(
            name="1A1",
            programme=self.programme,
        )

    def _csv_upload(self, rows, headers=None, name="roster.csv"):
        headers = headers or [
            "Admission No.",
            "Index Number",
            "Full Name",
            "Gender",
            "Programme",
            "House",
            "Class",
        ]
        content = io.StringIO()
        writer = csv.writer(content)
        writer.writerow(headers)
        writer.writerows(rows)
        return SimpleUploadedFile(
            name,
            content.getvalue().encode("utf-8"),
            content_type="text/csv",
        )

    def _xlsx_upload(self, rows, headers=None):
        headers = headers or [
            "Admission No.",
            "Index Number",
            "Full Name",
            "Gender",
            "Programme",
            "House",
            "Class",
        ]
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.title = "Students"
        worksheet.append(headers)
        for row in rows:
            worksheet.append(row)
        content = io.BytesIO()
        workbook.save(content)
        workbook.close()
        return SimpleUploadedFile(
            "roster.xlsx",
            content.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )

    def test_registry_and_registration_are_admin_only(self):
        self.client.logout()
        housemaster = get_user_model().objects.create_user(
            username="housemaster",
            password="password123",
        )
        UserProfile.objects.update_or_create(
            user=housemaster,
            defaults={"role": StaffRole.HOUSEMASTER},
        )
        self.client.force_login(housemaster)

        for name in (
            "core:student_registry",
            "core:student_create",
            "core:student_import",
            "core:student_import_template",
        ):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertEqual(response["Location"], reverse("core:dashboard"))

        self.client.logout()
        response = self.client.get(reverse("core:student_registry"))
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.user)
        self.assertEqual(
            self.client.get(reverse("core:student_registry")).status_code,
            200,
        )

    def create_boarding_student(
        self,
        admission_no,
        *,
        full_name="Registry Student",
        gender=Student.Gender.MALE,
        dormitory=None,
        room=None,
        status=Student.EnrollmentStatus.ENROLLED,
    ):
        dormitory = dormitory or self.male_dorm
        return Student.objects.create(
            admission_no=admission_no,
            index_number=f"IDX-{admission_no}",
            full_name=full_name,
            gender=gender,
            programme=self.programme,
            class_group=self.class_group,
            house=dormitory.house,
            dormitory=dormitory,
            room=room,
            status=status,
        )

    def test_registry_search_matches_student_and_boarding_details(self):
        room = DormitoryRoom.objects.create(
            dormitory=self.male_dorm,
            name="North Wing 12",
            capacity=2,
        )
        student = self.create_boarding_student(
            "SEARCH-001",
            full_name="Kwame Search",
            room=room,
        )

        for term in ("Kwame Search", "SEARCH-001", "IDX-SEARCH-001", "Boys Block", "North Wing"):
            with self.subTest(term=term):
                response = self.client.get(
                    reverse("core:student_registry"),
                    {"q": term},
                )
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, student.full_name)

    def test_registry_search_requires_two_characters_and_returns_htmx_partial(self):
        student = self.create_boarding_student("HTMX-001", full_name="Htmx Student")

        short_response = self.client.get(
            reverse("core:student_registry"),
            {"q": "H"},
            HTTP_HX_REQUEST="true",
        )
        self.assertContains(short_response, "Type at least 2 characters to search")
        self.assertNotContains(short_response, student.full_name)

        response = self.client.get(
            reverse("core:student_registry"),
            {"q": "Htmx"},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, student.full_name)
        self.assertNotContains(response, "Student Registry")

    def test_registry_filters_can_be_combined(self):
        room = DormitoryRoom.objects.create(
            dormitory=self.female_dorm,
            name="Filtered Room",
            capacity=2,
        )
        female_student = self.create_boarding_student(
            "FILTER-001",
            full_name="Filtered Female",
            gender=Student.Gender.FEMALE,
            dormitory=self.female_dorm,
            room=room,
            status=Student.EnrollmentStatus.APPROVED,
        )
        self.create_boarding_student("FILTER-002", full_name="Other Male")

        response = self.client.get(
            reverse("core:student_registry"),
            {
                "gender": Student.Gender.FEMALE,
                "status": Student.EnrollmentStatus.APPROVED,
                "residency": Student.ResidencyStatus.BOARDING,
                "house": str(self.house.pk),
                "dormitory": str(self.female_dorm.pk),
                "room": str(room.pk),
                "programme": str(self.programme.pk),
                "class_group": str(self.class_group.pk),
            },
        )

        self.assertContains(response, female_student.full_name)
        self.assertNotContains(response, "Other Male")

    def test_registry_paginates_and_keeps_search_and_filter_parameters(self):
        students = [
            Student(
                admission_no=f"PAGE-{number:03}",
                index_number=f"IDX-PAGE-{number:03}",
                full_name=f"Paged Student {number:03}",
                gender=Student.Gender.MALE,
                programme=self.programme,
                class_group=self.class_group,
                house=self.house,
                dormitory=self.male_dorm,
            )
            for number in range(51)
        ]
        Student.objects.bulk_create(students)

        response = self.client.get(
            reverse("core:student_registry"),
            {"q": "Paged Student", "residency": Student.ResidencyStatus.BOARDING},
        )

        self.assertContains(response, "of 51")
        self.assertContains(response, "Page 1 of 2")
        self.assertContains(response, "q=Paged+Student")
        self.assertContains(response, "residency=Boarding")

    def test_admin_student_search_filters_and_pagination(self):
        room = DormitoryRoom.objects.create(
            dormitory=self.male_dorm,
            name="Admin Search Room",
            capacity=2,
        )
        student = self.create_boarding_student(
            "ADMIN-SEARCH-001",
            full_name="Admin Search Student",
            room=room,
        )
        administrator = get_user_model().objects.create_superuser(
            username="registry-admin",
            email="registry-admin@example.test",
            password="test-password",
        )
        self.client.force_login(administrator)

        for term in (
            "Admin Search Student",
            student.admission_no,
            student.index_number,
            "General Science",
            "1A1",
            "Red House",
            "Boys Block",
            "Admin Search Room",
        ):
            with self.subTest(term=term):
                search_response = self.client.get(
                    reverse("admin:core_student_changelist"),
                    {"q": term},
                )
                self.assertContains(search_response, student.full_name)
        self.assertContains(search_response, "Room")
        self.assertContains(search_response, "Programme")

        students = [
            Student(
                admission_no=f"ADMIN-PAGE-{number:03}",
                index_number=f"IDX-ADMIN-PAGE-{number:03}",
                full_name=f"Admin Paged Student {number:03}",
                gender=Student.Gender.MALE,
                programme=self.programme,
                class_group=self.class_group,
                house=self.house,
                dormitory=self.male_dorm,
            )
            for number in range(51)
        ]
        Student.objects.bulk_create(students)
        page_response = self.client.get(reverse("admin:core_student_changelist"))

        changelist = page_response.context["cl"]
        self.assertEqual(changelist.result_count, 52)
        self.assertEqual(changelist.paginator.per_page, 50)
        self.assertEqual(changelist.paginator.num_pages, 2)

    def test_individual_registration_creates_student_guardian_and_photo(self):
        image = Image.new("RGB", (160, 220), color="navy")
        image_data = io.BytesIO()
        image.save(image_data, format="JPEG")
        image_data.seek(0)
        photo = SimpleUploadedFile(
            "portrait.jpg",
            image_data.read(),
            content_type="image/jpeg",
        )
        payload = {
            "admission_no": "ADM-001",
            "index_number": "IDX-001",
            "full_name": "Ama Mensah",
            "gender": Student.Gender.FEMALE,
            "date_of_birth": "2009-04-10",
            "programme": self.programme.pk,
            "class_group": self.class_group.pk,
            "residency": Student.ResidencyStatus.BOARDING,
            "house": self.house.pk,
            "dormitory": self.female_dorm.pk,
            "status": Student.EnrollmentStatus.ENROLLED,
            "guardian_name": "Akosua Mensah",
            "guardian_phone": "+233240000001",
        }

        response = self.client.post(
            reverse("core:student_create"),
            {**payload, "photo": photo},
        )

        self.assertRedirects(response, reverse("core:student_registry"))
        student = Student.objects.get(admission_no="ADM-001")
        self.assertEqual(student.guardian.full_name, "Akosua Mensah")
        self.assertTrue(student.photo.name.startswith("students/photos/"))
        self.assertEqual(student.photo.name.rsplit(".", 1)[-1], "jpg")
        self.assertEqual(Guardian.objects.count(), 1)

    def test_registration_form_updates_existing_guardian_alternative_phone(self):
        guardian = Guardian.objects.create(
            full_name="Akosua Mensah",
            phone_number="+233240000001",
            alt_phone_number="+233240000002",
        )
        student = Student.objects.create(
            admission_no="ADM-GUARDIAN",
            index_number="IDX-GUARDIAN",
            full_name="Ama Mensah",
            gender=Student.Gender.FEMALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.female_dorm,
            class_group=self.class_group,
            guardian=guardian,
        )
        form = StudentRegistrationForm(
            data={
                "admission_no": student.admission_no,
                "index_number": student.index_number,
                "full_name": student.full_name,
                "gender": student.gender,
                "programme": self.programme.pk,
                "class_group": self.class_group.pk,
                "residency": student.residency,
                "house": self.house.pk,
                "dormitory": self.female_dorm.pk,
                "status": student.status,
                "guardian_name": guardian.full_name,
                "guardian_phone": guardian.phone_number,
                "guardian_alt_phone": "+233240000003",
            },
            instance=student,
        )

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        guardian.refresh_from_db()
        self.assertEqual(guardian.alt_phone_number, "+233240000003")

    def test_photo_upload_rejects_fake_image_content(self):
        invalid_photo = SimpleUploadedFile(
            "portrait.jpg",
            b"not an actual image",
            content_type="image/jpeg",
        )
        form = StudentRegistrationForm(
            data={
                "admission_no": "ADM-BAD",
                "index_number": "IDX-BAD",
                "full_name": "Bad Image",
                "gender": Student.Gender.MALE,
                "programme": self.programme.pk,
                "residency": Student.ResidencyStatus.BOARDING,
                "house": self.house.pk,
                "dormitory": self.male_dorm.pk,
                "status": Student.EnrollmentStatus.ENROLLED,
            },
            files={"photo": invalid_photo},
        )

        self.assertFalse(form.is_valid())
        self.assertIn("photo", form.errors)

    def test_csv_import_validates_then_creates_with_configured_placement(self):
        uploaded_file = self._csv_upload(
            [
                [
                    "ADM-CSV-1",
                    "IDX-CSV-1",
                    "Kwame Boateng",
                    "Male",
                    "GSCI",
                    "Red House",
                    "1A1",
                ],
                [
                    "ADM-CSV-2",
                    "IDX-CSV-2",
                    "Akua Boateng",
                    "Female",
                    "General Science",
                    "Red House",
                    "1A1",
                ],
            ]
        )

        imported, errors = import_student_roster(uploaded_file)

        self.assertEqual(errors, [])
        self.assertEqual(imported, 2)
        male_student = Student.objects.get(admission_no="ADM-CSV-1")
        female_student = Student.objects.get(admission_no="ADM-CSV-2")
        self.assertEqual(male_student.dormitory, self.male_dorm)
        self.assertEqual(female_student.dormitory, self.female_dorm)
        self.assertEqual(male_student.residency, Student.ResidencyStatus.BOARDING)

    def test_excel_import_reads_each_worksheet(self):
        headers = [
            "Admission No.",
            "Index Number",
            "Full Name",
            "Gender",
            "Programme",
            "House",
            "Class",
        ]
        workbook = Workbook()
        first = workbook.active
        first.title = "First"
        first.append(headers)
        first.append(
            [
                "ADM-XLS-1",
                "IDX-XLS-1",
                "First Student",
                "Male",
                "General Science",
                "Red House",
                "1A1",
            ]
        )
        second = workbook.create_sheet("Second")
        second.append(headers)
        second.append(
            [
                "ADM-XLS-2",
                "IDX-XLS-2",
                "Second Student",
                "Female",
                "General Science",
                "Red House",
                "1A1",
            ]
        )
        content = io.BytesIO()
        workbook.save(content)
        workbook.close()
        uploaded_file = SimpleUploadedFile(
            "roster.xlsx",
            content.getvalue(),
            content_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
        )

        imported, errors = import_student_roster(uploaded_file)

        self.assertEqual(errors, [])
        self.assertEqual(imported, 2)
        self.assertEqual(Student.objects.filter(admission_no__startswith="ADM-XLS").count(), 2)

    def test_invalid_or_duplicate_csv_imports_nothing(self):
        uploaded_file = self._csv_upload(
            [
                [
                    "ADM-DUP",
                    "IDX-DUP-1",
                    "First Student",
                    "Male",
                    "General Science",
                    "Red House",
                    "1A1",
                ],
                [
                    "ADM-DUP",
                    "IDX-DUP-2",
                    "Second Student",
                    "Female",
                    "General Science",
                    "Red House",
                    "1A1",
                ],
            ]
        )

        imported, errors = import_student_roster(uploaded_file)

        self.assertEqual(imported, 0)
        self.assertTrue(any("duplicate admission number" in error for error in errors))
        self.assertFalse(Student.objects.exists())

    def test_invalid_placement_or_unknown_references_reject_the_whole_file(self):
        uploaded_file = self._csv_upload(
            [
                [
                    "ADM-GOOD",
                    "IDX-GOOD",
                    "Good Student",
                    "Male",
                    "General Science",
                    "Red House",
                    "1A1",
                ],
                [
                    "ADM-BAD",
                    "IDX-BAD",
                    "Bad Student",
                    "Female",
                    "Unknown Programme",
                    "Unknown House",
                    "1A1",
                ],
            ]
        )

        imported, errors = import_student_roster(uploaded_file)

        self.assertEqual(imported, 0)
        self.assertTrue(any("unknown programme" in error for error in errors))
        self.assertTrue(any("unknown house" in error for error in errors))
        self.assertFalse(Student.objects.exists())

    def test_import_rejects_class_from_a_different_programme(self):
        other_programme = Programme.objects.create(
            name="General Arts",
            code="GART",
        )
        ClassGroup.objects.create(name="1A2", programme=other_programme)
        uploaded_file = self._csv_upload(
            [
                [
                    "ADM-MISMATCH",
                    "IDX-MISMATCH",
                    "Mismatched Student",
                    "Male",
                    "General Science",
                    "Red House",
                    "1A2",
                ],
            ]
        )

        imported, errors = import_student_roster(uploaded_file)

        self.assertEqual(imported, 0)
        self.assertTrue(any("different programme" in error for error in errors))
        self.assertFalse(Student.objects.exists())

    def test_template_download_and_form_errors_render(self):
        response = self.client.get(reverse("core:student_import_template"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("Admission No.", response.content.decode())

        response = self.client.post(
            reverse("core:student_import"),
            {
                "roster_file": SimpleUploadedFile(
                    "students.txt",
                    b"unsupported",
                    content_type="text/plain",
                )
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Upload a CSV or Excel .xlsx file.")


class TemporaryStudentMediaTests(TransactionTestCase):
    def test_registry_displays_student_photo_as_a_cropped_portrait(self):
        with tempfile.TemporaryDirectory() as media_dir:
            with override_settings(
                MEDIA_ROOT=media_dir,
                PRIVATE_STUDENT_PHOTO_ROOT=media_dir,
            ):
                user = get_user_model().objects.create_user(
                    username="photo-admin",
                    password="password123",
                )
                UserProfile.objects.update_or_create(
                    user=user,
                    defaults={"role": StaffRole.ADMIN},
                )
                programme = Programme.objects.create(name="Arts")
                house = House.objects.create(name="Blue House")
                dormitory = Dormitory.objects.create(
                    house=house,
                    name="Girls Block",
                    gender=Dormitory.GenderBlock.FEMALE,
                )
                image = Image.new("RGB", (100, 140), color="teal")
                image_data = io.BytesIO()
                image.save(image_data, format="PNG")
                student = Student.objects.create(
                    admission_no="ADM-PHOTO",
                    index_number="IDX-PHOTO",
                    full_name="Photo Student",
                    gender=Student.Gender.FEMALE,
                    programme=programme,
                    house=house,
                    dormitory=dormitory,
                    photo=SimpleUploadedFile(
                        "portrait.png",
                        image_data.getvalue(),
                        content_type="image/png",
                    ),
                )
                self.client.force_login(user)

                response = self.client.get(reverse("core:student_registry"))

                self.assertEqual(response.status_code, 200)
                self.assertContains(
                    response,
                    reverse("core:student_photo", args=[student.pk]),
                )
                self.assertContains(response, "object-cover")
                self.assertTrue(Path(media_dir, student.photo.name).exists())
                photo_response = self.client.get(
                    reverse("core:student_photo", args=[student.pk])
                )
                self.assertEqual(photo_response.status_code, 200)
                self.assertEqual(photo_response["X-Content-Type-Options"], "nosniff")
                photo_response.close()
                with self.assertRaises(ValueError):
                    student.photo.url
