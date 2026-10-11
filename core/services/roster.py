import re

from core.models import Dormitory, Student


def normalize_roster_house_name(house_value: str) -> str:
    """Normalize colored, numbered roster houses to their configured name."""
    house_name = house_value.strip()
    composite_match = re.fullmatch(
        r"(?P<color>.+?)\s+House\s*\([MF]\)\s*,\s*(?P<number>[A-Z0-9]+)",
        house_name,
        re.IGNORECASE,
    )
    if composite_match:
        color = composite_match.group("color").strip().title()
        number = composite_match.group("number").strip().title()
        return f"House {number} ({color})"

    gender_match = re.search(r"\s*\(([MF])\)\s*$", house_name, re.IGNORECASE)
    if gender_match:
        house_name = house_name[:gender_match.start()].strip()
    return house_name


def get_roster_house_identity(house_value: str, student_gender: str) -> tuple[str, str]:
    """Return the configured house name and dormitory gender for a roster row."""
    house_name = normalize_roster_house_name(house_value)

    normalized_gender = student_gender.strip().lower()
    if normalized_gender == Student.Gender.MALE:
        dormitory_gender = Dormitory.GenderBlock.MALE
    elif normalized_gender == Student.Gender.FEMALE:
        dormitory_gender = Dormitory.GenderBlock.FEMALE
    else:
        raise ValueError(f"Unsupported student gender in roster: {student_gender!r}")

    return house_name, dormitory_gender
