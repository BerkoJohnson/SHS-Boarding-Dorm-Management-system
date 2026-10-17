import base64
import tempfile
import uuid
from io import BytesIO
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from core.models import (
    Dormitory,
    Exeat,
    ExeatMovement,
    House,
    HouseMaster,
    MovementConcernReport,
    Programme,
    SchoolConfiguration,
    StaffRole,
    Student,
    StudentIDExport,
    UserProfile,
)
from core.services.student_id_images import (
    CARD_HEIGHT,
    CARD_WIDTH,
    render_student_card_side,
)
from core.tasks import cleanup_expired_student_id_exports


class StudentIdCardTests(TestCase):
    def setUp(self):
        self.programme = Programme.objects.create(name="Id Science")
        self.house = House.objects.create(name="Id House")
        self.dormitory = Dormitory.objects.create(
            house=self.house,
            name="Id Block",
            gender=Dormitory.GenderBlock.MALE,
        )
        self.student = Student.objects.create(
            admission_no="ID-001",
            index_number="IDX-ID-001",
            full_name="Kwame Identity",
            gender=Student.Gender.MALE,
            programme=self.programme,
            house=self.house,
            dormitory=self.dormitory,
        )
        SchoolConfiguration.objects.create(
            name="Atebubu SHS",
            address="Atebubu, Bono East Region",
            contact_number="+233 20 000 0000",
        )
        self.admin = get_user_model().objects.create_user(
            username="id-admin",
            password="password123",
        )
        UserProfile.objects.update_or_create(
            user=self.admin,
            defaults={"role": StaffRole.ADMIN},
        )
        self.approver = HouseMaster.objects.create(
            full_name="Senior Approver",
            staff_id="ID-SENIOR",
            phone_number="+233200000010",
            is_senior_housemaster=True,
        )

    def create_exeat(self, **kwargs):
        now = timezone.now()
        values = {
            "student": self.student,
            "exeat_type": Exeat.ExeatType.TOWN,
            "expected_departure": now + timezone.timedelta(hours=1),
            "expected_return": now + timezone.timedelta(hours=4),
            "destination": "Private destination",
            "reason": "Private reason",
        }
        values.update(kwargs)
        return Exeat.objects.create(**values)

    def test_id_card_is_restricted_and_uses_configured_school_identity(self):
        url = reverse("core:student_id_card", args=[self.student.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        self.client.force_login(self.admin)
        response = self.client.get(url, {"layout": "crest"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_layout"], "crest")
        self.assertEqual(len(response.context["layouts"]), 3)
        self.assertContains(response, "layout-crest")
        self.assertContains(response, "crest-front-header")
        self.assertContains(response, "crest-back-panel")
        self.assertContains(response, "Kwame Identity")
        self.assertContains(response, "Scan to verify")
        self.assertContains(response, "Atebubu SHS")
        self.assertContains(response, "Atebubu, Bono East Region")
        self.assertContains(response, "+233 20 000 0000")
        self.assertContains(response, "LOGO")
        card_markup = response.content.split(b'<main class="card-print-area', 1)[1].split(b"</main>", 1)[0]
        self.assertIn(b"Atebubu SHS", card_markup)
        self.assertIn(b"Atebubu, Bono East Region", card_markup)
        self.assertIn(b"+233 20 000 0000", card_markup)
        self.assertNotIn(b"SCHOOL NAME", card_markup)
        self.assertNotIn(b"SCHOOL ADDRESS", card_markup)
        self.assertNotIn(b"SCHOOL CONTACT", card_markup)
        self.assertNotContains(response, "Parent Contact")
        qr_data_uri = response.context["qr_code"]
        self.assertTrue(qr_data_uri.startswith("data:image/svg+xml;base64,"))
        svg = base64.b64decode(qr_data_uri.partition(",")[2])
        self.assertIn(b"<svg", svg)
        self.assertIn(
            reverse(
                "core:public_student_exeat",
                args=[self.student.public_token],
            ),
            response.context["public_url"],
        )

    def test_other_card_layouts_use_configured_school_identity(self):
        self.client.force_login(self.admin)
        url = reverse("core:student_id_card", args=[self.student.pk])

        for layout in ("classic", "modern"):
            with self.subTest(layout=layout):
                response = self.client.get(url, {"layout": layout})

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Atebubu SHS")
                self.assertContains(response, "Atebubu, Bono East Region")
                self.assertContains(response, "+233 20 000 0000")
                card_markup = response.content.split(b'<main class="card-print-area', 1)[1].split(b"</main>", 1)[0]
                self.assertIn(b"Atebubu SHS", card_markup)
                self.assertIn(b"Atebubu, Bono East Region", card_markup)
                self.assertIn(b"+233 20 000 0000", card_markup)
                self.assertNotIn(b"SCHOOL NAME", card_markup)
                self.assertNotIn(b"SCHOOL ADDRESS", card_markup)
                self.assertNotIn(b"SCHOOL CONTACT", card_markup)

    def test_generated_card_images_use_configured_school_identity(self):
        for layout in ("classic", "crest", "modern"):
            for side in ("front", "back"):
                with self.subTest(layout=layout, side=side):
                    image_bytes = render_student_card_side(
                        self.student,
                        school_configuration=SchoolConfiguration.get_solo(),
                        public_url="https://example.test/verify",
                        layout=layout,
                        side=side,
                        image_format="PNG",
                    )

                    with Image.open(BytesIO(image_bytes)) as image:
                        self.assertEqual(image.format, "PNG")
                        self.assertEqual(image.size, (CARD_WIDTH, CARD_HEIGHT))

    def test_generated_card_image_embeds_the_configured_school_logo(self):
        logo_output = BytesIO()
        Image.new("RGBA", (30, 30), (220, 30, 40, 255)).save(
            logo_output,
            format="PNG",
        )
        configuration = SchoolConfiguration.get_solo()

        with tempfile.TemporaryDirectory() as media_directory:
            with override_settings(MEDIA_ROOT=media_directory):
                configuration.logo.save(
                    "school-logo.png",
                    SimpleUploadedFile(
                        "school-logo.png",
                        logo_output.getvalue(),
                        content_type="image/png",
                    ),
                    save=True,
                )
                image_bytes = render_student_card_side(
                    self.student,
                    school_configuration=configuration,
                    public_url="https://example.test/verify",
                    layout="classic",
                    side="front",
                    image_format="PNG",
                )

        with Image.open(BytesIO(image_bytes)) as image:
            logo_area = image.crop((30, 20, 105, 105))
            self.assertGreater(
                sum(1 for pixel in logo_area.getdata() if pixel == (220, 30, 40)),
                0,
            )

    def test_id_card_rejects_unknown_layout(self):
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse("core:student_id_card", args=[self.student.pk]),
            {"layout": "custom-user-html"},
        )

        self.assertEqual(response.status_code, 400)

    def test_completed_student_id_export_can_be_downloaded(self):
        self.client.force_login(self.admin)
        with tempfile.TemporaryDirectory() as temporary_directory:
            with override_settings(BASE_DIR=Path(temporary_directory)):
                export = StudentIDExport.objects.create(
                    requested_by=self.admin,
                    layout="crest",
                    image_format=StudentIDExport.ImageFormat.PNG,
                    status=StudentIDExport.Status.COMPLETED,
                    expires_at=timezone.now() + timezone.timedelta(days=7),
                )
                archive_path = (
                    Path(temporary_directory)
                    / ".private"
                    / "student_id_exports"
                    / export.archive_name
                )
                archive_path.parent.mkdir(parents=True)
                archive_path.write_bytes(b"test archive")

                response = self.client.get(
                    reverse(
                        "core:student_id_export_download",
                        args=[export.pk],
                    )
                )

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response["Content-Type"], "application/zip")
                self.assertEqual(
                    b"".join(response.streaming_content),
                    b"test archive",
                )

    def test_expired_student_id_export_cannot_be_downloaded(self):
        self.client.force_login(self.admin)
        export = StudentIDExport.objects.create(
            requested_by=self.admin,
            layout="crest",
            image_format=StudentIDExport.ImageFormat.PNG,
            status=StudentIDExport.Status.COMPLETED,
            expires_at=timezone.now() - timezone.timedelta(seconds=1),
        )

        response = self.client.get(
            reverse("core:student_id_export_download", args=[export.pk]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "expired")

    def test_expired_student_id_export_cleanup_removes_archive_and_record(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            with override_settings(BASE_DIR=Path(temporary_directory)):
                export = StudentIDExport.objects.create(
                    requested_by=self.admin,
                    layout="crest",
                    image_format=StudentIDExport.ImageFormat.PNG,
                    status=StudentIDExport.Status.COMPLETED,
                    expires_at=timezone.now() - timezone.timedelta(seconds=1),
                )
                archive_path = (
                    Path(temporary_directory)
                    / ".private"
                    / "student_id_exports"
                    / export.archive_name
                )
                archive_path.parent.mkdir(parents=True)
                archive_path.write_bytes(b"expired archive")

                deleted_count = cleanup_expired_student_id_exports.run()

                self.assertEqual(deleted_count, 1)
                self.assertFalse(archive_path.exists())
                self.assertFalse(StudentIDExport.objects.filter(pk=export.pk).exists())

    def test_replacing_qr_invalidates_the_previous_public_link(self):
        old_token = self.student.public_token
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse(
                "core:student_rotate_public_token",
                args=[self.student.pk],
            )
        )

        self.assertRedirects(
            response,
            reverse("core:student_id_card", args=[self.student.pk]),
        )
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.public_token, old_token)
        self.assertEqual(
            self.client.get(
                reverse("core:public_student_exeat", args=[old_token])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse(
                    "core:public_student_exeat",
                    args=[self.student.public_token],
                )
            ).status_code,
            200,
        )

    def test_public_qr_verifies_a_record_without_disclosing_student_or_exeat_data(self):
        response = self.client.get(
            reverse(
                "core:public_student_exeat",
                args=[self.student.public_token],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atebubu SHS")
        self.assertContains(response, "Atebubu, Bono East Region")
        self.assertContains(response, "LOGO")
        self.assertNotContains(response, "SCHOOL NAME")
        self.assertNotContains(response, "SCHOOL ADDRESS")
        self.assertNotContains(response, "SCHOOL CONTACT")
        self.assertContains(response, "QR code recognized")
        self.assertContains(response, "No active authorization found")
        self.assertContains(response, "Report a concern")
        self.assertNotContains(response, "Kwame Identity")
        self.assertNotContains(response, "ID-001")
        self.assertNotContains(response, "Id Science")
        self.assertNotContains(response, "Latest exeat request")
        self.assertNotContains(response, "Approved departure")
        self.assertNotContains(response, "Gate departure")
        self.assertNotContains(response, "Approved")
        self.assertNotContains(response, "portrait")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")

    def test_public_qr_handles_no_request_and_unknown_token(self):
        response = self.client.get(
            reverse(
                "core:public_student_exeat",
                args=[self.student.public_token],
            )
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "QR code recognized")

        response = self.client.get(
            reverse("core:public_student_exeat", args=[uuid.uuid4()])
        )
        self.assertEqual(response.status_code, 404)

    def test_public_qr_shows_authorization_and_gate_departure_status(self):
        now = timezone.now()
        exeat = self.create_exeat(
            status=Exeat.Status.APPROVED,
            approved_by=self.approver,
            expected_departure=now - timezone.timedelta(hours=1),
            expected_return=now + timezone.timedelta(hours=1),
        )
        url = reverse("core:public_student_exeat", args=[self.student.public_token])

        response = self.client.get(url)

        self.assertContains(response, "Departure not verified")
        self.assertNotContains(response, "Authorized movement")
        self.assertNotContains(response, "Kwame Identity")
        self.assertNotContains(response, "ID-001")
        self.assertNotContains(response, exeat.exeat_number)

        ExeatMovement.objects.create(
            exeat=exeat,
            actual_departure=now,
            departure_recorded_by="Gate Officer",
        )
        response = self.client.get(url)

        self.assertContains(response, "Authorized movement")
        self.assertContains(response, "departure has been recorded")
        self.assertNotContains(response, "Kwame Identity")
        self.assertNotContains(response, "ID-001")

    def test_public_qr_report_is_saved_for_school_staff_review(self):
        response = self.client.post(
            reverse(
                "core:public_student_exeat",
                args=[self.student.public_token],
            ),
            {"details": "The person says there is no approved leave."},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["report_submitted"])
        self.assertContains(response, "sent to school staff for review")
        report = MovementConcernReport.objects.get()
        self.assertEqual(report.student, self.student)
        self.assertIsNone(report.exeat)
        self.assertEqual(
            report.details,
            "The person says there is no approved leave.",
        )
        self.assertEqual(report.status, MovementConcernReport.Status.OPEN)

    def test_public_student_photo_endpoint_is_removed(self):
        response = self.client.get(
            f"/verify/student/{self.student.public_token}/photo/"
        )

        self.assertEqual(response.status_code, 404)
