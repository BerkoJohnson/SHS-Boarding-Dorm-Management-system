from contextlib import nullcontext
import os

from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from core.models import (
    ClassGroup,
    DisciplinaryRecord,
    Dormitory,
    DormitoryRoom,
    Exeat,
    ExeatMovement,
    Guardian,
    House,
    HouseMaster,
    Programme,
    RollCallAbsenceFollowUp,
    RollCallNotification,
    RollCallSession,
    SMSConfiguration,
    SMSDelivery,
    StaffRole,
    Student,
    StudentIDExport,
    UserProfile,
)


class Command(BaseCommand):
    help = "Plan or create an isolated 100-student synthetic pilot dataset."

    houses_to_create = 4
    students_to_create = 100

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            default=None,
            help="Tenant schema name to seed into (required when TENANCY_ENABLED=True).",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Create the dataset; without this flag, only print the plan.",
        )
        parser.add_argument(
            "--confirm-empty-staging",
            action="store_true",
            help="Confirm this is a dedicated empty staging database.",
        )

    def handle(self, *args, **options):
        if not options["apply"]:
            self.stdout.write(
                "Dry run only. This command would create 100 fictional students "
                "(80 boarders, 20 day students), 4 houses, 8 dormitories, "
                "16 rooms, 3 programmes, 6 class groups, and disabled test "
                "staff accounts. No guardian contacts, photos, exeats, or "
                "attendance sessions are created."
            )
            self.stdout.write(
                "To apply, set SYNTHETIC_PILOT_DATABASE to the exact dedicated "
                "staging database name, disable SMS, and rerun with "
                "--apply --confirm-empty-staging."
            )
            return

        schema = options.get("schema")
        if django_settings.TENANCY_ENABLED and not schema:
            raise CommandError(
                "TENANCY_ENABLED is True; pass --schema to specify the "
                "target tenant schema."
            )

        if not options["confirm_empty_staging"]:
            raise CommandError(
                "Refusing to seed without --confirm-empty-staging."
            )

        # Activate the tenant schema if specified.
        if schema:
            from tenancy.models import Tenant
            tenant = Tenant.objects.filter(schema_name=schema).first()
            if tenant is None:
                raise CommandError(f"Tenant schema '{schema}' was not found.")
            context = tenant
        else:
            context = nullcontext()

        with context:
            expected_database = os.getenv("SYNTHETIC_PILOT_DATABASE", "").strip()
            if not expected_database or expected_database != connection.settings_dict["NAME"]:
                raise CommandError(
                    "SYNTHETIC_PILOT_DATABASE must exactly match the configured "
                    "dedicated staging database name."
                )

            if SMSConfiguration.is_enabled():
                raise CommandError(
                    "SMS is enabled. Disable SMS before creating synthetic pilot data."
                )

            occupied_tables = self._occupied_school_tables()
            if occupied_tables:
                table_summary = ", ".join(
                    f"{model.__name__}={count}"
                    for model, count in occupied_tables
                )
                raise CommandError(
                    "Refusing to mix synthetic fixtures with existing school records: "
                    f"{table_summary}. Use a fresh, isolated staging database."
                )

            user_model = get_user_model()
            if not user_model.objects.filter(
                is_superuser=True,
                is_active=True,
            ).exists():
                raise CommandError(
                    "Create an active staging superuser before seeding so an "
                    "administrator can manage the disabled pilot accounts."
                )

            with transaction.atomic():
                self._create_dataset()

            self.stdout.write(
                self.style.SUCCESS(
                    "Synthetic pilot dataset created. Test accounts are inactive "
                    "and have unusable passwords; have an administrator set "
                    "individual credentials before supervised testing."
                )
            )

    @staticmethod
    def _occupied_school_tables():
        models_to_check = (
            ClassGroup,
            DisciplinaryRecord,
            Dormitory,
            DormitoryRoom,
            Exeat,
            ExeatMovement,
            Guardian,
            House,
            HouseMaster,
            Programme,
            RollCallAbsenceFollowUp,
            RollCallNotification,
            RollCallSession,
            SMSDelivery,
            Student,
            StudentIDExport,
            UserProfile,
        )
        occupied = []
        for model in models_to_check:
            queryset = model.objects.all()
            if model is UserProfile:
                queryset = queryset.exclude(user__is_superuser=True)
            count = queryset.count()
            if count:
                occupied.append((model, count))
        return occupied

    @staticmethod
    def _create_test_user(username, role):
        user_model = get_user_model()
        user = user_model(
            username=username,
            is_active=False,
            is_staff=False,
            is_superuser=False,
        )
        user.set_unusable_password()
        user.save()
        UserProfile.objects.update_or_create(
            user=user,
            defaults={"role": role},
        )
        return user

    def _create_dataset(self):
        programmes = [
            Programme.objects.create(name=f"PILOT Programme {letter}")
            for letter in ("A", "B", "C")
        ]
        class_groups = [
            ClassGroup.objects.create(
                name=f"PILOT SHS{level}-{letter}",
                programme=programme,
            )
            for level in (1, 2)
            for letter, programme in zip(("A", "B", "C"), programmes)
        ]
        houses = [
            House.objects.create(
                name=f"PILOT House {number}",
                display_order=number,
            )
            for number in range(1, self.houses_to_create + 1)
        ]

        rooms_by_dormitory = {}
        for house_number, house in enumerate(houses, start=1):
            for gender_code, gender_label in (
                (Dormitory.GenderBlock.MALE, "Boys"),
                (Dormitory.GenderBlock.FEMALE, "Girls"),
            ):
                dormitory = Dormitory.objects.create(
                    house=house,
                    name=f"PILOT {gender_label} Block",
                    gender=gender_code,
                    capacity=10,
                )
                rooms_by_dormitory[dormitory.pk] = [
                    DormitoryRoom.objects.create(
                        dormitory=dormitory,
                        name=f"PILOT Room {room_number}",
                        capacity=5,
                    )
                    for room_number in (1, 2)
                ]

            housemaster_user = self._create_test_user(
                f"pilot-housemaster-{house_number}",
                StaffRole.HOUSEMASTER,
            )
            HouseMaster.objects.create(
                user=housemaster_user,
                full_name=f"Synthetic Housemaster {house_number}",
                staff_id=f"PILOT-HM-{house_number:02d}",
                phone_number="000000000000000",
                house=house,
            )
            tutor_user = self._create_test_user(
                f"pilot-house-tutor-{house_number}",
                StaffRole.HOUSE_TUTOR,
            )
            HouseMaster.objects.create(
                user=tutor_user,
                full_name=f"Synthetic House Tutor {house_number}",
                staff_id=f"PILOT-TUTOR-{house_number:02d}",
                phone_number="000000000000000",
                house=house,
            )

        senior_user = self._create_test_user(
            "pilot-senior-housemaster",
            StaffRole.SENIOR_HOUSEMASTER,
        )
        HouseMaster.objects.create(
            user=senior_user,
            full_name="Synthetic Senior Housemaster",
            staff_id="PILOT-SENIOR",
            phone_number="000000000000000",
            is_senior_housemaster=True,
        )
        self._create_test_user("pilot-gate-officer", StaffRole.GATE_OFFICER)

        students = []
        student_number = 0
        for house in houses:
            for gender_code, gender in (
                (Dormitory.GenderBlock.MALE, Student.Gender.MALE),
                (Dormitory.GenderBlock.FEMALE, Student.Gender.FEMALE),
            ):
                dormitory = Dormitory.objects.get(house=house, gender=gender_code)
                rooms = rooms_by_dormitory[dormitory.pk]
                for _ in range(10):
                    student_number += 1
                    students.append(
                        self._build_student(
                            student_number=student_number,
                            house=house,
                            dormitory=dormitory,
                            room=rooms[((student_number - 1) // 5) % len(rooms)],
                            gender=gender,
                            residency=Student.ResidencyStatus.BOARDING,
                            programmes=programmes,
                            class_groups=class_groups,
                        )
                    )

            for day_student_index in range(5):
                student_number += 1
                students.append(
                    self._build_student(
                        student_number=student_number,
                        house=house,
                        dormitory=None,
                        room=None,
                        gender=(
                            Student.Gender.MALE
                            if day_student_index % 2 == 0
                            else Student.Gender.FEMALE
                        ),
                        residency=Student.ResidencyStatus.DAY,
                        programmes=programmes,
                        class_groups=class_groups,
                    )
                )
        Student.objects.bulk_create(students, batch_size=100)

    @staticmethod
    def _build_student(
        *,
        student_number,
        house,
        dormitory,
        room,
        gender,
        residency,
        programmes,
        class_groups,
    ):
        programme_index = (student_number - 1) % len(programmes)
        class_group_index = (student_number - 1) % len(class_groups)
        status = (
            Student.EnrollmentStatus.APPROVED
            if student_number % 20 == 0
            else Student.EnrollmentStatus.ENROLLED
        )
        return Student(
            admission_no=f"PILOT-2026-{student_number:04d}",
            index_number=f"PILOT-ID-{student_number:04d}",
            full_name=f"Synthetic Learner {student_number:04d}",
            gender=gender,
            programme=programmes[programme_index],
            house=house,
            dormitory=dormitory,
            room=room,
            class_group=class_groups[class_group_index],
            residency=residency,
            status=status,
        )
