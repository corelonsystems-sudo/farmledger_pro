from django.db.models import DecimalField, Sum
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Equipment, EquipmentUsage


def _sync_hours(equipment_id):
    """Keep Equipment.hours_used equal to the sum of its usage records."""
    total = EquipmentUsage.objects.filter(equipment_id=equipment_id).aggregate(
        t=Coalesce(Sum('hours'), 0, output_field=DecimalField()))['t']
    Equipment.objects.filter(pk=equipment_id).update(hours_used=total)


@receiver(post_save, sender=EquipmentUsage)
def usage_saved(sender, instance, **kwargs):
    _sync_hours(instance.equipment_id)


@receiver(post_delete, sender=EquipmentUsage)
def usage_deleted(sender, instance, **kwargs):
    _sync_hours(instance.equipment_id)
