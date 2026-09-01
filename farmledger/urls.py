from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.db.models import F, Sum
from django.shortcuts import render
from django.urls import path, include
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.models import FarmProfile
from crops.models import CropSeason, HarvestRecord, Sale
from equipment.models import Equipment, MaintenanceLog
from expenses.models import Expense
from fields.models import Field
from inventory.models import InventoryItem
from labor.models import Worker, Task

from accounts.views import set_currency_view
from budgets.views import budget_detail_view, budget_item_add_view, budget_code_detail_view
from crops.views import crop_detail_view, harvest_detail_view, crop_season_detail_view, sale_detail_view, waste_detail_view
from .views import model_list, model_create, model_update, model_delete


@login_required
def home(request):
    total_expenses = Expense.objects.aggregate(
        total=Sum('amount')
    )['total'] or 0

    total_revenue = Sale.objects.aggregate(
        total=Sum(F('quantity') * F('unit_price'))
    )['total'] or 0

    context = {
        'report_title': 'FarmLedger Pro',
        'farm_count': FarmProfile.objects.count(),
        'field_count': Field.objects.count(),
        'crop_count': CropSeason.objects.count(),
        'expense_total': total_expenses,
        'revenue_total': total_revenue,
        'equipment_count': Equipment.objects.count(),
        'inventory_count': InventoryItem.objects.count(),
        'worker_count': Worker.objects.count(),
        'task_count': Task.objects.exclude(status='COMPLETED').count(),
        'maintenance_count': MaintenanceLog.objects.count(),
        'recent_expenses': Expense.objects.select_related(
            'category', 'farm_profile'
        ).order_by('-date', '-id')[:5],
        'recent_crops': CropSeason.objects.select_related(
            'field'
        ).order_by('-created_at')[:5],
        'today': __import__('datetime').datetime.now().strftime('%a, %b %d'),
    }
    return render(request, 'home.html', context)


urlpatterns = [
    path('', home, name='home'),
    path('admin/', admin.site.urls),
    path('budgets/<int:pk>/', budget_detail_view, name='budget_detail'),
    path('budgets/<int:budget_pk>/items/add/', budget_item_add_view, name='budget_item_add'),
    path('codes/<int:pk>/', budget_code_detail_view, name='budget_code_detail'),
    path('crops/<int:pk>/', crop_detail_view, name='crop_detail'),
    path('seasons/<int:pk>/', crop_season_detail_view, name='crop_season_detail'),
    path('harvests/<int:pk>/', harvest_detail_view, name='harvest_detail'),
    path('sales/<int:pk>/', sale_detail_view, name='sale_detail'),
    path('waste/<int:pk>/', waste_detail_view, name='waste_detail'),
    path('set-currency/', set_currency_view, name='set_currency'),
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/', include('api.urls')),
    path('reports/', include('reports.urls')),
    path('integrations/', include('integrations.urls')),
    # Dashboard CRUD
    path('dashboard/<str:app>/<str:model>/', model_list, name='model_list'),
    path('dashboard/<str:app>/<str:model>/new/', model_create, name='model_create'),
    path('dashboard/<str:app>/<str:model>/<int:pk>/edit/', model_update, name='model_update'),
    path('dashboard/<str:app>/<str:model>/<int:pk>/delete/', model_delete, name='model_delete'),
]
