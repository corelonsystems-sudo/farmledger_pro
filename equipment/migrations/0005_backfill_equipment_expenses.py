"""Backfill auto-generated expenses for existing maintenance logs and
equipment purchases so historical equipment spend reaches the ledger.

Also preserves manually-entered hours: ``opening_hours`` is set to any
``hours_used`` in excess of logged usage so the displayed total is
unchanged after the new sync rule (hours_used = opening + usage).

Historical models only — no live-model imports.
"""

from decimal import Decimal

from django.db import migrations
from django.db.models import DecimalField, Sum
from django.db.models.functions import Coalesce


def _category(apps, code, name, sched_code, sched_name):
    Category = apps.get_model('expenses', 'Category')
    ScheduleLine = apps.get_model('expenses', 'ScheduleLine')
    sched, _ = ScheduleLine.objects.get_or_create(
        code=sched_code, defaults={'name': sched_name})
    category, _ = Category.objects.get_or_create(
        code=code, defaults={
            'name': name, 'schedule_f_line': sched, 'description': ''})
    return category


def backfill(apps, schema_editor):
    Equipment = apps.get_model('equipment', 'Equipment')
    MaintenanceLog = apps.get_model('equipment', 'MaintenanceLog')
    EquipmentUsage = apps.get_model('equipment', 'EquipmentUsage')
    Expense = apps.get_model('expenses', 'Expense')

    maint_cat = _category(
        apps, 'EQMAINT', 'Equipment Maintenance',
        'part1_repairs_maintenance', 'Repairs and maintenance')
    purch_cat = _category(
        apps, 'EQPURCH', 'Equipment Purchase',
        'part1_depreciation', 'Depreciation')

    for eq in Equipment.objects.all():
        usage_total = EquipmentUsage.objects.filter(equipment=eq).aggregate(
            t=Coalesce(Sum('hours'), 0, output_field=DecimalField()))['t']
        opening = (eq.hours_used or Decimal('0')) - usage_total
        if opening > 0:
            Equipment.objects.filter(pk=eq.pk).update(opening_hours=opening)
        if eq.purchase_cost and eq.purchase_cost > 0 and not Expense.objects.filter(
                equipment_purchase=eq).exists():
            Expense.objects.create(
                farm_profile=eq.farm_profile, category=purch_cat,
                equipment=eq, amount=eq.purchase_cost,
                date=eq.purchase_date or eq.created_at.date(),
                notes=f'Purchase: {eq.name}', equipment_purchase=eq)

    for log in MaintenanceLog.objects.select_related('equipment').all():
        if log.cost and log.cost > 0 and not Expense.objects.filter(
                maintenance_log=log).exists():
            Expense.objects.create(
                farm_profile=log.equipment.farm_profile, category=maint_cat,
                equipment=log.equipment, amount=log.cost, date=log.date,
                vendor=log.performed_by,
                notes=f'Maintenance on {log.equipment.name}: {log.description[:200]}',
                maintenance_log=log)


def unbackfill(apps, schema_editor):
    Expense = apps.get_model('expenses', 'Expense')
    Expense.objects.exclude(
        maintenance_log__isnull=True).filter(maintenance_log__isnull=False).delete()
    Expense.objects.exclude(
        equipment_purchase__isnull=True).filter(equipment_purchase__isnull=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('equipment', '0004_equipment_opening_hours_equipment_ownership_and_more'),
        ('expenses', '0014_expense_equipment_purchase_expense_maintenance_log'),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
