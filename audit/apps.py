from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'audit'

    def ready(self):
        from .mixin import register_audit_signals
        from expenses.models import Expense
        register_audit_signals(Expense)
