from django.db import migrations
from django.db.models import DecimalField, Sum
from django.db.models.functions import Coalesce


def backfill_hours_used(apps, schema_editor):
    """Set Equipment.hours_used to the sum of its logged usage records."""
    Equipment = apps.get_model('equipment', 'Equipment')
    for equipment in Equipment.objects.all():
        total = equipment.usage_records.aggregate(
            t=Coalesce(Sum('hours'), 0, output_field=DecimalField()))['t']
        Equipment.objects.filter(pk=equipment.pk).update(hours_used=total)


class Migration(migrations.Migration):

    dependencies = [
        ('equipment', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(backfill_hours_used, migrations.RunPython.noop),
    ]
