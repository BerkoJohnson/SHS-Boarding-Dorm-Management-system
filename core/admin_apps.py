from django.contrib.admin.apps import AdminConfig


class InstitutionalAdminConfig(AdminConfig):
    default_site = "core.admin_site.InstitutionalAdminSite"
