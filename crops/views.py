from datetime import date
from decimal import Decimal

from django import forms
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

    queryset = scoped_queryset(
        Invoice, request.user,
        Invoice.objects.select_related('customer').prefetch_related('items', 'payments'))
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
        balance = max(inv.billed - inv.paid, Decimal('0'))
        rows.append({
            'pk': inv.pk,
            'number': inv.number,
            'buyer': inv.buyer or (inv.customer.name if inv.customer else '-'),
            'date': inv.date,
            'due_date': inv.due_date,
            'total': inv.billed,
            'paid': inv.paid,
            'balance': balance,
            'status': inv.status,
            'status_label': inv.get_status_display(),
            'overdue': bool(inv.due_date and inv.due_date < today and balance > 0
                            and inv.status != Invoice.Status.CANCELLED),
            'detail_url': reverse('invoice_detail', args=[inv.pk]),
            'payment_url': reverse('invoice_record_payment', args=[inv.pk]),
            'edit_url': reverse('invoice_edit', args=[inv.pk]),
            'delete_url': reverse('invoice_delete', args=[inv.pk]),
        })

    stats = [
        {'label': 'Invoices', 'value': len(rows), 'icon': 'layers', 'color': 'green'},
        {'label': 'Total Billed', 'value': sum((r['total'] for r in rows), Decimal('0')),
         'icon': 'money', 'color': 'blue', 'is_money': True},
        {'label': 'Outstanding', 'value': sum((r['balance'] for r in rows), Decimal('0')),
         'icon': 'money', 'color': 'amber', 'is_money': True},
        {'label': 'Overdue', 'value': sum(1 for r in rows if r['overdue']),
         'icon': 'check', 'color': 'red'},
    ]
    can_write = request.user.is_superuser or _invoice_write_farms(request.user).exists()
    context = {
        'rows': rows,
        'stats': stats,
        'q': q,
        'status': status,
        'customer_id': customer_id,
        'status_choices': Invoice.Status.choices,
        'customers': scoped_queryset(Customer, request.user).order_by('name'),
        'can_write': can_write,
        'create_url': reverse('invoice_create'),
        'title': 'Invoices',
        'subtitle': 'Bill customers for produce and inventory sales, record payments, and print.',
        'today': today,
    }
    return render(request, 'invoices/invoice_list.html', context)


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['farm_profile', 'customer', 'buyer', 'date', 'due_date',
                  'payment_terms', 'reference', 'tax_percent', 'discount',
                  'payment_details', 'status', 'notes']
        widgets = {
            'buyer': forms.TextInput(attrs={'placeholder': 'Leave blank to use the customer name'}),
            'payment_terms': forms.TextInput(attrs={'placeholder': 'e.g. Net 30'}),
            'reference': forms.TextInput(attrs={'placeholder': 'PO / reference number'}),
            'payment_details': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Bank name, account name, account no, mobile money...'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Notes & terms shown on the invoice...'}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        farms = _invoice_write_farms(user) if user else accessible_farms(None)
        self.fields['farm_profile'].queryset = farms
        self.fields['farm_profile'].initial = farms.first()
        self.fields['customer'].queryset = scoped_queryset(
            Customer, user, Customer.objects.all()) if user else Customer.objects.none()
        self.fields['customer'].required = False
        self.fields['buyer'].required = False
        if self.instance.pk is None:
            self.fields['date'].initial = date.today()


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
    form = InvoiceForm(data, instance=invoice, user=request.user)
    items = Items(data, instance=invoice, prefix='items')
    if data is not None:
        if form.is_valid() and items.is_valid():
            with transaction.atomic():
                invoice = form.save()
                items.instance = invoice
                items.save()
                invoice.refresh_status()
            return HttpResponseRedirect(reverse('invoice_detail', args=[invoice.pk]))
    return render(request, 'invoices/invoice_form.html', {
        'form': form,
        'items': items,
        'invoice': invoice,
        'action': 'Edit' if pk else 'New',
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
    can_write = request.user.is_superuser or _invoice_write_farms(request.user).exists()
    return render(request, 'invoices/invoice_detail.html', {
        'invoice': invoice,
        'items': items,
        'chart_rows': chart_rows,
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
        'edit_url': reverse('invoice_edit', args=[invoice.pk]),
        'delete_url': reverse('invoice_delete', args=[invoice.pk]),
        'pdf_url': reverse('invoice_pdf', args=[invoice.pk]),
    })


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

    primary = colors.HexColor('#2d6a4f')
    dark = colors.HexColor('#1b4332')
    muted = colors.HexColor('#64748b')
    label_style = ParagraphStyle(
        'SectionLabel', fontName='Helvetica-Bold', fontSize=7.5, leading=10,
        textColor=primary)
    name_style = ParagraphStyle(
        'Name', fontName='Helvetica-Bold', fontSize=11, leading=14, textColor=dark)
    muted_style = ParagraphStyle(
        'Muted', fontName='Helvetica', fontSize=8, leading=11, textColor=muted)
    cell_style = ParagraphStyle(
        'Cell', fontName='Helvetica', fontSize=8.5, leading=11, textColor=dark)
    cell_muted = ParagraphStyle('CellMuted', parent=cell_style, fontSize=7.5, textColor=muted)
    cell_bold = ParagraphStyle('CellBold', parent=cell_style, fontName='Helvetica-Bold')
    numeric_style = ParagraphStyle(
        'Numeric', parent=cell_style, alignment=2, fontName='Helvetica')
    numeric_bold = ParagraphStyle(
        'NumericBold', parent=numeric_style, fontName='Helvetica-Bold')

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=0, bottomMargin=38 * mm, title=f'Invoice {invoice.number}')

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
                if self._pageNumber == page_count:
                    self._draw_invoice_footer()
                canvas.Canvas.showPage(self)
            canvas.Canvas.save(self)

        def _draw_invoice_footer(self):
            page_width, _ = A4
            right = page_width - 18 * mm
            line_start = right - 56 * mm
            signature_y = 28 * mm
            self.setStrokeColor(colors.HexColor('#9ca3af'))
            self.setLineWidth(.65)
            self.line(line_start, signature_y, right, signature_y)
            self.setFillColor(muted)
            self.setFont('Helvetica', 7)
            self.drawCentredString(
                (line_start + right) / 2, signature_y - 4 * mm,
                'Authorized signature')

            self.setStrokeColor(colors.HexColor('#e3ebe6'))
            self.setLineWidth(.5)
            self.line(18 * mm, 14 * mm, right, 14 * mm)
            self.setFillColor(dark)
            self.setFont('Helvetica-Bold', 9)
            self.drawCentredString(
                page_width / 2, 8 * mm, 'Thank you for your business!')

    story = []
    # Top accent bar
    bar = Table([['']], colWidths=[doc.width], rowHeights=[4 * mm])
    bar.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), primary)]))
    story.append(bar)
    story.append(Spacer(1, 10 * mm))

    if farm.logo:
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(farm.logo.path) as pim:
                lw, lh = pim.size
            logo_img = RLImage(farm.logo.path, height=14 * mm,
                               width=14 * mm * (lw / lh) if lh else 14 * mm)
            logo_img.hAlign = 'LEFT'
            story.append(logo_img)
            story.append(Spacer(1, 2 * mm))
        except Exception:
            pass

    contact = ' Â· '.join(p for p in (farm.location, farm.phone, farm.email) if p)
    head_left = [
        Paragraph(escape(farm.farm_name), ParagraphStyle(
            'Farm', fontName='Helvetica-Bold', fontSize=16, leading=20, textColor=dark)),
        Paragraph(escape(contact), muted_style),
    ]
    head_right = [
        Paragraph('INVOICE', ParagraphStyle(
            'InvTitle', fontName='Helvetica-Bold', fontSize=26, leading=30,
            alignment=2, textColor=dark)),
        Paragraph(escape(f'No. {invoice.number}'), ParagraphStyle(
            'InvNo', fontName='Helvetica-Bold', fontSize=9, leading=12,
            alignment=2, textColor=primary)),
    ]
    head = Table([[head_left, head_right]], colWidths=[doc.width * 0.55, doc.width * 0.45])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(head)
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width='100%', thickness=1.2, color=dark))
    story.append(Spacer(1, 7 * mm))

    billed = [
        Paragraph('BILLED TO', label_style),
        Spacer(1, 1.5 * mm),
        Paragraph(escape(invoice.buyer or '-'), name_style),
    ]
    if invoice.customer:
        for line in (invoice.customer.phone, invoice.customer.email):
            if line:
                billed.append(Paragraph(escape(line), muted_style))
    details_rows = [[Paragraph('INVOICE DETAILS', label_style), '']]
    for key, value in (
            ('Invoice date', invoice.date.strftime('%d / %m / %Y')),
            ('Due date', invoice.due_date.strftime('%d / %m / %Y') if invoice.due_date else '-'),
            ('Payment terms', invoice.payment_terms or '-'),
            ('Reference', invoice.reference or '-')):
        details_rows.append([
            Paragraph(escape(key), muted_style),
            Paragraph(escape(value), ParagraphStyle(
                'DetailValue', parent=muted_style, alignment=2,
                fontName='Helvetica-Bold', textColor=dark)),
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

    # Items table
    data = [['DESCRIPTION', 'QTY', 'RATE', 'AMOUNT']]
    for item in items:
        desc = [Paragraph(escape(item.description), cell_bold)]
        if item.detail:
            desc.append(Paragraph(escape(item.detail), cell_muted))
        data.append([
            desc,
            Paragraph(f'{item.quantity:,.2f} {escape(item.unit)}', numeric_style),
            Paragraph(money(item.unit_price), numeric_style),
            Paragraph(money(item.total), numeric_bold),
        ])
    if not items:
        data.append(['No line items yet', '', '', ''])
    t = Table(data, colWidths=[
        doc.width * 0.47, doc.width * 0.14, doc.width * 0.19, doc.width * 0.20])
    t.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, 0), 1, dark),
        ('LINEBELOW', (0, 1), (-1, -2), 0.4, colors.HexColor('#e3ebe6')),
        ('TEXTCOLOR', (0, 0), (-1, 0), dark),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8.5),
        ('ALIGN', (1, 0), (-1, -1), 'RIGHT'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(t)
    story.append(Spacer(1, 8 * mm))

    # Payment details + totals
    pay_lines = []
    if invoice.payment_details:
        for line in invoice.payment_details.splitlines():
            if line.strip():
                pay_lines.append(Paragraph(escape(line.strip()), cell_style))
    elif bank:
        pay_lines = [
            Paragraph('<font color="#64748b">Bank</font>&nbsp;&nbsp;&nbsp;&nbsp;'
                      f'<b>{escape(bank.bank_name)}</b>', cell_style),
            Paragraph('<font color="#64748b">Account name</font>&nbsp;&nbsp;&nbsp;&nbsp;'
                      f'<b>{escape(bank.name)}</b>', cell_style),
            Paragraph('<font color="#64748b">Account no</font>&nbsp;&nbsp;&nbsp;&nbsp;'
                      f'<b>â€¢â€¢â€¢â€¢ {escape(bank.account_number_last4 or "----")}</b>', cell_style),
        ]
    pay_block = [Paragraph('PAYMENT DETAILS', label_style), Spacer(1, 1.5 * mm)] + pay_lines

    # Highlight Total Due with color only; keep its type size and weight aligned
    # with the other summary amounts.
    total_label = ParagraphStyle(
        'TotalLabel', parent=cell_style, alignment=0, textColor=primary)
    total_value = ParagraphStyle(
        'TotalValue', parent=numeric_style, alignment=2, textColor=primary)
    totals = [
        [Paragraph('Subtotal', cell_style), Paragraph(money(invoice.subtotal), numeric_style)],
        [Paragraph(f'Tax ({escape(str(invoice.tax_percent or 0))}%)', cell_style),
         Paragraph(money(invoice.tax_amount), numeric_style)],
        [Paragraph('Discount', cell_style), Paragraph(money(invoice.discount or 0), numeric_style)],
        [Paragraph('TOTAL DUE', total_label), Paragraph(money(invoice.total), total_value)],
        [Paragraph('Amount paid', cell_style), Paragraph(money(invoice.amount_paid), numeric_style)],
        [Paragraph('<b>Balance due</b>', cell_style),
         Paragraph(money(invoice.balance), ParagraphStyle(
             'BalanceValue', parent=numeric_bold, textColor=colors.HexColor('#b91c1c')))],
    ]
    totals_table = Table(totals, colWidths=[doc.width * 0.17, doc.width * 0.19])
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
                   colWidths=[doc.width * 0.62, doc.width * 0.38])
    bottom.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(bottom)
    story.append(Spacer(1, 9 * mm))

    if invoice.notes:
        story.append(Paragraph('NOTES & TERMS', label_style))
        story.append(Spacer(1, 1.5 * mm))
        for line in invoice.notes.splitlines():
            if line.strip():
                story.append(Paragraph(escape(line.strip()), muted_style))
        story.append(Spacer(1, 12 * mm))

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
def invoice_record_payment_view(request, pk):
    invoice = get_object_or_404(
        scoped_queryset(Invoice, request.user, write=True), pk=pk)
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




