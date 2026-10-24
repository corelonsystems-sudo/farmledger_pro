"""Business services for the crops app.

Keeps the Sale -> Income bookkeeping synchronized so financial reports and
the ledger never drift from recorded sales.
"""

from decimal import Decimal

from django.db import transaction

PAYMENT_METHOD_MAP = {
    'CASH': 'CASH',
    'BANK': 'BANK_TRANSFER',
    'MOBILE': 'CARD',
    'CREDIT': 'CARD',
    'OTHER': 'OTHER',
}


def _sale_farm_profile(sale):
    harvest = sale.harvest
    if harvest and harvest.crop_season_id and harvest.crop_season.field_id:
        return harvest.crop_season.field.farm_profile
    if sale.inventory_item_id:
        return sale.inventory_item.farm_profile
    return None


def sync_sale_income(sale):
    """Create or update the auto-generated Income record for a sale.

    Exactly one Income row is kept per sale (keyed by ``Income.sale``). On
    update only the synced fields are overwritten so user edits to notes or
    reconciliation status are preserved. Sale deletion removes the income
    via ``on_delete=CASCADE``.
    """
    from income.models import Income, IncomeSource

    farm_profile = _sale_farm_profile(sale)
    if farm_profile is None:
        # Cannot file income without a farm; nothing to sync.
        return

    if sale.inventory_item_id:
        source, _ = IncomeSource.objects.get_or_create(
            name='Inventory Sales',
            defaults={
                'code': 'IS',
                'schedule_f_line': 'part1_other',
                'description': 'Sales of general inventory items',
            },
        )
    else:
        source, _ = IncomeSource.objects.get_or_create(
            name='Crop Sales',
            defaults={
                'code': 'CS',
                'schedule_f_line': 'part1_sales',
                'description': 'Sales from crop harvests',
            },
        )

    harvest = sale.harvest
    crop_season = harvest.crop_season if harvest else None
    synced = {
        'farm_profile': farm_profile,
        'source': source,
        'crop_season': crop_season,
        'field': crop_season.field if crop_season else None,
        'amount': sale.total_amount,
        'date': sale.sale_date,
        'customer': sale.buyer,
        'customer_ref': sale.customer,
        'payment_method': PAYMENT_METHOD_MAP.get(sale.payment_method, 'OTHER'),
    }

    with transaction.atomic():
        income = Income.objects.filter(sale=sale).first()
        if income is None:
            Income.objects.create(
                sale=sale,
                notes=f'Auto-generated from sale {sale.pk}: {sale.notes}',
                **synced,
            )
        else:
            for attr, value in synced.items():
                setattr(income, attr, value)
            income.save(update_fields=[*synced.keys(), 'updated_at'])


INVOICE_PAYMENT_METHOD_MAP = {
    'CASH': 'CASH',
    'BANK': 'BANK_TRANSFER',
    'MOBILE': 'CARD',
    'CHEQUE': 'CHECK',
    'OTHER': 'OTHER',
}


def sync_invoice_payment_income(payment):
    """Keep exactly one auto-generated Income per invoice payment.

    Payments on sale-linked invoices are skipped: the sale's own income
    record (``Income.sale``) already covers that revenue, so booking it
    again here would double-count. Payment deletion removes the income
    via ``on_delete=CASCADE``.
    """
    from income.models import Income, IncomeSource

    invoice = payment.invoice
    if invoice.sale_id or invoice.farm_profile is None:
        return
    source, _ = IncomeSource.objects.get_or_create(
        name='Invoice Payments',
        defaults={
            'code': 'INV',
            'schedule_f_line': 'part1_sales',
            'description': 'Payments received on invoices',
        },
    )
    synced = {
        'farm_profile': invoice.farm_profile,
        'source': source,
        'amount': payment.amount,
        'date': payment.date,
        'customer': (invoice.buyer
                     or (invoice.customer.name if invoice.customer_id else '')),
        'customer_ref': invoice.customer,
        'payment_method': INVOICE_PAYMENT_METHOD_MAP.get(
            payment.method, 'OTHER'),
    }
    with transaction.atomic():
        income = Income.objects.filter(invoice_payment=payment).first()
        if income is None:
            Income.objects.create(
                invoice_payment=payment,
                notes=f'Auto-generated from invoice {invoice.number} payment.',
                **synced,
            )
        else:
            for attr, value in synced.items():
                setattr(income, attr, value)
            income.save(update_fields=[*synced.keys(), 'updated_at'])


