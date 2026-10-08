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
    }
    return render(request, 'budgets/budget_code_detail.html', context)


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
        fields=['line_code', 'funding_type', 'schedule_line', 'planned_amount', 'figure_type', 'notes'],
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


@login_required
def budget_detail_view(request, pk):
    budget = get_object_or_404(
        scoped_queryset(Budget, request.user, Budget.objects.select_related('farm_profile')),
        pk=pk)
    plan = budget_plan_data(budget)
    expenses = Expense.objects.filter(budget_item__budget=budget).select_related('category', 'budget_item').order_by('-date')
    if request.GET.get('export'):
        from farmledger.views import _M, _export_report
        sections = budget_export_sections(budget, plan)
        sections.append({'title': 'Actual Expenditure Ledger',
                         'headers': ['Date', 'Line Code', 'Budget Code', 'Vendor', 'Amount'],
                         'rows': [[str(e.date), e.budget_item.line_code, e.category.name,
                                   e.vendor, _M(e.amount)] for e in expenses]})
        return _export_report(
            request, request.GET['export'], f'Annual Operating Budget - {budget.name}',
            f'budget_{budget.pk}', sections, str(budget.start_date or ''),
            str(budget.end_date or ''), farm=budget.farm_profile)
    return render(request, 'budgets/budget_detail.html', {
        'budget': budget, 'expenses': expenses, **plan,
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
