from decimal import Decimal
from types import SimpleNamespace

from django.db.models import DecimalField, Sum, Value
from django.db.models.functions import Coalesce

from expenses.models import Expense, ScheduleLine
from .models import BudgetIncomePlan, BudgetItem, FigureType


def get_budget_variance_report(crop_season_id=None, farm_ids=None):
    """Build a budget variance report from budget items.

    Each row is a BudgetItem (budget + category, optionally a crop season)
    with its planned amount and actual spend from linked expenses.
    """
    qs = BudgetItem.objects.select_related('budget', 'category', 'crop_season')

    if farm_ids is not None:
        qs = qs.filter(budget__farm_profile_id__in=farm_ids)
    if crop_season_id:
        qs = qs.filter(crop_season_id=crop_season_id)

    results = []
    for item in qs:
        expenses = Expense.objects.filter(budget_item=item)
        if crop_season_id:
            expenses = expenses.filter(crop_season_id=crop_season_id)
        actual = expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        variance = item.planned_amount - actual
        if item.planned_amount and item.planned_amount > 0:
            variance_pct = (variance / item.planned_amount) * Decimal('100')
        else:
            variance_pct = Decimal('0')

        results.append({
            'id': item.id,
            'crop_season': item.crop_season_id,
            'crop_season_name': str(item.crop_season) if item.crop_season else '',
            'category': item.category.name,
            'category_id': item.category_id,
            'planned_amount': str(item.planned_amount),
            'actual_amount': str(actual),
            'variance': str(variance),
            'variance_percentage': str(variance_pct),
        })

    return results


def budget_plan_data(budget):
    items = list(budget.items.select_related('category', 'crop_season').annotate(
        actual=Coalesce(Sum('expenses__amount'), Value(Decimal('0')), output_field=DecimalField())))
    centres = {}
    for item in items:
        centre = centres.setdefault(item.cost_centre, {
            'name': item.cost_centre, 'heads': {}, 'planned': Decimal('0'), 'spent': Decimal('0')})
        head_name = item.head or item.category.name
        head_key = ('sub', item.subcode_id) if item.subcode_id else ('head', head_name)
        head = centre['heads'].setdefault(head_key, {
            'name': head_name, 'cost_name': item.category.name, 'lines': [], 'register': None,
            'subcode_id': item.subcode_id, 'category_obj': item.category,
            'planned': Decimal('0'), 'spent': Decimal('0')})
        item.balance = item.planned_amount - item.actual
        if item.subcode_id and not item.schedule_line_id:
            head['register'] = item
        else:
            head['lines'].append(item)
        head['planned'] += item.planned_amount
        head['spent'] += item.actual
        centre['planned'] += item.planned_amount
        centre['spent'] += item.actual
    line_actuals = dict(
        Expense.objects.filter(budget_item__budget=budget, schedule_line__isnull=False)
        .values_list('schedule_line_id')
        .annotate(total=Sum('amount'))
        .values_list('schedule_line_id', 'total'))
    for centre in centres.values():
        centre['heads'] = list(centre['heads'].values())
        for head in centre['heads']:
            head['remaining'] = head['planned'] - head['spent']
            if head['subcode_id']:
                covered = {line.schedule_line_id for line in head['lines']
                           if getattr(line, 'schedule_line_id', None)}
                head['lines'].extend(
                    SimpleNamespace(pk=None, line_code=sl.code, reference='',
                                    line_name=sl.name, crop_season=None,
                                    category=head['category_obj'], schedule_line_id=sl.pk,
                                    figure_type=FigureType.ESTIMATE,
                                    planned_amount=Decimal('0'),
                                    actual=line_actuals.get(sl.pk, Decimal('0')),
                                    balance=Decimal('0'), notes=sl.description,
                                    get_figure_type_display=lambda: '')
                    for sl in ScheduleLine.objects.filter(subcode_id=head['subcode_id'])
                    if sl.pk not in covered)
        centre['remaining'] = centre['planned'] - centre['spent']
    incomes = list(budget.income_plans.all())
    income_groups = []
    for value, label in BudgetIncomePlan.FundingType.choices:
        lines = [line for line in incomes if line.funding_type == value]
        if lines:
            income_groups.append({'name': label, 'lines': lines,
                                  'total': sum((line.planned_amount for line in lines), Decimal('0'))})
    planned = sum((item.planned_amount for item in items), Decimal('0'))
    spent = sum((item.actual for item in items), Decimal('0'))
    income = sum((line.planned_amount for line in incomes), Decimal('0'))
    return {
        'centres': list(centres.values()), 'income_groups': income_groups,
        'total_planned': planned, 'total_spent': spent, 'total_income_planned': income,
        'remaining': planned - spent, 'funding_balance': income - planned,
        'line_count': len(items), 'income_count': len(incomes),
        'unconfirmed_count': (
            sum(item.figure_type == FigureType.UNCONFIRMED and not item.schedule_line_id for item in items)
            + sum(line.figure_type == FigureType.UNCONFIRMED for line in incomes)),
        'overall_pct': round(spent / planned * 100, 1) if planned else 0,
    }


