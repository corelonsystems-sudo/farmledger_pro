from threading import local

from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import AuditLog

# Populated by audit.middleware.AuditMiddleware for the duration of a request.
_thread_locals = local()


def _get_audit_user():
    return getattr(_thread_locals, 'audit_user', None)


def _get_audit_ip():
    return getattr(_thread_locals, 'audit_ip', None)


def _create_audit_entry(instance, action_type, changed_fields=None):
    """Create an AuditLog entry for the given instance."""
    content_type = ContentType.objects.get_for_model(instance.__class__)
    AuditLog.objects.create(
        content_type=content_type,
        object_id=instance.pk,
        action_type=action_type,
        changed_fields=changed_fields or {},
        user=_get_audit_user(),
        ip_address=_get_audit_ip(),
    )


def register_audit_signals(sender):
    """Register post_save and post_delete signals for a model class."""
    post_save.connect(_audit_post_save, sender=sender, weak=False)
    post_delete.connect(_audit_post_delete, sender=sender, weak=False)


def _audit_post_save(sender, instance, created, **kwargs):
    _create_audit_entry(
        instance, AuditLog.ActionType.CREATE if created else AuditLog.ActionType.UPDATE)


def _audit_post_delete(sender, instance, **kwargs):
    _create_audit_entry(instance, AuditLog.ActionType.DELETE)
