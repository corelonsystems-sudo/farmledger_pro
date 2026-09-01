from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from .models import Crop, CropSeason, HarvestRecord, ProcessingStage, Sale


@login_required
def crop_detail_view(request, pk):
    crop = get_object_or_404(
        Crop.objects.prefetch_related(
            Prefetch(
                'seasons',
                queryset=CropSeason.objects.select_related('field').prefetch_related(
                    Prefetch(
                        'harvests',
                        queryset=HarvestRecord.objects.prefetch_related(
                            'sales', 'processing_stages'
                        ).order_by('-harvest_date'),
                    )
                ).order_by('-planting_date')
            )
        ),
        pk=pk
    )

    seasons = list(crop.seasons.all())
    harvests = []
    total_harvested = Decimal('0')
    total_expenses = Decimal('0')
    for season in seasons:
        for h in season.harvests.all():
            harvests.append(h)
            total_harvested += h.quantity
        total_expenses += season.total_expenses

    processing_stages = list(ProcessingStage.objects.filter(
        harvest__crop_season__crop=crop
    ).select_related('harvest__crop_season__crop').order_by('harvest', 'sequence'))

    processing_groups = []
    for harvest in harvests:
        stages = [s for s in processing_stages if s.harvest_id == harvest.pk]
        if not stages:
            continue
        last_with_output = None
        for stage in stages:
            if stage.output_quantity:
                last_with_output = stage
        # Per-stage balance after the next stage's input is deducted from output
        stage_balances = []
        stage_depreciations = []
        for idx, stage in enumerate(stages):
            next_input = stages[idx + 1].input_quantity if idx + 1 < len(stages) else Decimal('0')
            balance = (stage.output_quantity or Decimal('0')) - next_input
            stage_balances.append(balance)
            depreciated = (stage.input_quantity or Decimal('0')) - (stage.output_quantity or Decimal('0'))
            stage_depreciations.append(depreciated)

        total_depreciated_group = sum(stage_depreciations, Decimal('0'))

        processing_groups.append({
            'harvest': harvest,
            'stages': stages,
            'stage_balances': stage_balances,
            'stages_with_balances': list(zip(stages, stage_balances, stage_depreciations)),
            'total_cost': sum((s.cost for s in stages), Decimal('0')),
            'total_added_value': sum((s.added_value for s in stages), Decimal('0')),
            'total_input': stages[0].input_quantity if stages else Decimal('0'),
            'total_depreciated': total_depreciated_group,
            'final_output_quantity': last_with_output.output_quantity if last_with_output else None,
            'final_output_unit': last_with_output.output_unit if last_with_output else harvest.unit,
        })

    all_sales = list(Sale.objects.filter(
        harvest__crop_season__crop=crop
    ).select_related('harvest__crop_season__crop', 'processing_stage').order_by('-sale_date'))
    sales = [s for s in all_sales if not s.is_waste]
    waste_sales = [s for s in all_sales if s.is_waste]
    waste_sales_by_stage = {}
    waste_sales_by_harvest = {}
    for s in waste_sales:
        waste_sales_by_stage.setdefault(s.processing_stage_id, []).append(s)
        waste_sales_by_harvest.setdefault(s.harvest_id, []).append(s)

    # Per-harvest breakdown: raw produce vs the output of each processing stage,
    # so it is clear what was sold processed and what was sold unprocessed.
    harvest_rows = []
    total_raw_sold = Decimal('0')
    total_processed_sold = Decimal('0')
    total_raw_in_store = Decimal('0')
    total_processed_in_store = Decimal('0')
    for harvest in harvests:
        harvest_sales = [s for s in sales if s.harvest_id == harvest.pk]
        stages = [s for s in processing_stages if s.harvest_id == harvest.pk]

        raw_sales = [s for s in harvest_sales if s.processing_stage_id is None]
        raw_sold = sum((s.quantity for s in raw_sales), Decimal('0'))
        raw_revenue = sum((s.total_amount for s in raw_sales), Decimal('0'))
        # Only the first stage draws from raw harvest; later stages draw from
        # the previous stage's output.  When processing reduces weight (e.g.
        # drying coffee) the output_quantity is already lower than input.
        first_input = stages[0].input_quantity if stages else Decimal('0')
        raw_available = harvest.quantity - first_input
        raw_remaining = raw_available - raw_sold

        lines = [{
            'label': 'Raw / unprocessed',
            'is_raw': True,
            'stage': None,
            'available': harvest.quantity,
            'qty_before': harvest.quantity,
            'processed_qty': first_input,
            'depreciated': Decimal('0'),
            'balance': raw_available,
            'unit': harvest.unit,
            'sold': raw_sold,
            'remaining': raw_remaining,
            'revenue': raw_revenue,
            'sale_count': len(raw_sales),
        }]

        processed_sold = Decimal('0')
        processed_revenue = Decimal('0')
        for idx, stage in enumerate(stages):
            stage_sales = [s for s in harvest_sales if s.processing_stage_id == stage.pk]
            sold = sum((s.quantity for s in stage_sales), Decimal('0'))
            revenue = sum((s.total_amount for s in stage_sales), Decimal('0'))
            processed_sold += sold
            processed_revenue += revenue

            # The next stage in sequence takes its input from this stage's output,
            # so the usable stock of this stage is output minus what the next stage consumed.
            next_input = stages[idx + 1].input_quantity if idx + 1 < len(stages) else Decimal('0')
            stage_available = (stage.output_quantity or Decimal('0')) - next_input
            stage_remaining = stage_available - sold
            # Quantity lost during processing (e.g. moisture loss when drying)
            depreciated = (stage.input_quantity or Decimal('0')) - (stage.output_quantity or Decimal('0'))

            lines.append({
                'label': f'{stage.sequence}. {stage.name}',
                'is_raw': False,
                'stage': stage,
                'available': stage.output_quantity,
                'qty_before': stage.input_quantity,
                'processed_qty': next_input,
                'depreciated': depreciated,
                'balance': stage_available if stage.output_quantity is not None else None,
                'unit': stage.output_unit or harvest.unit,
                'sold': sold,
                'remaining': stage_remaining if stage.output_quantity is not None else None,
                'revenue': revenue,
                'sale_count': len(stage_sales),
            })

        total_raw_sold += raw_sold
        total_processed_sold += processed_sold
        total_raw_in_store += raw_remaining

        # Processed stock remaining across all stages for this harvest.
        processed_in_store = sum(
            (line['remaining'] for line in lines if not line['is_raw'] and line['remaining'] is not None),
            Decimal('0')
        )
        total_processed_in_store += processed_in_store

        total_depreciated = sum(
            ((s.input_quantity or Decimal('0')) - (s.output_quantity or Decimal('0')))
            for s in stages
        )
        processing_cost = sum((s.cost for s in stages), Decimal('0'))

        harvest_rows.append({
            'harvest': harvest,
            'lines': lines,
            'stage_count': len(stages),
            'raw_sold': raw_sold,
            'raw_available': raw_available,
            'raw_remaining': raw_remaining,
            'raw_revenue': raw_revenue,
            'processed_sold': processed_sold,
            'processed_revenue': processed_revenue,
            'total_sold': raw_sold + processed_sold,
            'total_revenue': raw_revenue + processed_revenue,
            'first_input': first_input,
            'total_depreciated': total_depreciated,
            'processed_in_store': processed_in_store,
            'processing_cost': processing_cost,
        })

    total_revenue = sum((s.total_amount for s in sales), Decimal('0'))
    total_quantity_sold = sum((s.quantity for s in sales), Decimal('0'))
    total_value_added = sum((s.added_value for s in processing_stages), Decimal('0'))
    total_processing_cost = sum((s.cost for s in processing_stages), Decimal('0'))
    total_depreciated = sum(
        ((s.input_quantity or Decimal('0')) - (s.output_quantity or Decimal('0')))
        for s in processing_stages
    )
    net_profit = total_revenue - total_expenses - total_processing_cost

    chart_data = {
        'labels': [row['harvest'].harvest_date.strftime('%Y-%m-%d') if row['harvest'].harvest_date else f"HVT-{row['harvest'].pk}" for row in harvest_rows],
        'costs': [row['processing_cost'] for row in harvest_rows],
        'revenues': [row['total_revenue'] for row in harvest_rows],
        'depreciated': [row['total_depreciated'] for row in harvest_rows],
    }

    pie_data = {
        'labels': ['Raw in Store', 'Processed in Store', 'Raw Sold', 'Processed Sold', 'Depreciated'],
        'values': [total_raw_in_store, total_processed_in_store, total_raw_sold, total_processed_sold, total_depreciated],
    }

    # Build a chronological stock-flow ledger from harvests, processing, and sales.
    stock_flow = []
    for harvest in harvests:
        stock_flow.append({
            'date': harvest.harvest_date,
            'season': harvest.crop_season,
            'harvest': harvest,
            'type': 'Harvest',
            'form_label': 'Raw',
            'stage': None,
            'in_qty': harvest.quantity,
            'out_qty': None,
            'unit': harvest.unit,
        })
    for stage in processing_stages:
        stock_flow.append({
            'date': stage.end_date or stage.start_date,
            'season': stage.harvest.crop_season,
            'harvest': stage.harvest,
            'type': 'Processing',
            'form_label': f'{stage.sequence}. {stage.name}',
            'stage': stage,
            'in_qty': stage.output_quantity,
            'out_qty': stage.input_quantity,
            'unit': stage.output_unit or stage.harvest.unit,
        })
    for sale in sales:
        stock_flow.append({
            'date': sale.sale_date,
            'season': sale.harvest.crop_season,
            'harvest': sale.harvest,
            'type': 'Sale',
            'form_label': f'{sale.processing_stage.sequence}. {sale.processing_stage.name}' if sale.processing_stage else 'Raw',
            'stage': sale.processing_stage,
            'in_qty': None,
            'out_qty': sale.quantity,
            'unit': sale.unit,
        })
    stock_flow.sort(key=lambda e: (e['date'] or date.min, e['type']))

    stock_flow_seasons = sorted({e['season'] for e in stock_flow if e['season']}, key=lambda s: s.planting_date or date.min, reverse=True)
    stock_flow_types = ['Harvest', 'Processing', 'Sale']
    stock_flow_harvests = sorted({e['harvest'] for e in stock_flow if e['harvest']}, key=lambda h: h.harvest_date or date.min, reverse=True)

    context = {
        'crop': crop,
        'seasons': seasons,
        'harvests': harvests,
        'harvest_rows': harvest_rows,
        'processing_stages': processing_stages,
        'processing_groups': processing_groups,
        'sales': sales,
        'waste_sales': waste_sales,
        'waste_sales_by_stage': waste_sales_by_stage,
        'waste_sales_by_harvest': waste_sales_by_harvest,
        'total_seasons': len(seasons),
        'total_harvests': len(harvests),
        'total_sales': len(sales),
        'total_harvested': total_harvested,
        'total_quantity_sold': total_quantity_sold,
        'total_raw_sold': total_raw_sold,
        'total_processed_sold': total_processed_sold,
        'quantity_in_store': total_raw_in_store,
        'total_processed_in_store': total_processed_in_store,
        'total_revenue': total_revenue,
        'total_expenses': total_expenses,
        'total_value_added': total_value_added,
        'total_processing_cost': total_processing_cost,
        'total_depreciated': total_depreciated,
        'net_profit': net_profit,
        'chart_data': chart_data,
        'pie_data': pie_data,
        'stock_flow': stock_flow,
        'stock_flow_seasons': stock_flow_seasons,
        'stock_flow_types': stock_flow_types,
        'stock_flow_harvests': stock_flow_harvests,
    }
    return render(request, 'crops/crop_detail.html', context)


