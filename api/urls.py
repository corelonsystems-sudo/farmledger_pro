from django.urls import path, include

from .views import (
    BulkExpenseSyncEndpoint,
    CategoryListView,
    CropProfitabilityEndpoint,
    CropSeasonDetailView,
    CropSeasonListView,
    DashboardEndpoint,
    EquipmentDetailView,
    EquipmentListView,
    ExpenseDetailView,
    ExpenseListView,
    FarmProfileDetailView,
    FarmProfileListView,
    FieldCostSummaryEndpoint,
    FieldDetailView,
    FieldListView,
    InventoryItemDetailView,
    InventoryItemListView,
    UserDetailView,
    UserListView,
    WorkerDetailView,
    WorkerListView,
)

urlpatterns = [
    # Users
    path('users/', UserListView.as_view(), name='api_user_list'),
    path('users/<int:pk>/', UserDetailView.as_view(), name='api_user_detail'),

    # Farm Profiles
    path('farm-profiles/', FarmProfileListView.as_view(), name='api_farmprofile_list'),
    path('farm-profiles/<int:pk>/', FarmProfileDetailView.as_view(), name='api_farmprofile_detail'),

    # Fields
    path('fields/', FieldListView.as_view(), name='api_field_list'),
    path('fields/<int:pk>/', FieldDetailView.as_view(), name='api_field_detail'),
    path('fields/<int:field_id>/cost-summary/', FieldCostSummaryEndpoint.as_view(), name='api_field_cost_summary'),

    # Crop Seasons
    path('crop-seasons/', CropSeasonListView.as_view(), name='api_cropseason_list'),
    path('crop-seasons/<int:pk>/', CropSeasonDetailView.as_view(), name='api_cropseason_detail'),
    path('crop-seasons/<int:crop_season_id>/profitability/', CropProfitabilityEndpoint.as_view(), name='api_crop_profitability'),

    # Categories
    path('categories/', CategoryListView.as_view(), name='api_category_list'),

    # Expenses
    path('expenses/', ExpenseListView.as_view(), name='api_expense_list'),
    path('expenses/<int:pk>/', ExpenseDetailView.as_view(), name='api_expense_detail'),
    path('expenses/bulk-sync/', BulkExpenseSyncEndpoint.as_view(), name='api_expense_bulk_sync'),

    # Inventory
    path('inventory-items/', InventoryItemListView.as_view(), name='api_inventoryitem_list'),
    path('inventory-items/<int:pk>/', InventoryItemDetailView.as_view(), name='api_inventoryitem_detail'),

    # Equipment
    path('equipment/', EquipmentListView.as_view(), name='api_equipment_list'),
    path('equipment/<int:pk>/', EquipmentDetailView.as_view(), name='api_equipment_detail'),

    # Workers
    path('workers/', WorkerListView.as_view(), name='api_worker_list'),
    path('workers/<int:pk>/', WorkerDetailView.as_view(), name='api_worker_detail'),

    # Dashboard
    path('dashboard/', DashboardEndpoint.as_view(), name='api_dashboard'),

    # Expense app endpoints (re-include)
    path('expenses-app/', include('expenses.urls')),
    # Budget app endpoints
    path('', include('budgets.urls')),
]
