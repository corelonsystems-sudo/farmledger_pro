"""Separate budget income lines from the expenditure schedule-line catalog.

BudgetIncomePlan.schedule_line (FK to expenses.ScheduleLine) is removed; the
income line item is now the free-text ``name`` field. Existing names copied
from schedule lines are preserved via backfill.
"""
from django.db import migrations, models


def backfill_names(apps, schema_editor):
    BudgetIncomePlan = apps.get_model('budgets', 'BudgetIncomePlan')
    for plan in BudgetIncomePlan.objects.filter(name='').exclude(schedule_line=None):
        BudgetIncomePlan.objects.filter(pk=plan.pk).update(name=plan.schedule_line.name)


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0008_budgetitem_schedule_line'),
    ]

    operations = [
        migrations.RunPython(backfill_names, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='budgetincomeplan',
            name='schedule_line',
        ),
        migrations.AlterField(
            model_name='budgetincomeplan',
            name='name',
            field=models.CharField(blank=True, default='', help_text='Name of this funding line, e.g. "Maize sales" or "Donor grant". Independent of the expenditure schedule-line catalog.', max_length=200, verbose_name='Income line item'),
        ),
    ]