@login_required
def harvest_detail_view(request, pk):
    harvest = get_object_or_404(
        HarvestRecord.objects.select_related('crop_season__crop', 'crop_season__field'),
        pk=pk
    )

    crop = harvest.crop_season.crop
    stages = list(ProcessingStage.objects.filter(
        harvest=harvest
    ).order_by('sequence'))

    all_sales = list(Sale.objects.filter(
        harvest=harvest
    ).select_related('processing_stage').order_by('-sale_date'))
    sales = [s for s in all_sales if not s.is_waste]
    waste_sales = [s for s in all_sales if s.is_waste]
    waste_sales_by_stage = {}
    for s in waste_sales:
        waste_sales_by_stage.setdefault(s.processing_stage_id, []).append(s)

    raw_sales = [s for s in sales if s.processing_stage_id is None]
    raw_sold = sum((s.quantity for s in raw_sales), Decimal('0'))
    raw_revenue = sum((s.total_amount for s in raw_sales), Decimal('0'))

    first_input = stages[0].input_quantity if stages else Decimal('0')
    raw_available = harvest.quantity - first_input
    raw_remaining = raw_available - raw_sold

    lines = [{
        'label': 'Raw / unprocessed',
        'is_raw': True,
        'stage': None,
        'qty_in': harvest.quantity,
        'in_source': 'Harvested from field',
        'out_processing': first_input,
        'out_sold': raw_sold,
        'loss': Decimal('0'),
        'balance': raw_remaining,
        'unit': harvest.unit,
        'revenue': raw_revenue,
        'sale_count': len(raw_sales),
    }]

    processed_sold = Decimal('0')
    processed_revenue = Decimal('0')
    for idx, stage in enumerate(stages):
        stage_sales = [s for s in sales if s.processing_stage_id == stage.pk]
        sold = sum((s.quantity for s in stage_sales), Decimal('0'))
        revenue = sum((s.total_amount for s in stage_sales), Decimal('0'))
        processed_sold += sold
        processed_revenue += revenue

        qty_in = stage.input_quantity
        output_qty = stage.output_quantity
        next_input = stages[idx + 1].input_quantity if idx + 1 < len(stages) else Decimal('0')
        loss = (qty_in or Decimal('0')) - (output_qty or Decimal('0'))
        balance = (output_qty - next_input - sold) if output_qty is not None else None

        if idx == 0:
            in_source = f'Processed from raw ({stage.input_quantity} {harvest.unit} in)'
        else:
            prev = stages[idx - 1]
            in_source = f'Processed from {prev.sequence}. {prev.name} ({stage.input_quantity} {harvest.unit} in)'

        lines.append({
            'label': f'{stage.sequence}. {stage.name}',
            'is_raw': False,
            'stage': stage,
            'qty_in': qty_in,
            'in_source': in_source,
            'out_processing': next_input,
            'out_sold': sold,
            'loss': loss,
            'balance': balance,
            'unit': stage.output_unit or harvest.unit,
            'revenue': revenue,
            'sale_count': len(stage_sales),
        })

    total_depreciated = sum(
        ((s.input_quantity or Decimal('0')) - (s.output_quantity or Decimal('0')))
        for s in stages
    )

    total_processing_cost = sum((s.cost for s in stages), Decimal('0'))
    total_value_added = sum((s.added_value for s in stages), Decimal('0'))
    total_revenue = raw_revenue + processed_revenue
    processed_in_store = sum(
        (line['balance'] for line in lines if not line['is_raw'] and line['balance'] is not None),
        Decimal('0')
    )

    chart_data = {
        'labels': [line['label'] for line in lines],
        'costs': [0 if line['is_raw'] else (line['stage'].cost or Decimal('0')) for line in lines],
        'revenues': [line['revenue'] for line in lines],
        'depreciated': [0 if line['is_raw'] else (line['loss'] or Decimal('0')) for line in lines],
    }

    pie_data = {
        'labels': [line['label'] for line in lines if line['balance'] is not None],
        'values': [line['balance'] for line in lines if line['balance'] is not None],
    }

    context = {
        'crop': crop,
        'harvest': harvest,
        'stages': stages,
        'sales': sales,
        'waste_sales': waste_sales,
        'waste_sales_by_stage': waste_sales_by_stage,
        'lines': lines,
        'raw_sold': raw_sold,
        'raw_available': raw_available,
        'raw_remaining': raw_remaining,
        'raw_revenue': raw_revenue,
        'processed_sold': processed_sold,
        'processed_revenue': processed_revenue,
        'total_sold': raw_sold + processed_sold,
        'total_revenue': total_revenue,
        'first_input': first_input,
        'total_depreciated': total_depreciated,
        'total_processing_cost': total_processing_cost,
        'total_value_added': total_value_added,
        'processed_in_store': processed_in_store,
        'chart_data': chart_data,
        'pie_data': pie_data,
    }
    return render(request, 'crops/harvest_detail.html', context)


