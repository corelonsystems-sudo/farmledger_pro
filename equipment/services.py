"""Auto-sync services for the equipment domain.

Mirrors ``crops.services.sync_processing_expense``: each ``MaintenanceLog``
and each ``Equipment`` purchase posts exactly one auto-generated ``Expense``
row so equipment costs reach the expense ledger, budget actuals and reports.
Deleting the source record removes the expense via ``on_delete=CASCADE``.
"""

from django.db import transaction


def _equipment_category(code, name, sched_code, sched_name, description):
    from expenses.models import Category, ScheduleLine
    sched_line, _ = ScheduleLine.objects.get_or_create(
        code=sched_code, defaults={'name': sched_name})
    category, _ = Category.objects.get_or_create(
        code=code, defaults={
            'name': name, 'schedule_f_line': sched_line, 'description': description})
    return category


def sync_maintenance_expense(log):
    """Keep exactly one auto-generated Expense per maintenance log.

    The log's ``cost`` is posted under the "Equipment Maintenance" category
    (Schedule F repairs & maintenance line). Zero-cost logs remove a stale
    expense instead.
    """
    from expenses.models import Expense

    with transaction.atomic():
        expense = Expense.objects.filter(maintenance_log=log).first()
        if not log.cost or log.cost <= 0:
            if expense is not None:
                expense.delete()
            return

        from expenses.services import default_budget_item
        farm = log.equipment.farm_profile
        budget_item = log.budget_item
        category = (budget_item.category if budget_item
                    else _equipment_category(
                        'EQMAINT', 'Equipment Maintenance',
                        'part1_repairs_maintenance', 'Repairs and maintenance',
                        'Equipment service and repair costs'))
        synced = {
            'farm_profile': farm,
            'category': category,
            'budget_item': budget_item or default_budget_item(farm, category),
            'equipment': log.equipment,
            'amount': log.cost,
            'date': log.date,
            'vendor': log.performed_by,
            'notes': f'Maintenance on {log.equipment.name}: {log.description[:200]}',
        }
        if expense is None:
            Expense.objects.create(maintenance_log=log, **synced)
        else:
            for attr, value in synced.items():
                setattr(expense, attr, value)
            expense.save(update_fields=[*synced.keys(), 'updated_at'])


def sync_equipment_purchase_expense(equipment):
    """Keep exactly one auto-generated Expense per equipment purchase.

    ``purchase_cost`` is posted under the "Equipment Purchase" category
    (Schedule F depreciation line — capital assets are recovered through
    depreciation). Zero-cost or leased equipment removes a stale expense.
    """
    from expenses.models import Expense

    with transaction.atomic():
        expense = Expense.objects.filter(equipment_purchase=equipment).first()
        if (not equipment.purchase_cost or equipment.purchase_cost <= 0
                or equipment.ownership == equipment.Ownership.LEASED):
            if expense is not None:
                expense.delete()
            return

        from expenses.services import default_budget_item
        farm = equipment.farm_profile
        budget_item = equipment.budget_item
        category = (budget_item.category if budget_item
                    else _equipment_category(
                        'EQPURCH', 'Equipment Purchase',
                        'part1_depreciation', 'Depreciation',
                        'Equipment and machinery purchases'))
        synced = {
            'farm_profile': farm,
            'category': category,
            'budget_item': budget_item or default_budget_item(farm, category),
            'equipment': equipment,
            'amount': equipment.purchase_cost,
            'date': equipment.purchase_date or equipment.created_at.date(),
            'notes': f'Purchase: {equipment.name} ({equipment.get_equipment_type_display()})',
        }
        if expense is None:
            Expense.objects.create(equipment_purchase=equipment, **synced)
        else:
            for attr, value in synced.items():
                setattr(expense, attr, value)
            expense.save(update_fields=[*synced.keys(), 'updated_at'])


def sync_equipment_fuel_expense(usage):
    """Keep exactly one auto-generated Expense per equipment usage fuel cost.

    ``fuel_cost`` is posted under the "Fuel & Oil" category (Schedule F
    gasoline/fuel line). Zero-cost usage removes a stale expense instead.
    """
    from expenses.models import Expense

    with transaction.atomic():
        expense = Expense.objects.filter(equipment_usage=usage).first()
        if not usage.fuel_cost or usage.fuel_cost <= 0:
            if expense is not None:
                expense.delete()
            return

        from expenses.services import default_budget_item
        farm = usage.equipment.farm_profile
        budget_item = usage.budget_item
        category = (budget_item.category if budget_item
                    else _equipment_category(
                        'EQFUEL', 'Fuel & Oil',
                        'part1_gasoline_fuel', 'Gasoline, fuel, and oil',
                        'Fuel consumed by farm equipment'))
        synced = {
            'farm_profile': farm,
            'category': category,
            'budget_item': budget_item or default_budget_item(farm, category),
            'equipment': usage.equipment,
            'crop_season': usage.crop_season,
            'field': usage.field,
            'amount': usage.fuel_cost,
            'date': usage.date,
            'notes': f'Fuel for {usage.equipment.name}'
                     + (f' ({usage.fuel_liters}L)' if usage.fuel_liters else ''),
        }
        if expense is None:
            Expense.objects.create(equipment_usage=usage, **synced)
        else:
            for attr, value in synced.items():
                setattr(expense, attr, value)
            expense.save(update_fields=[*synced.keys(), 'updated_at'])