def sync_processing_expense(stage):
    """Keep exactly one auto-generated Expense per processing stage cost.

    The stage's ``cost`` is posted to the expense ledger (category
    "Processing", tagged to the crop season) so season/budget actuals include
    processing spend. Zero-cost stages remove a stale expense instead.
    Deleting the stage removes the expense via ``on_delete=CASCADE``.
    """
    from expenses.models import Category, Expense

    with transaction.atomic():
        expense = Expense.objects.filter(processing_stage=stage).first()
        if not stage.cost or stage.cost <= 0:
            if expense is not None:
                expense.delete()
            return

        season = stage.harvest.crop_season
        farm_profile = season.field.farm_profile if season and season.field_id else None
        if farm_profile is None:
            return
        from expenses.models import ScheduleLine
        sched_line, _ = ScheduleLine.objects.get_or_create(
            code='part1_other', defaults={'name': 'Other or unspecified'})
        category, _ = Category.objects.get_or_create(
            code='PROC',
            defaults={'name': 'Processing', 'schedule_f_line': sched_line,
                      'description': 'Crop processing costs'},
        )
        from expenses.services import default_budget_item
        synced = {
            'farm_profile': farm_profile,
            'category': stage.budget_item.category if stage.budget_item_id else category,
            'crop_season': season,
            'budget_item': stage.budget_item or default_budget_item(
                farm_profile,
                stage.budget_item.category if stage.budget_item_id else category),
            'amount': stage.cost,
            'date': stage.end_date or stage.start_date or stage.harvest.harvest_date,
            'notes': f'Auto-generated from processing stage {stage.sequence}. {stage.name}',
        }
        if expense is None:
            Expense.objects.create(processing_stage=stage, **synced)
        else:
            for attr, value in synced.items():
                setattr(expense, attr, value)
            expense.save(update_fields=[*synced.keys(), 'updated_at'])


def sync_harvest_expense(harvest):
    """Keep exactly one auto-generated Expense per harvest record.

    The harvest's ``cost`` is posted under the "Harvesting" category
    (Schedule F custom-hire line, tagged to the crop season) so season/budget
    actuals and the harvest's net profit include harvest spend. The record's
    ``budget_item`` drives the expense category when set. Zero-cost harvests
    remove a stale expense instead.
    """
    from expenses.models import Category, Expense

    with transaction.atomic():
        expense = Expense.objects.filter(harvest_record=harvest).first()
        if not harvest.cost or harvest.cost <= 0:
            if expense is not None:
                expense.delete()
            return

        season = harvest.crop_season
        farm_profile = season.field.farm_profile if season and season.field_id else None
        if farm_profile is None:
            return
        from expenses.models import ScheduleLine
        sched_line, _ = ScheduleLine.objects.get_or_create(
            code='part1_custom_hire', defaults={'name': 'Custom hire (machine work)'})
        category, _ = Category.objects.get_or_create(
            code='HARV',
            defaults={'name': 'Harvesting', 'schedule_f_line': sched_line,
                      'description': 'Harvest labor and machine-hire costs'},
        )
        if harvest.budget_item_id:
            category = harvest.budget_item.category
        from expenses.services import default_budget_item
        synced = {
            'farm_profile': farm_profile,
            'category': category,
            'crop_season': season,
            'field': season.field,
            'budget_item': harvest.budget_item or default_budget_item(
                farm_profile, category),
            'amount': harvest.cost,
            'date': harvest.harvest_date,
            'vendor': harvest.performed_by,
            'notes': f'Harvesting: {harvest}',
        }
        if expense is None:
            Expense.objects.create(harvest_record=harvest, **synced)
        else:
            for attr, value in synced.items():
                setattr(expense, attr, value)
            expense.save(update_fields=[*synced.keys(), 'updated_at'])


