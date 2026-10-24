"""Auto-sync services for the livestock domain.

Mirrors ``crops.services.sync_processing_expense``: each cost-bearing
livestock record posts exactly one auto-generated ``Expense`` row so
livestock spend reaches the expense ledger, budget actuals and reports.
The record's ``budget_item`` is carried onto the expense (and drives its
category when set). Deleting the source record removes the expense via
``on_delete=CASCADE``; zero-cost records remove a stale expense instead.
"""

from django.db import transaction


def _category(code, name, sched_code, sched_name, description):
    from expenses.models import Category, ScheduleLine
    sched_line, _ = ScheduleLine.objects.get_or_create(
        code=sched_code, defaults={'name': sched_name})
    category, _ = Category.objects.get_or_create(
        code=code, defaults={
            'name': name, 'schedule_f_line': sched_line, 'description': description})
    return category


def _feed_category():
    return _category('LVFEED', 'Livestock Feed', 'part1_feed', 'Feed',
                     'Feed purchased for livestock')


def _vet_category():
    return _category('LVVET', 'Veterinary & Medicine', 'part1_veterinary',
                     'Veterinary, breeding, and medicine',
                     'Livestock health and veterinary costs')


def _sync(link_filter, create_kwargs, cost, farm_profile, budget_item,
          default_category, synced_extra):
    from expenses.models import Expense
    from expenses.services import default_budget_item
    expense = Expense.objects.filter(**link_filter).first()
    if not cost or cost <= 0:
        if expense is not None:
            expense.delete()
        return
    category = budget_item.category if budget_item else default_category
    synced = {
        'farm_profile': farm_profile,
        'category': category,
        'budget_item': budget_item or default_budget_item(farm_profile, category),
        'amount': cost,
        **synced_extra,
    }
    if expense is None:
        Expense.objects.create(**create_kwargs, **synced)
    else:
        for attr, value in synced.items():
            setattr(expense, attr, value)
        expense.save(update_fields=[*synced.keys(), 'updated_at'])


def sync_animal_health_expense(record):
    """One Expense per animal health record (vets, medication)."""
    animal = record.animal
    with transaction.atomic():
        _sync(
            {'animal_health_record': record},
            {'animal_health_record': record},
            record.cost, animal.farm_profile, record.budget_item,
            _vet_category(),
            {
                'date': record.date,
                'vendor': record.veterinarian,
                'notes': f'{record.get_record_type_display()} - {animal}: '
                         f'{record.description[:180]}',
            })


def sync_animal_feed_expense(log):
    """One Expense per animal group feed log."""
    group = log.group
    with transaction.atomic():
        _sync(
            {'animal_feed_log': log},
            {'animal_feed_log': log},
            log.cost, group.farm_profile, log.budget_item,
            _feed_category(),
            {
                'date': log.date,
                'notes': f'Feed for {group.name}: {log.feed_type} '
                         f'({log.quantity_kg}kg)',
            })


def sync_poultry_feed_expense(log):
    """One Expense per poultry batch feed log."""
    batch = log.batch
    with transaction.atomic():
        _sync(
            {'poultry_feed_log': log},
            {'poultry_feed_log': log},
            log.cost, batch.farm_profile, log.budget_item,
            _feed_category(),
            {
                'date': log.date,
                'notes': f'Feed for {batch.batch_name}: {log.feed_type} '
                         f'({log.quantity_kg}kg)',
            })


def sync_poultry_health_expense(record):
    """One Expense per poultry batch health record."""
    batch = record.batch
    with transaction.atomic():
        _sync(
            {'poultry_health_record': record},
            {'poultry_health_record': record},
            record.cost, batch.farm_profile, record.budget_item,
            _vet_category(),
            {
                'date': record.date,
                'notes': f'{record.get_record_type_display()} - '
                         f'{batch.batch_name}: {record.description[:180]}',
            })


def sync_animal_acquisition_expense(animal):
    """One Expense per animal acquisition (stock purchases)."""
    from expenses.models import Category  # noqa: F401  (kept for symmetry)
    default = _category(
        'LVPURCH', 'Livestock Purchase', 'part1_other',
        'Other or unspecified', 'Livestock purchased for the farm')
    with transaction.atomic():
        _sync(
            {'animal_acquisition': animal},
            {'animal_acquisition': animal},
            animal.acquisition_cost, animal.farm_profile, animal.budget_item,
            default,
            {
                'date': animal.acquisition_date or animal.created_at.date(),
                'notes': f'Livestock purchase: {animal}',
            })
