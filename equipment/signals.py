from django.db.models import DecimalField, Sum
from django.db.models.functions import Coalesce
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Equipment, EquipmentUsage, MaintenanceLog
from .services import (sync_equipment_fuel_expense,
                       sync_equipment_purchase_expense,
                       sync_maintenance_expense)


def _sync_hours(equipment_id):
    """Keep Equipment.hours_used = opening_hours + sum of usage records."""
    equipment = Equipment.objects.filter(pk=equipment_id).first()
    if equipment is None:
        return
    total = EquipmentUsage.objects.filter(equipment_id=equipment_id).aggregate(
        t=Coalesce(Sum('hours'), 0, output_field=DecimalField()))['t']
    Equipment.objects.filter(pk=equipment_id).update(
        hours_used=equipment.opening_hours + total)


@receiver(post_save, sender=EquipmentUsage)
def usage_saved(sender, instance, **kwargs):
    _sync_hours(instance.equipment_id)
    sync_equipment_fuel_expense(instance)


@receiver(post_delete, sender=EquipmentUsage)
def usage_deleted(sender, instance, **kwargs):
    _sync_hours(instance.equipment_id)


@receiver(post_save, sender=Equipment)
def equipment_saved(sender, instance, **kwargs):
    """Resync meter hours (opening_hours edits) and the purchase expense."""
    _sync_hours(instance.pk)
    sync_equipment_purchase_expense(instance)


@receiver(post_save, sender=MaintenanceLog)
def maintenance_saved(sender, instance, **kwargs):
    sync_maintenance_expense(instance)


@receiver(post_delete, sender=MaintenanceLog)
def maintenance_deleted(sender, instance, **kwargs):
    # The linked expense is removed by CASCADE via Expense.maintenance_log,
    # but delete explicitly in case the FK predates the constraint.
    from expenses.models import Expense
    Expense.objects.filter(maintenance_log_id=instance.pk).delete()
