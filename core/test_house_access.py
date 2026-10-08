from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import Dormitory, House, HouseMaster, StaffRole, UserProfile


User = get_user_model()


class HouseOverviewAccessTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.house = House.objects.create(
            name="Northfield House",
            number=House.HouseNumber.ONE,
        )
        Dormitory.objects.create(
            house=cls.house,
            name="Northfield Block",
            gender=Dormitory.GenderBlock.MALE,
            capacity=40,
        )

        cls.superuser = User.objects.create_superuser(
            username="housing-superuser",
            password="secure-test-password",
        )
        cls.admin_user = cls._create_role_user("housing-admin", StaffRole.ADMIN)
        cls.senior_user = cls._create_role_user(
            "housing-senior",
            StaffRole.SENIOR_HOUSEMASTER,
        )
        cls.housemaster_user = cls._create_role_user(
            "housing-housemaster",
            StaffRole.HOUSEMASTER,
        )
        HouseMaster.objects.create(
            user=cls.housemaster_user,
            full_name="Housing Housemaster",
            staff_id="HOUSE-ACCESS-1",
            phone_number="+233200000000",
            house=cls.house,
        )
        cls.tutor_user = cls._create_role_user(
            "housing-tutor",
            StaffRole.HOUSE_TUTOR,
        )
        cls.gate_user = cls._create_role_user(
            "housing-gate",
            StaffRole.GATE_OFFICER,
        )

    @classmethod
    def _create_role_user(cls, username, role):
        user = User.objects.create_user(
            username=username,
            password="secure-test-password",
        )
        UserProfile.objects.update_or_create(user=user, defaults={"role": role})
        return user

    def test_superusers_and_admin_roles_can_view_house_overview(self):
        for user in (self.superuser, self.admin_user, self.senior_user):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("core:house_list"))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Northfield House")

    def test_other_staff_roles_cannot_open_house_overview(self):
        for user in (
            self.housemaster_user,
            self.tutor_user,
            self.gate_user,
        ):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("core:house_list"))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], reverse("core:dashboard"))

    def test_housemaster_does_not_see_house_navigation_or_search_results(self):
        self.client.force_login(self.housemaster_user)

        dashboard_response = self.client.get(reverse("core:dashboard"))
        search_response = self.client.get(
            reverse("core:global_search"),
            {"q": "Northfield"},
        )

        self.assertNotContains(
            dashboard_response,
            reverse("core:house_list"),
        )
        self.assertNotContains(search_response, "Northfield House")
        self.assertNotContains(search_response, "Houses &amp; Dormitories")

    def test_admin_role_sees_house_navigation_and_search_results(self):
        self.client.force_login(self.admin_user)

        dashboard_response = self.client.get(reverse("core:dashboard"))
        search_response = self.client.get(
            reverse("core:global_search"),
            {"q": "Northfield"},
        )

        self.assertContains(dashboard_response, reverse("core:house_list"))
        self.assertContains(search_response, "Northfield House")