def budget_export_sections(budget, data):
    from farmledger.views import _M
    headers = ['Code', 'Cost Centre', 'No.', 'Head', 'Ref', 'Line Item', 'Amount', 'Basis / Notes']
    sections = []
    for group in data['income_groups']:
        rows = [[line.line_code, f"Income ({group['name']})", '', group['name'], '', line.name,
                 None if line.figure_type == FigureType.UNCONFIRMED else _M(line.planned_amount),
                 f'{line.get_figure_type_display()}: {line.notes}'] for line in group['lines']]
        rows.append(['', '', '', '', '', f"Subtotal - {group['name']}", _M(group['total']), ''])
        sections.append({'title': f"Sources of Income - {group['name']}", 'headers': headers,
                         'rows': rows, 'row_types': [line.figure_type.lower() for line in group['lines']] + ['subtotal'],
                         'amount_column': 6, 'total_role': 'income'})
    sections.append({'title': 'Total Income', 'headers': ['Metric', 'Amount'],
                     'rows': [['TOTAL INCOME', _M(data['total_income_planned'])]],
                     'row_types': ['subtotal'], 'formula_income_total': True})
    for centre in data['centres']:
        rows, kinds = [], []
        for number, head in enumerate(centre['heads'], start=1):
            rows.append(['', head['cost_name'], number, head['name'], '', '', _M(head['planned']), 'Activity subtotal'])
            kinds.append('head')
            for line in head['lines']:
                rows.append([line.line_code, '', '', '', line.reference,
                             line.line_name or line.category.name,
                             None if line.schedule_line_id or line.figure_type == FigureType.UNCONFIRMED else _M(line.planned_amount),
                             ('' if line.schedule_line_id else f'{line.get_figure_type_display()}: ') + (line.notes or '-')])
                kinds.append(line.figure_type.lower())
        rows.append(['', '', '', '', '', f"Subtotal - {centre['name']}", _M(centre['planned']), ''])
        kinds.append('subtotal')
        sections.append({'title': f"Expenditure - {centre['name']}", 'headers': headers,
                         'rows': rows, 'row_types': kinds, 'amount_column': 6, 'total_role': 'expenditure'})
    sections.append({'title': 'Budget Reconciliation', 'headers': ['Metric', 'Amount'], 'formula_summary': True, 'rows': [
        ['TOTAL INCOME', _M(data['total_income_planned'])],
        ['TOTAL EXPENDITURE', _M(data['total_planned'])],
        ['FUNDING SURPLUS / SHORTFALL', _M(data['funding_balance'])],
        ['ACTUAL EXPENDITURE', _M(data['total_spent'])],
        ['REMAINING EXPENDITURE BUDGET', _M(data['remaining'])],
        ['Unconfirmed lines (excluded until priced)', data['unconfirmed_count']],
    ]})
    return sections
