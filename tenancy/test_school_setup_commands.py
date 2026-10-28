from io import StringIO
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from core.models import (
    AcademicTerm,
    AcademicYear,
    ClassGroup,
    Dormitory,
    DormitoryRoom,
    House,
    Programme,
)


class SchoolSetupCommandGuardTests(SimpleTestCase):
    setup_commands = {
        "create_house": {"name": "Test House"},
        "create_dormitory": {
            "house": "Test House",
            "name": "Test Block",
            "gender": "M",
        },
        "create_room": {
            "dormitory": "Test Block",
            "name": "Test Room",
            "capacity": 4,
        },
        "create_classroom": {"name": "Test Class"},
        "create_programme": {"name": "Test Programme"},
        "create_academic_year": {
            "start_date": "2025-09-01",
            "end_date": "2026-07-01",
        },
        "create_academic_term": {
            "academic_year": "2025/2026 Academic Year",
            "term": "1",
            "start_date": "2025-09-01",
            "end_date": "2025-12-20",
        },
    }

    @override_settings(TENANCY_ENABLED=True)
    def test_commands_require_a_schema_in_tenant_mode(self):
        for command_name, options in self.setup_commands.items():
            with self.subTest(command=command_name):
                with self.assertRaisesMessage(
                    CommandError,
                    "TENANCY_ENABLED is True; pass --schema to select the school.",
                ):
                    call_command(
                        command_name,
                        stdout=StringIO(),
                        **options,
                    )

    @override_settings(TENANCY_ENABLED=False)
    def test_schema_is_rejected_in_single_school_mode(self):
        for command_name, options in self.setup_commands.items():
            with self.subTest(command=command_name):
                with self.assertRaisesMessage(
                    CommandError,
                    "--schema can only be used when TENANCY_ENABLED=True.",
                ):
                    call_command(
                        command_name,
                        schema="school_one",
                        stdout=StringIO(),
                        **options,
                    )

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands._school_setup.Tenant.objects")
    def test_unknown_schema_is_rejected(self, tenant_objects):
        tenant_objects.filter.return_value.first.return_value = None

        with self.assertRaisesMessage(
            CommandError,
            "Tenant schema 'missing_school' was not found.",
        ):
            call_command(
                "create_house",
                "--name=Red House",
                schema="missing_school",
                stdout=StringIO(),
            )

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands._guided.call_command")
    @patch("tenancy.management.commands._guided.sys.stdin")
    def test_missing_required_options_open_the_guided_flow(
        self,
        stdin,
        guided_call,
    ):
        stdin.isatty.return_value = True
        tasks = {
            "create_house": "house",
            "create_dormitory": "dormitory",
            "create_room": "room",
            "create_classroom": "classroom",
            "create_programme": "programme",
            "create_academic_year": "academic-year",
            "create_academic_term": "academic-term",
        }
        for command_name, task in tasks.items():
            with self.subTest(command=command_name):
                guided_call.reset_mock()

                call_command(command_name, stdout=StringIO())

                guided_call.assert_called_once()
                self.assertEqual(
                    guided_call.call_args.args,
                    ("tenant_wizard", f"--task={task}"),
                )


