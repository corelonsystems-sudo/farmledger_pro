"""Make ScheduleLine.code editable.

Category.schedule_f_line was a FK pointing at the ScheduleLine ``code`` string
(to_field='code'), which forced the code to be immutable. This migration
rewires it as a normal pk-based FK: references follow the row when the code
changes, so the code can be edited freely.
"""
import django.db.models.deletion
from django.db import migrations, models

from expenses.models import _default_schedule_line


def map_code_to_pk(apps, schema_editor):
    Category = apps.get_model('expenses', 'Category')
    ScheduleLine = apps.get_model('expenses', 'ScheduleLine')
    codes = {row['code']: row['pk'] for row in ScheduleLine.objects.values('code', 'pk')}
    for cat in Category.objects.all():
        # schedule_f_line_id still stores the code string in this state.
        target = codes.get(cat.schedule_f_line_id)
        if target is not None:
            Category.objects.filter(pk=cat.pk).update(schedule_f_line_ref=target)


class Migration(migrations.Migration):

    dependencies = [
        ('expenses', '0012_expense_processing_stage_alter_expense_budget_item'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='schedule_f_line_ref',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='expenses.scheduleline'),
        ),
        migrations.RunPython(map_code_to_pk, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='category',
            name='schedule_f_line',
        ),
        migrations.RenameField(
            model_name='category',
            old_name='schedule_f_line_ref',
            new_name='schedule_f_line',
        ),
        migrations.AlterField(
            model_name='category',
            name='schedule_f_line',
            field=models.ForeignKey(db_column='schedule_f_line', default=_default_schedule_line, on_delete=django.db.models.deletion.PROTECT, related_name='budget_codes', to='expenses.scheduleline', verbose_name='Budget Schedule Line'),
        ),
    ]
