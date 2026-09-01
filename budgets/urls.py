from django.urls import path

from .views import (
    BudgetDetailView,
    BudgetItemDetailView,
    BudgetItemListView,
    BudgetListView,
)

urlpatterns = [
    path('budgets/', BudgetListView.as_view(), name='budget-list'),
    path('budgets/<int:pk>/', BudgetDetailView.as_view(), name='budget-detail'),
    path('budget-items/', BudgetItemListView.as_view(), name='budgetitem-list'),
    path('budget-items/<int:pk>/', BudgetItemDetailView.as_view(), name='budgetitem-detail'),
]
