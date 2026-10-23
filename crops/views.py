from datetime import date
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Prefetch
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from accounts.access import accessible_farms, scoped_queryset

from .models import (
    Crop, CropSeason, Customer, HarvestRecord, Invoice, InvoiceItem,
    InvoicePayment, ProcessingStage, Sale,
)


def _sold_qty(sales, unit):
    """Sum sale quantities normalized to the given unit."""
    return sum((s.quantity_in_unit(unit) for s in sales), Decimal('0'))


def _crop_stock_chart(rows):
    """SVG two-line chart over generic rows {'label','sold','in_store'} â€”
    cumulative quantity sold against remaining stock in store, one shared
    quantity axis. None when rows is empty."""
    import math
    from farmledger.views import _nice_ceiling, _smooth_path
    if not rows:
        return None
    X0, X1, Y0, Y1 = 72, 572, 36, 190
    H = Y1 - Y0
    count = len(rows)
    max_v = _nice_ceiling(max(
        max(r['sold'] for r in rows), max(r['in_store'] for r in rows)))

    def ypos(value):
        return round(Y1 - H * float(value / max_v), 1) if max_v else Y1

    label_step = max(1, math.ceil(count / 12))
    for i, r in enumerate(rows):
        r['x'] = round(X0 + (X1 - X0) * i / (count - 1), 1) if count > 1 else (X0 + X1) / 2
        r['sy'] = ypos(r['sold'])
        r['iy'] = ypos(r['in_store'])
        r['show_label'] = i % label_step == 0 or i == count - 1
    sold_path = _smooth_path([(r['x'], r['sy']) for r in rows], Y0, Y1)
    return {
        'rows': rows,
        'sold_path': sold_path,
        'area_path': (f'{sold_path} L {rows[-1]["x"]},{Y1} L {rows[0]["x"]},{Y1} Z'
                      if count > 1 else ''),
        'store_path': _smooth_path([(r['x'], r['iy']) for r in rows], Y0, Y1),
        'ticks': [{'value': max_v * Decimal(n) / 4,
                   'y': round(Y1 - H * n / 4, 1)} for n in (4, 3, 2, 1, 0)],
        'last': rows[-1],
    }


def _crop_stage_flow(harvest_rows, total_harvested, raw_sold, raw_in_store):
    """Aggregate every harvest's processing chain by stage (sequence, name)
    into a top-down production flow: raw harvest -> stage 1 -> stage 2 ...
    Each row carries sold / in-store / passed-on / lost splits scaled to the
    raw harvest total so the bars shrink as quantity is consumed."""
    from farmledger.views import _bar_scale
    first = {'label': 'Harvested (raw)', 'sequence': 0, 'qty': total_harvested,
             'sold': raw_sold, 'in_store': raw_in_store, 'lost': Decimal('0'),
             'unit': None, 'is_raw': True}
    stages = {}
    for r in harvest_rows:
        if first['unit'] is None:
            first['unit'] = r['harvest'].unit
        for line in r['lines']:
            if line['is_raw']:
                continue
            st = line['stage']
            key = (st.sequence, st.name)
            agg = stages.setdefault(key, {
                'label': f'{st.sequence}. {st.name}', 'sequence': st.sequence,
                'qty': Decimal('0'), 'sold': Decimal('0'), 'in_store': Decimal('0'),
                'lost': Decimal('0'), 'unit': line['unit'], 'is_raw': False, 'n': 0})
            agg['qty'] += line['available'] or Decimal('0')
            agg['sold'] += line['sold'] or Decimal('0')
            agg['in_store'] += line['remaining'] or Decimal('0')
            agg['lost'] += line['depreciated'] or Decimal('0')
            agg['n'] += 1
    rows = [first] + [stages[k] for k in sorted(stages)]
    for row in rows:
        # Whatever was neither sold nor left in store moved on to the next stage.
        row['passed_on'] = max(row['qty'] - row['sold'] - row['in_store'], Decimal('0'))
        row['total'] = row['qty']
    _bar_scale(rows, 'total')
    max_v = max((r['qty'] for r in rows), default=Decimal('0'))
    for row in rows:
        for k in ('sold', 'in_store', 'passed_on', 'lost'):
            row[f'{k}_pct'] = round(float(row[k] / max_v * 100), 1) if max_v else 0
    return rows if total_harvested or len(rows) > 1 else []


