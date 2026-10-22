import csv
import io

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from core.forms import StudentRegistrationForm
from core.models import (
    Dormitory,
    DormitoryRoom,
    House,
    Programme,
    Student,
)
from core.services.reports import get_detailed_report
from core.services.student_import import import_student_roster


class DormitoryRoomAllocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.house = House.objects.create(name="Room Allocation House")
        cls.other_house = House.objects.create(name="Other Allocation House")
        cls.dormitory = Dormitory.objects.create(
            house=cls.house,
            name="Boys Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=10,
        )
        cls.other_dormitory = Dormitory.objects.create(
            house=cls.other_house,
            name="Other Boys Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=10,
        )
        cls.room = DormitoryRoom.objects.create(
            dormitory=cls.dormitory,
            name="Room 1",
            capacity=2,
        )
        cls.other_room = DormitoryRoom.objects.create(
            dormitory=cls.dormitory,
            name="Room 2",
            capacity=2,
        )
        cls.programme = Programme.objects.create(name="Room Allocation Science")

    def create_student(
        self, admission_no, *, room=None, dormitory=None, residency=None, gender=None
    ):
        dormitory = dormitory or self.dormitory
        return Student.objects.create(
            admission_no=admission_no,
            index_number=f"IDX-{admission_no}",
            full_name=f"Student {admission_no}",
            gender=gender or Student.Gender.MALE,
            programme=self.programme,
            house=dormitory.house,
            dormitory=dormitory,
            room=room,
            residency=residency or Student.ResidencyStatus.BOARDING,
        )

    def registration_data(self, *, room):
        return {
            "admission_no": "FORM-001",
            "index_number": "FORM-IDX-001",
            "full_name": "Room Form Student",
            "gender": Student.Gender.MALE,
            "programme": self.programme.pk,
            "residency": Student.ResidencyStatus.BOARDING,
            "house": self.house.pk,
            "dormitory": self.dormitory.pk,
            "room": room.pk if room else "",
            "status": Student.EnrollmentStatus.ENROLLED,
        }

    def test_room_capacity_is_a_hard_limit_for_student_assignments(self):
        room = DormitoryRoom.objects.create(
            dormitory=self.dormitory,
            name="Single Occupancy",
            capacity=1,
        )
        self.create_student("CAP-001", room=room)

        with self.assertRaises(ValidationError) as error:
            self.create_student("CAP-002", room=room)

        self.assertIn(
            "at its capacity",
            error.exception.message_dict["room"][0],
        )

    def test_capacity_cannot_be_reduced_below_current_occupancy(self):
        self.create_student("CAPACITY-001", room=self.room)
        self.room.capacity = 0

        with self.assertRaises(ValidationError) as error:
            self.room.save()

        self.assertIn(
            "Capacity cannot be lower",
            error.exception.message_dict["capacity"][0],
        )

    def test_student_room_must_match_dormitory_and_day_students_cannot_be_assigned(self):
        with self.assertRaises(ValidationError) as mismatch:
            self.create_student(
                "MISMATCH-001",
                room=self.room,
                dormitory=self.other_dormitory,
            )
        self.assertIn(
            "must belong to the assigned dormitory",
            mismatch.exception.message_dict["room"][0],
        )

        with self.assertRaises(ValidationError) as day_student:
            Student.objects.create(
                admission_no="DAY-001",
                index_number="IDX-DAY-001",
                full_name="Day Student",
                gender=Student.Gender.MALE,
                programme=self.programme,
                room=self.room,
                residency=Student.ResidencyStatus.DAY,
            )
        self.assertIn(
            "Day students cannot be assigned",
            day_student.exception.message_dict["room"][0],
        )

    def test_registration_saves_selected_room(self):
        form = StudentRegistrationForm(
            data=self.registration_data(room=self.room),
        )

        self.assertTrue(form.is_valid(), form.errors)
        student = form.save()

        self.assertEqual(student.room, self.room)

    def test_registration_requires_room_after_room_inventory_is_configured(self):
        form = StudentRegistrationForm(
            data=self.registration_data(room=None),
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Assign this boarding student to a room", form.errors["room"][0])

    def test_registration_form_does_not_offer_a_full_room(self):
        full_room = DormitoryRoom.objects.create(
            dormitory=self.dormitory,
            name="Full Room",
            capacity=1,
        )
        self.create_student("FULL-001", room=full_room)

        form = StudentRegistrationForm(
            data=self.registration_data(room=full_room),
        )

        self.assertFalse(form.is_valid())
        self.assertNotIn(full_room, form.fields["room"].queryset)

    def test_roster_import_validates_room_capacity_before_creating_any_students(self):
        content = io.StringIO()
        writer = csv.writer(content)
        writer.writerow(
            [
                "Admission No.",
                "Index Number",
                "Full Name",
                "Gender",
                "Programme",
                "House",
                "Dormitory",
                "Room",
            ]
        )
        writer.writerows(
            [
                [
                    "IMPORT-001",
                    "IMPORT-IDX-001",
                    "First Import Student",
                    "Male",
                    self.programme.name,
                    self.house.name,
                    self.dormitory.name,
                    "Single Import Room",
                ],
                [
                    "IMPORT-002",
                    "IMPORT-IDX-002",
                    "Second Import Student",
                    "Male",
                    self.programme.name,
                    self.house.name,
                    self.dormitory.name,
                    "Single Import Room",
                ],
            ]
        )
        upload = SimpleUploadedFile(
            "rooms.csv",
            content.getvalue().encode("utf-8"),
            content_type="text/csv",
        )
        room = DormitoryRoom.objects.create(
            dormitory=self.dormitory,
            name="Single Import Room",
            capacity=1,
        )

        imported, errors = import_student_roster(upload)

        self.assertEqual(imported, 0)
        self.assertEqual(Student.objects.count(), 0)
        self.assertTrue(any("at its capacity" in error for error in errors))
        self.assertEqual(room.students.count(), 0)

    def test_roster_import_reports_dormitory_capacity_overflow_without_saving(self):
        self.dormitory.capacity = 1
        self.dormitory.save()
        room = DormitoryRoom.objects.create(
            dormitory=self.dormitory,
            name="Dormitory Overflow Room",
            capacity=2,
        )
        content = io.StringIO()
        writer = csv.writer(content)
        writer.writerow(
            [
                "Admission No.",
                "Index Number",
                "Full Name",
                "Gender",
                "Programme",
                "House",
                "Dormitory",
                "Room",
            ]
        )
        writer.writerows(
            [
                [
                    "DORM-IMPORT-001",
                    "DORM-IMPORT-IDX-001",
                    "First Dormitory Student",
                    "Male",
                    self.programme.name,
                    self.house.name,
                    self.dormitory.name,
                    room.name,
                ],
                [
                    "DORM-IMPORT-002",
                    "DORM-IMPORT-IDX-002",
                    "Second Dormitory Student",
                    "Male",
                    self.programme.name,
                    self.house.name,
                    self.dormitory.name,
                    room.name,
                ],
            ]
        )
        upload = SimpleUploadedFile(
            "dormitory-capacity.csv",
            content.getvalue().encode("utf-8"),
            content_type="text/csv",
        )

        imported, errors = import_student_roster(upload)

        self.assertEqual(imported, 0)
        self.assertEqual(Student.objects.count(), 0)
        self.assertTrue(any("dormitory" in error and "capacity" in error for error in errors))
        self.assertEqual(room.students.count(), 0)

    def test_housing_report_includes_room_occupancy_and_unassigned_count(self):
        self.create_student("REPORT-001", room=self.room)

        report = get_detailed_report("housing")

        self.assertIn(("Configured rooms", 2), report["summary"])
        self.assertIn(("Boarders assigned to rooms", 1), report["summary"])
        self.assertIn(("Awaiting room placement", 0), report["summary"])
        self.assertTrue(any(row[2] == "Room 1" for row in report["rows"]))

    def test_housing_report_groups_beds_by_cohort_with_signed_vacancy(self):
        girls_block = Dormitory.objects.create(
            house=self.house,
            name="Girls Block",
            gender=Dormitory.GenderBlock.FEMALE,
            capacity=10,
        )
        unrecorded_block = Dormitory.objects.create(
            house=self.other_house,
            name="Overflow Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=1,
        )
        self.create_student("COHORT-001")
        self.create_student("COHORT-002", dormitory=girls_block, gender=Student.Gender.FEMALE)
        self.create_student("COHORT-003", dormitory=unrecorded_block)
        # Simulate historical occupancy recorded against a block whose bed
        # count was later corrected to zero. The report must show the negative
        # vacancy rather than hiding it.
        Dormitory.objects.filter(pk=unrecorded_block.pk).update(capacity=0)
        unrecorded_block.refresh_from_db()

        report = get_detailed_report("housing")

        # Beds are grouped by cohort, because a girls' bed cannot take a boy.
        self.assertEqual(
            [group["label"] for group in report["cohort_groups"]],
            ["Girls", "Boys"],
        )
        girls, boys = report["cohort_groups"]
        self.assertEqual(
            (
                girls["subtotal"]["capacity"],
                girls["subtotal"]["placed"],
                girls["subtotal"]["free"],
            ),
            (10, 1, 9),
        )
        self.assertEqual(
            (
                boys["subtotal"]["capacity"],
                boys["subtotal"]["placed"],
                boys["subtotal"]["free"],
            ),
            (20, 2, 18),
        )
        self.assertEqual(report["totals"]["free"], 27)
        self.assertEqual(
            report["verdict"]["headline"],
            "27 free beds — 9 girls', 18 boys' (10.0% full)",
        )
        self.assertIn("Nothing over capacity", report["verdict"]["notes"])

        # Vacancy is signed: a block holding boarders with no configured beds
        # reads as negative rather than as a calm "0 vacant".
        unrecorded = next(
            row for row in boys["rows"] if row["dormitory"] == "Overflow Block"
        )
        self.assertEqual(
            (unrecorded["free"], unrecorded["state"]),
            (-1, "Beds unrecorded"),
        )

        # The problems the old table hid now surface as named attention items.
        self.assertEqual(
            [item["label"] for item in report["attention"]],
            ["Beds unrecorded", "Awaiting a room"],
        )
        self.assertIn(
            "Overflow Block: 1 boarder with 0 beds configured",
            report["attention"][0]["detail"],
        )