@login_required
def crop_season_detail_view(request, pk):
    season = get_object_or_404(
        CropSeason.objects.select_related('crop', 'field').prefetch_related(
            Prefetch(
                'harvests',
                queryset=HarvestRecord.objects.prefetch_related(
                    'sales', 'processing_stages'
                ).order_by('-harvest_date'),
            )
        ),
        pk=pk
    )

    crop = season.crop
    field = season.field
    harvests = list(season.harvests.all())
    total_harvested = sum((h.quantity for h in harvests), Decimal('0'))

    processing_stages = list(ProcessingStage.objects.filter(
        harvest__crop_season=season
    ).select_related('harvest__crop_season__crop').order_by('harvest', 'sequence'))

    sales = list(Sale.objects.filter(
        harvest__crop_season=season
    ).select_related('harvest__crop_season__crop', 'processing_stage').order_by('-sale_date'))

    harvest_rows = []
    total_raw_sold = Decimal('0')
    total_processed_sold = Decimal('0')
    total_raw_in_store = Decimal('0')
    total_processed_in_store = Decimal('0')
    for harvest in harvests:
        harvest_sales = [s for s in sales if s.harvest_id == harvest.pk]
        stages = [s for s in processing_stages if s.harvest_id == harvest.pk]

        raw_sales = [s for s in harvest_sales if s.processing_stage_id is None]
        raw_sold = sum((s.quantity for s in raw_sales), Decimal('0'))
        raw_revenue = sum((s.total_amount for s in raw_sales), Decimal('0'))
        first_input = stages[0].input_quantity if stages else Decimal('0')
        raw_available = harvest.quantity - first_input
        raw_remaining = raw_available - raw_sold

        lines = [{
            'label': 'Raw / unprocessed',
            'is_raw': True,
            'stage': None,
            'available': harvest.quantity,
            'qty_before': harvest.quantity,
            'processed_qty': first_input,
            'depreciated': Decimal('0'),
            'balance': raw_available,
            'unit': harvest.unit,
            'sold': raw_sold,
            'remaining': raw_remaining,
            'revenue': raw_revenue,
            'sale_count': len(raw_sales),
        }]

        processed_sold = Decimal('0')
        processed_revenue = Decimal('0')
        for idx, stage in enumerate(stages):
            stage_sales = [s for s in harvest_sales if s.processing_stage_id == stage.pk]
            sold = sum((s.quantity for s in stage_sales), Decimal('0'))
            revenue = sum((s.total_amount for s in stage_sales), Decimal('0'))
            processed_sold += sold
            processed_revenue += revenue

            next_input = stages[idx + 1].input_quantity if idx + 1 < len(stages) else Decimal('0')
            stage_available = (stage.output_quantity or Decimal('0')) - next_input
            stage_remaining = stage_available - sold
            depreciated = (stage.input_quantity or Decimal('0')) - (stage.output_quantity or Decimal('0'))

            lines.append({
                'label': f'{stage.sequence}. {stage.name}',
                'is_raw': False,
                'stage': stage,
                'available': stage.output_quantity,
                'qty_before': stage.input_quantity,
                'processed_qty': next_input,
                'depreciated': depreciated,
                'balance': stage_available if stage.output_quantity is not None else None,
                'unit': stage.output_unit or harvest.unit,
                'sold': sold,
                'remaining': stage_remaining if stage.output_quantity is not None else None,
                'revenue': revenue,
                'sale_count': len(stage_sales),
            })

        total_raw_sold += raw_sold
        total_processed_sold += processed_sold
        total_raw_in_store += raw_remaining

        processed_in_store = sum(
            (line['remaining'] for line in lines if not line['is_raw'] and line['remaining'] is not None),
            Decimal('0')
        )
        total_processed_in_store += processed_in_store

        total_depreciated = sum(
            ((s.input_quantity or Decimal('0')) - (s.output_quantity or Decimal('0')))
            for s in stages
        )
        processing_cost = sum((s.cost for s in stages), Decimal('0'))

        harvest_rows.append({
            'harvest': harvest,
            'lines': lines,
            'stage_count': len(stages),
            'raw_sold': raw_sold,
            'raw_available': raw_available,
            'raw_remaining': raw_remaining,
            'raw_revenue': raw_revenue,
            'processed_sold': processed_sold,
            'processed_revenue': processed_revenue,
            'total_sold': raw_sold + processed_sold,
            'total_revenue': raw_revenue + processed_revenue,
            'first_input': first_input,
            'total_depreciated': total_depreciated,
            'processed_in_store': processed_in_store,
            'processing_cost': processing_cost,
        })

    total_revenue = sum((s.total_amount for s in sales), Decimal('0'))
    total_quantity_sold = sum((s.quantity for s in sales), Decimal('0'))
    total_value_added = sum((s.added_value for s in processing_stages), Decimal('0'))
    total_processing_cost = sum((s.cost for s in processing_stages), Decimal('0'))
    total_depreciated = sum(
        ((s.input_quantity or Decimal('0')) - (s.output_quantity or Decimal('0')))
        for s in processing_stages
    )
    total_expenses = season.total_expenses
    net_profit = total_revenue - total_expenses - total_processing_cost

    chart_data = {
        'labels': [row['harvest'].harvest_date.strftime('%Y-%m-%d') if row['harvest'].harvest_date else f"HVT-{row['harvest'].pk}" for row in harvest_rows],
        'costs': [row['processing_cost'] for row in harvest_rows],
        'revenues': [row['total_revenue'] for row in harvest_rows],
        'depreciated': [row['total_depreciated'] for row in harvest_rows],
    }

    pie_data = {
        'labels': ['Raw in Store', 'Processed in Store', 'Raw Sold', 'Processed Sold', 'Depreciated'],
        'values': [total_raw_in_store, total_processed_in_store, total_raw_sold, total_processed_sold, total_depreciated],
    }

    context = {
        'crop': crop,
        'season': season,
        'field': field,
        'harvests': harvests,
        'harvest_rows': harvest_rows,
        'processing_stages': processing_stages,
        'sales': sales,
        'total_harvests': len(harvests),
        'total_harvested': total_harvested,
        'total_quantity_sold': total_quantity_sold,
        'total_raw_sold': total_raw_sold,
        'total_processed_sold': total_processed_sold,
        'quantity_in_store': total_raw_in_store,
        'total_processed_in_store': total_processed_in_store,
        'total_revenue': total_revenue,
        'total_expenses': total_expenses,
        'total_value_added': total_value_added,
        'total_processing_cost': total_processing_cost,
        'total_depreciated': total_depreciated,
        'net_profit': net_profit,
        'chart_data': chart_data,
        'pie_data': pie_data,
    }
    return render(request, 'crops/crop_season_detail.html', context)