@login_required
def crop_detail_view(request, pk):
    crop = get_object_or_404(
        Crop.objects.prefetch_related(
            Prefetch(
                'seasons',
                queryset=scoped_queryset(
                    CropSeason, request.user,
                    CropSeason.objects.select_related('field'),
                ).prefetch_related(
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

    farm_ids = accessible_farms(request.user).values('pk')
    processing_stages = list(ProcessingStage.objects.filter(
        harvest__crop_season__crop=crop,
        harvest__crop_season__field__farm_profile_id__in=farm_ids,
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
        harvest__crop_season__crop=crop,
        harvest__crop_season__field__farm_profile_id__in=farm_ids,
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
        raw_sold = _sold_qty(raw_sales, harvest.unit)
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
            sold = _sold_qty(stage_sales, stage.output_unit or harvest.unit)
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
    total_quantity_sold = _sold_qty(sales, crop.default_unit or 'kg')
    total_value_added = sum((s.added_value for s in processing_stages), Decimal('0'))
    total_processing_cost = sum((s.cost for s in processing_stages), Decimal('0'))
    total_depreciated = sum(
        ((s.input_quantity or Decimal('0')) - (s.output_quantity or Decimal('0')))
        for s in processing_stages
    )
    # Stage costs are synced into the expense ledger (Expense.processing_stage)
    # and therefore already counted in total_expenses - do not subtract twice.
    net_profit = total_revenue - total_expenses

    # ---- Chart filters (GET): season, year, processing stage, harvest ------
    season_id = request.GET.get('season') or ''
    year = request.GET.get('year') or ''
    stage_name = request.GET.get('stage') or ''
    harvest_id = request.GET.get('harvest') or ''

    def stage_date(st):
        return st.end_date or st.start_date or st.harvest.harvest_date

    years = sorted({d.year for d in
                    [s.sale_date for s in sales if s.sale_date]
                    + [stage_date(st) for st in processing_stages if stage_date(st)]
                    + [h.harvest_date for h in harvests if h.harvest_date]}, reverse=True)
    stage_names = sorted({st.name for st in processing_stages})
    chart_seasons = [s for s in seasons if str(s.pk) == season_id] if season_id else seasons
    chart_harvests = [h for h in harvests
                      if (not harvest_id or str(h.pk) == harvest_id)
                      and h.crop_season in chart_seasons]
    chart_stages = [st for st in processing_stages
                    if st.harvest in chart_harvests
                    and (not stage_name or st.name == stage_name)]
    chart_sales = [s for s in sales
                   if s.harvest in chart_harvests
                   and (not stage_name or (s.processing_stage and s.processing_stage.name == stage_name))]

    # The active year must have activity within the filtered selection.
    f_years = {d.year for d in
               [s.sale_date for s in chart_sales if s.sale_date]
               + [stage_date(st) for st in chart_stages if stage_date(st)]}
    active_year = year if year in {str(y) for y in f_years} else (str(max(f_years)) if f_years else '')
    stock_chart = None
    if active_year:
        import calendar
        y = int(active_year)
        unit = crop.default_unit or 'kg'
        sold_m = {m: Decimal('0') for m in range(1, 13)}
        dep_m = {m: Decimal('0') for m in range(1, 13)}
        for s in chart_sales:
            if s.sale_date and s.sale_date.year == y:
                sold_m[s.sale_date.month] += s.quantity_in_unit(unit)
        for st in chart_stages:
            d = stage_date(st)
            if d and d.year == y:
                dep_m[d.month] += (st.input_quantity or Decimal('0')) - (st.output_quantity or Decimal('0'))
        harvested_total = sum((h.quantity or Decimal('0') for h in chart_harvests), Decimal('0'))
        month_rows, cum_sold, cum_dep = [], Decimal('0'), Decimal('0')
        for m in range(1, 13):
            cum_sold += sold_m[m]
            cum_dep += dep_m[m]
            month_rows.append({'label': calendar.month_abbr[m], 'sold': cum_sold,
                               'in_store': harvested_total - cum_sold - cum_dep})
        stock_chart = _crop_stock_chart(month_rows)
        scope_sold = cum_sold
        scope_store = harvested_total - cum_sold - cum_dep
    else:
        scope_sold = scope_store = Decimal('0')

    # Stages-of-production flow follows the season/harvest scope.
    flow_rows = [r for r in harvest_rows if r['harvest'] in chart_harvests]
    flow_harvested = sum((r['harvest'].quantity for r in flow_rows), Decimal('0'))
    flow_raw_sold = sum((r['raw_sold'] for r in flow_rows), Decimal('0'))
    flow_raw_store = sum((r['raw_remaining'] for r in flow_rows), Decimal('0'))
    stage_flow = _crop_stage_flow(flow_rows, flow_harvested, flow_raw_sold, flow_raw_store)

    chart_filters = {
        'season': season_id, 'year': active_year, 'stage': stage_name,
        'harvest': harvest_id, 'years': years, 'stage_names': stage_names,
    }
    pie_data = {
        'labels': ['Raw in Store', 'Processed in Store', 'Raw Sold', 'Processed Sold', 'Depreciated'],
        'values': [total_raw_in_store, total_processed_in_store, total_raw_sold, total_processed_sold, total_depreciated],
    }
    from farmledger.views import _donut_segments
    qty_segments = _donut_segments(sorted(
        [{'label': l, 'total': v} for l, v in zip(pie_data['labels'], pie_data['values']) if v and v > 0],
        key=lambda r: r['total'], reverse=True))
    qty_total = sum((s['total'] for s in qty_segments), Decimal('0'))
    qty_center = f'{qty_total.normalize():f} {crop.default_unit or ""}'.strip()

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
        'line_chart': stock_chart,
        'stock_scope_sold': scope_sold,
        'stock_scope_store': scope_store,
        'qty_segments': qty_segments,
        'qty_total': qty_total,
        'qty_center': qty_center,
        'stage_flow': stage_flow,
        'chart_filters': chart_filters,
        'stock_flow': stock_flow,
        'stock_flow_seasons': stock_flow_seasons,
        'stock_flow_types': stock_flow_types,
        'stock_flow_harvests': stock_flow_harvests,
        'can_write': request.user.is_superuser or accessible_farms(
            request.user, write_model='crops.cropseason').exists(),
    }
    return render(request, 'crops/crop_detail.html', context)


@login_required
def harvest_detail_view(request, pk):
    harvest = get_object_or_404(
        scoped_queryset(
            HarvestRecord, request.user,
            HarvestRecord.objects.select_related('crop_season__crop', 'crop_season__field'),
        ),
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
    raw_sold = _sold_qty(raw_sales, harvest.unit)
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
        sold = _sold_qty(stage_sales, stage.output_unit or harvest.unit)
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

        stage_depreciated = (qty_in or Decimal('0')) - (output_qty or Decimal('0'))

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
            'waste_quantity': stage.waste_quantity or Decimal('0'),
            'waste_name': stage.waste_name or '',
            'unit': stage.output_unit or harvest.unit,
            'revenue': revenue,
            'sale_count': len(stage_sales),
        })

    total_depreciated = sum(
        ((s.input_quantity or Decimal('0')) - (s.output_quantity or Decimal('0')))
        for s in stages
    )
    total_waste_quantity = sum((s.waste_quantity or Decimal('0')) for s in stages)

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
        'total_waste_quantity': total_waste_quantity,
        'total_processing_cost': total_processing_cost,
        'total_value_added': total_value_added,
        'processed_in_store': processed_in_store,
        'chart_data': chart_data,
        'pie_data': pie_data,
        'can_write': request.user.is_superuser or accessible_farms(
            request.user, write_model='crops.harvestrecord').exists(),
    }
    return render(request, 'crops/harvest_detail.html', context)


@login_required
def crop_season_detail_view(request, pk):
    season = get_object_or_404(
        scoped_queryset(
            CropSeason, request.user,
            CropSeason.objects.select_related('crop', 'field'),
        ).prefetch_related(
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
        raw_sold = _sold_qty(raw_sales, harvest.unit)
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
            sold = _sold_qty(stage_sales, stage.output_unit or harvest.unit)
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
    primary_unit = crop.default_unit if crop else 'kg'
    total_quantity_sold = _sold_qty(sales, primary_unit or 'kg')
    total_value_added = sum((s.added_value for s in processing_stages), Decimal('0'))
    total_processing_cost = sum((s.cost for s in processing_stages), Decimal('0'))
    total_depreciated = sum(
        ((s.input_quantity or Decimal('0')) - (s.output_quantity or Decimal('0')))
        for s in processing_stages
    )
    total_expenses = season.total_expenses
    # Stage costs sync into the expense ledger (Expense.processing_stage) and
    # are already counted in total_expenses - do not subtract twice.
    net_profit = total_revenue - total_expenses

    yield_per_acre = None
    if field and field.acreage:
        yield_per_acre = total_harvested / field.acreage
    yield_variance = (
        total_harvested - season.expected_yield
        if season.expected_yield is not None else None
    )

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
        'expected_yield': season.expected_yield,
        'yield_per_acre': yield_per_acre,
        'yield_per_plant': season.yield_per_plant,
        'yield_variance': yield_variance,
        'yield_unit': primary_unit,
        'chart_data': chart_data,
        'pie_data': pie_data,
        'can_write': request.user.is_superuser or accessible_farms(
            request.user, write_model='crops.cropseason').exists(),
    }
    return render(request, 'crops/crop_season_detail.html', context)


@login_required
def customer_detail_view(request, pk):
    from .models import Customer
    customer = get_object_or_404(
        scoped_queryset(Customer, request.user, Customer.objects.all()), pk=pk)
    sales = list(
        Sale.objects.filter(customer=customer)
        .select_related('harvest__crop_season__crop', 'processing_stage', 'inventory_item')
        .order_by('-sale_date'))
    total_spend = sum((s.total_amount for s in sales), Decimal('0'))
    outstanding = sum((s.receivable for s in sales), Decimal('0'))
    last_sale = sales[0].sale_date if sales else None
    return render(request, 'crops/customer_detail.html', {
        'customer': customer,
        'sales': sales,
        'total_spend': total_spend,
        'outstanding': outstanding,
        'last_sale': last_sale,
        'can_write': request.user.is_superuser or accessible_farms(
            request.user, write_model='crops.customer').exists(),
    })


@login_required
def sale_detail_view(request, pk):
    sale = get_object_or_404(
        scoped_queryset(
            Sale, request.user,
            Sale.objects.select_related('harvest__crop_season__crop', 'harvest__crop_season__field', 'processing_stage'),
        ),
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


class SalePaymentForm(forms.Form):
    amount = forms.DecimalField(
        min_value=Decimal('0.01'), max_digits=14, decimal_places=2,
        help_text='Amount collected from the buyer now.')

    def __init__(self, *args, balance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.balance = balance if balance is not None else Decimal('0')

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount > self.balance:
            raise forms.ValidationError(
                f'Amount exceeds the outstanding balance ({self.balance}).')
        return amount


@login_required
def sale_record_payment_view(request, pk):
    """Record a payment against a sale's outstanding receivable."""
    sale = get_object_or_404(scoped_queryset(Sale, request.user, write=True), pk=pk)
    balance = sale.receivable
    if request.method == 'POST':
        form = SalePaymentForm(request.POST, balance=balance)
        if form.is_valid():
            received = (sale.amount_received or Decimal('0')) + form.cleaned_data['amount']
            if received >= sale.total_amount:
                sale.payment_status = Sale.PaymentStatus.PAID
                sale.amount_received = sale.total_amount
            else:
                sale.payment_status = Sale.PaymentStatus.PARTIAL
                sale.amount_received = received
            sale.save()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': True, 'redirect': reverse(
                    'model_list', args=['crops', 'sale'])})
            return HttpResponseRedirect(reverse('model_list', args=['crops', 'sale']))
    else:
        form = SalePaymentForm(balance=balance, initial={'amount': balance})
    return render(request, 'dashboard/form.html', {
        'app': 'crops', 'model': 'sale', 'model_name': 'Payment',
        'form': form, 'action': 'Record',
    })


@login_required
def waste_detail_view(request, pk):
    sale = get_object_or_404(
        scoped_queryset(
            Sale, request.user,
            Sale.objects.select_related('harvest__crop_season__crop', 'harvest__crop_season__field', 'processing_stage'),
        ),
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

    total_sold = _sold_qty(waste_sales, source['unit'])
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
        running += s.quantity_in_unit(source['unit'])
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
        'can_write': request.user.is_superuser or accessible_farms(
            request.user, write_model='crops.sale').exists(),
    }
    return render(request, 'crops/waste_detail.html', context)


# ---------------- Invoices & receipts ----------------

def _invoice_write_farms(user):
    return accessible_farms(user, write_model='crops.invoice')


def _sale_farm(sale):
    """Farm a sale belongs to (mirrors services._sale_farm_profile)."""
    harvest = sale.harvest
    if harvest and harvest.crop_season_id and harvest.crop_season.field_id:
        return harvest.crop_season.field.farm_profile
    if sale.inventory_item_id:
        return sale.inventory_item.farm_profile
    return None


@login_required
def invoice_list_view(request):
    from django.core.paginator import Paginator
    from django.db.models import DecimalField, F, Q, Sum
    from django.db.models.functions import Coalesce

    q = request.GET.get('q', '')
    status = request.GET.get('status', '')
    customer_id = request.GET.get('customer', '')
    kind = request.GET.get('kind', '')

    queryset = scoped_queryset(
        Invoice, request.user,
        Invoice.objects.select_related('customer', 'converted_invoice')
        .prefetch_related('items', 'payments'))
    if kind in Invoice.Kind.values:
        queryset = queryset.filter(kind=kind)
    queryset = queryset.annotate(
        billed=Coalesce(Sum(
            F('items__quantity') * F('items__unit_price'),
            output_field=DecimalField()), Decimal('0'), output_field=DecimalField()),
        paid=Coalesce(Sum('payments__amount', output_field=DecimalField()),
                      Decimal('0'), output_field=DecimalField()),
    )
    if q:
        queryset = queryset.filter(
            Q(number__icontains=q) | Q(buyer__icontains=q) | Q(notes__icontains=q))
    if status:
        queryset = queryset.filter(status=status)
    if customer_id:
        queryset = queryset.filter(customer_id=customer_id)

    today = date.today()
    rows = []
    for inv in queryset:
        is_proforma = inv.kind == Invoice.Kind.PROFORMA
        balance = Decimal('0') if is_proforma else max(inv.billed - inv.paid, Decimal('0'))
        rows.append({
            'pk': inv.pk,
            'number': inv.number,
            'kind': inv.kind,
            'is_proforma': is_proforma,
            'converted_number': (inv.converted_invoice.number
                                 if inv.converted_invoice_id else ''),
            'converted_url': (reverse('invoice_detail', args=[inv.converted_invoice_id])
                              if inv.converted_invoice_id else ''),
            'buyer': inv.buyer or (inv.customer.name if inv.customer else '-'),
            'date': inv.date,
            'due_date': inv.due_date,
            'total': inv.billed,
            'paid': inv.paid,
            'balance': balance,
            'status': inv.status,
            'status_label': inv.get_status_display(),
            'overdue': bool(not is_proforma and inv.due_date and inv.due_date < today
                            and balance > 0 and inv.status != Invoice.Status.CANCELLED),
            'detail_url': reverse('invoice_detail', args=[inv.pk]),
            'payment_url': reverse('invoice_record_payment', args=[inv.pk]),
            'convert_url': reverse('invoice_convert', args=[inv.pk]),
            'edit_url': reverse('invoice_edit', args=[inv.pk]),
            'delete_url': reverse('invoice_delete', args=[inv.pk]),
            'pdf_url': reverse('invoice_pdf', args=[inv.pk]),
        })

    real = [r for r in rows if not r['is_proforma']]
    stats = [
        {'label': 'Invoices', 'value': len(real), 'icon': 'layers', 'color': 'green'},
        {'label': 'Total Billed', 'value': sum((r['total'] for r in real), Decimal('0')),
         'icon': 'money', 'color': 'blue', 'is_money': True},
        {'label': 'Outstanding', 'value': sum((r['balance'] for r in real), Decimal('0')),
         'icon': 'money', 'color': 'amber', 'is_money': True},
        {'label': 'Proformas', 'value': sum(1 for r in rows if r['is_proforma']),
         'icon': 'check', 'color': 'amber'},
    ]
    can_write = request.user.is_superuser or _invoice_write_farms(request.user).exists()
    context = {
        'rows': rows,
        'stats': stats,
        'q': q,
        'status': status,
        'kind': kind,
        'customer_id': customer_id,
        'status_choices': Invoice.Status.choices,
        'customers': scoped_queryset(Customer, request.user).order_by('name'),
        'can_write': can_write,
        'create_url': reverse('invoice_create'),
        'create_proforma_url': reverse('invoice_create') + '?kind=PROFORMA',
        'title': 'Invoices',
        'subtitle': 'Bill customers for produce and inventory sales, record payments, and print.',
        'today': today,
    }
    return render(request, 'invoices/invoice_list.html', context)


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['farm_profile', 'kind', 'customer', 'buyer', 'date', 'due_date',
                  'payment_terms', 'reference', 'tax_percent', 'discount',
                  'payment_details', 'status', 'notes']
        labels = {'kind': 'Document type'}
        help_texts = {
            'due_date': 'For a proforma this is the "valid until" date.',
        }
        widgets = {
            'buyer': forms.TextInput(attrs={'placeholder': 'Leave blank to use the customer name'}),
            'payment_terms': forms.TextInput(attrs={'placeholder': 'e.g. Net 30'}),
            'reference': forms.TextInput(attrs={'placeholder': 'PO / reference number'}),
            'payment_details': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Bank name, account name, account no, mobile money...'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Notes & terms shown on the invoice...'}),
        }

    def __init__(self, *args, user=None, initial_kind=None, **kwargs):
        super().__init__(*args, **kwargs)
        farms = _invoice_write_farms(user) if user else accessible_farms(None)
        self.fields['farm_profile'].queryset = farms
        self.fields['farm_profile'].initial = farms.first()
        self.fields['customer'].queryset = scoped_queryset(
            Customer, user, Customer.objects.all()) if user else Customer.objects.none()
        self.fields['customer'].required = False
        self.fields['buyer'].required = False
        self.fields['kind'].required = False  # falls back to the model default
        # Proformas never carry payments, so the paid statuses do not apply.
        self.fields['status'].choices = [
            c for c in Invoice.Status.choices
            if c[0] not in (Invoice.Status.PARTIAL, Invoice.Status.PAID)
        ] if (initial_kind == Invoice.Kind.PROFORMA
              or self.instance.kind == Invoice.Kind.PROFORMA) else Invoice.Status.choices
        if self.instance.pk is None:
            self.fields['date'].initial = date.today()
            if initial_kind in Invoice.Kind.values:
                self.initial['kind'] = initial_kind
            default_farm = farms.first()
            if default_farm:
                if default_farm.invoice_payment_details:
                    self.initial['payment_details'] = default_farm.invoice_payment_details
                if default_farm.invoice_notes:
                    self.initial['notes'] = default_farm.invoice_notes

    def clean_kind(self):
        return self.cleaned_data.get('kind') or Invoice.Kind.INVOICE


@login_required
def invoice_form_view(request, pk=None):
    from django.forms import inlineformset_factory
    invoice = get_object_or_404(
        scoped_queryset(Invoice, request.user, Invoice.objects.all()), pk=pk) if pk else Invoice()
    if pk is None and not (_invoice_write_farms(request.user).exists()
                           or request.user.is_superuser):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    Items = inlineformset_factory(
        Invoice, InvoiceItem,
        fields=['description', 'detail', 'quantity', 'unit', 'unit_price', 'unit_cost'],
        extra=1, can_delete=True, max_num=50, validate_max=True)
    data = request.POST if request.method == 'POST' else None
    form = InvoiceForm(data, instance=invoice, user=request.user,
                       initial_kind=request.GET.get('kind', ''))
    items = Items(data, instance=invoice, prefix='items')
    if data is not None:
        if form.is_valid() and items.is_valid():
            with transaction.atomic():
                invoice = form.save()
                items.instance = invoice
                items.save()
                invoice.refresh_status()
            return HttpResponseRedirect(reverse('invoice_detail', args=[invoice.pk]))
    is_proforma = (invoice.kind == Invoice.Kind.PROFORMA if pk
                   else request.GET.get('kind') == Invoice.Kind.PROFORMA)
    return render(request, 'invoices/invoice_form.html', {
        'form': form,
        'items': items,
        'invoice': invoice,
        'action': 'Edit' if pk else 'New',
        'doc_label': 'Proforma Invoice' if is_proforma else 'Invoice',
    })


@login_required
def invoice_detail_view(request, pk):
    invoice = get_object_or_404(
        scoped_queryset(Invoice, request.user,
                        Invoice.objects.select_related('customer', 'farm_profile__default_currency')),
        pk=pk)
    items = list(invoice.items.all())
    payments = list(invoice.payments.all())
    bank = invoice.farm_profile.bank_accounts.filter(is_active=True).first()
    max_quantity = max((item.quantity for item in items), default=Decimal('0'))
    max_unit_price = max((item.unit_price for item in items), default=Decimal('0'))
    max_unit_cost = max((item.unit_cost for item in items), default=Decimal('0'))
    max_money = max(
        max((item.total for item in items), default=Decimal('0')),
        max((item.production_cost for item in items), default=Decimal('0')))
    chart_rows = []
    for item in items:
        chart_rows.append({
            'description': item.description,
            'detail': item.detail,
            'quantity': item.quantity,
            'unit': item.unit,
            'unit_price': item.unit_price,
            'unit_cost': item.unit_cost,
            'revenue': item.total,
            'production_cost': item.production_cost,
            'quantity_pct': round(float(item.quantity / max_quantity * 100), 1) if max_quantity else 0,
            'price_pct': round(float(item.unit_price / max_unit_price * 100), 1) if max_unit_price else 0,
            'cost_pct': round(float(item.unit_cost / max_unit_cost * 100), 1) if max_unit_cost else 0,
            'revenue_pct': round(float(item.total / max_money * 100), 1) if max_money else 0,
            'production_cost_pct': round(float(item.production_cost / max_money * 100), 1) if max_money else 0,
        })
    line_chart = None
    if chart_rows:
        import math
        width, height = 620, 240
        m_left, m_right, m_top, m_bottom = 58, 14, 12, 30
        plot_w = width - m_left - m_right
        plot_h = height - m_top - m_bottom
        raw_step = float(max_money) / 4 if max_money else 0
        mag = 10 ** math.floor(math.log10(raw_step)) if raw_step > 0 else 1
        step = next(
            (m * mag for m in (1, 2, 2.5, 5, 10) if raw_step <= m * mag),
            mag)
        top_value = step * 4 or 1

        def y_pos(value):
            return m_top + plot_h - (float(value) / top_value) * plot_h

        count = len(chart_rows)

        def x_pos(i):
            return m_left + (plot_w * i / (count - 1)
                             if count > 1 else plot_w / 2)

        points, rev_pts, cost_pts = [], [], []
        for i, row in enumerate(chart_rows):
            xi = round(x_pos(i), 1)
            ry = round(y_pos(row['revenue']), 1)
            cy = round(y_pos(row['production_cost']), 1)
            rev_pts.append(f'{xi},{ry}')
            cost_pts.append(f'{xi},{cy}')
            points.append({'x': xi, 'ry': ry, 'cy': cy,
                           'label': (row['description'] or '')[:14]})
        line_chart = {
            'width': width, 'height': height,
            'plot_left': m_left, 'plot_right': width - m_right,
            'axis_y': round(m_top + plot_h, 1),
            'x_label_y': height - 8,
            'rev_points': ' '.join(rev_pts),
            'cost_points': ' '.join(cost_pts),
            'points': points,
            'ticks': [{'y': round(y_pos(step * i), 1),
                       'label_y': round(y_pos(step * i), 1) + 3,
                       'value': step * i} for i in range(5)],
        }
    can_write = request.user.is_superuser or _invoice_write_farms(request.user).exists()
    source_proforma = (Invoice.objects.filter(converted_invoice=invoice).first()
                       if not invoice.is_proforma else None)
    return render(request, 'invoices/invoice_detail.html', {
        'invoice': invoice,
        'source_proforma': source_proforma,
        'items': items,
        'chart_rows': chart_rows,
        'line_chart': line_chart,
        'total_production_cost': sum((item.production_cost for item in items), Decimal('0')),
        'payments': payments,
        'subtotal': invoice.subtotal,
        'tax_amount': invoice.tax_amount,
        'total': invoice.total,
        'amount_paid': invoice.amount_paid,
        'balance': invoice.balance,
        'bank': bank,
        'can_write': can_write,
        'payment_url': reverse('invoice_record_payment', args=[invoice.pk]),
        'convert_url': reverse('invoice_convert', args=[invoice.pk]),
        'edit_url': reverse('invoice_edit', args=[invoice.pk]),
        'delete_url': reverse('invoice_delete', args=[invoice.pk]),
        'pdf_url': reverse('invoice_pdf', args=[invoice.pk]),
    })


def _amount_in_words(amount):
    """'1234.50' -> 'One Thousand Two Hundred Thirty-Four and 50/100'."""
    ones = ('', 'One', 'Two', 'Three', 'Four', 'Five', 'Six', 'Seven', 'Eight',
            'Nine', 'Ten', 'Eleven', 'Twelve', 'Thirteen', 'Fourteen', 'Fifteen',
            'Sixteen', 'Seventeen', 'Eighteen', 'Nineteen')
    tens = ('', '', 'Twenty', 'Thirty', 'Forty', 'Fifty', 'Sixty', 'Seventy',
            'Eighty', 'Ninety')

    def three(n):
        h, r = divmod(n, 100)
        parts = [f'{ones[h]} Hundred'] if h else []
        if r >= 20:
            t, o = divmod(r, 10)
            parts.append(tens[t] + (f'-{ones[o]}' if o else ''))
        elif r:
            parts.append(ones[r])
        return ' '.join(parts)

    n = int(amount)
    cents = int(round((float(amount) - n) * 100))
    if not n:
        words = 'Zero'
    else:
        groups = []
        for scale, label in ((10 ** 9, ' Billion'), (10 ** 6, ' Million'),
                             (10 ** 3, ' Thousand'), (1, '')):
            g, n = divmod(n, scale)
            if g:
                groups.append(three(g) + label)
        words = ' '.join(groups)
    return f'{words} and {cents:02d}/100'


def _fiscal_verification_code(*parts):
    """Deterministic XXXX-XXXX-XXXX code from the document's core data."""
    import hashlib
    digest = hashlib.sha256('|'.join(str(p) for p in parts).encode()).hexdigest()
    return '-'.join(digest.upper()[i:i + 4] for i in range(0, 12, 4))


def _qr_png_bytes(payload):
    """QR code PNG in a BytesIO buffer, ready for canvas.drawImage."""
    from io import BytesIO as _IO
    import qrcode
    buf = _IO()
    qrcode.make(payload, box_size=4, border=1).save(buf, format='PNG')
    buf.seek(0)
    return buf


def _fiscal_qr_image(payload, mm_size=24):
    """A reportlab Image flowable holding a QR code for the given payload."""
    from reportlab.lib.units import mm as _mm
    from reportlab.platypus import Image as _Img
    return _Img(_qr_png_bytes(payload), width=mm_size * _mm,
                height=mm_size * _mm)


FISCAL_BAND_HEIGHT_MM = 30


def _document_fonts():
    """(regular, bold, italic) for fiscal documents.

    Registers Arial from the OS font dir when available (the invoice
    template's font); falls back to Helvetica on systems without it.
    """
    from reportlab.pdfbase import pdfmetrics
    if 'DocSans' in pdfmetrics.getRegisteredFontNames():
        bold = 'DocSans-Bold' if 'DocSans-Bold' in pdfmetrics.getRegisteredFontNames() else 'DocSans'
        ital = 'DocSans-Italic' if 'DocSans-Italic' in pdfmetrics.getRegisteredFontNames() else 'DocSans'
        return 'DocSans', bold, ital
    import os
    from reportlab.pdfbase.ttfonts import TTFont
    font_dirs = [os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts'),
                 '/usr/share/fonts/truetype/msttcorefonts',
                 '/usr/share/fonts/truetype/ttf-mscorefonts',
                 '/usr/share/fonts/truetype']
    registered = {}
    for reg_name, candidates in {
            'DocSans': ('arial.ttf', 'Arial.ttf'),
            'DocSans-Bold': ('arialbd.ttf', 'arialbd.TTF', 'Arial Bold.ttf'),
            'DocSans-Italic': ('ariali.ttf', 'ariali.TTF', 'Arial Italic.ttf')}.items():
        for base in font_dirs:
            for fname in candidates:
                path = os.path.join(base, fname)
                if os.path.exists(path):
                    try:
                        pdfmetrics.registerFont(TTFont(reg_name, path))
                        registered[reg_name] = True
                    except Exception:
                        pass
                    break
            if reg_name in registered:
                break
    if 'DocSans' in registered:
        bold = 'DocSans-Bold' if 'DocSans-Bold' in registered else 'DocSans'
        ital = 'DocSans-Italic' if 'DocSans-Italic' in registered else 'DocSans'
        pdfmetrics.registerFontFamily(
            'DocSans', normal='DocSans', bold=bold, italic=ital,
            boldItalic=bold)
        return 'DocSans', bold, ital
    return 'Helvetica', 'Helvetica-Bold', 'Helvetica-Oblique'


def _draw_fiscal_band(c, page_width, page_height, *, farm_name, contact,
                      tin, title, subtitle, logo_path=None):
    """Letterhead matching the shared invoice document: logo + farm name +
    contact and TIN lines on the left, document title + number on the
    right, closed by a single green rule. Drawn directly on the canvas.
    """
    from reportlab.lib import colors
    from reportlab.lib.units import mm

    font, font_b, _ = _document_fonts()
    green = colors.HexColor('#2F5D3A')
    muted = colors.HexColor('#6B7A8D')
    tint = colors.HexColor('#DDE3E8')
    band_h = FISCAL_BAND_HEIGHT_MM * mm
    top = page_height
    left = 18 * mm
    right = page_width - 18 * mm

    # Identity block is vertically centred on the logo.
    block_top = top - 7 * mm
    logo_h = 19 * mm
    centre_y = block_top - logo_h / 2
    x = left
    if logo_path:
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(logo_path) as pim:
                lw, lh = pim.size
            logo_w = logo_h * (lw / lh) if lh else logo_h
            c.drawImage(logo_path, x, block_top - logo_h,
                        width=logo_w, height=logo_h, mask='auto')
            x += logo_w + 5 * mm
        except Exception:
            pass

    # Farm name + contact + TIN, stacked and centred against the logo.
    lines = [l for l in (contact, tin) if l]
    stack_h = 15 * 0.72 + len(lines) * 4.6 * mm
    y = centre_y + stack_h / 2 - 15 * 0.72
    c.setFillColor(green)
    c.setFont(font_b, 15)
    c.drawString(x, y, farm_name)
    c.setFillColor(muted)
    c.setFont(font, 8.5)
    for line in lines:
        y -= 4.6 * mm
        c.drawString(x, y, line)

    # Title block, right-aligned and centred on the same axis. Long titles
    # (e.g. PROFORMA INVOICE) step down so they never run into the farm name.
    from reportlab.pdfbase.pdfmetrics import stringWidth
    title_size = 24
    max_title_w = right - x - stringWidth(farm_name, font_b, 15) - 8 * mm
    while title_size > 14 and stringWidth(title, font_b, title_size) > max_title_w:
        title_size -= 1
    c.setFillColor(green)
    c.setFont(font_b, title_size)
    c.drawRightString(right, centre_y + 1 * mm, title)
    c.setFillColor(muted)
    c.setFont(font_b, 9.5)
    c.drawRightString(right, centre_y - 4.5 * mm, subtitle)

    # Closing rule: slim green accent under the title, soft hairline across.
    rule_y = top - band_h
    c.setStrokeColor(tint)
    c.setLineWidth(0.6)
    c.line(left, rule_y, right, rule_y)
    c.setStrokeColor(green)
    c.setLineWidth(2)
    c.line(right - 42 * mm, rule_y, right, rule_y)


@login_required
def invoice_pdf_view(request, pk):
    """Download the invoice as a print-ready PDF (reportlab)."""
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.platypus import (
        HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate, Spacer,
        Table, TableStyle)

    invoice = get_object_or_404(
        scoped_queryset(Invoice, request.user,
                        Invoice.objects.select_related('customer', 'farm_profile')),
        pk=pk)
    items = list(invoice.items.all())
    bank = invoice.farm_profile.bank_accounts.filter(is_active=True).first()
    farm = invoice.farm_profile
    currency_obj = farm.default_currency
    symbol = currency_obj.symbol if currency_obj else ''

    def money(value):
        return f'{symbol} {value:,.2f}'

    font, font_b, font_i = _document_fonts()
    green = colors.HexColor('#2F5D3A')
    muted = colors.HexColor('#6B7A8D')
    line_c = colors.HexColor('#DDE3E8')
    shade = colors.HexColor('#F3F6F4')
    red = colors.HexColor('#C00000')
    label_style = ParagraphStyle(
        'SectionLabel', fontName=font_b, fontSize=9, leading=12,
        textColor=green)
    name_style = ParagraphStyle(
        'Name', fontName=font_b, fontSize=12, leading=15,
        textColor=colors.black)
    muted_style = ParagraphStyle(
        'Muted', fontName=font, fontSize=10, leading=13, textColor=muted)
    cell_style = ParagraphStyle(
        'Cell', fontName=font, fontSize=10, leading=13, textColor=colors.black)
    cell_muted = ParagraphStyle('CellMuted', parent=cell_style, fontSize=8, textColor=muted)
    cell_bold = ParagraphStyle('CellBold', parent=cell_style, fontName=font_b)
    numeric_style = ParagraphStyle(
        'Numeric', parent=cell_style, alignment=2, fontName=font)
    numeric_bold = ParagraphStyle(
        'NumericBold', parent=numeric_style, fontName=font_b)
    head_cell = ParagraphStyle(
        'HeadCell', parent=cell_style, fontName=font_b, textColor=green)
    head_cell_r = ParagraphStyle('HeadCellR', parent=head_cell, alignment=2)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=(FISCAL_BAND_HEIGHT_MM + 6) * mm, bottomMargin=38 * mm,
        title=f'Invoice {invoice.number}')

    class LastPageSignatureCanvas(canvas.Canvas):
        """Keep the authorization area and thank-you line at the page foot."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            saved = self._saved_page_states
            page_count = len(saved)
            for state in saved:
                self.__dict__.update(state)
                if self._pageNumber == 1:
                    self._draw_invoice_header()
                if self._pageNumber == page_count:
                    self._draw_invoice_footer()
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

        def _draw_invoice_header(self):
            _draw_fiscal_band(
                self, A4[0], A4[1], farm_name=farm.farm_name,
                contact=contact, tin=tin_line,
                title=invoice.document_title,
                subtitle=f'No. {invoice.number}',
                logo_path=farm.logo.path if farm.logo else None)

        def _draw_invoice_footer(self):
            page_width, _ = A4
            left = 18 * mm
            right = page_width - 18 * mm

            if is_proforma:
                # A proforma is a quotation, not a fiscal document: no QR or
                # verification code - just the disclaimer.
                self.setFillColor(green)
                self.setFont(font_b, 9)
                self.drawString(left, 32.5 * mm, 'PROFORMA - NOT A TAX INVOICE')
                self.setFillColor(muted)
                self.setFont(font, 8.5)
                self.drawString(
                    left, 27.5 * mm,
                    'This document is a quotation for the goods/services listed '
                    'and is not a demand for payment.')
                self.drawString(
                    left, 23 * mm,
                    'A tax invoice will be issued on confirmation of the order.')
                if invoice.due_date:
                    self.drawString(
                        left, 18.5 * mm,
                        f'Valid until {invoice.due_date.strftime("%d / %m / %Y")}.')
            else:
                # Fiscal verification block, bottom-left beside the signature.
                from reportlab.lib.utils import ImageReader
                qr_size = 19 * mm
                self.drawImage(ImageReader(qr_buf), left, 16 * mm,
                               width=qr_size, height=qr_size, mask='auto')
                tx = left + qr_size + 4 * mm
                self.setFillColor(green)
                self.setFont(font_b, 9)
                self.drawString(tx, 32.5 * mm, 'FISCAL VERIFICATION')
                self.setFillColor(colors.black)
                self.setFont(font_b, 10)
                self.drawString(tx, 27.5 * mm, f'Verification code: {ver_code}')
                self.setFillColor(muted)
                self.setFont(font, 9)
                self.drawString(
                    tx, 22.5 * mm,
                    f'Document no. {invoice.number or "-"} \u00b7 issued '
                    f'{invoice.date.strftime("%d / %m / %Y")}')
                self.setFont(font, 8)
                self.drawString(
                    tx, 18 * mm,
                    'Electronically generated fiscal document - '
                    'valid without a signature.')

            line_start = right - 56 * mm
            signature_y = 28 * mm
            self.setStrokeColor(colors.HexColor('#9ca3af'))
            self.setLineWidth(.65)
            self.line(line_start, signature_y, right, signature_y)
            self.setFillColor(muted)
            self.setFont(font, 9)
            self.drawCentredString(
                (line_start + right) / 2, signature_y - 4 * mm,
                'Authorized signature')

            self.setStrokeColor(line_c)
            self.setLineWidth(.5)
            self.line(18 * mm, 14 * mm, right, 14 * mm)
            self.setFillColor(green)
            self.setFont(font_b, 10)
            self.drawCentredString(
                page_width / 2, 8 * mm, 'Thank you for your business!')

    story = []
    is_proforma = invoice.is_proforma
    # The letterhead band is drawn on the canvas (see
    # LastPageSignatureCanvas._draw_invoice_header): contact on one line,
    # TIN on the next - matching the invoice document template.
    contact = ' \u00b7 '.join(
        p for p in (farm.location, farm.phone, farm.email) if p)
    tin_line = f'TIN: {farm.tax_id}' if farm.tax_id else ''

    billed = [
        Paragraph('BILLED TO', label_style),
        Spacer(1, 1.5 * mm),
        Paragraph(escape(invoice.buyer or '-'), name_style),
    ]
    if invoice.customer:
        if invoice.customer.tin:
            billed.append(Paragraph(
                f'<font color="#64748b">TIN:</font> {escape(invoice.customer.tin)}',
                muted_style))
        for line in (invoice.customer.phone, invoice.customer.email):
            if line:
                billed.append(Paragraph(escape(line), muted_style))
    details_rows = [[Paragraph(
        'PROFORMA DETAILS' if is_proforma else 'INVOICE DETAILS', label_style), '']]
    for key, value in (
            ('Date', invoice.date.strftime('%d / %m / %Y')),
            ('Valid until' if is_proforma else 'Due date',
             invoice.due_date.strftime('%d / %m / %Y') if invoice.due_date else '-'),
            ('Payment terms', invoice.payment_terms or '-'),
            ('Reference', invoice.reference or '-')):
        details_rows.append([
            Paragraph(escape(key), muted_style),
            Paragraph(escape(value), ParagraphStyle(
                'DetailValue', parent=muted_style, alignment=2,
                fontName=font_b, textColor=colors.black)),
        ])
    details = Table(details_rows, colWidths=[doc.width * .23, doc.width * .20])
    details.setStyle(TableStyle([
        ('SPAN', (0, 0), (-1, 0)),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 1), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 2),
    ]))
    meta = Table([[billed, details]], colWidths=[doc.width * 0.55, doc.width * 0.45])
    meta.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(meta)
    story.append(Spacer(1, 9 * mm))

    # Items table - plain columns, green bold headers over a green rule,
    # light row separators, matching the invoice document.
    cur_code = getattr(currency_obj, 'code', '') or 'UGX'
    data = [[Paragraph('#', head_cell),
             Paragraph('DESCRIPTION', head_cell),
             Paragraph('QTY', head_cell_r),
             Paragraph(f'RATE ({cur_code})', head_cell_r),
             Paragraph(f'AMOUNT ({cur_code})', head_cell_r)]]
    for line_no, item in enumerate(items, 1):
        desc = [Paragraph(escape(item.description), cell_style)]
        if item.detail:
            desc.append(Paragraph(escape(item.detail), cell_muted))
        data.append([
            Paragraph(str(line_no), cell_style),
            desc,
            Paragraph(f'{item.quantity:,.2f} {escape(item.unit)}', numeric_style),
            Paragraph(money(item.unit_price), numeric_style),
            Paragraph(money(item.total), numeric_style),
        ])
    if not items:
        data.append(['', Paragraph('No line items yet', cell_style), '', '', ''])
    t = Table(data, colWidths=[
        doc.width * 0.05, doc.width * 0.44, doc.width * 0.14,
        doc.width * 0.18, doc.width * 0.19])
    t.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, green),
        ('LINEBELOW', (0, 1), (-1, -2), 0.5, line_c),
        ('ALIGN', (2, 0), (-1, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 4),
        ('RIGHTPADDING', (0, 0), (-1, -1), 4),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 6 * mm))

    # EFRIS-style tax summary (URA requirement: category, gross, tax, net).
    rate = float(invoice.tax_percent or 0)
    category = (f'A - Standard rate ({rate:g}%)' if rate > 0
                else 'C - Exempt (0%)')
    story.append(Paragraph('TAX SUMMARY', label_style))
    story.append(Spacer(1, 1.5 * mm))
    tax_table = Table(
        [[Paragraph('Tax Category', head_cell),
          Paragraph('Gross Amount', head_cell_r),
          Paragraph('Tax Amount', head_cell_r),
          Paragraph('Net Amount', head_cell_r)],
         [Paragraph(escape(category), cell_style),
          Paragraph(money(invoice.subtotal + invoice.tax_amount), numeric_style),
          Paragraph(money(invoice.tax_amount), numeric_style),
          Paragraph(money(invoice.subtotal), numeric_style)]],
        colWidths=[doc.width * 0.40, doc.width * 0.20,
                   doc.width * 0.20, doc.width * 0.20])
    tax_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), shade),
        ('GRID', (0, 0), (-1, -1), 0.5, line_c),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(tax_table)
    story.append(Spacer(1, 6 * mm))

    # Payment details + totals
    pay_block = [Paragraph('PAYMENT DETAILS', label_style), Spacer(1, 2 * mm)]
    payment_details = invoice.payment_details or farm.invoice_payment_details
    if payment_details:
        for line in payment_details.splitlines():
            if line.strip():
                pay_block.append(Paragraph(escape(line.strip()), muted_style))
    elif bank:
        pay_rows = Table(
            [[Paragraph(escape(key), muted_style),
              Paragraph(escape(value), ParagraphStyle(
                  'PayValue', parent=cell_style, alignment=2,
                  fontName=font_b))]
             for key, value in (
                 ('Bank', bank.bank_name),
                 ('Account name', bank.name),
                 ('Account no.', f'\u2022\u2022\u2022\u2022 {bank.account_number_last4 or "----"}'))],
            colWidths=[doc.width * 0.17, doc.width * 0.17])
        pay_rows.setStyle(TableStyle([
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 1.5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 1.5),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        pay_block.append(pay_rows)
    else:
        pay_block.append(Paragraph(
            'Please contact the farm for payment instructions.', muted_style))

    # Highlight Total Due with color only; keep its type size and weight
    # aligned with the other summary amounts.
    total_label = ParagraphStyle(
        'TotalLabel', parent=cell_style, alignment=0, textColor=green)
    total_value = ParagraphStyle(
        'TotalValue', parent=numeric_style, alignment=2, textColor=green)
    totals = [
        [Paragraph('Subtotal', cell_style), Paragraph(money(invoice.subtotal), numeric_style)],
        [Paragraph(f'Tax ({escape(str(invoice.tax_percent or 0))}%)', cell_style),
         Paragraph(money(invoice.tax_amount), numeric_style)],
        [Paragraph('Discount', cell_style), Paragraph(money(invoice.discount or 0), numeric_style)],
        [Paragraph('ESTIMATED TOTAL' if is_proforma else 'TOTAL DUE', total_label),
         Paragraph(money(invoice.total), total_value)],
    ]
    if not is_proforma:
        totals += [
            [Paragraph('Amount paid', cell_style), Paragraph(money(invoice.amount_paid), numeric_style)],
            [Paragraph('<b>Balance due</b>', cell_style),
             Paragraph(money(invoice.balance), ParagraphStyle(
                 'BalanceValue', parent=numeric_bold, textColor=red))],
        ]
    totals_table = Table(totals, colWidths=[doc.width * 0.20, doc.width * 0.20])
    totals_table.hAlign = 'RIGHT'
    totals_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (0, -1), 'LEFT'),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('TOPPADDING', (0, 0), (-1, -1), 2.5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
    ]))
    bottom = Table([[pay_block, totals_table]],
                   colWidths=[doc.width * 0.58, doc.width * 0.42])
    bottom.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(bottom)
    story.append(Spacer(1, 4 * mm))

    cur_label = (getattr(currency_obj, 'name', '') or
                 getattr(currency_obj, 'code', '') or 'UGX')
    story.append(Paragraph(
        f'<font color="#6B7A8D"><i>'
        f'{"Estimated amount" if is_proforma else "Amount due"} in words:</i></font> '
        f'<i>{escape(cur_label)} {escape(_amount_in_words(invoice.total))} only.</i>',
        ParagraphStyle('InWords', parent=cell_style, fontSize=10,
                       fontName=font_i)))
    story.append(Spacer(1, 7 * mm))

    story.append(Paragraph('NOTES & TERMS', label_style))
    story.append(Spacer(1, 1.5 * mm))
    note_lines = [line.strip() for line in
                (invoice.notes or farm.invoice_notes).splitlines() if line.strip()]
    for line in note_lines or ['Please include the invoice number with your payment.']:
        story.append(Paragraph(escape(line), muted_style))

    # Fiscal block is drawn by the canvas footer at the bottom-left of the
    # final page, beside the signature line.
    ver_code = _fiscal_verification_code(
        invoice.pk, invoice.number, invoice.total, farm.tax_id)
    qr_payload = (f'{invoice.number}|{invoice.date}|'
                  f'{invoice.total} {cur_label}|TIN:{farm.tax_id}|{ver_code}')
    qr_buf = _qr_png_bytes(qr_payload)

    doc.build(story, canvasmaker=LastPageSignatureCanvas)
    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{invoice.number or "invoice"}.pdf"'
    return resp


class InvoicePaymentForm(forms.ModelForm):
    class Meta:
        model = InvoicePayment
        fields = ['amount', 'date', 'method', 'reference', 'notes']
        widgets = {
            'reference': forms.TextInput(attrs={'placeholder': 'Receipt no, transaction id...'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }

    def __init__(self, *args, invoice=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invoice = invoice
        if invoice is not None:
            self.fields['amount'].help_text = f'Outstanding balance: {invoice.balance}'
            self.fields['amount'].initial = invoice.balance
            self.fields['date'].initial = date.today()



    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if self.invoice is not None and amount > self.invoice.balance:
            raise forms.ValidationError(
                f'Payment exceeds the outstanding balance ({self.invoice.balance}).')
        return amount


@login_required
def invoice_convert_view(request, pk):
    """Raise a real invoice from a proforma (POST), then open it."""
    proforma = get_object_or_404(
        scoped_queryset(Invoice, request.user, write=True), pk=pk)
    if request.method != 'POST':
        return HttpResponseRedirect(reverse('invoice_detail', args=[proforma.pk]))
    if not proforma.is_proforma:
        return HttpResponseRedirect(reverse('invoice_detail', args=[proforma.pk]))
    invoice = proforma.convert_to_invoice()
    messages.success(
        request, f'Invoice {invoice.number} raised from proforma {proforma.number}.')
    return HttpResponseRedirect(reverse('invoice_detail', args=[invoice.pk]))


@login_required
def invoice_record_payment_view(request, pk):
    invoice = get_object_or_404(
        scoped_queryset(Invoice, request.user, write=True), pk=pk)
    if invoice.is_proforma:
        messages.error(
            request, 'A proforma invoice cannot take payments - convert it to '
                     'an invoice first.')
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'ok': False, 'redirect': reverse(
                'invoice_detail', args=[invoice.pk])}, status=400)
        return HttpResponseRedirect(reverse('invoice_detail', args=[invoice.pk]))
    if request.method == 'POST':
        form = InvoicePaymentForm(request.POST, invoice=invoice)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.invoice = invoice
            payment.save()
            invoice.refresh_status()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': True, 'redirect': reverse(
                    'invoice_detail', args=[invoice.pk])})
            return HttpResponseRedirect(reverse('invoice_detail', args=[invoice.pk]))
    else:
        form = InvoicePaymentForm(invoice=invoice)
    return render(request, 'dashboard/form.html', {
        'app': 'crops', 'model': 'invoice', 'model_name': 'Invoice Payment',
        'form': form, 'action': 'Record',
    })


@login_required
def invoice_delete_view(request, pk):
    invoice = get_object_or_404(
        scoped_queryset(Invoice, request.user, write=True), pk=pk)
    if request.method == 'POST':
        invoice.delete()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'ok': True, 'redirect': reverse('invoice_list')})
        return HttpResponseRedirect(reverse('invoice_list'))
    return render(request, 'dashboard/confirm_delete.html', {
        'model_name': 'Invoice',
        'obj': str(invoice),
        'protected_counts': [],
    })




def _receipt_pdf_response(farm, *, title, receipt_no, payment_date,
                          received_name, received_lines, pay_description,
                          pay_sub, pay_date, pay_method, amount,
                          amount_caption, processed_by, filename,
                          doc_ref='', meta_lines=()):
    """Render a one-page payment receipt as a downloadable PDF.

    Layout mirrors the shared receipt design: brand header with an
    ECG-style accent line, RECEIVED FROM / PAYMENT SUMMARY sections,
    underlined signature lines, and a footer with farm details + QR +
    colour bar.
    """
    from io import BytesIO
    from xml.sax.saxutils import escape
    from django.utils import timezone
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    from reportlab.platypus import (
        HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=38 * mm, bottomMargin=52 * mm, title=title)

    primary = colors.HexColor('#2d6a4f')
    dark = colors.HexColor('#1b4332')
    muted = colors.HexColor('#64748b')
    border = colors.HexColor('#e3ebe6')
    label_style = ParagraphStyle(
        'SectionLabel', fontName='Helvetica-Bold', fontSize=8, leading=11,
        textColor=primary)
    muted_style = ParagraphStyle(
        'Muted', fontName='Helvetica', fontSize=8, leading=11, textColor=muted)
    cell_style = ParagraphStyle(
        'Cell', fontName='Helvetica', fontSize=8.5, leading=11, textColor=dark)

    currency_obj = getattr(farm, 'default_currency', None)
    symbol = currency_obj.symbol if currency_obj else ''

    def money(value):
        return f'{symbol} {value:,.2f}'

    contact_parts = [p for p in (
        (farm.location if farm else ''), (farm.phone if farm else ''),
        (farm.email if farm else '')) if p]
    contact = ' \u00b7 '.join(contact_parts)

    cur_label = (getattr(currency_obj, 'name', '') or
                 getattr(currency_obj, 'code', '') or 'UGX')

    story = []
    # The document title + receipt number are rendered in the shared
    # letterhead band (same as the invoice header).
    story.append(Paragraph(
        f'Payment Date: <b>{escape(payment_date)}</b>',
        ParagraphStyle('PayDate', parent=cell_style, fontSize=8.5,
                       textColor=muted)))
    story.append(Spacer(1, 8 * mm))

    # RECEIVED FROM
    story.append(Paragraph('RECEIVED FROM', label_style))
    story.append(Spacer(1, 1.5 * mm))
    story.append(HRFlowable(width='100%', thickness=.6, color=border))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(escape(received_name or '-'), ParagraphStyle(
        'RecName', parent=cell_style, fontName='Helvetica-Bold',
        fontSize=10, leading=13)))
    for line in received_lines:
        if line:
            story.append(Paragraph(escape(line), muted_style))
    story.append(Spacer(1, 8 * mm))

    # PAYMENT SUMMARY
    story.append(Paragraph('PAYMENT SUMMARY', label_style))
    story.append(Spacer(1, 1.5 * mm))
    story.append(HRFlowable(width='100%', thickness=.6, color=border))
    story.append(Spacer(1, 3 * mm))
    head_cell = ParagraphStyle(
        'HeadCell', parent=cell_style, fontName='Helvetica-Bold',
        fontSize=8, textColor=colors.white)
    head_cell_r = ParagraphStyle('HeadCellR', parent=head_cell, alignment=2)
    pay_table = Table(
        [[Paragraph('Description', head_cell), Paragraph('Date', head_cell),
          Paragraph('Method', head_cell), Paragraph('Amount', head_cell_r)],
         [[Paragraph(escape(pay_description), ParagraphStyle(
             'PayDesc', parent=cell_style, fontName='Helvetica-Bold',
             fontSize=8.5)),
           Paragraph(escape(pay_sub), ParagraphStyle(
               'PaySub', parent=muted_style, fontSize=7.5))],
          Paragraph(escape(pay_date), cell_style),
          Paragraph(escape(pay_method), cell_style),
          Paragraph(money(amount), ParagraphStyle(
              'PayAmt', parent=cell_style, alignment=2,
              fontName='Helvetica-Bold'))]],
        colWidths=[doc.width * .44, doc.width * .19,
                   doc.width * .17, doc.width * .20])
    pay_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), primary),
        ('LINEBELOW', (0, 1), (-1, -1), .5, border),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
    ]))
    story.append(pay_table)
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        f'<b>{escape(amount_caption)}:</b>&nbsp;&nbsp;&nbsp;'
        f'<b>{money(amount)}</b>',
        ParagraphStyle('TotalPaid', parent=cell_style, alignment=2,
                       fontSize=10, leading=13)))
    for line in meta_lines:
        story.append(Paragraph(escape(line), ParagraphStyle(
            'MetaLine', parent=muted_style, fontSize=7.5, alignment=2)))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        f'<font color="#64748b">Amount in words:</font> '
        f'{escape(cur_label)} {escape(_amount_in_words(amount))} only.',
        ParagraphStyle('InWords', parent=cell_style, fontSize=8,
                       fontName='Helvetica-Oblique')))
    story.append(Spacer(1, 16 * mm))

    # Underlined signature lines
    sig = Table(
        [[Paragraph(f'Processed By: {escape(processed_by)}', ParagraphStyle(
            'SigL', parent=muted_style, alignment=1, fontSize=8)),
          Paragraph('Customer Signature', ParagraphStyle(
              'SigR', parent=muted_style, alignment=1, fontSize=8))]],
        colWidths=[doc.width * .5, doc.width * .5])
    sig.setStyle(TableStyle([
        ('LINEABOVE', (0, 0), (0, 0), .9, dark),
        ('LINEABOVE', (1, 0), (1, 0), .9, dark),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (0, 0), 0),
        ('LEFTPADDING', (1, 0), (1, 0), 18),
        ('RIGHTPADDING', (1, 0), (1, 0), 0),
        ('RIGHTPADDING', (0, 0), (0, 0), 18),
    ]))
    story.append(sig)

    # Footer content is drawn on the canvas at fixed positions.
    ver_code = _fiscal_verification_code(
        doc_ref or filename, amount,
        getattr(farm, 'tax_id', '') if farm else '')
    qr_payload = (f'{doc_ref or filename}|{amount} {cur_label}|'
                  f'TIN:{getattr(farm, "tax_id", "") if farm else ""}|{ver_code}')
    qr_buf = _qr_png_bytes(qr_payload)
    printed_on = timezone.localtime().strftime('%d %b %Y, %I:%M %p')

    class ReceiptCanvas(canvas.Canvas):
        """Header (page 1) and footer (last page) drawn at fixed positions."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            saved = self._saved_page_states
            page_count = len(saved)
            for state in saved:
                self.__dict__.update(state)
                if self._pageNumber == 1:
                    self._draw_header()
                if self._pageNumber == page_count:
                    self._draw_footer()
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

        def _draw_header(self):
            _draw_fiscal_band(
                self, A4[0], A4[1],
                farm_name=farm.farm_name if farm else 'FarmLedger Pro',
                contact=contact,
                tin=(f'TIN: {farm.tax_id}'
                     if farm and getattr(farm, 'tax_id', '') else ''),
                title=title,
                subtitle=f'No. {receipt_no}',
                logo_path=(farm.logo.path
                           if farm and getattr(farm, 'logo', None) else None))

        def _draw_footer(self):
            from reportlab.lib.utils import ImageReader
            page_width, _ = A4
            left = 18 * mm
            right = page_width - 18 * mm

            self.setStrokeColor(border)
            self.setLineWidth(.7)
            self.line(left, 45 * mm, right, 45 * mm)

            self.setFillColor(primary)
            self.setFont('Helvetica-Bold', 8.5)
            self.drawString(
                left, 40 * mm,
                farm.farm_name if farm else 'FarmLedger Pro')
            self.setFillColor(muted)
            self.setFont('Helvetica', 6.8)
            self.drawString(left, 35.5 * mm, contact)
            self.drawString(
                left, 31 * mm,
                f'Printed: {printed_on}   |   Receipt No: {receipt_no}   |'
                f'   Verification: {ver_code}')

            qr_size = 19 * mm
            self.drawImage(ImageReader(qr_buf), right - qr_size, 18 * mm,
                           width=qr_size, height=qr_size, mask='auto')
            self.setFont('Helvetica', 6)
            self.drawCentredString(right - qr_size / 2, 14.5 * mm,
                                   'Scan to view receipt')

            self.setFillColor(primary)
            self.rect(0, 0, page_width, 7 * mm, stroke=0, fill=1)
            self.setFillColor(colors.white)
            self.setFont('Helvetica', 6.5)
            self.drawCentredString(
                page_width / 2, 2.6 * mm,
                farm.tagline if farm and farm.tagline
                else 'Generated by FarmLedger Pro')

    doc.build(story, canvasmaker=ReceiptCanvas)
    response = HttpResponse(buf.getvalue(), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def invoice_payment_receipt_view(request, pk, payment_pk):
    invoice = get_object_or_404(
        scoped_queryset(Invoice, request.user,
                        Invoice.objects.select_related('farm_profile__default_currency')),
        pk=pk)
    payment = get_object_or_404(InvoicePayment, pk=payment_pk, invoice=invoice)
    customer = invoice.customer
    barcode_value = f'RCP-{payment.pk:05d}'
    meta = [f'Invoice total {invoice.total:,.2f}    '
            f'Balance after payment {invoice.balance:,.2f}']
    if payment.reference:
        meta.append(f'Payment reference: {payment.reference}')
    if payment.notes:
        meta.append(f'Notes: {payment.notes}')
    return _receipt_pdf_response(
        invoice.farm_profile,
        title='PAYMENT RECEIPT',
        receipt_no=barcode_value,
        payment_date=payment.date.strftime('%B %d, %Y'),
        received_name=invoice.buyer or (customer.name if customer else '-'),
        received_lines=[f'Invoice: {invoice.number}'] + [
            line for line in (
                customer.phone if customer else '',
                customer.email if customer else '') if line],
        pay_description=f'Payment Receipt {barcode_value}',
        pay_sub=f'Invoice: {invoice.number}',
        pay_date=payment.date.strftime('%b %d, %Y'),
        pay_method=payment.get_method_display(),
        amount=payment.amount,
        amount_caption='Total paid',
        processed_by=request.user.get_full_name() or request.user.username,
        filename=f'{barcode_value}.pdf',
        doc_ref=barcode_value,
        meta_lines=meta)


@login_required
def sale_receipt_view(request, pk):
    """Download a printable receipt for a sale's collected payment."""
    sale = get_object_or_404(
        scoped_queryset(
            Sale, request.user,
            Sale.objects.select_related(
                'harvest__crop_season__crop', 'harvest__crop_season__field',
                'processing_stage', 'customer', 'inventory_item',
                'inventory_item__farm_profile__default_currency'),
        ),
        pk=pk,
    )
    farm = _sale_farm(sale)
    if sale.processing_stage and sale.harvest:
        sale_desc = f'{sale.harvest.crop_season.crop.name} - {sale.processing_stage.name}'
    elif sale.harvest:
        sale_desc = sale.harvest.crop_season.crop.name
    elif sale.inventory_item:
        sale_desc = sale.inventory_item.name
    else:
        sale_desc = 'Produce'
    return _receipt_pdf_response(
        farm,
        title='SALE RECEIPT',
        receipt_no=f'SLC-{sale.pk:05d}',
        payment_date=sale.sale_date.strftime('%B %d, %Y'),
        received_name=sale.buyer or '-',
        received_lines=[f'Sale #{sale.pk}'] + [
            line for line in (
                sale.customer.phone if sale.customer else '',
                sale.customer.email if sale.customer else '') if line],
        pay_description=f'Sale Receipt SLC-{sale.pk:05d}',
        pay_sub=sale_desc,
        pay_date=sale.sale_date.strftime('%b %d, %Y'),
        pay_method=sale.get_payment_method_display(),
        amount=sale.amount_received,
        amount_caption='Total paid',
        processed_by=request.user.get_full_name() or request.user.username,
        filename=f'sale-receipt-{sale.pk:05d}.pdf',
        doc_ref=f'SLC-{sale.pk:05d}',
        meta_lines=[
            f'{sale.quantity:,.2f} {sale.unit} @ {sale.unit_price:,.2f}    '
            f'Sale total {sale.total_amount:,.2f}    '
            f'Balance outstanding {sale.receivable:,.2f}'])


@login_required
def sale_generate_invoice_view(request, pk):
    """Open the sale's automatic invoice (creates it if missing)."""
    sale = get_object_or_404(
        scoped_queryset(Sale, request.user, write=True), pk=pk)
    if request.method != 'POST':
        return HttpResponseRedirect(reverse('model_list', args=['crops', 'sale']))
    from .services import sync_sale_invoice
    sync_sale_invoice(sale)
    invoice = sale.invoices.first()
    if invoice is None:
        from django.contrib import messages
        messages.error(request, 'This sale has no farm assigned; it cannot be invoiced.')
        return HttpResponseRedirect(reverse('model_list', args=['crops', 'sale']))
    return HttpResponseRedirect(reverse('invoice_detail', args=[invoice.pk]))
