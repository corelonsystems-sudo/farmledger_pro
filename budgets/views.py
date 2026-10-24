from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.forms import BaseInlineFormSet, inlineformset_factory
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, HttpResponseRedirect, render
from django.urls import reverse
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from accounts.access import FarmScopedMixin, accessible_farms, scoped_queryset
from expenses.models import BudgetSubCode, Category, Expense, ScheduleLine
from farmledger.forms import BudgetForm, BudgetIncomePlanForm, BudgetItemForm
from .models import Budget, BudgetIncomePlan, BudgetItem
from .queries import budget_export_sections, budget_plan_data
from .serializers import BudgetItemSerializer, BudgetSerializer


@login_required
def budget_code_detail_view(request, pk):
    code = get_object_or_404(Category.objects.select_related(), pk=pk)
    items = scoped_queryset(BudgetItem, request.user).filter(
        category=code).select_related('budget', 'crop_season')

    item_data = []
    for item in items:
        spent = item.spent_amount
        planned = item.planned_amount
        fund = item.fund_amount
        if planned and planned > 0:
            pct = min((spent / planned) * 100, 100)
        else:
            pct = 0
        item_data.append({
            'item': item,
            'spent': spent,
            'remaining': planned - spent,
            'pct': round(pct, 1),
            'over_budget': spent > planned if planned else False,
            'fund_amount': fund,
            'fund_remaining': fund - planned,
        })

    total_fund = sum(i.fund_amount for i in items)
    total_planned = sum(i.planned_amount for i in items)
    total_spent = sum(i.spent_amount for i in items)

    from farmledger.views import _donut_segments
    by_budget = {}
    for item in items:
        by_budget[item.budget] = by_budget.get(item.budget, Decimal('0')) + (item.planned_amount or Decimal('0'))
    budget_segments = _donut_segments(sorted(
        [{'label': b.name or f'Budget {b.pk}', 'total': v} for b, v in by_budget.items() if v > 0],
        key=lambda r: r['total'], reverse=True))

    header_farms = list(accessible_farms(request.user).order_by('pk')[:2])
    context = {
        'code': code,
        'subcodes': code.subcodes.all(),
        'header_farm': header_farms[0] if len(header_farms) == 1 else None,
        'can_edit_code': request.user.is_superuser,
        'item_data': item_data,
        'total_fund': total_fund,
        'total_planned': total_planned,
        'total_spent': total_spent,
        'total_remaining': total_fund - total_spent,
        'budget_segments': budget_segments,
    }
    return render(request, 'budgets/budget_code_detail.html', context)


@login_required
def budget_subcode_detail_view(request, pk):
    """Detail page for a Budget SubCode: schedule lines, budget items and spend."""
    subcode = get_object_or_404(
        BudgetSubCode.objects.select_related('category'), pk=pk)
    lines = list(subcode.schedule_lines.all().order_by('name', 'code'))
    items = scoped_queryset(BudgetItem, request.user).filter(
        subcode=subcode).select_related('budget', 'category', 'crop_season', 'schedule_line')

    item_data = []
    line_stats = {line.pk: {'items': 0, 'planned': Decimal('0')} for line in lines}
    for item in items:
        spent = item.spent_amount
        planned = item.planned_amount
        if planned and planned > 0:
            pct = min((spent / planned) * 100, 100)
        else:
            pct = 0
        item_data.append({
            'item': item,
            'spent': spent,
            'remaining': planned - spent,
            'pct': round(pct, 1),
            'over_budget': spent > planned if planned else False,
            'fund_amount': item.fund_amount,
        })
        if item.schedule_line_id in line_stats:
            line_stats[item.schedule_line_id]['items'] += 1
            line_stats[item.schedule_line_id]['planned'] += planned or Decimal('0')
    for line in lines:
        line.items_used = line_stats[line.pk]['items']
        line.planned_used = line_stats[line.pk]['planned']

    total_planned = sum(i.planned_amount for i in items)
    total_spent = sum(i.spent_amount for i in items)

    from farmledger.views import _donut_segments
    by_line = {}
    for item in items:
        key = item.schedule_line.name if item.schedule_line else 'Unassigned'
        by_line[key] = by_line.get(key, Decimal('0')) + (item.planned_amount or Decimal('0'))
    line_segments = _donut_segments(sorted(
        [{'label': k, 'total': v} for k, v in by_line.items() if v > 0],
        key=lambda r: r['total'], reverse=True))

    header_farms = list(accessible_farms(request.user).order_by('pk')[:2])
    context = {
        'subcode': subcode,
        'code': subcode.category,
        'lines': lines,
        'header_farm': header_farms[0] if len(header_farms) == 1 else None,
        'can_edit_code': request.user.is_superuser,
        'item_data': item_data,
        'total_planned': total_planned,
        'total_spent': total_spent,
        'total_remaining': total_planned - total_spent,
        'execution_pct': round(float(total_spent / total_planned * 100), 1) if total_planned else 0,
        'line_segments': line_segments,
    }
    return render(request, 'budgets/budget_subcode_detail.html', context)


class BudgetLineFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        codes = set()
        for form in self.forms:
            data = form.cleaned_data
            original = data.get('id')
            if original and original.budget_id != self.instance.pk:
                raise ValidationError('A submitted line does not belong to this budget.')
            if data.get('DELETE'):
                if self.model is BudgetItem and original and original.expenses.exists():
                    raise ValidationError('Lines with posted expenses cannot be removed. Keep the line to preserve the ledger.')
                continue
            code = data.get('line_code')
            if code and code in codes:
                raise ValidationError('Each line code must be unique within this budget section.')
            if code:
                codes.add(code)

    def save(self, commit=True):
        reserved = set(self.model.objects.filter(budget_id=self.instance.pk).values_list('line_code', flat=True))
        reserved.update(form.cleaned_data.get('line_code') for form in self.forms)
        prefix = 'EXP' if self.model is BudgetItem else 'INC'
        index = 1
        for form in self.forms:
            if not form.has_changed() or form.cleaned_data.get('DELETE') or form.instance.line_code:
                continue
            while f'{prefix}-{index:03d}' in reserved:
                index += 1
            form.instance.line_code = f'{prefix}-{index:03d}'
            reserved.add(form.instance.line_code)
        return super().save(commit=commit)


@login_required
def budget_plan_edit_view(request, pk=None):
    farms = accessible_farms(request.user, write_model='budgets.budget')
    if not request.user.is_superuser and not farms.exists():
        raise PermissionDenied
    budget = get_object_or_404(scoped_queryset(Budget, request.user, write=True), pk=pk) if pk else Budget()
    data = request.POST if request.method == 'POST' else None
    initial = {'farm_profile': farms.first()} if not pk and farms.count() == 1 else None
    form = BudgetForm(data, instance=budget, initial=initial)
    form.fields['farm_profile'].queryset = farms
    form_valid = form.is_valid() if data is not None else False
    IncomeForms = inlineformset_factory(
        Budget, BudgetIncomePlan, form=BudgetIncomePlanForm, formset=BudgetLineFormSet,
        fields=['line_code', 'funding_type', 'income_source', 'planned_amount', 'figure_type', 'notes'],
        extra=1, can_delete=True, max_num=300, validate_max=True, absolute_max=600)
    ItemForms = inlineformset_factory(
        Budget, BudgetItem, form=BudgetItemForm, formset=BudgetLineFormSet,
        fields=['line_code', 'cost_centre', 'category', 'subcode', 'reference', 'schedule_line',
                'crop_season', 'planned_amount', 'figure_type', 'notes'],
        extra=1, can_delete=True, max_num=300, validate_max=True, absolute_max=600)
    incomes = IncomeForms(data, instance=budget, prefix='incomes')
    items = ItemForms(data, instance=budget, prefix='items',
                      form_kwargs={'user': request.user, 'farm_id': budget.farm_profile_id})
    if data is not None:
        incomes_valid, items_valid = incomes.is_valid(), items.is_valid()
        if form_valid and incomes_valid and items_valid:
            if budget.status == Budget.Status.ACTIVE and (any(
                    line.cleaned_data.get('figure_type') == 'UNCONFIRMED'
                    and not line.cleaned_data.get('DELETE') for line in incomes.forms) or any(
                    line.cleaned_data.get('figure_type') == 'UNCONFIRMED'
                    and not line.cleaned_data.get('DELETE')
                    and not line.cleaned_data.get('schedule_line') for line in items.forms)):
                form.add_error('status', 'Confirm all line amounts before activating this budget.')
                form_valid = False
        if form_valid and incomes_valid and items_valid:
            try:
                with transaction.atomic():
                    if budget.pk:
                        Budget.objects.select_for_update().get(pk=budget.pk)
                    form.save()
                    incomes.save()
                    items.save()
                    if budget.status == Budget.Status.ACTIVE and (
                            budget.items.filter(figure_type='UNCONFIRMED', schedule_line__isnull=True).exists()
                            or budget.income_plans.filter(figure_type='UNCONFIRMED').exists()):
                        raise ValidationError('Confirm all line amounts before activating this budget.')
            except ProtectedError:
                form.add_error(None, 'A line with posted expenses cannot be removed.')
            except ValidationError as exc:
                form.add_error(None, exc)
            except IntegrityError:
                form.add_error(None, 'The plan changed while you were editing. Reload it and check line codes and selections.')
            else:
                return HttpResponseRedirect(reverse('budget_detail', kwargs={'pk': budget.pk}))
            if not pk:
                budget.pk = None
                budget._state.adding = True
    return render(request, 'budgets/budget_edit.html', {
        'form': form, 'budget': budget, 'income_formset': incomes, 'item_formset': items,
        'action': 'Edit' if pk else 'New', 'plan': budget_plan_data(budget) if budget.pk else None,
        'subcode_categories': {str(s.pk): s.category_id for s in BudgetSubCode.objects.all()},
        'line_subcodes': {str(s.pk): s.subcode_id for s in ScheduleLine.objects.all()},
        'sub_lines': {str(s.pk): [{'code': l.code, 'name': l.name}
                                  for l in s.schedule_lines.all()]
                      for s in BudgetSubCode.objects.prefetch_related('schedule_lines')},
    })


