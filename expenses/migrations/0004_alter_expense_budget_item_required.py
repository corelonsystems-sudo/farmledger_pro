from django.db import migrations, models
import django.db.models.deletion


def ensure_budget_item_on_expenses(apps, schema_editor):
    """Assign any expenses with NULL budget_item to a fallback BudgetItem.

    Creates a default Budget + BudgetItem if none exist yet so that no
    expense is left without a budget_item before the NOT NULL constraint
    is applied.
    """
    Expense = apps.get_model('expenses', 'Expense')
    Budget = apps.get_model('budgets', 'Budget')
    BudgetItem = apps.get_model('budgets', 'BudgetItem')
    Category = apps.get_model('expenses', 'Category')

    orphan_count = Expense.objects.filter(budget_item__isnull=True).count()
    if orphan_count == 0:
        return

    # Get or create a fallback category
    category, _ = Category.objects.get_or_create(
        name='General',
        defaults={'schedule_f_line': 'part1_other'},
    )

    # Get or create a fallback budget
    budget, _ = Budget.objects.get_or_create(
        name='Unbudgeted (auto)',
        defaults={'status': 'ACTIVE'},
    )

    # Get or create a fallback budget item
    budget_item, _ = BudgetItem.objects.get_or_create(
        budget=budget,
        category=category,
        defaults={'planned_amount': 0},
    )

    Expense.objects.filter(budget_item__isnull=True).update(budget_item=budget_item)


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0002_alter_budget_unique_together_budget_end_date_and_more'),
        ('expenses', '0003_remove_expense_budget_expense_budget_item'),
    ]

    operations = [
        migrations.RunPython(ensure_budget_item_on_expenses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='expense',
            name='budget_item',
            field=models.ForeignKey(
                help_text='Every expense must be linked to a budget item.',
                on_delete=django.db.models.deletion.PROTECT,
                related_name='expenses',
                to='budgets.budgetitem',
            ),
        ),
    ]
