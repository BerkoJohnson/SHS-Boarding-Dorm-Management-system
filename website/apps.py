from django.apps import AppConfig


class WebsiteConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "website"
    verbose_name = "Public Website"

    def ready(self):
        # Registers the domain-hygiene checks in website/checks.py.
        from website import checks  # noqa: F401