def _budget_burn_chart(budget, expenses, total_planned):
    """Cumulative actual spend vs straight-line planned pace, per month.

    Returns {'rows', 'actual_points', 'plan_points', 'max'} or None when no
    date range can be determined."""
    import math
    from datetime import date, timedelta
    from decimal import Decimal

    months = []
    if budget.start_date and budget.end_date and budget.start_date <= budget.end_date:
        cur = budget.start_date.replace(day=1)
        while cur <= budget.end_date:
            months.append(cur)
            cur = (cur.replace(day=28) + timedelta(days=7)).replace(day=1)
    if not months:
        dates = [e.date for e in expenses if e.date]
        if not dates:
            return None
        cur = min(dates).replace(day=1)
        end = max(dates)
        while cur <= end:
            months.append(cur)
            cur = (cur.replace(day=28) + timedelta(days=7)).replace(day=1)
    # Cumulative actual spend per month
    monthly = {}
    for e in expenses:
        if e.date:
            key = e.date.strftime('%Y-%m')
            monthly[key] = monthly.get(key, Decimal('0')) + (e.amount or Decimal('0'))
    today = date.today()
    this_month = today.replace(day=1)
    rows, cumulative = [], Decimal('0')
    count = len(months)
    for i, m in enumerate(months):
        spend = monthly.get(m.strftime('%Y-%m'), Decimal('0'))
        cumulative += spend
        pace = total_planned * Decimal(i + 1) / count if count else Decimal('0')
        rows.append({'label': m.strftime('%b %Y'), 'month': m, 'spend': spend,
                     'actual': cumulative, 'plan': pace})
    # Only months that have elapsed (or carry spend) are plotted on the actual
    # line; future months are left blank instead of drawing a flat tail.
    last_spend_idx = max((i for i, r in enumerate(rows) if r['spend']), default=-1)
    plotted = [i for i, r in enumerate(rows) if r['month'] <= this_month or i <= last_spend_idx]
    plot_until = max(plotted) if plotted else -1

    # The bold series is spend *per month* (so the line rises and falls with
    # each month's expenses); cumulative spend and the plan diagonal share the axis.
    from farmledger.views import _nice_ceiling, _smooth_path
    monthly_pace = total_planned / count if count else Decimal('0')
    raw_max = max(max((r['actual'] for r in rows), default=Decimal('0')), total_planned)
    max_v = _nice_ceiling(raw_max)

    X0, X1, Y0, Y1 = 72, 608, 36, 190  # plot box
    H = Y1 - Y0

    def ypos(value):
        return round(Y1 - H * float(value / max_v), 1) if max_v else Y1

    label_step = max(1, math.ceil(count / 12))
    for i, row in enumerate(rows):
        row['x'] = round(X0 + (X1 - X0) * i / (count - 1), 1) if count > 1 else (X0 + X1) / 2
        row['ay'] = ypos(row['spend'])
        row['cy'] = ypos(row['actual'])
        row['py'] = ypos(row['plan'])
        row['plot'] = i <= plot_until
        row['show_label'] = i % label_step == 0 or i == count - 1

    def smooth_path(points):
        return _smooth_path(points, Y0, Y1)

    plotted_rows = [r for r in rows if r['plot']]
    actual_path = smooth_path([(r['x'], r['ay']) for r in plotted_rows])
    area_path = (f'{actual_path} L {plotted_rows[-1]["x"]},{Y1} L {plotted_rows[0]["x"]},{Y1} Z'
                 if len(plotted_rows) > 1 else '')
    cumulative_path = smooth_path([(r['x'], r['cy']) for r in plotted_rows])
    # Plan pace is the straight diagonal from zero at the start to the full plan at the end.
    plan_path = f'M {rows[0]["x"]},{Y1} L {rows[-1]["x"]},{ypos(total_planned)}'

    today_x = expected_today = None
    if (budget.start_date and budget.end_date and budget.start_date < budget.end_date
            and budget.start_date <= today <= budget.end_date):
        frac = (today - budget.start_date).days / (budget.end_date - budget.start_date).days
        today_x = round(X0 + (X1 - X0) * frac, 1)
        expected_today = (total_planned * Decimal(str(frac))).quantize(Decimal('1'))
    spent = plotted_rows[-1]['actual'] if plotted_rows else Decimal('0')
    status = None
    if expected_today is not None:
        diff = spent - expected_today
        status = {'expected': expected_today, 'diff': abs(diff),
                  'tone': 'over' if diff > 0 else 'under',
                  'label': 'ahead of pace' if diff > 0 else 'under pace'}
    last = plotted_rows[-1] if plotted_rows else None
    ticks = [{'value': max_v * Decimal(n) / 4, 'y': round(Y1 - H * n / 4, 1)} for n in (4, 3, 2, 1, 0)]
    return {
        'rows': rows,
        'max': max_v,
        'ticks': ticks,
        'actual_path': actual_path,
        'cumulative_path': cumulative_path,
        'plan_path': plan_path,
        'plan_top_y': ypos(total_planned),
        'area_path': area_path,
        'today_x': today_x,
        'today_anchor': 'start' if today_x is not None and today_x < (X0 + X1) / 2 else 'end',
        'status': status,
        'spent': spent,
        'monthly_pace': monthly_pace,
        'pace_y': ypos(monthly_pace),
        'plan_pct': round(float(spent / total_planned * 100), 1) if total_planned else 0,
        'last': last,
        'end_label': ({'x': last['x'] - 8 if last['x'] > (X0 + X1) / 2 else last['x'] + 8,
                       'y': max(last['ay'] - 10, Y0 + 10),
                       'anchor': 'end' if last['x'] > (X0 + X1) / 2 else 'start'}
                      if last else None),
        'has_actual': any(r['actual'] for r in rows),
    }


