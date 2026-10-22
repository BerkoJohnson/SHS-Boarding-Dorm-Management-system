"""Single definition of an active student and active boarder.

The audit found the house overview and housing report using different
populations (F-14).  All current-occupancy views, capacity checks, roll-call
rosters, and operational counts must use these helpers.  Historical views can
still query ``Student`` directly.
"""

from core.models import Student

ACTIVE_ENROLLMENT_STATUSES = (
    Student.EnrollmentStatus.ENROLLED,
    Student.EnrollmentStatus.APPROVED,
    Student.EnrollmentStatus.IN_PROGRESS,
)


def active_students():
    """Students who have not completed/left the school."""
    return Student.objects.filter(status__in=ACTIVE_ENROLLMENT_STATUSES)


def active_boarders():
    """Current boarding population, including approved/in-progress admissions."""
    return active_students().filter(residency=Student.ResidencyStatus.BOARDING)


def active_boarders_for_dormitory(dormitory):
    return active_boarders().filter(dormitory=dormitory)


def active_boarders_for_house(house):
    return active_boarders().filter(house=house)
