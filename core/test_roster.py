from django.test import SimpleTestCase

from core.models import Dormitory
from core.services.roster import (
    get_roster_house_identity,
    normalize_roster_house_name,
)


class RosterHouseIdentityTests(SimpleTestCase):
    def test_gendered_house_suffixes_resolve_to_one_house_name(self):
        male_identity = get_roster_house_identity(
            "Kwame Nkrumah House (M)",
            "male",
        )
        female_identity = get_roster_house_identity(
            "Kwame Nkrumah House (F)",
            "female",
        )

        self.assertEqual(male_identity, (
            "Kwame Nkrumah House",
            Dormitory.GenderBlock.MALE,
        ))
        self.assertEqual(female_identity, (
            "Kwame Nkrumah House",
            Dormitory.GenderBlock.FEMALE,
        ))

    def test_house_names_without_numeric_identity_are_preserved(self):
        self.assertEqual(
            get_roster_house_identity("Akwapim House", "male"),
            ("Akwapim House", Dormitory.GenderBlock.MALE),
        )

    def test_colored_numbered_house_is_normalized_to_canonical_name(self):
        self.assertEqual(
            normalize_roster_house_name("Blue House(M), TWO"),
            "House Two (Blue)",
        )
        self.assertEqual(
            normalize_roster_house_name("RED HOUSE(F), ONE"),
            "House One (Red)",
        )

    def test_unsupported_student_gender_is_rejected(self):
        with self.assertRaises(ValueError):
            get_roster_house_identity("Akwapim House", "unknown")