@login_required
def budget_detail_view(request, pk):
    budget = get_object_or_404(
        scoped_queryset(Budget, request.user, Budget.objects.select_related('farm_profile')),
        pk=pk)
    plan = budget_plan_data(budget)
    expenses = list(Expense.objects.filter(budget_item__budget=budget).select_related('category', 'budget_item', 'budget_item__subcode').order_by('-date'))
    if request.GET.get('export'):
        from farmledger.views import _M, _export_report
        sections = budget_export_sections(budget, plan)
        sections.append({'title': 'Actual Expenditure Ledger',
                         'headers': ['Date', 'Line Code', 'Budget Code', 'Subcode', 'Vendor', 'Amount'],
                         'rows': [[str(e.date), e.budget_item.line_code, e.category.name,
                                   str(e.budget_item.subcode) if e.budget_item.subcode_id else '-',
                                   e.vendor, _M(e.amount)] for e in expenses]})
        return _export_report(
            request, request.GET['export'], f'Annual Operating Budget - {budget.name}',
            f'budget_{budget.pk}', sections, str(budget.start_date or ''),
            str(budget.end_date or ''), farm=budget.farm_profile)
    from farmledger.views import _donut_segments
    sub_totals = {}
    for item in budget.items.select_related('subcode', 'category'):
        name = item.subcode.name if item.subcode_id else (item.head or item.category.name)
        sub_totals[name] = sub_totals.get(name, Decimal('0')) + item.planned_amount
    alloc_segments = _donut_segments(sorted(
        ({'label': k, 'total': v} for k, v in sub_totals.items()),
        key=lambda r: r['total'], reverse=True))
    return render(request, 'budgets/budget_detail.html', {
        'budget': budget, 'expenses': expenses, **plan,
        'burn_chart': _budget_burn_chart(budget, expenses, plan['total_planned']),
        'alloc_segments': alloc_segments,
        'can_edit': scoped_queryset(Budget, request.user, write=True).filter(pk=budget.pk).exists(),
    })


