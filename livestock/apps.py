from django.apps import AppConfig


class LivestockConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'livestock'

    def ready(self):
        from . import signals  # noqa: F401
