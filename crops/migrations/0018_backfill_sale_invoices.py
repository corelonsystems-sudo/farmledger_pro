"""Backfill automatic invoices for sales that predate the feature.

New sales get an invoice via the post_save signal; this migration runs the
same sync for every existing sale so no sale is left without one.
"""
from django.db import migrations


def backfill_sale_invoices(apps, schema_editor):
    from crops.models import Sale
    from crops.services import sync_sale_invoice
    for sale in Sale.objects.select_related(
            'harvest__crop_season__crop', 'harvest__crop_season__field',
            'processing_stage', 'customer', 'inventory_item').iterator():
        sync_sale_invoice(sale)


class Migration(migrations.Migration):

    dependencies = [
        ('crops', '0017_alter_invoice_sale'),
    ]

    operations = [
        migrations.RunPython(backfill_sale_invoices, migrations.RunPython.noop),
    ]
