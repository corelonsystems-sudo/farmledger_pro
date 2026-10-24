"""Attach every expense that lacks a budget item to an AUTO-<category>
item on the farm's budget.

Mirrors ``expenses.services.default_budget_item`` with historical models:
uses the farm's ACTIVE budget (latest otherwise; creates a catch-all
"Operations Budget" when the farm has none) and a per-category
'AUTO-<code>' BudgetItem.
"""

import datetime

from django.db import migrations


def attach(apps, schema_editor):
    Expense = apps.get_model('expenses', 'Expense')
    Budget = apps.get_model('budgets', 'Budget')
    BudgetItem = apps.get_model('budgets', 'BudgetItem')

    item_cache = {}

    def item_for(farm_id, category_id, category_code, category_name):
        key = (farm_id, category_id)
        if key in item_cache:
            return item_cache[key]

        budget = (Budget.objects.filter(farm_profile_id=farm_id, status='ACTIVE')
                  .order_by('-start_date', '-pk').first())
        if budget is None:
            budget = (Budget.objects.filter(farm_profile_id=farm_id)
                      .order_by('-start_date', '-pk').first())
        if budget is None:
            today = datetime.date.today()
            budget = Budget.objects.create(
                farm_profile_id=farm_id,
                name=f'Operations Budget {today.year}',
                start_date=datetime.date(today.year, 1, 1),
                end_date=datetime.date(today.year, 12, 31),
                status='ACTIVE',
                notes='Auto-created to catch expenses posted without an '
                      'explicit budget item.')

        item, _ = BudgetItem.objects.get_or_create(
            budget=budget, line_code=f'AUTO-{category_code}',
            defaults={
                'category_id': category_id,
                'cost_centre': 'Auto-posted',
                'line_name': category_name,
                'planned_amount': 0,
            })
        item_cache[key] = item
        return item

    for expense in (Expense.objects.filter(budget_item__isnull=True)
                    .select_related('category').iterator()):
        if expense.category_id is None:
            continue
        item = item_for(expense.farm_profile_id, expense.category_id,
                        expense.category.code, expense.category.name)
        Expense.objects.filter(pk=expense.pk).update(budget_item_id=item.pk)


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0017_backfill_cost_expenses'),
        ('budgets', '0011_alter_budgetincomeplan_name'),
    ]

    operations = [
        migrations.RunPython(attach, migrations.RunPython.noop),
    ]