def _receipt_pdf_response(farm, *, title, amount, amount_caption, rows, filename):
    """Render a one-page receipt as a downloadable PDF in the invoice style."""
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate, Spacer,
        Table, TableStyle)

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=0, bottomMargin=22 * mm,
        title=title[0] if isinstance(title, tuple) else title)
    primary = colors.HexColor('#2d6a4f')
    dark = colors.HexColor('#1b4332')
    muted = colors.HexColor('#64748b')
    border = colors.HexColor('#e3ebe6')
    label_style = ParagraphStyle(
        'SectionLabel', fontName='Helvetica-Bold', fontSize=7.5, leading=10,
        textColor=primary)
    muted_style = ParagraphStyle(
        'Muted', fontName='Helvetica', fontSize=8, leading=11, textColor=muted)
    cell_style = ParagraphStyle(
        'Cell', fontName='Helvetica', fontSize=8.5, leading=11, textColor=dark)
    numeric_style = ParagraphStyle('Numeric', parent=cell_style, alignment=2)

    currency_obj = getattr(farm, 'default_currency', None)
    symbol = currency_obj.symbol if currency_obj else ''

    def money(value):
        return f'{symbol} {value:,.2f}'

    story = []
    accent = Table([['']], colWidths=[doc.width], rowHeights=[4 * mm])
    accent.setStyle(TableStyle([('BACKGROUND', (0, 0), (-1, -1), primary)]))
    story.append(accent)
    story.append(Spacer(1, 9 * mm))

    if farm and getattr(farm, 'logo', None):
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(farm.logo.path) as pim:
                lw, lh = pim.size
            logo_img = RLImage(farm.logo.path, height=14 * mm,
                               width=14 * mm * (lw / lh) if lh else 14 * mm)
            logo_img.hAlign = 'LEFT'
            story.append(logo_img)
            story.append(Spacer(1, 2 * mm))
        except Exception:
            pass
    contact = ' · '.join(p for p in ((farm.location if farm else ''),
                            (farm.phone if farm else ''), (farm.email if farm else '')) if p)
    head_left = [
        Paragraph(escape(farm.farm_name if farm else 'FarmLedger Pro'), ParagraphStyle(
            'FarmName', fontName='Helvetica-Bold', fontSize=16, leading=20,
            textColor=dark)),
        Paragraph(escape(contact), muted_style),
    ]
    title_text, subtitle_text = title
    head_right = [
        Paragraph(escape(title_text), ParagraphStyle(
            'ReceiptTitle', fontName='Helvetica-Bold', fontSize=18, leading=22,
            alignment=2, textColor=dark)),
        Paragraph(escape(subtitle_text), ParagraphStyle(
            'ReceiptSub', fontName='Helvetica-Bold', fontSize=9, leading=12,
            alignment=2, textColor=primary)),
    ]
    head = Table([[head_left, head_right]], colWidths=[doc.width * .58, doc.width * .42])
    head.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'TOP')]))
    story.append(head)
    story.append(Spacer(1, 4 * mm))
    story.append(HRFlowable(width='100%', thickness=1.2, color=dark))
    story.append(Spacer(1, 8 * mm))

    # Amount box
    amount_box = Table([
        [Paragraph(escape(amount_caption), label_style)],
        [Paragraph(money(amount), ParagraphStyle(
            'Amount', fontName='Helvetica-Bold', fontSize=21, leading=25,
            textColor=dark))],
    ], colWidths=[doc.width])
    amount_box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f7faf8')),
        ('BOX', (0, 0), (-1, -1), 1, border),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, -1), (-1, -1), 9),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
    ]))
    story.append(amount_box)
    story.append(Spacer(1, 8 * mm))

    details = Table(
        [[Paragraph(escape(key), muted_style),
          Paragraph(escape(value), numeric_style)] for key, value in rows],
        colWidths=[doc.width * .52, doc.width * .48])
    details.setStyle(TableStyle([
        ('LINEBELOW', (0, 0), (-1, -1), .5, border),
        ('TEXTCOLOR', (0, 0), (0, -1), muted),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('FONTNAME', (1, 0), (1, -1), 'Helvetica-Bold'),
    ]))
    story.append(details)

    story.append(Spacer(1, 14 * mm))
    sig = Table([
        ['', '________________________'],
        ['', Paragraph('Authorized signature', ParagraphStyle(
            'Sig', parent=muted_style, alignment=2, fontSize=7))],
    ], colWidths=[doc.width * .58, doc.width * .42])
    sig.setStyle(TableStyle([('VALIGN', (0, 0), (-1, -1), 'BOTTOM')]))
    story.append(sig)
    story.append(Spacer(1, 12 * mm))
    story.append(HRFlowable(width='100%', thickness=.5, color=border))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph('Thank you for your payment.', ParagraphStyle(
        'Thanks', fontName='Helvetica-Bold', fontSize=9.5, alignment=1,
        textColor=dark)))

    doc.build(story)
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
    title = (
        'PAYMENT RECEIPT',
        f'Receipt RCP-{payment.pk:05d} - Invoice {invoice.number}')
    rows = [
        ('Received from', invoice.buyer or invoice.customer.name if invoice.customer else '-'),
        ('Date received', payment.date.strftime('%d / %m / %Y')),
        ('Payment method', payment.get_method_display()),
        ('Reference', payment.reference or '-'),
        ('Invoice total', f'{invoice.total:,.2f}'),
        ('Balance after payment', f'{invoice.balance:,.2f}'),
    ]
    if payment.notes:
        rows.append(('Notes', payment.notes))
    barcode_value = f'RCP-{payment.pk:05d}'
    return _receipt_pdf_response(
        invoice.farm_profile, title=title, amount=payment.amount,
        amount_caption='Amount received', rows=rows, filename=f'{barcode_value}.pdf')


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
    rows = [
        ('Sold to', sale.buyer or '-'),
        ('Item', sale_desc),
        ('Quantity', f'{sale.quantity:,.2f} {sale.unit}'),
        ('Unit price', f'{sale.unit_price:,.2f}'),
        ('Payment method', sale.get_payment_method_display()),
        ('Sale total', f'{sale.total_amount:,.2f}'),
        ('Balance outstanding', f'{sale.receivable:,.2f}'),
    ]
    return _receipt_pdf_response(
        farm, title=('SALE RECEIPT', f'Receipt SLC-{sale.pk:05d} - {sale.sale_date.strftime("%d / %m / %Y")}'),
        amount=sale.amount_received, amount_caption='Amount received',
        rows=rows, filename=f'sale-receipt-{sale.pk:05d}.pdf')


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