@login_required
def budget_item_add_view(request, budget_pk):
    budget = get_object_or_404(
        scoped_queryset(Budget, request.user, write=True), pk=budget_pk)
    ItemForm = inlineformset_factory(
        Budget, BudgetItem, form=BudgetItemForm,
        fields=['line_code', 'cost_centre', 'head', 'reference', 'line_name', 'category',
                'crop_season', 'planned_amount', 'figure_type', 'notes']).form
    if request.method == 'POST':
        form = ItemForm(request.POST, instance=BudgetItem(budget=budget),
                        user=request.user, farm_id=budget.farm_profile_id)
        if form.is_valid():
            item = form.save(commit=False)
            item.budget = budget
            item.save()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': True, 'redirect': reverse('budget_detail', kwargs={'pk': budget.pk})})
            return HttpResponseRedirect(reverse('budget_detail', kwargs={'pk': budget.pk}))
    else:
        form = ItemForm(instance=BudgetItem(budget=budget), user=request.user, farm_id=budget.farm_profile_id)

    context = {
        'form': form,
        'budget': budget,
        'action': 'Add',
        'model_name': 'Budget Item',
    }
    return render(request, 'budgets/budget_item_form.html', context)


class BudgetListView(FarmScopedMixin, generics.ListCreateAPIView):
    queryset = Budget.objects.all()
    serializer_class = BudgetSerializer
    permission_classes = (IsAuthenticated,)


class BudgetDetailView(FarmScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = Budget.objects.all()
    serializer_class = BudgetSerializer
    permission_classes = (IsAuthenticated,)


class BudgetItemListView(FarmScopedMixin, generics.ListCreateAPIView):
    queryset = BudgetItem.objects.all()
    serializer_class = BudgetItemSerializer
    permission_classes = (IsAuthenticated,)


class BudgetItemDetailView(FarmScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = BudgetItem.objects.all()
    serializer_class = BudgetItemSerializer
    permission_classes = (IsAuthenticated,)
