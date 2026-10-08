from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.db.models import F, Sum
from django.shortcuts import render
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.access import accessible_farms, scoped_queryset
from accounts.models import FarmProfile
from crops.models import CropSeason, HarvestRecord, Sale
from equipment.models import Equipment, MaintenanceLog
from expenses.models import Expense
from fields.models import Field
from income.models import Income
from inventory.models import InventoryItem
from labor.models import Worker, Task

from accounts.views import PostOnlyLogoutView, set_currency_view
from budgets.views import budget_detail_view, budget_item_add_view, budget_code_detail_view, budget_plan_edit_view
from crops.views import crop_detail_view, harvest_detail_view, crop_season_detail_view, sale_detail_view, waste_detail_view
from income.views import income_source_detail_view
from .views import (
    model_list, model_create, model_update, model_delete, farm_settings,
    report_workspace,
)


@login_required
def home(request):
    user = request.user
    total_expenses = scoped_queryset(Expense, user).aggregate(
        total=Sum('amount')
    )['total'] or 0

    total_revenue = scoped_queryset(Sale, user).aggregate(
        total=Sum(F('quantity') * F('unit_price'))
    )['total'] or 0

    total_income = scoped_queryset(Income, user).aggregate(
        total=Sum('amount')
    )['total'] or 0

    context = {
        'report_title': 'FarmLedger Pro',
        'farm_count': accessible_farms(user).count(),
        'field_count': scoped_queryset(Field, user).count(),
        'crop_count': scoped_queryset(CropSeason, user).count(),
        'expense_total': total_expenses,
        'revenue_total': total_revenue,
        'income_total': total_income,
        'equipment_count': scoped_queryset(Equipment, user).count(),
        'inventory_count': scoped_queryset(InventoryItem, user).count(),
        'worker_count': scoped_queryset(Worker, user).count(),
        'task_count': scoped_queryset(Task, user).exclude(status='COMPLETED').count(),
        'maintenance_count': scoped_queryset(MaintenanceLog, user).count(),
        'recent_expenses': scoped_queryset(Expense, user).select_related(
            'category', 'farm_profile'
        ).order_by('-date', '-id')[:5],
        'recent_crops': scoped_queryset(CropSeason, user).select_related(
            'field'
        ).order_by('-created_at')[:5],
        'recent_income': scoped_queryset(Income, user).select_related(
            'source', 'farm_profile'
        ).order_by('-date', '-id')[:5],
        'today': __import__('datetime').datetime.now().strftime('%a, %b %d'),
    }
    return render(request, 'home.html', context)


urlpatterns = [
    path('login/', auth_views.LoginView.as_view(
        template_name='accounts/login.html', redirect_authenticated_user=True,
    ), name='login'),
    path('logout/', PostOnlyLogoutView.as_view(), name='logout'),
    path('', home, name='home'),
    path('reports/', report_workspace, name='reports'),
    path('reports/sales/', report_workspace, {'default_section': 'sales'}, name='report_sales'),
    path('reports/production/', report_workspace, {'default_section': 'production'}, name='report_production'),
    path('reports/livestock/', report_workspace, {'default_section': 'livestock'}, name='report_livestock'),
    path('reports/operations/', report_workspace, {'default_section': 'operations'}, name='report_operations'),
    path('admin/', admin.site.urls),
    path('budgets/new/', budget_plan_edit_view, name='budget_new'),
    path('budgets/<int:pk>/edit/', budget_plan_edit_view, name='budget_edit'),
    path('budgets/<int:pk>/', budget_detail_view, name='budget_detail'),
    path('budgets/<int:budget_pk>/items/add/', budget_item_add_view, name='budget_item_add'),
    path('codes/<int:pk>/', budget_code_detail_view, name='budget_code_detail'),
    path('crops/<int:pk>/', crop_detail_view, name='crop_detail'),
    path('seasons/<int:pk>/', crop_season_detail_view, name='crop_season_detail'),
    path('harvests/<int:pk>/', harvest_detail_view, name='harvest_detail'),
    path('sales/<int:pk>/', sale_detail_view, name='sale_detail'),
    path('waste/<int:pk>/', waste_detail_view, name='waste_detail'),
    path('income-sources/<int:pk>/', income_source_detail_view, name='income_source_detail'),
    path('set-currency/', set_currency_view, name='set_currency'),
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/', include('api.urls')),
    path('integrations/', include('integrations.urls')),
    path('api/income/', include('income.urls')),
    path('dashboard/settings/', farm_settings, name='farm_settings'),
    # Dashboard CRUD
    path('dashboard/<str:app>/<str:model>/', model_list, name='model_list'),
    path('dashboard/<str:app>/<str:model>/new/', model_create, name='model_create'),
    path('dashboard/<str:app>/<str:model>/<int:pk>/edit/', model_update, name='model_update'),
    path('dashboard/<str:app>/<str:model>/<int:pk>/delete/', model_delete, name='model_delete'),
]
