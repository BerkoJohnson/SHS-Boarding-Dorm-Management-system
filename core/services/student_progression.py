from django.db import transaction
from django.utils import timezone

from core.models import (
    AcademicYear,
    ClassGroup,
    Student,
    StudentProgressionRecord,
)


def class_group_level(class_group):
    name = (class_group.name or "").strip()
    if name and name[0] in {"1", "2", "3"}:
        return int(name[0])
    return None


def promote_student_cohort(academic_year, source_class, target_class, actor):
    source_level = class_group_level(source_class)
    target_level = class_group_level(target_class)
    if source_level not in {1, 2} or target_level != source_level + 1:
        raise ValueError("Choose consecutive SHS classes, such as SHS 1 to SHS 2.")
    if academic_year.end_date < timezone.localdate():
        raise ValueError(
            "Students cannot be promoted into an academic year that has ended."
        )
    if academic_year.students_phased_out_at is not None:
        raise ValueError(
            "This academic year has already completed its student progression."
        )

    with transaction.atomic():
        year = AcademicYear.objects.select_for_update().get(pk=academic_year.pk)
        students = list(
            Student.objects.select_for_update()
            .filter(
                class_group=source_class,
                status=Student.EnrollmentStatus.ENROLLED,
            )
            .order_by("pk")
        )
        if not students:
            raise ValueError("There are no enrolled students in the selected class.")
        if target_class.programme_id and any(
            student.programme_id != target_class.programme_id for student in students
        ):
            raise ValueError(
                "The target class programme does not match every student in this cohort."
            )

        for student in students:
            StudentProgressionRecord.objects.create(
                student=student,
                academic_year=year,
                event=StudentProgressionRecord.Event.PROMOTED,
                from_class_group=source_class,
                to_class_group=target_class,
                effective_on=year.start_date,
                actor=actor,
            )
            student.class_group = target_class
            student.expected_completion_year = year if target_level == 3 else None
            student.save(
                update_fields=[
                    "class_group",
                    "expected_completion_year",
                    "updated_at",
                ]
            )

    return len(students)