def _sale_description(sale):
    """Human-readable line description for a sale's invoice/receipt."""
    if sale.processing_stage and sale.harvest:
        return f'{sale.harvest.crop_season.crop.name} - {sale.processing_stage.name}'
    if sale.harvest:
        return sale.harvest.crop_season.crop.name
    if sale.inventory_item_id:
        return sale.inventory_item.name
    return 'Produce sale'


def sync_sale_invoice(sale):
    """Keep exactly one auto-generated Invoice per sale.

    The invoice mirrors the sale as a single line item, carries the sale's
    collected amount as one payment, and is removed with the sale (CASCADE
    on Invoice.sale).
    """
    from .models import Invoice, InvoiceItem, InvoicePayment

    farm_profile = _sale_farm_profile(sale)
    if farm_profile is None:
        return

    invoice = Invoice.objects.filter(sale=sale).first()
    if invoice is None:
        invoice = Invoice(
            farm_profile=farm_profile, customer=sale.customer, buyer=sale.buyer,
            sale=sale, date=sale.sale_date, status=Invoice.Status.ISSUED,
            notes=f'Auto-generated from sale #{sale.pk}.')
        invoice.save()
    else:
        changed = []
        if invoice.farm_profile_id != farm_profile.pk:
            invoice.farm_profile = farm_profile
            changed.append('farm_profile')
        if invoice.customer_id != sale.customer_id:
            invoice.customer = sale.customer
            changed.append('customer')
        if invoice.buyer != sale.buyer:
            invoice.buyer = sale.buyer
            changed.append('buyer')
        if invoice.date != sale.sale_date:
            invoice.date = sale.sale_date
            changed.append('date')
        if changed:
            invoice.save(update_fields=[*changed, 'updated_at'])

    description = _sale_description(sale)
    unit_cost = Decimal('0')
    if sale.inventory_item_id:
        unit_cost = sale.inventory_item.cost_per_unit or Decimal('0')
    elif (sale.processing_stage_id and sale.processing_stage.output_quantity
          and sale.processing_stage.output_quantity > 0):
        unit_cost = (sale.processing_stage.cost or Decimal('0')) / sale.processing_stage.output_quantity
    item = invoice.items.first()
    if item is None:
        InvoiceItem.objects.create(
            invoice=invoice, description=description,
            quantity=sale.quantity, unit=sale.unit, unit_price=sale.unit_price,
            unit_cost=unit_cost)
    elif (item.description != description or item.quantity != sale.quantity
            or item.unit != sale.unit or item.unit_price != sale.unit_price
            or item.unit_cost != unit_cost):
        item.description = description
        item.quantity = sale.quantity
        item.unit = sale.unit
        item.unit_price = sale.unit_price
        item.unit_cost = unit_cost
        item.save()

    reference = f'Sale #{sale.pk} collection'
    received = sale.amount_received or Decimal('0')
    payment = invoice.payments.filter(reference=reference).first()
    if received > 0:
        method = {'CASH': 'CASH', 'BANK': 'BANK', 'MOBILE': 'MOBILE'}.get(
            sale.payment_method, 'OTHER')
        if payment is None:
            InvoicePayment.objects.create(
                invoice=invoice, amount=received, date=sale.sale_date,
                method=method, reference=reference)
        elif payment.amount != received:
            payment.amount = received
            payment.save()
    elif payment is not None:
        payment.delete()

    invoice.refresh_status()
