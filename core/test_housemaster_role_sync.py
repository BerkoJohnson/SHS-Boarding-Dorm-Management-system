from django.contrib.auth import get_user_model
from django.test import TestCase

from core.models import House, HouseMaster, StaffRole, UserProfile


User = get_user_model()


class HouseMasterRoleSyncTests(TestCase):
    """A deleted HouseMaster must not leave an elevated profile role behind.

    ``HouseMaster.save`` keeps ``UserProfile.role`` in sync while the record
    exists, but deleting the record used to leave ``HOUSEMASTER`` /
    ``SENIOR_HOUSEMASTER`` in place.  Because ``SENIOR_HOUSEMASTER`` grants
    school-wide scope, that retention was a privilege-escalation risk.
    """

    @classmethod
    def setUpTestData(cls):
        cls.house = House.objects.create(name="Role Sync House")

    def _user_with_role(self, username, role):
        user = User.objects.create_user(
            username=username,
            password="secure-test-password",
        )
        UserProfile.objects.update_or_create(user=user, defaults={"role": role})
        return user

    def test_deleting_a_senior_housemaster_demotes_the_profile(self):
        user = self._user_with_role("senior-hm", StaffRole.UNASSIGNED)
        housemaster = HouseMaster.objects.create(
            user=user,
            full_name="Senior Housemaster",
            staff_id="ROLE-SYNC-SENIOR",
            phone_number="+233200000001",
            is_senior_housemaster=True,
        )
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.role, StaffRole.SENIOR_HOUSEMASTER)

        housemaster.delete()

        user.profile.refresh_from_db()
        self.assertEqual(user.profile.role, StaffRole.UNASSIGNED)

    def test_deleting_a_housemaster_demotes_the_profile(self):
        user = self._user_with_role("regular-hm", StaffRole.UNASSIGNED)
        housemaster = HouseMaster.objects.create(
            user=user,
            full_name="Regular Housemaster",
            staff_id="ROLE-SYNC-REGULAR",
            phone_number="+233200000002",
            house=self.house,
        )
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.role, StaffRole.HOUSEMASTER)

        housemaster.delete()

        user.profile.refresh_from_db()
        self.assertEqual(user.profile.role, StaffRole.UNASSIGNED)

    def test_deleting_an_unlinked_housemaster_is_a_no_op(self):
        # No user attached: deletion must not raise or touch any profile.
        housemaster = HouseMaster.objects.create(
            full_name="Unlinked Housemaster",
            staff_id="ROLE-SYNC-UNLINKED",
            phone_number="+233200000003",
            house=self.house,
        )
        housemaster.delete()  # must not raise

    def test_deleting_a_housemaster_preserves_unrelated_roles(self):
        # A Gate Officer linked to a HouseMaster record keeps that role.
        user = self._user_with_role("gate-officer", StaffRole.GATE_OFFICER)
        housemaster = HouseMaster.objects.create(
            user=user,
            full_name="Gate Officer",
            staff_id="ROLE-SYNC-GATE",
            phone_number="+233200000004",
            house=self.house,
        )
        user.profile.refresh_from_db()
        self.assertEqual(user.profile.role, StaffRole.GATE_OFFICER)

        housemaster.delete()

        user.profile.refresh_from_db()
        self.assertEqual(user.profile.role, StaffRole.GATE_OFFICER)
