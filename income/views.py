from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.shortcuts import get_object_or_404, render
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.access import FarmScopedMixin, accessible_farms, scoped_queryset

from .models import Income, IncomeSource
from .serializers import (
    BulkIncomeSyncSerializer,
    IncomeSerializer,
    IncomeSourceSerializer,
)


@login_required
def income_source_detail_view(request, pk):
    source = get_object_or_404(IncomeSource.objects.select_related(), pk=pk)
    base_incomes = scoped_queryset(Income, request.user).filter(
        source=source).select_related(
        'farm_profile', 'crop_season', 'field', 'sale'
    ).order_by('-date')

    # Get filter options from all incomes (not filtered)
    from crops.models import CropSeason
    all_crop_seasons = scoped_queryset(CropSeason, request.user).filter(
        incomes__source=source).distinct()
    all_fields = list({income.field for income in base_incomes if income.field})

    # Apply filters
    incomes = base_incomes
    status = request.GET.get('status')
    customer = request.GET.get('customer')
    crop_season = request.GET.get('crop_season')
    field = request.GET.get('field')

    if status:
        incomes = incomes.filter(reconciliation_status=status)
    if customer:
        incomes = incomes.filter(customer__icontains=customer)
    if crop_season:
        incomes = incomes.filter(crop_season_id=crop_season)
    if field:
        incomes = incomes.filter(field_id=field)

    # Calculate statistics
    total_amount = incomes.aggregate(total=Sum('amount'))['total'] or 0
    total_count = incomes.count()

    # Group by status
    pending_count = incomes.filter(reconciliation_status='PENDING').count()
    reconciled_count = incomes.filter(reconciliation_status='RECONCILED').count()

    # Recent incomes (all filtered results)
    recent_incomes = incomes[:50]

    header_farms = list(accessible_farms(request.user).order_by('pk')[:2])
    context = {
        'source': source,
        'header_farm': header_farms[0] if len(header_farms) == 1 else None,
        'can_edit_source': request.user.is_superuser,
        'can_record_income': request.user.is_superuser or accessible_farms(
            request.user, write_model='income.income').exists(),
        'incomes': incomes,
        'recent_incomes': recent_incomes,
        'total_amount': total_amount,
        'total_count': total_count,
        'pending_count': pending_count,
        'reconciled_count': reconciled_count,
        'crop_seasons': all_crop_seasons,
        'fields': all_fields,
        'filters': {
            'status': status,
            'customer': customer,
            'crop_season': crop_season,
            'field': field,
        }
    }
    return render(request, 'income/income_source_detail.html', context)


class IncomeSourceListView(FarmScopedMixin, generics.ListCreateAPIView):
    queryset = IncomeSource.objects.all()
    serializer_class = IncomeSourceSerializer
    permission_classes = (IsAuthenticated,)


class IncomeSourceDetailView(FarmScopedMixin, generics.RetrieveUpdateDestroyAPIView):
    queryset = IncomeSource.objects.all()
    serializer_class = IncomeSourceSerializer
    permission_classes = (IsAuthenticated,)


class IncomeListView(FarmScopedMixin, APIView):
    permission_classes = (IsAuthenticated,)
    queryset = Income.objects.all()

    def get(self, request):
        qs = self.get_queryset().order_by('-date')
        serializer = IncomeSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = IncomeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        self._validate_related_farms(serializer.validated_data)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class BulkIncomeSyncView(APIView):
    """Bulk sync endpoint for mobile devices.

    Accepts a list of incomes and uses get_or_create with the offline UUID
    to prevent duplicates when the device reconnects to the internet.
    """
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        serializer = BulkIncomeSyncSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        for item in serializer.validated_data['incomes']:
            farm = item['farm_profile']
            if not accessible_farms(request.user, write_model='income.income').filter(pk=farm.pk).exists():
                raise PermissionDenied('You cannot change records for this farm.')
            budget_item = item.get('budget_item')
            if budget_item and budget_item.budget.farm_profile_id != farm.pk:
                raise ValidationError({'budget_item': 'Budget item must belong to the selected farm.'})
        results = serializer.save()
        return Response({'results': results}, status=status.HTTP_200_OK)
