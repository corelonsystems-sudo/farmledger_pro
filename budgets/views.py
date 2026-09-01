from django.contrib.auth.decorators import login_required
from django.forms import modelform_factory
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, HttpResponseRedirect, render
from django.urls import reverse
from rest_framework import generics
from rest_framework.permissions import IsAuthenticated

from expenses.models import Category, Expense
from .models import Budget, BudgetItem
from .serializers import BudgetItemSerializer, BudgetSerializer


@login_required
def budget_code_detail_view(request, pk):
    code = get_object_or_404(Category.objects.select_related(), pk=pk)
    items = BudgetItem.objects.filter(category=code).select_related('budget', 'crop_season')

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

    context = {
        'code': code,
        'item_data': item_data,
        'total_fund': total_fund,
        'total_planned': total_planned,
        'total_spent': total_spent,
        'total_remaining': total_fund - total_spent,
    }
    return render(request, 'budgets/budget_code_detail.html', context)


@login_required
def budget_detail_view(request, pk):
    budget = get_object_or_404(Budget.objects.select_related('farm_profile'), pk=pk)
    items = budget.items.select_related('category', 'crop_season').all()

    item_data = []
    for item in items:
        spent = item.spent_amount
        planned = item.planned_amount
        if planned and planned > 0:
            pct = min((spent / planned) * 100, 100)
        else:
            pct = 0
        cat = item.category
        fund = item.fund_amount
        item_data.append({
            'item': item,
            'spent': spent,
            'remaining': planned - spent,
            'pct': round(pct, 1),
            'over_budget': spent > planned if planned else False,
            'code': cat.code if cat else '',
            'fund_amount': fund,
            'fund_remaining': fund - planned,
        })

    expenses = Expense.objects.filter(budget_item__budget=budget).select_related('category').order_by('-date')

    context = {
        'budget': budget,
        'item_data': item_data,
        'expenses': expenses,
        'total_planned': budget.total_planned,
        'total_spent': budget.total_spent,
        'remaining': budget.remaining,
        'overall_pct': round((budget.total_spent / budget.total_planned) * 100, 1) if budget.total_planned and budget.total_planned > 0 else 0,
    }
    return render(request, 'budgets/budget_detail.html', context)


@login_required
def budget_item_add_view(request, budget_pk):
    budget = get_object_or_404(Budget, pk=budget_pk)
    BudgetItemForm = modelform_factory(
        BudgetItem,
        fields=['category', 'crop_season', 'planned_amount', 'fund_amount', 'notes'],
    )
    if request.method == 'POST':
        form = BudgetItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.budget = budget
            item.save()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': True, 'redirect': reverse('budget_detail', kwargs={'pk': budget.pk})})
            return HttpResponseRedirect(reverse('budget_detail', kwargs={'pk': budget.pk}))
    else:
        form = BudgetItemForm()

    context = {
        'form': form,
        'budget': budget,
        'action': 'Add',
        'model_name': 'Budget Item',
    }
    return render(request, 'budgets/budget_item_form.html', context)


class BudgetListView(generics.ListCreateAPIView):
    queryset = Budget.objects.all()
    serializer_class = BudgetSerializer
    permission_classes = (IsAuthenticated,)


class BudgetDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Budget.objects.all()
    serializer_class = BudgetSerializer
    permission_classes = (IsAuthenticated,)


class BudgetItemListView(generics.ListCreateAPIView):
    queryset = BudgetItem.objects.all()
    serializer_class = BudgetItemSerializer
    permission_classes = (IsAuthenticated,)


class BudgetItemDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = BudgetItem.objects.all()
    serializer_class = BudgetItemSerializer
    permission_classes = (IsAuthenticated,)
