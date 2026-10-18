"""Backfill auto-generated codes for income sources created without one.

IncomeSource.code is unique; sources created with the blank default could
only ever exist once. New saves generate a unique slug code from the name;
this migration assigns codes to any existing codeless rows.
"""
from django.db import migrations


def assign_codes(apps, schema_editor):
    from django.utils.text import slugify
    IncomeSource = apps.get_model('income', 'IncomeSource')
    taken = set(IncomeSource.objects.exclude(code='').values_list('code', flat=True))
    for source in IncomeSource.objects.filter(code=''):
        base = slugify(source.name).upper()[:15] or 'INC'
        code, index = base, 1
        while code in taken:
            code = f'{base}-{index}'
            index += 1
        taken.add(code)
        IncomeSource.objects.filter(pk=source.pk).update(code=code)


class Migration(migrations.Migration):

    dependencies = [
        ('income', '0005_income_customer_ref'),
    ]

    operations = [
        migrations.RunPython(assign_codes, migrations.RunPython.noop),
    ]
