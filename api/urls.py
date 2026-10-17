from django.urls import path, include

from .views import (
    AttendanceRecordDetailView,
    AttendanceRecordListView,
    BudgetDetailView,
    BudgetIncomePlanDetailView,
    BudgetIncomePlanListView,
    BudgetItemDetailView,
    BudgetItemListView,
    BudgetListView,
    BulkExpenseSyncEndpoint,
    CategoryListView,
    CropListView,
    CropProfitabilityEndpoint,
    CropSeasonDetailView,
    CropSeasonListView,
    CustomerDetailView,
    CustomerListView,
    DashboardEndpoint,
    EquipmentDetailView,
    EquipmentListView,
    EquipmentUsageDetailView,
    EquipmentUsageListView,
    ExpenseDetailView,
    ExpenseListView,
    FarmProfileDetailView,
    FarmProfileListView,
    FieldCostSummaryEndpoint,
    FieldDetailView,
    FieldListView,
    HarvestRecordDetailView,
    HarvestRecordListView,
    IncomeDetailView,
    IncomeListView,
    InventoryAlertDetailView,
    InventoryAlertListView,
    InventoryItemDetailView,
    InventoryItemListView,
    InventoryTransactionDetailView,
    InventoryTransactionListView,
    LandParcelDetailView,
    LandParcelListView,
    MaintenanceLogDetailView,
    MaintenanceLogListView,
    PayrollLineDetailView,
    PayrollLineListView,
    PayrollRunDetailView,
    PayrollRunListView,
    ProcessingStageDetailView,
    ProcessingStageListView,
    SaleDetailView,
    SaleListView,
    TaskDetailView,
    TaskListView,
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

    # Fields & Parcels
    path('fields/', FieldListView.as_view(), name='api_field_list'),
    path('fields/<int:pk>/', FieldDetailView.as_view(), name='api_field_detail'),
    path('fields/<int:field_id>/cost-summary/', FieldCostSummaryEndpoint.as_view(), name='api_field_cost_summary'),
    path('land-parcels/', LandParcelListView.as_view(), name='api_landparcel_list'),
    path('land-parcels/<int:pk>/', LandParcelDetailView.as_view(), name='api_landparcel_detail'),

    # Crops, Harvests, Processing, Sales
    path('crops/', CropListView.as_view(), name='api_crop_list'),
    path('crop-seasons/', CropSeasonListView.as_view(), name='api_cropseason_list'),
    path('crop-seasons/<int:pk>/', CropSeasonDetailView.as_view(), name='api_cropseason_detail'),
    path('crop-seasons/<int:crop_season_id>/profitability/', CropProfitabilityEndpoint.as_view(), name='api_crop_profitability'),
    path('harvests/', HarvestRecordListView.as_view(), name='api_harvest_list'),
    path('harvests/<int:pk>/', HarvestRecordDetailView.as_view(), name='api_harvest_detail'),
    path('processing-stages/', ProcessingStageListView.as_view(), name='api_stage_list'),
    path('processing-stages/<int:pk>/', ProcessingStageDetailView.as_view(), name='api_stage_detail'),
    path('sales/', SaleListView.as_view(), name='api_sale_list'),
    path('sales/<int:pk>/', SaleDetailView.as_view(), name='api_sale_detail'),
    path('customers/', CustomerListView.as_view(), name='api_customer_list'),
    path('customers/<int:pk>/', CustomerDetailView.as_view(), name='api_customer_detail'),

    # Categories
    path('categories/', CategoryListView.as_view(), name='api_category_list'),

    # Expenses
    path('expenses/', ExpenseListView.as_view(), name='api_expense_list'),
    path('expenses/<int:pk>/', ExpenseDetailView.as_view(), name='api_expense_detail'),
    path('expenses/bulk-sync/', BulkExpenseSyncEndpoint.as_view(), name='api_expense_bulk_sync'),

    # Finance
    path('incomes/', IncomeListView.as_view(), name='api_income_list'),
    path('incomes/<int:pk>/', IncomeDetailView.as_view(), name='api_income_detail'),
    path('budgets/', BudgetListView.as_view(), name='api_budget_list'),
    path('budgets/<int:pk>/', BudgetDetailView.as_view(), name='api_budget_detail'),
    path('budget-items/', BudgetItemListView.as_view(), name='api_budgetitem_list'),
    path('budget-items/<int:pk>/', BudgetItemDetailView.as_view(), name='api_budgetitem_detail'),
    path('budget-income-plans/', BudgetIncomePlanListView.as_view(), name='api_incomeplan_list'),
    path('budget-income-plans/<int:pk>/', BudgetIncomePlanDetailView.as_view(), name='api_incomeplan_detail'),

    # Inventory
    path('inventory-items/', InventoryItemListView.as_view(), name='api_inventoryitem_list'),
    path('inventory-items/<int:pk>/', InventoryItemDetailView.as_view(), name='api_inventoryitem_detail'),
    path('inventory-transactions/', InventoryTransactionListView.as_view(), name='api_inventorytxn_list'),
    path('inventory-transactions/<int:pk>/', InventoryTransactionDetailView.as_view(), name='api_inventorytxn_detail'),
    path('inventory-alerts/', InventoryAlertListView.as_view(), name='api_inventoryalert_list'),
    path('inventory-alerts/<int:pk>/', InventoryAlertDetailView.as_view(), name='api_inventoryalert_detail'),

    # Equipment
    path('equipment/', EquipmentListView.as_view(), name='api_equipment_list'),
    path('equipment/<int:pk>/', EquipmentDetailView.as_view(), name='api_equipment_detail'),
    path('maintenance-logs/', MaintenanceLogListView.as_view(), name='api_maintenance_list'),
    path('maintenance-logs/<int:pk>/', MaintenanceLogDetailView.as_view(), name='api_maintenance_detail'),
    path('equipment-usage/', EquipmentUsageListView.as_view(), name='api_usage_list'),
    path('equipment-usage/<int:pk>/', EquipmentUsageDetailView.as_view(), name='api_usage_detail'),

    # Labor
    path('workers/', WorkerListView.as_view(), name='api_worker_list'),
    path('workers/<int:pk>/', WorkerDetailView.as_view(), name='api_worker_detail'),
    path('tasks/', TaskListView.as_view(), name='api_task_list'),
    path('tasks/<int:pk>/', TaskDetailView.as_view(), name='api_task_detail'),
    path('attendance/', AttendanceRecordListView.as_view(), name='api_attendance_list'),
    path('attendance/<int:pk>/', AttendanceRecordDetailView.as_view(), name='api_attendance_detail'),
    path('payroll-runs/', PayrollRunListView.as_view(), name='api_payroll_list'),
    path('payroll-runs/<int:pk>/', PayrollRunDetailView.as_view(), name='api_payroll_detail'),
    path('payroll-lines/', PayrollLineListView.as_view(), name='api_payrollline_list'),
    path('payroll-lines/<int:pk>/', PayrollLineDetailView.as_view(), name='api_payrollline_detail'),

    # Dashboard
    path('dashboard/', DashboardEndpoint.as_view(), name='api_dashboard'),

    # Expense app endpoints (re-include)
    path('expenses-app/', include('expenses.urls')),
    # Budget app endpoints
    path('', include('budgets.urls')),
]
