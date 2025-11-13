from django.apps import AppConfig


class WeldsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "welds"

    def ready(self):
        # Import signal handlers
        from . import signals  # noqa: F401
