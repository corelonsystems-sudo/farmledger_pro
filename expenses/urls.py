from django.urls import path

from .views import BulkExpenseSyncView, CategoryListView, ExpenseListView

urlpatterns = [
    path('categories/', CategoryListView.as_view(), name='category-list'),
    path('expenses/', ExpenseListView.as_view(), name='expense-list'),
    path('expenses/bulk-sync/', BulkExpenseSyncView.as_view(), name='expense-bulk-sync'),
]