@login_required
def sale_detail_view(request, pk):
    sale = get_object_or_404(
        Sale.objects.select_related('harvest__crop_season__crop', 'harvest__crop_season__field', 'processing_stage'),
        pk=pk,
    )
    waste_name = ''
    if sale.is_waste:
        if sale.processing_stage and sale.processing_stage.waste_name:
            waste_name = sale.processing_stage.waste_name
        elif sale.harvest and sale.harvest.waste_name:
            waste_name = sale.harvest.waste_name
        else:
            waste_name = 'Unnamed Waste'
    return render(request, 'crops/sale_detail.html', {'sale': sale, 'waste_name': waste_name})


@login_required
def waste_detail_view(request, pk):
    sale = get_object_or_404(
        Sale.objects.select_related('harvest__crop_season__crop', 'harvest__crop_season__field', 'processing_stage'),
        pk=pk,
    )
    if not sale.is_waste:
        raise Http404('Not a waste sale')

    harvest = sale.harvest
    stage = sale.processing_stage
    crop = harvest.crop_season.crop

    if stage:
        source = {
            'label': f'Stage {stage.sequence}. {stage.name}',
            'waste_name': stage.waste_name or 'Unnamed Waste',
            'waste_quantity': stage.waste_quantity or Decimal('0'),
            'waste_value': stage.waste_value or Decimal('0'),
            'unit': stage.output_unit or harvest.unit,
        }
        waste_sales = list(Sale.objects.filter(
            processing_stage=stage, is_waste=True
        ).select_related('harvest__crop_season__crop', 'processing_stage').order_by('-sale_date'))
    else:
        source = {
            'label': f'Harvest ({harvest.get_harvest_type_display})',
            'waste_name': harvest.waste_name or 'Unnamed Waste',
            'waste_quantity': harvest.waste_quantity or Decimal('0'),
            'waste_value': harvest.waste_value or Decimal('0'),
            'unit': harvest.unit,
        }
        waste_sales = list(Sale.objects.filter(
            harvest=harvest, processing_stage__isnull=True, is_waste=True
        ).select_related('harvest__crop_season__crop', 'processing_stage').order_by('-sale_date'))

    for s in waste_sales:
        s.update_url = reverse('model_update', kwargs={'app': 'crops', 'model': 'sale', 'pk': s.pk})
        s.delete_url = reverse('model_delete', kwargs={'app': 'crops', 'model': 'sale', 'pk': s.pk})

    total_sold = sum((s.quantity for s in waste_sales), Decimal('0'))
    total_revenue = sum((s.total_amount for s in waste_sales), Decimal('0'))
    remaining = source['waste_quantity'] - total_sold
    unit_price = sale.unit_price

    if stage:
        edit_source_url = reverse('model_update', kwargs={'app': 'crops', 'model': 'processingstage', 'pk': stage.pk}) + '?waste=1&next=' + request.path
    else:
        edit_source_url = reverse('model_update', kwargs={'app': 'crops', 'model': 'harvestrecord', 'pk': harvest.pk}) + '?waste=1&next=' + request.path

    create_params = [('waste', '1'), ('next', request.path)]
    if stage:
        create_params.append(('processing_stage', str(stage.pk)))
    else:
        create_params.append(('harvest', str(harvest.pk)))
    create_url = reverse('model_create', kwargs={'app': 'crops', 'model': 'sale'}) + '?' + '&'.join(f'{k}={v}' for k, v in create_params)

    remaining_value = remaining * (unit_price or Decimal('0'))
    if source['waste_quantity']:
        sold_percentage = (total_sold / source['waste_quantity']) * Decimal('100')
    else:
        sold_percentage = Decimal('0')

    chrono = sorted(waste_sales, key=lambda s: s.sale_date or s.created_at)
    chart_labels = []
    sold_series = []
    available_series = []
    running = Decimal('0')
    for s in chrono:
        running += s.quantity
        label = s.sale_date.strftime('%Y-%m-%d') if s.sale_date else f'Sale {s.pk}'
        chart_labels.append(label)
        sold_series.append(running)
        available_series.append(source['waste_quantity'] - running)
    chart_data = {
        'labels': chart_labels,
        'sold': sold_series,
        'available': available_series,
    }
    pie_data = {
        'labels': ['Sold', 'Remaining'],
        'values': [total_sold, remaining],
    }

    context = {
        'app': 'crops',
        'model': 'sale',
        'crop': crop,
        'harvest': harvest,
        'stage': stage,
        'source': source,
        'waste_sales': waste_sales,
        'total_sold': total_sold,
        'total_revenue': total_revenue,
        'remaining': remaining,
        'remaining_value': remaining_value,
        'sold_percentage': sold_percentage,
        'unit_price': unit_price,
        'sale': sale,
        'create_url': create_url,
        'edit_source_url': edit_source_url,
        'chart_data': chart_data,
        'pie_data': pie_data,
    }
    return render(request, 'crops/waste_detail.html', context)
