import re

from core.models import Dormitory, Student


def get_roster_house_identity(house_value: str, student_gender: str) -> tuple[str, str]:
    """Return the configured house name and dormitory gender for a roster row."""
    house_name = house_value.strip()
    gender_match = re.search(r"\s*\(([MF])\)\s*$", house_name, re.IGNORECASE)
    if gender_match:
        house_name = house_name[:gender_match.start()].strip()

    normalized_gender = student_gender.strip().lower()
    if normalized_gender == Student.Gender.MALE:
        dormitory_gender = Dormitory.GenderBlock.MALE
    elif normalized_gender == Student.Gender.FEMALE:
        dormitory_gender = Dormitory.GenderBlock.FEMALE
    else:
        raise ValueError(f"Unsupported student gender in roster: {student_gender!r}")

    return house_name, dormitory_gender
