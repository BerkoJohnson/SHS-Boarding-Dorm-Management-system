from django.contrib.admin import AdminSite

from core.models import get_user_profile
from core.permissions import ADMIN_ROLES


class InstitutionalAdminSite(AdminSite):
    def has_permission(self, request):
        if not super().has_permission(request):
            return False
        if request.user.is_superuser:
            return True
        profile = get_user_profile(request.user)
        return bool(profile and profile.role in ADMIN_ROLES)
