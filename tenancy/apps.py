from django.apps import AppConfig


class TenancyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenancy"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from tenancy.models import Domain, Tenant

        def invalidate_csrf_domain_cache(sender, **kwargs):
            """Clear DynamicCsrfMiddleware's in-process domain cache whenever
            a Domain or Tenant record is created, updated, or deleted."""
            from tenancy.middleware import DynamicCsrfMiddleware

            DynamicCsrfMiddleware.invalidate_cache()

        post_save.connect(
            invalidate_csrf_domain_cache,
            sender=Domain,
            dispatch_uid="csrf_cache_domain_save",
        )
        post_delete.connect(
            invalidate_csrf_domain_cache,
            sender=Domain,
            dispatch_uid="csrf_cache_domain_delete",
        )
        post_save.connect(
            invalidate_csrf_domain_cache,
            sender=Tenant,
            dispatch_uid="csrf_cache_tenant_save",
        )
        post_delete.connect(
            invalidate_csrf_domain_cache,
            sender=Tenant,
            dispatch_uid="csrf_cache_tenant_delete",
        )
