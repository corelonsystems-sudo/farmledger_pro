from datetime import date
from decimal import Decimal

from django.db.models import F, Sum
from django_filters import rest_framework as filters
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import FarmProfile, User
from accounts.serializers import FarmProfileSerializer, UserSerializer
from budgets.models import Budget
from budgets.queries import get_budget_variance_report
from crops.models import CropSeason, HarvestRecord
from equipment.models import Equipment, EquipmentUsage, MaintenanceLog
from expenses.models import Category, Expense
from expenses.serializers import (
    BulkExpenseSyncSerializer,
    CategorySerializer,
    ExpenseSerializer,
)
from fields.models import Field, LandParcel
from inventory.models import InventoryAlert, InventoryItem, InventoryTransaction
from labor.models import AttendanceRecord, PayrollRun, Task, Worker

from .serializers import (
    AttendanceRecordSerializer,
    CropSeasonSerializer,
    EquipmentSerializer,
    FieldSerializer,
    InventoryItemSerializer,
    PayrollRunSerializer,
    TaskSerializer,
    WorkerSerializer,
)


# --- Filters ---

class ExpenseFilter(filters.FilterSet):
    start_date = filters.DateFilter(field_name='date', lookup_expr='gte')
    end_date = filters.DateFilter(field_name='date', lookup_expr='lte')

    class Meta:
        model = Expense
        fields = ['category', 'crop_season', 'field', 'equipment', 'farm_profile', 'reconciliation_status']


class CropSeasonFilter(filters.FilterSet):
    class Meta:
        model = CropSeason
        fields = ['field', 'status', 'crop']


class FieldFilter(filters.FilterSet):
    class Meta:
        model = Field
        fields = ['farm_profile', 'soil_type', 'is_active']


class InventoryItemFilter(filters.FilterSet):
    class Meta:
        model = InventoryItem
        fields = ['farm_profile', 'supplier']


class EquipmentFilter(filters.FilterSet):
    class Meta:
        model = Equipment
        fields = ['farm_profile', 'equipment_type', 'is_active']


class WorkerFilter(filters.FilterSet):
    class Meta:
        model = Worker
        fields = ['farm_profile', 'is_active']


# --- CRUD Views ---

