from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import AuditLog


class AuditableMixin:
    """Reusable mixin that any model can inherit to automatically register
    post_save and post_delete signals that write to the audit log.

    Usage:
        class MyModel(AuditableMixin, models.Model):
            ...
    """

    def _get_audit_user(self):
        """Try to get the current user from thread-local storage if available."""
        try:
            from threading import local
            _thread_locals = local()
            user = getattr(_thread_locals, 'audit_user', None)
            return user
        except Exception:
            return None

    def _get_audit_ip(self):
        try:
            from threading import local
            _thread_locals = local()
            return getattr(_thread_locals, 'audit_ip', None)
        except Exception:
            return None


def _create_audit_entry(instance, action_type, changed_fields=None):
    """Create an AuditLog entry for the given instance."""
    content_type = ContentType.objects.get_for_model(instance.__class__)
    user = None
    ip_address = None

    if hasattr(instance, '_get_audit_user'):
        user = instance._get_audit_user()
    if hasattr(instance, '_get_audit_ip'):
        ip_address = instance._get_audit_ip()

    AuditLog.objects.create(
        content_type=content_type,
        object_id=instance.pk,
        action_type=action_type,
        changed_fields=changed_fields or {},
        user=user,
        ip_address=ip_address,
    )


def register_audit_signals(sender):
    """Register post_save and post_delete signals for a model class.

    Call this in the model's Meta or use the mixin's __init_subclass__.
    """
    post_save.connect(
        _audit_post_save,
        sender=sender,
        weak=False,
    )
    post_delete.connect(
        _audit_post_delete,
        sender=sender,
        weak=False,
    )


def _audit_post_save(sender, instance, created, **kwargs):
    if created:
        _create_audit_entry(instance, AuditLog.ActionType.CREATE)
    else:
        _create_audit_entry(instance, AuditLog.ActionType.UPDATE)


def _audit_post_delete(sender, instance, **kwargs):
    _create_audit_entry(instance, AuditLog.ActionType.DELETE)
