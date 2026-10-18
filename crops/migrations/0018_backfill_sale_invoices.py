"""Backfill automatic invoices for sales that predate the feature.

New sales get an invoice via the post_save signal; this migration runs the
same sync for every existing sale so no sale is left without one.

This migration intentionally uses historical models only - the live
``sync_sale_invoice`` touches columns (customer.tin, invoice.tax_percent,
etc.) that did not exist at this point in the migration history.
"""
import re

from django.db import migrations


def backfill_sale_invoices(apps, schema_editor):
    Sale = apps.get_model('crops', 'Sale')
    Invoice = apps.get_model('crops', 'Invoice')
    InvoiceItem = apps.get_model('crops', 'InvoiceItem')
    InvoicePayment = apps.get_model('crops', 'InvoicePayment')
    HarvestRecord = apps.get_model('crops', 'HarvestRecord')
    CropSeason = apps.get_model('crops', 'CropSeason')
    Crop = apps.get_model('crops', 'Crop')
    ProcessingStage = apps.get_model('crops', 'ProcessingStage')
    Field = apps.get_model('fields', 'Field')
    InventoryItem = apps.get_model('inventory', 'InventoryItem')

    def farm_id_for(sale):
        if sale.harvest_id:
            season_id = HarvestRecord.objects.filter(
                pk=sale.harvest_id).values_list('crop_season_id', flat=True).first()
            field_id = CropSeason.objects.filter(
                pk=season_id).values_list('field_id', flat=True).first()
            farm_id = Field.objects.filter(
                pk=field_id).values_list('farm_profile_id', flat=True).first()
            if farm_id:
                return farm_id
        if sale.inventory_item_id:
            return InventoryItem.objects.filter(
                pk=sale.inventory_item_id).values_list(
                'farm_profile_id', flat=True).first()
        return None

    def crop_name_for(sale):
        if not sale.harvest_id:
            return None
        season_id = HarvestRecord.objects.filter(
            pk=sale.harvest_id).values_list('crop_season_id', flat=True).first()
        crop_id = CropSeason.objects.filter(
            pk=season_id).values_list('crop_id', flat=True).first()
        return Crop.objects.filter(pk=crop_id).values_list('name', flat=True).first()

    def description_for(sale):
        crop_name = crop_name_for(sale)
        if sale.processing_stage_id and crop_name:
            stage = ProcessingStage.objects.filter(
                pk=sale.processing_stage_id).values_list('name', flat=True).first()
            return f'{crop_name} - {stage}' if stage else crop_name
        if crop_name:
            return crop_name
        if sale.inventory_item_id:
            name = InventoryItem.objects.filter(
                pk=sale.inventory_item_id).values_list('name', flat=True).first()
            return name or 'Produce'
        return 'Produce'

    seq = 0
    for number in Invoice.objects.values_list('number', flat=True):
        match = re.fullmatch(r'INV-(\d+)', number or '')
        if match:
            seq = max(seq, int(match.group(1)))

    for sale in Sale.objects.all().order_by('pk').iterator():
        if Invoice.objects.filter(sale_id=sale.pk).exists():
            continue
        farm_id = farm_id_for(sale)
        if not farm_id:
            continue
        seq += 1
        invoice = Invoice.objects.create(
            number=f'INV-{seq:05d}', farm_profile_id=farm_id,
            customer_id=sale.customer_id, buyer=sale.buyer,
            sale_id=sale.pk, date=sale.sale_date, status='ISSUED',
            notes=f'Auto-generated from sale #{sale.pk}.')
        InvoiceItem.objects.create(
            invoice=invoice, description=description_for(sale),
            quantity=sale.quantity, unit=sale.unit,
            unit_price=sale.unit_price)
        if sale.amount_received:
            InvoicePayment.objects.create(
                invoice=invoice, amount=sale.amount_received,
                date=sale.sale_date, method=sale.payment_method,
                reference=f'Sale #{sale.pk} collection')


class Migration(migrations.Migration):

    dependencies = [
        ('crops', '0017_alter_invoice_sale'),
    ]

    operations = [
        migrations.RunPython(backfill_sale_invoices, migrations.RunPython.noop),
    ]