class UserListView(generics.ListCreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (IsAuthenticated,)


class UserDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = (IsAuthenticated,)


class FarmProfileListView(generics.ListCreateAPIView):
    queryset = FarmProfile.objects.all()
    serializer_class = FarmProfileSerializer
    permission_classes = (IsAuthenticated,)


class FarmProfileDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = FarmProfile.objects.all()
    serializer_class = FarmProfileSerializer
    permission_classes = (IsAuthenticated,)


class FieldListView(generics.ListCreateAPIView):
    queryset = Field.objects.all()
    serializer_class = FieldSerializer
    permission_classes = (IsAuthenticated,)
    filterset_class = FieldFilter


class FieldDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Field.objects.all()
    serializer_class = FieldSerializer
    permission_classes = (IsAuthenticated,)


class CropSeasonListView(generics.ListCreateAPIView):
    queryset = CropSeason.objects.all()
    serializer_class = CropSeasonSerializer
    permission_classes = (IsAuthenticated,)
    filterset_class = CropSeasonFilter


class CropSeasonDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = CropSeason.objects.all()
    serializer_class = CropSeasonSerializer
    permission_classes = (IsAuthenticated,)


class CategoryListView(generics.ListCreateAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = (IsAuthenticated,)


class ExpenseListView(generics.ListCreateAPIView):
    queryset = Expense.objects.all()
    serializer_class = ExpenseSerializer
    permission_classes = (IsAuthenticated,)
    filterset_class = ExpenseFilter


class ExpenseDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Expense.objects.all()
    serializer_class = ExpenseSerializer
    permission_classes = (IsAuthenticated,)


class InventoryItemListView(generics.ListCreateAPIView):
    queryset = InventoryItem.objects.all()
    serializer_class = InventoryItemSerializer
    permission_classes = (IsAuthenticated,)
    filterset_class = InventoryItemFilter


class InventoryItemDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = InventoryItem.objects.all()
    serializer_class = InventoryItemSerializer
    permission_classes = (IsAuthenticated,)


class EquipmentListView(generics.ListCreateAPIView):
    queryset = Equipment.objects.all()
    serializer_class = EquipmentSerializer
    permission_classes = (IsAuthenticated,)
    filterset_class = EquipmentFilter


class EquipmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Equipment.objects.all()
    serializer_class = EquipmentSerializer
    permission_classes = (IsAuthenticated,)


class WorkerListView(generics.ListCreateAPIView):
    queryset = Worker.objects.all()
    serializer_class = WorkerSerializer
    permission_classes = (IsAuthenticated,)
    filterset_class = WorkerFilter


class WorkerDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Worker.objects.all()
    serializer_class = WorkerSerializer
    permission_classes = (IsAuthenticated,)


# --- Special Endpoints ---

class BulkExpenseSyncEndpoint(APIView):
    """Bulk expense sync endpoint with offline UUID deduplication."""
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = BulkExpenseSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        results = serializer.save()
        return Response({'results': results}, status=status.HTTP_200_OK)


class DashboardEndpoint(APIView):
    """Dashboard endpoint returning YTD spend, YTD revenue, top 5 expense
    categories, and budget burn rate.
    """
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        today = date.today()
        year_start = date(today.year, 1, 1)

        ytd_spend = Expense.objects.filter(
            date__gte=year_start,
            date__lte=today,
        ).aggregate(total=Sum('amount'))['total'] or Decimal('0')

        from crops.models import Sale
        ytd_revenue = Decimal('0')
        sales = Sale.objects.filter(
            sale_date__gte=year_start,
            sale_date__lte=today,
        )
        for s in sales:
            ytd_revenue += s.total_amount

        top_categories = (
            Expense.objects
            .filter(date__gte=year_start, date__lte=today)
            .values('category__name')
            .annotate(total=Sum('amount'))
            .order_by('-total')[:5]
        )

        budget_data = get_budget_variance_report()
        total_planned = sum(Decimal(b['planned_amount']) for b in budget_data)
        total_actual = sum(Decimal(b['actual_amount']) for b in budget_data)
        burn_rate = (total_actual / total_planned * 100) if total_planned > 0 else Decimal('0')

        return Response({
            'ytd_spend': str(ytd_spend),
            'ytd_revenue': str(ytd_revenue),
            'top_expense_categories': [
                {'category': c['category__name'], 'total': str(c['total'])}
                for c in top_categories
            ],
            'budget_burn_rate': str(burn_rate) + '%',
        })


class FieldCostSummaryEndpoint(APIView):
    """Field cost summary endpoint returning total cost breakdown for a single field."""
    permission_classes = (IsAuthenticated,)

    def get(self, request, field_id):
        from fields.models import Field
        try:
            field = Field.objects.get(id=field_id)
        except Field.DoesNotExist:
            return Response({'error': 'Field not found'}, status=404)

        expenses = Expense.objects.filter(field=field)
        total_cost = expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0')

        category_breakdown = (
            expenses
            .values('category__name')
            .annotate(total=Sum('amount'))
            .order_by('-total')
        )

        crop_season_breakdown = (
            expenses
            .filter(crop_season__isnull=False)
            .values('crop_season__crop__name')
            .annotate(total=Sum('amount'))
            .order_by('-total')
        )

        return Response({
            'field_id': field.id,
            'field_name': field.name,
            'acreage': str(field.acreage),
            'total_cost': str(total_cost),
            'cost_per_acre': str(total_cost / field.acreage) if field.acreage > 0 else '0',
            'category_breakdown': [
                {'category': c['category__name'], 'total': str(c['total'])}
                for c in category_breakdown
            ],
            'crop_season_breakdown': [
                {'crop': c['crop_season__crop__name'], 'total': str(c['total'])}
                for c in crop_season_breakdown
            ],
        })


class CropProfitabilityEndpoint(APIView):
    """Crop profitability endpoint returning revenue vs cost vs net margin
    for a single crop season.
    """
    permission_classes = (IsAuthenticated,)

    def get(self, request, crop_season_id):
        try:
            crop_season = CropSeason.objects.get(id=crop_season_id)
        except CropSeason.DoesNotExist:
            return Response({'error': 'Crop season not found'}, status=404)

        harvests = crop_season.harvests.all()
        revenue = Decimal('0')
        total_quantity = Decimal('0')
        for h in harvests:
            revenue += h.total_revenue
            total_quantity += h.quantity

        expenses = Expense.objects.filter(crop_season=crop_season)
        total_cost = expenses.aggregate(total=Sum('amount'))['total'] or Decimal('0')

        net_margin = revenue - total_cost
        margin_pct = (net_margin / revenue * 100) if revenue > 0 else Decimal('0')

        return Response({
            'crop_season_id': crop_season.id,
            'crop_type': crop_season.crop.name,
            'field': crop_season.field.name,
            'status': crop_season.status,
            'total_revenue': str(revenue),
            'total_cost': str(total_cost),
            'net_margin': str(net_margin),
            'margin_percentage': str(margin_pct) + '%',
            'total_quantity_produced': str(total_quantity),
            'cost_per_kg': str(total_cost / total_quantity) if total_quantity > 0 else '0',
        })
