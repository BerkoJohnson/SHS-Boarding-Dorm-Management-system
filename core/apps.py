from functools import wraps

from django.apps import AppConfig
from django.conf import settings


class CoreConfig(AppConfig):
    name = 'core'

    def ready(self):
        _install_staff_quota_guard()


def _install_staff_quota_guard():
    """Run staff-account saves inside the tenant staff-quota lock.

    Django sends ``pre_save`` *before* it opens any transaction, and a
    parentless model such as the default ``User`` is saved with only
    ``mark_for_rollback_on_error`` (no transaction). A ``pre_save`` receiver
    therefore cannot hold a transaction-scoped advisory lock across the
    ``INSERT`` it is validating.

    Wrapping ``User.save`` guarantees the quota check and the write share one
    transaction and lock for *every* creation path -- the Django admin,
    ``manage.py createsuperuser``, and direct ``User.objects.create_user``
    calls alike.
    """
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    if getattr(user_model, "_staff_quota_guard_installed", False):
        return

    original_save = user_model.save

    @wraps(original_save)
    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        # Only genuine new-staff creations or promotions need the lock; targeted
        # updates such as ``last_login`` must not pay for it.
        if (
            settings.TENANCY_ENABLED
            and self.is_staff
            and (
                self._state.adding
                or update_fields is None
                or "is_staff" in update_fields
            )
        ):
            from tenancy.entitlements import quota_guard

            with quota_guard("max_staff"):
                return original_save(self, *args, **kwargs)
        return original_save(self, *args, **kwargs)

    save.alters_data = True
    user_model.save = save
    user_model._staff_quota_guard_installed = True
