"""Backfill auto-generated expenses for pre-existing cost-bearing records.

Each source record gets exactly one linked Expense (skipped if already
linked). Payroll lines skip when a matching 'Payroll:' expense already
exists for the same run (the run_payroll command posted them without
links). Expenses post without a budget_item — historical records never
had one to choose; they surface in the "No Budget Item" KPI.
"""

from django.db import migrations


def _category(apps, code, name, sched_code, sched_name, description):
    ScheduleLine = apps.get_model('expenses', 'ScheduleLine')
    Category = apps.get_model('expenses', 'Category')
    sched_line, _ = ScheduleLine.objects.get_or_create(
        code=sched_code, defaults={'name': sched_name})
    category, _ = Category.objects.get_or_create(
        code=code, defaults={
            'name': name, 'schedule_f_line': sched_line,
            'description': description})
    return category


def backfill(apps, schema_editor):
    Expense = apps.get_model('expenses', 'Expense')

    fuel_cat = _category(apps, 'EQFUEL', 'Fuel & Oil', 'part1_gasoline_fuel',
                         'Gasoline, fuel, and oil', 'Fuel consumed by farm equipment')
    feed_cat = _category(apps, 'LVFEED', 'Livestock Feed', 'part1_feed',
                         'Feed', 'Feed purchased for livestock')
    vet_cat = _category(apps, 'LVVET', 'Veterinary & Medicine', 'part1_veterinary',
                        'Veterinary, breeding, and medicine',
                        'Livestock health and veterinary costs')
    purch_cat = _category(apps, 'LVPURCH', 'Livestock Purchase', 'part1_other',
                          'Other or unspecified', 'Livestock purchased for the farm')
    harv_cat = _category(apps, 'HARV', 'Harvesting', 'part1_custom_hire',
                         'Custom hire (machine work)',
                         'Harvest labor and machine-hire costs')
    labor_cat = _category(apps, 'LABOR', 'Labor', 'part1_labor_hired',
                          'Labor hired (less employment credits)',
                          'Wages paid to farm workers')

    # Equipment usage fuel
    EquipmentUsage = apps.get_model('equipment', 'EquipmentUsage')
    for usage in EquipmentUsage.objects.select_related('equipment__farm_profile').filter(
            fuel_cost__gt=0).iterator():
        if Expense.objects.filter(equipment_usage_id=usage.pk).exists():
            continue
        Expense.objects.create(
            equipment_usage_id=usage.pk,
            farm_profile_id=usage.equipment.farm_profile_id,
            category=fuel_cat, equipment_id=usage.equipment_id,
            crop_season_id=usage.crop_season_id, field_id=usage.field_id,
            amount=usage.fuel_cost, date=usage.date,
            notes=f'Fuel for {usage.equipment.name}'
                  + (f' ({usage.fuel_liters}L)' if usage.fuel_liters else ''))

    # Animal health records
    AnimalHealthRecord = apps.get_model('livestock', 'AnimalHealthRecord')
    for rec in AnimalHealthRecord.objects.select_related('animal__farm_profile').filter(
            cost__gt=0).iterator():
        if Expense.objects.filter(animal_health_record_id=rec.pk).exists():
            continue
        Expense.objects.create(
            animal_health_record_id=rec.pk,
            farm_profile_id=rec.animal.farm_profile_id,
            category=vet_cat, amount=rec.cost, date=rec.date,
            vendor=rec.veterinarian,
            notes=f'{rec.record_type} - {rec.animal}: {rec.description[:180]}')

    # Animal feed logs
    AnimalFeedLog = apps.get_model('livestock', 'AnimalFeedLog')
    for log in AnimalFeedLog.objects.select_related('group__farm_profile').filter(
            cost__gt=0).iterator():
        if Expense.objects.filter(animal_feed_log_id=log.pk).exists():
            continue
        Expense.objects.create(
            animal_feed_log_id=log.pk,
            farm_profile_id=log.group.farm_profile_id,
            category=feed_cat, amount=log.cost, date=log.date,
            notes=f'Feed for {log.group.name}: {log.feed_type} ({log.quantity_kg}kg)')

    # Poultry feed logs
    PoultryFeedLog = apps.get_model('livestock', 'PoultryFeedLog')
    for log in PoultryFeedLog.objects.select_related('batch__farm_profile').filter(
            cost__gt=0).iterator():
        if Expense.objects.filter(poultry_feed_log_id=log.pk).exists():
            continue
        Expense.objects.create(
            poultry_feed_log_id=log.pk,
            farm_profile_id=log.batch.farm_profile_id,
            category=feed_cat, amount=log.cost, date=log.date,
            notes=f'Feed for {log.batch.batch_name}: {log.feed_type} ({log.quantity_kg}kg)')

    # Poultry health records
    PoultryHealthRecord = apps.get_model('livestock', 'PoultryHealthRecord')
    for rec in PoultryHealthRecord.objects.select_related('batch__farm_profile').filter(
            cost__gt=0).iterator():
        if Expense.objects.filter(poultry_health_record_id=rec.pk).exists():
            continue
        Expense.objects.create(
            poultry_health_record_id=rec.pk,
            farm_profile_id=rec.batch.farm_profile_id,
            category=vet_cat, amount=rec.cost, date=rec.date,
            notes=f'{rec.record_type} - {rec.batch.batch_name}: {rec.description[:180]}')

    # Animal acquisitions
    AnimalRecord = apps.get_model('livestock', 'AnimalRecord')
    for animal in AnimalRecord.objects.select_related('farm_profile').filter(
            acquisition_cost__gt=0).iterator():
        if Expense.objects.filter(animal_acquisition_id=animal.pk).exists():
            continue
        Expense.objects.create(
            animal_acquisition_id=animal.pk,
            farm_profile_id=animal.farm_profile_id,
            category=purch_cat, amount=animal.acquisition_cost,
            date=animal.acquisition_date or animal.created_at.date(),
            notes=f'Livestock purchase: {animal}')

    # Harvest records
    HarvestRecord = apps.get_model('crops', 'HarvestRecord')
    for harvest in HarvestRecord.objects.select_related(
            'crop_season__field__farm_profile').filter(cost__gt=0).iterator():
        if Expense.objects.filter(harvest_record_id=harvest.pk).exists():
            continue
        season = harvest.crop_season
        if not season or not season.field_id:
            continue
        Expense.objects.create(
            harvest_record_id=harvest.pk,
            farm_profile_id=season.field.farm_profile_id,
            category=harv_cat, crop_season_id=season.pk, field_id=season.field_id,
            amount=harvest.cost, date=harvest.harvest_date,
            vendor=harvest.performed_by, notes=f'Harvesting: {harvest}')

    # Payroll lines (dedupe against expenses the run_payroll command posted)
    PayrollLine = apps.get_model('labor', 'PayrollLine')
    for line in PayrollLine.objects.select_related(
            'payroll_run', 'worker', 'budget_item__category').filter(
            amount__gt=0).iterator():
        if Expense.objects.filter(payroll_line_id=line.pk).exists():
            continue
        run = line.payroll_run
        already_posted = Expense.objects.filter(
            farm_profile_id=run.farm_profile_id, amount=line.amount,
            date=run.end_date, vendor=f'Payroll: {line.worker.name}',
        ).exists()
        if already_posted:
            continue
        budget_item = line.budget_item or run.budget_item
        Expense.objects.create(
            payroll_line_id=line.pk,
            farm_profile_id=run.farm_profile_id,
            category=budget_item.category if budget_item else labor_cat,
            budget_item_id=budget_item.pk if budget_item else None,
            amount=line.amount, date=run.end_date,
            vendor=f'Payroll: {line.worker.name}', payment_method='BANK_TRANSFER',
            notes=f'Payroll {run.start_date} to {run.end_date}. '
                  f'Hours: {line.hours}, Overtime: {line.overtime_hours}')


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0016_expense_harvest_record'),
        ('crops', '0026_harvestrecord_budget_item_harvestrecord_cost_and_more'),
        ('equipment', '0006_equipment_budget_item_equipmentusage_budget_item_and_more'),
        ('livestock', '0002_animalfeedlog_budget_item_and_more'),
        ('labor', '0003_payrollline_budget_item_payrollrun_budget_item'),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
