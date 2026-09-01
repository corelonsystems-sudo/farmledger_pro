from django.urls import path

from .views import (
    CashFlowView,
    CropBalanceSheetView,
    HarvestReportView,
    ProfitLossView,
    QuickBooksExportView,
    ReportDashboardView,
    ScheduleFView,
)

urlpatterns = [
    path('', ReportDashboardView.as_view(), name='report_dashboard'),
    path('profit-loss/', ProfitLossView.as_view(), name='report_profit_loss'),
    path('crop-balance-sheet/', CropBalanceSheetView.as_view(), name='report_crop_balance'),
    path('crop-balance-sheet/<int:crop_season_id>/', CropBalanceSheetView.as_view(), name='report_crop_balance_detail'),
    path('cash-flow/', CashFlowView.as_view(), name='report_cash_flow'),
    path('schedule-f/', ScheduleFView.as_view(), name='report_schedule_f'),
    path('quickbooks-export/', QuickBooksExportView.as_view(), name='report_quickbooks_export'),
    path('harvest/<int:harvest_id>/', HarvestReportView.as_view(), name='report_harvest'),
]
