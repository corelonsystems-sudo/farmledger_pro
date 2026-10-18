"""Attach each budget income line to an independent income source.

BudgetIncomePlan gains an ``income_source`` FK to the income.IncomeSource
catalog. Existing lines are attached to sources created from their stored
line-item name, so every line ends up linked to a real income source.
"""
from django.db import migrations, models
import django.db.models.deletion


def attach_sources(apps, schema_editor):
    BudgetIncomePlan = apps.get_model('budgets', 'BudgetIncomePlan')
    IncomeSource = apps.get_model('income', 'IncomeSource')
    for plan in BudgetIncomePlan.objects.filter(income_source=None).exclude(name=''):
        source, _ = IncomeSource.objects.get_or_create(
            name=plan.name[:100],
            defaults={'schedule_f_line': 'part1_other'})
        BudgetIncomePlan.objects.filter(pk=plan.pk).update(income_source=source)


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0009_remove_budgetincomeplan_schedule_line'),
        ('income', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='budgetincomeplan',
            name='income_source',
            field=models.ForeignKey(blank=True, help_text='The income source this funding line is attached to. Independent of the expenditure schedule-line catalog.', null=True, on_delete=django.db.models.deletion.PROTECT, related_name='budget_income_plans', to='income.incomesource', verbose_name='Income source'),
        ),
        migrations.RunPython(attach_sources, migrations.RunPython.noop),
    ]
