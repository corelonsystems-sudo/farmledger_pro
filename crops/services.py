"""Business services for the crops app.

Keeps the Sale -> Income bookkeeping synchronized so financial reports and
the ledger never drift from recorded sales.
"""

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
        synced = {
            'farm_profile': farm_profile,
            'category': category,
            'crop_season': season,
            'budget_item': stage.budget_item,
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
