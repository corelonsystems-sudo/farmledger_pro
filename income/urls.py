from django.urls import path

from .views import (
    BulkIncomeSyncView,
    IncomeListView,
    IncomeSourceDetailView,
    IncomeSourceListView,
)

urlpatterns = [
    path('sources/', IncomeSourceListView.as_view(), name='income-source-list'),
    path('sources/<int:pk>/', IncomeSourceDetailView.as_view(), name='income-source-detail'),
    path('incomes/', IncomeListView.as_view(), name='income-list'),
    path('incomes/bulk-sync/', BulkIncomeSyncView.as_view(), name='income-bulk-sync'),
]