@skipUnless(
    not settings.TENANCY_ENABLED,
    "School models live in tenant schemas when TENANCY_ENABLED=True.",
)
@override_settings(TENANCY_ENABLED=False)
class SchoolSetupCommandTests(TestCase):
    def test_create_house_and_reject_duplicate_name(self):
        output = StringIO()

        call_command(
            "create_house",
            name="Red House",
            display_order=2,
            motto="Courage",
            stdout=output,
        )

        house = House.objects.get(name="Red House")
        self.assertEqual(house.display_order, 2)
        self.assertEqual(house.motto, "Courage")
        self.assertIn("Created house 'Red House'.", output.getvalue())

        with self.assertRaisesMessage(CommandError, "already exists"):
            call_command(
                "create_house",
                name="red house",
                stdout=StringIO(),
            )
        self.assertEqual(House.objects.count(), 1)

    def test_create_dormitory_validates_house_and_gender(self):
        house = House.objects.create(name="Blue House")
        output = StringIO()

        call_command(
            "create_dormitory",
            house=house.name,
            name="Block A",
            gender="boys",
            capacity=120,
            stdout=output,
        )

        dormitory = Dormitory.objects.get(name="Block A")
        self.assertEqual(dormitory.house, house)
        self.assertEqual(dormitory.gender, Dormitory.GenderBlock.MALE)
        self.assertEqual(dormitory.capacity, 120)
        self.assertIn("Created dormitory 'Block A'", output.getvalue())

        with self.assertRaisesMessage(CommandError, "Gender must be"):
            call_command(
                "create_dormitory",
                house=house.name,
                name="Block B",
                gender="mixed",
                stdout=StringIO(),
            )

        with self.assertRaisesMessage(CommandError, "already exists"):
            call_command(
                "create_dormitory",
                house=house.name,
                name="block a",
                gender="M",
                stdout=StringIO(),
            )

        with self.assertRaisesMessage(CommandError, "was not found"):
            call_command(
                "create_dormitory",
                house="Missing House",
                name="Block C",
                gender="F",
                stdout=StringIO(),
            )

    def test_create_room_requires_a_dormitory_and_unique_name(self):
        house = House.objects.create(name="Green House")
        dormitory = Dormitory.objects.create(
            house=house,
            name="Block A",
            gender=Dormitory.GenderBlock.FEMALE,
            capacity=50,
        )
        output = StringIO()

        call_command(
            "create_room",
            dormitory=dormitory.name,
            house=house.name,
            name="Room 1",
            capacity=4,
            stdout=output,
        )

        room = DormitoryRoom.objects.get(name="Room 1")
        self.assertEqual(room.dormitory, dormitory)
        self.assertEqual(room.capacity, 4)
        self.assertIn("Created room 'Room 1'", output.getvalue())

        with self.assertRaisesMessage(CommandError, "already exists"):
            call_command(
                "create_room",
                dormitory=dormitory.name,
                name="room 1",
                capacity=6,
                stdout=StringIO(),
            )

        with self.assertRaisesMessage(CommandError, "was not found"):
            call_command(
                "create_room",
                dormitory="Missing Dormitory",
                name="Room 2",
                capacity=4,
                stdout=StringIO(),
            )

    def test_create_classroom_links_a_programme_and_rejects_duplicate_name(self):
        programme = Programme.objects.create(name="General Science", code="GSCI")
        output = StringIO()

        call_command(
            "create_classroom",
            name="1A1",
            programme="GSCI",
            stdout=output,
        )

        classroom = ClassGroup.objects.get(name="1A1")
        self.assertEqual(classroom.programme, programme)
        self.assertIn("Created classroom '1A1'", output.getvalue())

        with self.assertRaisesMessage(CommandError, "already exists"):
            call_command(
                "create_classroom",
                name="1a1",
                stdout=StringIO(),
            )

        with self.assertRaisesMessage(CommandError, "was not found"):
            call_command(
                "create_classroom",
                name="1A2",
                programme="Missing Programme",
                stdout=StringIO(),
            )

    def test_create_classroom_batch_uses_form_programme_code_and_numbers(self):
        programme = Programme.objects.create(name="General Arts", code="A")
        output = StringIO()

        call_command(
            "create_classroom",
            form=1,
            programme="General Arts",
            start=1,
            end=10,
            stdout=output,
        )

        names = set(ClassGroup.objects.values_list("name", flat=True))
        self.assertEqual(names, {f"1A{number}" for number in range(1, 11)})
        self.assertEqual(
            ClassGroup.objects.filter(programme=programme).count(),
            10,
        )
        self.assertIn(
            "Created 10 class groups: 1A1 to 1A10.",
            output.getvalue(),
        )

    def test_create_classroom_single_uses_form_programme_code_and_division(self):
        programme = Programme.objects.create(name="General Science", code="SCI")
        output = StringIO()

        call_command(
            "create_classroom",
            form=2,
            programme="General Science",
            start=3,
            end=3,
            stdout=output,
        )

        classroom = ClassGroup.objects.get(name="2SCI3")
        self.assertEqual(classroom.programme, programme)
        self.assertIn("Created classroom '2SCI3'.", output.getvalue())

    def test_create_classroom_rejects_name_and_form_together(self):
        with self.assertRaisesMessage(CommandError, "not both"):
            call_command(
                "create_classroom",
                name="1A1",
                form=1,
                programme="General Arts",
                start=1,
                end=1,
                stdout=StringIO(),
            )

        self.assertFalse(ClassGroup.objects.exists())

    def test_create_classroom_batch_rejects_duplicates_without_partial_writes(self):
        programme = Programme.objects.create(name="General Arts", code="A")
        ClassGroup.objects.create(name="1A3", programme=programme)

        with self.assertRaisesMessage(CommandError, "already exist: 1A3"):
            call_command(
                "create_classroom",
                form=1,
                programme="General Arts",
                start=1,
                end=10,
                stdout=StringIO(),
            )

        self.assertEqual(ClassGroup.objects.count(), 1)

    def test_create_classroom_batch_requires_a_programme_code(self):
        Programme.objects.create(name="General Arts")

        with self.assertRaisesMessage(CommandError, "no short code"):
            call_command(
                "create_classroom",
                form=1,
                programme="General Arts",
                start=1,
                end=10,
                stdout=StringIO(),
            )

        self.assertFalse(ClassGroup.objects.exists())

    def test_create_classroom_batch_requires_all_range_options(self):
        with self.assertRaisesMessage(CommandError, "requires --programme"):
            call_command(
                "create_classroom",
                form=1,
                stdout=StringIO(),
            )

        self.assertFalse(ClassGroup.objects.exists())

    def test_create_programme_rejects_duplicate_name_and_code(self):
        output = StringIO()

        call_command(
            "create_programme",
            name="General Arts",
            code="GART",
            stdout=output,
        )

        programme = Programme.objects.get(name="General Arts")
        self.assertEqual(programme.code, "GART")
        self.assertIn("Created programme 'General Arts' (GART).", output.getvalue())

        with self.assertRaisesMessage(CommandError, "named 'general arts'"):
            call_command(
                "create_programme",
                name="general arts",
                stdout=StringIO(),
            )

        with self.assertRaisesMessage(CommandError, "code 'gart'"):
            call_command(
                "create_programme",
                name="Business",
                code="gart",
                stdout=StringIO(),
            )

    def test_create_academic_year_and_term(self):
        year_output = StringIO()

        call_command(
            "create_academic_year",
            start_date="2025-09-01",
            end_date="2026-07-01",
            current=True,
            stdout=year_output,
        )

        year = AcademicYear.objects.get(name="2025/2026 Academic Year")
        self.assertTrue(year.is_current)
        self.assertIn("Created academic year", year_output.getvalue())

        with self.assertRaisesMessage(CommandError, "already exists"):
            call_command(
                "create_academic_year",
                start_date="2025-09-01",
                end_date="2026-07-01",
                stdout=StringIO(),
            )

        with self.assertRaisesMessage(CommandError, "must use YYYY-MM-DD"):
            call_command(
                "create_academic_year",
                start_date="01/09/2025",
                end_date="2026-07-01",
                stdout=StringIO(),
            )

        term_output = StringIO()
        call_command(
            "create_academic_term",
            academic_year=year.name,
            term="Term 1",
            level="SHS1",
            start_date="2025-09-01",
            end_date="2025-12-20",
            current=True,
            stdout=term_output,
        )

        term = AcademicTerm.objects.get(academic_year=year, level="SHS1")
        self.assertEqual(term.term, AcademicTerm.TermChoice.TERM_1)
        self.assertTrue(term.is_current)
        self.assertIn("Created academic term", term_output.getvalue())

        with self.assertRaisesMessage(CommandError, "already has that term"):
            call_command(
                "create_academic_term",
                academic_year=year.name,
                term="1",
                level="SHS1",
                start_date="2026-01-05",
                end_date="2026-04-01",
                stdout=StringIO(),
            )

    def test_list_commands_show_configured_records(self):
        House.objects.create(name="Red House", display_order=1)
        output = StringIO()

        call_command("create_house", list=True, stdout=output)

        self.assertIn("Houses:", output.getvalue())
        self.assertIn("Red House (order 1)", output.getvalue())
