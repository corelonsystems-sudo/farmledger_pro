from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import FieldError, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models.deletion import ProtectedError
from django.db.models import Count, F, Max, Q, Sum, Value, DecimalField, OuterRef, Subquery
from django.db.models.functions import Coalesce, Lower
from django.forms import modelform_factory
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.template.loader import render_to_string
from django.template.response import TemplateResponse
from django.urls import reverse
from urllib.parse import quote
from collections import defaultdict
from decimal import Decimal
import datetime
import math

from accounts.access import accessible_farms, scoped_queryset, GLOBAL_CATALOGS
from accounts.models import FarmProfile
from .forms import FORM_REGISTRY, WASTE_FORM_REGISTRY


# Fields we never want in auto-generated dashboard forms
AUTO_FIELDS = {'created_at', 'updated_at', 'offline_uuid', 'uuid', 'id'}

# Models that have a dedicated detail page, keyed by (app_label, model_name).
DETAIL_URL_NAMES = {
    ('crops', 'crop'): 'crop_detail',
    ('crops', 'cropseason'): 'crop_season_detail',
    ('crops', 'harvestrecord'): 'harvest_detail',
    ('budgets', 'budget'): 'budget_detail',
    ('expenses', 'category'): 'budget_code_detail',
    ('income', 'incomesource'): 'income_source_detail',
    ('crops', 'customer'): 'customer_detail',
}

# Override generic dashboard list columns for specific models.
CUSTOM_LIST_DISPLAY = {
    ('accounts', 'farmprofile'): ['farm_name', 'location', 'acreage', 'phone', 'default_currency'],
    ('expenses', 'scheduleline'): ['code', 'name', 'description'],
    ('expenses', 'budgetsubcode'): ['category', 'code', 'name', 'description'],
    ('accounts', 'farmmembership'): ['farm_profile', 'user', 'role'],
    ('crops', 'cropseason'): ['crop', 'field', 'planting_date', 'status'],
    ('crops', 'customer'): ['name', 'phone', 'email'],
    ('income', 'income'): ['source', 'amount', 'date', 'customer'],
    ('income', 'incomesource'): ['name', 'code', 'schedule_f_line'],
}


def _get_model(app_label, model_name):
    try:
        return apps.get_model(app_label, model_name)
    except LookupError as exc:
        raise Http404(f"No model '{model_name}' in app '{app_label}'") from exc


def _get_form_class(model, waste=False):
    """Return the custom ModelForm for the given model, falling back to modelform_factory."""
    key = (model._meta.app_label, model._meta.model_name)
    if waste:
        form_cls = WASTE_FORM_REGISTRY.get(key)
        if form_cls:
            return form_cls
    form_cls = FORM_REGISTRY.get(key)
    if form_cls:
        return form_cls
    # Fallback: auto-generate a form excluding audit fields
    meta = model._meta
    exclude = [f.name for f in meta.fields if f.name in AUTO_FIELDS or f.auto_created]
    for f in meta.fields:
        if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False):
            if f.name not in exclude:
                exclude.append(f.name)
    return modelform_factory(model, exclude=exclude)


def _list_display(model):
    """Pick a few display columns for the list table."""
    preferred = ['name', 'code', 'farm_name', 'crop', 'category', 'equipment_type', 'status', 'date', 'amount']
    fields = [f for f in model._meta.fields if f.name in preferred and not f.is_relation]
    if not fields:
        # fallback: first non-FK / non-JSON non-text-large field
        for f in model._meta.fields:
            if f.is_relation:
                continue
            if f.name in AUTO_FIELDS:
                continue
            if f.get_internal_type() in ('JSONField', 'TextField'):
                continue
            fields.append(f)
            if len(fields) >= 4:
                break
    if not fields:
        fields = [model._meta.pk]
    return fields[:4]


def _search_filter(queryset, q):
    """Very light search: look for q in any CharField/TextField."""
    if not q:
        return queryset
    model = queryset.model
    q_filter = Q()
    for f in model._meta.fields:
        if f.get_internal_type() in ('CharField', 'TextField'):
            q_filter |= Q(**{f'{f.name}__icontains': q})
    if q_filter:
        try:
            return queryset.filter(q_filter)
        except FieldError:
            pass
    return queryset


def _crud_urls(app_label, model_name, pk=None):
    base = reverse('model_list', kwargs={'app': app_label, 'model': model_name})
    create = reverse('model_create', kwargs={'app': app_label, 'model': model_name})
    edit = reverse('model_update', kwargs={'app': app_label, 'model': model_name, 'pk': pk}) if pk else None
    delete = reverse('model_delete', kwargs={'app': app_label, 'model': model_name, 'pk': pk}) if pk else None
    return {'list': base, 'create': create, 'edit': edit, 'delete': delete}


def _table_field(obj, field):
    """Return a sensible string for a table cell."""
    value = getattr(obj, field.name)
    if field.is_relation and value is not None:
        return str(value)
    if value is None:
        return '-'
    return str(value)


def _compute_stats(app_label, model_name):
    """Compute simple statistics for a given model to display on the list page.

    Returns a list of {'label': ..., 'value': ...} dicts.
    """
    key = (app_label, model_name)
    stats = []

    if key == ('accounts', 'farmprofile'):
        from accounts.models import FarmProfile
        total = FarmProfile.objects.count()
        acreage = FarmProfile.objects.aggregate(t=Coalesce(Sum('acreage'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Farms', 'value': total},
            {'label': 'Total Acreage', 'value': f'{acreage} ac'},
        ]

    elif key == ('accounts', 'user'):
        from accounts.models import User
        stats = [
            {'label': 'Total Users', 'value': User.objects.count()},
            {'label': 'Active', 'value': User.objects.filter(is_active=True).count()},
            {'label': 'Staff', 'value': User.objects.filter(is_superuser=True).count()},
        ]

    elif key == ('fields', 'field'):
        from fields.models import Field
        stats = [
            {'label': 'Total Fields', 'value': Field.objects.count()},
            {'label': 'Active', 'value': Field.objects.filter(is_active=True).count()},
            {'label': 'Inactive', 'value': Field.objects.filter(is_active=False).count()},
            {'label': 'Total Acreage', 'value': f'{Field.objects.aggregate(t=Coalesce(Sum("acreage"), 0, output_field=DecimalField()))["t"]} ac'},
        ]

    elif key == ('fields', 'landparcel'):
        from fields.models import LandParcel
        stats = [
            {'label': 'Total Parcels', 'value': LandParcel.objects.count()},
            {'label': 'Owned', 'value': LandParcel.objects.filter(land_type='OWNED').count()},
            {'label': 'Leased', 'value': LandParcel.objects.filter(land_type='LEASED').count()},
            {'label': 'Rented', 'value': LandParcel.objects.filter(land_type='RENTED').count()},
        ]

    elif key == ('crops', 'crop'):
        from crops.models import Crop
        from collections import Counter
        cats = Counter(c.category for c in Crop.objects.all())
        stats = [
            {'label': 'Total Crops', 'value': Crop.objects.count()},
            {'label': 'Grains', 'value': cats.get('GRAIN', 0)},
            {'label': 'Oilseeds', 'value': cats.get('OILSEED', 0)},
            {'label': 'Forage', 'value': cats.get('FORAGE', 0)},
            {'label': 'Vegetables', 'value': cats.get('VEGETABLE', 0)},
        ]

    elif key == ('crops', 'cropseason'):
        from crops.models import CropSeason
        stats = [
            {'label': 'Total Seasons', 'value': CropSeason.objects.count()},
            {'label': 'Planned', 'value': CropSeason.objects.filter(status='PLANNED').count()},
            {'label': 'Growing', 'value': CropSeason.objects.filter(status='GROWING').count()},
            {'label': 'Harvested', 'value': CropSeason.objects.filter(status='HARVESTED').count()},
        ]

    elif key == ('crops', 'harvestrecord'):
        from crops.models import HarvestRecord
        total_qty = HarvestRecord.objects.aggregate(t=Coalesce(Sum('quantity'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Harvests', 'value': HarvestRecord.objects.count()},
            {'label': 'Total Quantity', 'value': f'{total_qty} kg'},
        ]

    elif key == ('crops', 'sale'):
        from crops.models import Sale
        total_qty = Sale.objects.aggregate(t=Coalesce(Sum('quantity'), 0, output_field=DecimalField()))['t']
        total_rev = Sale.objects.aggregate(t=Coalesce(Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Sales', 'value': Sale.objects.count()},
            {'label': 'Quantity Sold', 'value': f'{total_qty} kg'},
            {'label': 'Total Revenue', 'value': f'${total_rev}'},
        ]

    elif key == ('crops', 'processingstage'):
        from crops.models import ProcessingStage
        total_cost = ProcessingStage.objects.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t']
        total_value = ProcessingStage.objects.aggregate(t=Coalesce(Sum('added_value'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Stages', 'value': ProcessingStage.objects.count()},
            {'label': 'Processing Cost', 'value': f'${total_cost}'},
            {'label': 'Value Added', 'value': f'${total_value}'},
        ]

    elif key == ('expenses', 'category'):
        from expenses.models import Category
        stats = [
            {'label': 'Total Categories', 'value': Category.objects.count()},
        ]

    elif key == ('expenses', 'expense'):
        from expenses.models import Expense
        total = Expense.objects.aggregate(t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
        pending = Expense.objects.filter(reconciliation_status='PENDING').count()
        reconciled = Expense.objects.filter(reconciliation_status='RECONCILED').count()
        no_budget = Expense.objects.filter(budget_item__isnull=True).count()
        stats = [
            {'label': 'Total Expenses', 'value': Expense.objects.count()},
            {'label': 'Total Amount', 'value': f'${total}'},
            {'label': 'Pending', 'value': pending},
            {'label': 'Reconciled', 'value': reconciled},
            {'label': 'No Budget Item', 'value': no_budget},
        ]

    elif key == ('income', 'incomesource'):
        from income.models import IncomeSource
        stats = [
            {'label': 'Total Income Sources', 'value': IncomeSource.objects.count()},
        ]

    elif key == ('income', 'income'):
        from income.models import Income
        total = Income.objects.aggregate(t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
        pending = Income.objects.filter(reconciliation_status='PENDING').count()
        reconciled = Income.objects.filter(reconciliation_status='RECONCILED').count()
        stats = [
            {'label': 'Total Income', 'value': Income.objects.count()},
            {'label': 'Total Amount', 'value': f'${total}'},
            {'label': 'Pending', 'value': pending},
            {'label': 'Reconciled', 'value': reconciled},
        ]

    elif key == ('inventory', 'inventoryitem'):
        from inventory.models import InventoryItem
        total_value = InventoryItem.objects.aggregate(
            t=Coalesce(Sum('quantity_on_hand') * Sum('cost_per_unit'), 0, output_field=DecimalField())
        )['t']
        low_stock = sum(1 for item in InventoryItem.objects.all() if item.is_low_stock)
        stats = [
            {'label': 'Total Items', 'value': InventoryItem.objects.count()},
            {'label': 'Low Stock', 'value': low_stock},
            {'label': 'Est. Value', 'value': f'${total_value}'},
        ]

    elif key == ('inventory', 'inventorytransaction'):
        from inventory.models import InventoryTransaction
        stats = [
            {'label': 'Total Transactions', 'value': InventoryTransaction.objects.count()},
            {'label': 'Purchases', 'value': InventoryTransaction.objects.filter(transaction_type='PURCHASE').count()},
            {'label': 'Usage', 'value': InventoryTransaction.objects.filter(transaction_type='USAGE').count()},
            {'label': 'Adjustments', 'value': InventoryTransaction.objects.filter(transaction_type='ADJUSTMENT').count()},
        ]

    elif key == ('inventory', 'inventoryalert'):
        from inventory.models import InventoryAlert
        stats = [
            {'label': 'Total Alerts', 'value': InventoryAlert.objects.count()},
            {'label': 'Unresolved', 'value': InventoryAlert.objects.filter(is_resolved=False).count()},
            {'label': 'Resolved', 'value': InventoryAlert.objects.filter(is_resolved=True).count()},
        ]

    elif key == ('equipment', 'equipment'):
        from equipment.models import Equipment
        total_cost = Equipment.objects.aggregate(t=Coalesce(Sum('purchase_cost'), 0, output_field=DecimalField()))['t']
        total_value = Equipment.objects.aggregate(t=Coalesce(Sum('current_value'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Equipment', 'value': Equipment.objects.count()},
            {'label': 'Active', 'value': Equipment.objects.filter(is_active=True).count()},
            {'label': 'Purchase Cost', 'value': f'${total_cost}'},
            {'label': 'Current Value', 'value': f'${total_value}'},
        ]

    elif key == ('equipment', 'maintenancelog'):
        from equipment.models import MaintenanceLog
        total_cost = MaintenanceLog.objects.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Logs', 'value': MaintenanceLog.objects.count()},
            {'label': 'Total Cost', 'value': f'${total_cost}'},
        ]

    elif key == ('equipment', 'equipmentusage'):
        from equipment.models import EquipmentUsage
        total_hours = EquipmentUsage.objects.aggregate(t=Coalesce(Sum('hours'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Records', 'value': EquipmentUsage.objects.count()},
            {'label': 'Total Hours', 'value': f'{total_hours} h'},
        ]

    elif key == ('labor', 'worker'):
        from labor.models import Worker
        stats = [
            {'label': 'Total Workers', 'value': Worker.objects.count()},
            {'label': 'Active', 'value': Worker.objects.filter(is_active=True).count()},
            {'label': 'Inactive', 'value': Worker.objects.filter(is_active=False).count()},
        ]

    elif key == ('labor', 'task'):
        from labor.models import Task
        stats = [
            {'label': 'Total Tasks', 'value': Task.objects.count()},
            {'label': 'Assigned', 'value': Task.objects.filter(status='ASSIGNED').count()},
            {'label': 'In Progress', 'value': Task.objects.filter(status='IN_PROGRESS').count()},
            {'label': 'Completed', 'value': Task.objects.filter(status='COMPLETED').count()},
        ]

    elif key == ('labor', 'attendancerecord'):
        from labor.models import AttendanceRecord
        total_hours = AttendanceRecord.objects.aggregate(t=Coalesce(Sum('hours'), 0, output_field=DecimalField()))['t']
        total_ot = AttendanceRecord.objects.aggregate(t=Coalesce(Sum('overtime_hours'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Records', 'value': AttendanceRecord.objects.count()},
            {'label': 'Total Hours', 'value': f'{total_hours} h'},
            {'label': 'Overtime', 'value': f'{total_ot} h'},
        ]

    elif key == ('labor', 'payrollrun'):
        from labor.models import PayrollRun
        total = PayrollRun.objects.aggregate(t=Coalesce(Sum('total_amount'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Runs', 'value': PayrollRun.objects.count()},
            {'label': 'Pending', 'value': PayrollRun.objects.filter(status='PENDING').count()},
            {'label': 'Completed', 'value': PayrollRun.objects.filter(status='COMPLETED').count()},
            {'label': 'Total Amount', 'value': f'${total}'},
        ]

    elif key == ('expenses', 'category'):
        from expenses.models import Category
        stats = [
            {'label': 'Total Budget Codes', 'value': Category.objects.count()},
        ]

    elif key == ('budgets', 'budget'):
        from budgets.models import Budget
        from expenses.models import Expense
        total_planned = sum(b.total_planned for b in Budget.objects.all())
        total_spent = sum(b.total_spent for b in Budget.objects.all())
        remaining = total_planned - total_spent
        stats = [
            {'label': 'Total Budgets', 'value': Budget.objects.count()},
            {'label': 'Active', 'value': Budget.objects.filter(status='ACTIVE').count()},
            {'label': 'Planned', 'value': f'${total_planned}'},
            {'label': 'Spent', 'value': f'${total_spent}'},
            {'label': 'Remaining', 'value': f'${remaining}'},
        ]

    elif key == ('budgets', 'budgetitem'):
        from budgets.models import BudgetItem
        from expenses.models import Expense
        total_planned = BudgetItem.objects.aggregate(
            t=Coalesce(Sum('planned_amount'), 0, output_field=DecimalField())
        )['t']
        total_spent = Expense.objects.filter(budget_item__isnull=False).aggregate(
            t=Coalesce(Sum('amount'), 0, output_field=DecimalField())
        )['t']
        stats = [
            {'label': 'Total Items', 'value': BudgetItem.objects.count()},
            {'label': 'Planned', 'value': f'${total_planned}'},
            {'label': 'Spent', 'value': f'${total_spent}'},
            {'label': 'Remaining', 'value': f'${total_planned - total_spent}'},
        ]

    elif key == ('integrations', 'bankaccount'):
        from integrations.models import BankAccount
        stats = [
            {'label': 'Total Accounts', 'value': BankAccount.objects.count()},
            {'label': 'Active', 'value': BankAccount.objects.filter(is_active=True).count()},
        ]

    elif key == ('integrations', 'banktransaction'):
        from integrations.models import BankTransaction
        total = BankTransaction.objects.aggregate(t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Transactions', 'value': BankTransaction.objects.count()},
            {'label': 'Reconciled', 'value': BankTransaction.objects.filter(is_reconciled=True).count()},
            {'label': 'Flagged', 'value': BankTransaction.objects.filter(is_flagged=True).count()},
            {'label': 'Total Amount', 'value': f'${total}'},
        ]

    # --- Livestock: Animal Rearing ---
    elif key == ('livestock', 'animalspecies'):
        from livestock.models import AnimalSpecies
        from collections import Counter
        types = Counter(s.species_type for s in AnimalSpecies.objects.all())
        stats = [
            {'label': 'Total Species', 'value': AnimalSpecies.objects.count()},
            {'label': 'Cattle', 'value': types.get('CATTLE', 0)},
            {'label': 'Goats', 'value': types.get('GOAT', 0)},
            {'label': 'Sheep', 'value': types.get('SHEEP', 0)},
            {'label': 'Pigs', 'value': types.get('PIG', 0)},
        ]

    elif key == ('livestock', 'animalgroup'):
        from livestock.models import AnimalGroup
        total_animals = sum(g.count for g in AnimalGroup.objects.all())
        stats = [
            {'label': 'Total Groups', 'value': AnimalGroup.objects.count()},
            {'label': 'Active', 'value': AnimalGroup.objects.filter(is_active=True).count()},
            {'label': 'Total Animals', 'value': total_animals},
            {'label': 'Dairy', 'value': AnimalGroup.objects.filter(purpose='DAIRY').count()},
            {'label': 'Breeding', 'value': AnimalGroup.objects.filter(purpose='BREEDING').count()},
        ]

    elif key == ('livestock', 'animalrecord'):
        from livestock.models import AnimalRecord
        stats = [
            {'label': 'Total Animals', 'value': AnimalRecord.objects.count()},
            {'label': 'Healthy', 'value': AnimalRecord.objects.filter(health_status='HEALTHY').count()},
            {'label': 'Sick', 'value': AnimalRecord.objects.filter(health_status='SICK').count()},
            {'label': 'Sold', 'value': AnimalRecord.objects.filter(is_sold=True).count()},
            {'label': 'Breeding Stock', 'value': sum(1 for a in AnimalRecord.objects.all() if a.is_breeding_stock)},
        ]

    elif key == ('livestock', 'animalhealthrecord'):
        from livestock.models import AnimalHealthRecord
        total_cost = AnimalHealthRecord.objects.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Records', 'value': AnimalHealthRecord.objects.count()},
            {'label': 'Vaccinations', 'value': AnimalHealthRecord.objects.filter(record_type='VACCINATION').count()},
            {'label': 'Treatments', 'value': AnimalHealthRecord.objects.filter(record_type='TREATMENT').count()},
            {'label': 'Total Cost', 'value': f'${total_cost}'},
        ]

    elif key == ('livestock', 'breedingrecord'):
        from livestock.models import BreedingRecord
        stats = [
            {'label': 'Total Records', 'value': BreedingRecord.objects.count()},
            {'label': 'Pregnant', 'value': BreedingRecord.objects.filter(status='PREGNANT').count()},
            {'label': 'Delivered', 'value': BreedingRecord.objects.filter(status='DELIVERED').count()},
            {'label': 'Planned', 'value': BreedingRecord.objects.filter(status='PLANNED').count()},
        ]

    elif key == ('livestock', 'animalfeedlog'):
        from livestock.models import AnimalFeedLog
        total_kg = AnimalFeedLog.objects.aggregate(t=Coalesce(Sum('quantity_kg'), 0, output_field=DecimalField()))['t']
        total_cost = AnimalFeedLog.objects.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Logs', 'value': AnimalFeedLog.objects.count()},
            {'label': 'Total Feed', 'value': f'{total_kg} kg'},
            {'label': 'Total Cost', 'value': f'${total_cost}'},
        ]

    elif key == ('livestock', 'milkproductionrecord'):
        from livestock.models import MilkProductionRecord
        total_l = MilkProductionRecord.objects.aggregate(t=Coalesce(Sum('total_liters'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Records', 'value': MilkProductionRecord.objects.count()},
            {'label': 'Total Milk', 'value': f'{total_l} L'},
        ]

    # --- Livestock: Poultry ---
    elif key == ('livestock', 'poultrybatch'):
        from livestock.models import PoultryBatch
        total_birds = sum(b.current_count for b in PoultryBatch.objects.all())
        stats = [
            {'label': 'Total Batches', 'value': PoultryBatch.objects.count()},
            {'label': 'Active', 'value': PoultryBatch.objects.filter(status='ACTIVE').count()},
            {'label': 'Total Birds', 'value': total_birds},
            {'label': 'Layers', 'value': PoultryBatch.objects.filter(purpose='EGGS').count()},
            {'label': 'Broilers', 'value': PoultryBatch.objects.filter(purpose='MEAT').count()},
        ]

    elif key == ('livestock', 'eggproductionrecord'):
        from livestock.models import EggProductionRecord
        total_eggs = EggProductionRecord.objects.aggregate(t=Coalesce(Sum('eggs_collected'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Records', 'value': EggProductionRecord.objects.count()},
            {'label': 'Total Eggs', 'value': int(total_eggs)},
        ]

    elif key == ('livestock', 'poultryfeedlog'):
        from livestock.models import PoultryFeedLog
        total_kg = PoultryFeedLog.objects.aggregate(t=Coalesce(Sum('quantity_kg'), 0, output_field=DecimalField()))['t']
        total_cost = PoultryFeedLog.objects.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Logs', 'value': PoultryFeedLog.objects.count()},
            {'label': 'Total Feed', 'value': f'{total_kg} kg'},
            {'label': 'Total Cost', 'value': f'${total_cost}'},
        ]

    elif key == ('livestock', 'poultryhealthrecord'):
        from livestock.models import PoultryHealthRecord
        total_cost = PoultryHealthRecord.objects.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t']
        stats = [
            {'label': 'Total Records', 'value': PoultryHealthRecord.objects.count()},
            {'label': 'Vaccinations', 'value': PoultryHealthRecord.objects.filter(record_type='VACCINATION').count()},
            {'label': 'Treatments', 'value': PoultryHealthRecord.objects.filter(record_type='TREATMENT').count()},
            {'label': 'Total Cost', 'value': f'${total_cost}'},
        ]

    return stats


def _sale_list(request, app, model):
    from crops.models import Sale, Crop
    q = request.GET.get('q', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    buyer = request.GET.get('buyer', '')
    payment_method = request.GET.get('payment_method', '')
    payment_status = request.GET.get('payment_status', '')
    crop = request.GET.get('crop', '')
    waste = request.GET.get('waste', '')
    is_waste = waste == '1'

    queryset = Sale.objects.select_related(
        'harvest__crop_season__crop', 'harvest__crop_season__field',
        'processing_stage', 'customer', 'inventory_item'
    ).order_by('-sale_date')
    queryset = _search_filter(scoped_queryset(Sale, request.user, queryset), q)

    if date_from and date_to:
        queryset = queryset.filter(sale_date__range=[date_from, date_to])
    elif date_from:
        queryset = queryset.filter(sale_date__gte=date_from)
    elif date_to:
        queryset = queryset.filter(sale_date__lte=date_to)

    if buyer:
        queryset = queryset.filter(
            Q(buyer__icontains=buyer) | Q(customer__name__icontains=buyer))

    if payment_method:
        queryset = queryset.filter(payment_method=payment_method)

    if payment_status:
        queryset = queryset.filter(payment_status=payment_status)

    if crop:
        queryset = queryset.filter(harvest__crop_season__crop_id=crop)

    if is_waste:
        queryset = queryset.filter(is_waste=True)

    model_name = 'Waste Sales' if is_waste else 'Sales'
    create_label = 'New Waste Sale' if is_waste else 'New Sale'
    if is_waste:
        total_waste_qty = queryset.aggregate(t=Coalesce(Sum('quantity'), 0, output_field=DecimalField()))['t']
        total_waste_rev = queryset.aggregate(
            t=Coalesce(Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField())
        )['t']
        stats = {
            'total_amount': total_waste_rev,
            'total_count': queryset.count(),
            'total_qty': str(total_waste_qty),
        }
    else:
        total_qty = queryset.aggregate(t=Coalesce(Sum('quantity'), 0, output_field=DecimalField()))['t']
        total_rev = queryset.aggregate(
            t=Coalesce(Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField())
        )['t']
        stats = {
            'total_amount': total_rev,
            'total_count': queryset.count(),
            'total_qty': str(total_qty),
        }

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    if is_waste:
        next_url = quote(request.get_full_path(), safe='/')
        for sale in page_obj:
            if sale.processing_stage:
                sale.waste_edit_url = (
                    reverse('model_update', kwargs={'app': 'crops', 'model': 'processingstage', 'pk': sale.processing_stage.pk})
                    + f'?waste=1&next={next_url}'
                )
            else:
                sale.waste_edit_url = (
                    reverse('model_update', kwargs={'app': 'crops', 'model': 'harvestrecord', 'pk': sale.harvest.pk})
                    + f'?waste=1&next={next_url}'
                )

        all_waste = list(queryset.order_by('sale_date', 'created_at'))
        source_qty = {}
        running = defaultdict(lambda: Decimal('0'))
        balance_map = {}
        for s in all_waste:
            if s.processing_stage:
                key = ('stage', s.processing_stage.pk)
                qty = s.processing_stage.waste_quantity or Decimal('0')
            else:
                key = ('harvest', s.harvest.pk)
                qty = s.harvest.waste_quantity or Decimal('0')
            source_qty[key] = qty
            running[key] += s.quantity
            balance_map[s.pk] = qty - running[key]
        for sale in page_obj:
            sale.waste_balance = balance_map.get(sale.pk)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': model_name,
        'sales': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {
            'date_from': date_from,
            'date_to': date_to,
            'buyer': buyer,
            'payment_method': payment_method,
            'payment_status': payment_status,
            'crop': crop,
        },
        'crops': Crop.objects.order_by('name'),
        'payment_methods': Sale.PaymentMethod.choices,
        'payment_statuses': Sale.PaymentStatus.choices,
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'is_waste': is_waste,
        'create_label': create_label,
        'view_label': 'Waste Details' if is_waste else 'Sale Details',
        'stats': stats,
    }
    return render(request, 'crops/sale_list.html', context)


def _income_list(request, app, model):
    from income.models import Income, IncomeSource
    from crops.models import CropSeason
    from fields.models import Field

    q = request.GET.get('q', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    customer = request.GET.get('customer', '')
    source = request.GET.get('source', '')
    status = request.GET.get('status', '')
    payment_method = request.GET.get('payment_method', '')
    crop_season = request.GET.get('crop_season', '')
    field = request.GET.get('field', '')

    queryset = scoped_queryset(Income, request.user, Income.objects.select_related(
        'source', 'farm_profile', 'crop_season', 'field', 'sale'
    ).order_by('-date'))

    if q:
        queryset = queryset.filter(
            Q(customer__icontains=q) | Q(notes__icontains=q) | Q(source__name__icontains=q)
        )

    if date_from and date_to:
        queryset = queryset.filter(date__range=[date_from, date_to])
    elif date_from:
        queryset = queryset.filter(date__gte=date_from)
    elif date_to:
        queryset = queryset.filter(date__lte=date_to)

    if customer:
        queryset = queryset.filter(customer__icontains=customer)
    if source:
        queryset = queryset.filter(source_id=source)
    if status:
        queryset = queryset.filter(reconciliation_status=status)
    if payment_method:
        queryset = queryset.filter(payment_method=payment_method)
    if crop_season:
        queryset = queryset.filter(crop_season_id=crop_season)
    if field:
        queryset = queryset.filter(field_id=field)

    total_amount = queryset.aggregate(t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
    stats = {
        'total_count': queryset.count(),
        'total_amount': total_amount,
        'pending': queryset.filter(reconciliation_status='PENDING').count(),
        'reconciled': queryset.filter(reconciliation_status='RECONCILED').count(),
        'from_sales': queryset.filter(sale__isnull=False).count(),
    }

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': 'Income',
        'incomes': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {
            'date_from': date_from,
            'date_to': date_to,
            'customer': customer,
            'source': source,
            'status': status,
            'payment_method': payment_method,
            'crop_season': crop_season,
            'field': field,
        },
        'sources': IncomeSource.objects.order_by('name'),
        'statuses': Income.ReconciliationStatus.choices,
        'payment_methods': Income.PaymentMethod.choices,
        'crop_seasons': CropSeason.objects.order_by('-planting_date'),
        'fields': Field.objects.order_by('name'),
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'income/income_list.html', context)


def _income_source_list(request, app, model):
    from income.models import IncomeSource

    q = request.GET.get('q', '')
    schedule_f = request.GET.get('schedule_f', '')

    owned_income = Q(incomes__farm_profile__in=accessible_farms(request.user))
    queryset = IncomeSource.objects.annotate(
        income_count=Count('incomes', filter=owned_income),
        total_amount=Coalesce(Sum('incomes__amount', filter=owned_income), 0, output_field=DecimalField()),
        pending_count=Count('incomes', filter=owned_income & Q(incomes__reconciliation_status='PENDING')),
    ).order_by('name')

    if q:
        queryset = queryset.filter(
            Q(name__icontains=q) | Q(code__icontains=q) | Q(description__icontains=q)
        )
    if schedule_f:
        queryset = queryset.filter(schedule_f_line=schedule_f)

    stats = {
        'total_count': queryset.count(),
        'total_records': queryset.aggregate(
            t=Coalesce(Sum('income_count'), 0, output_field=DecimalField())
        )['t'],
        'total_amount': queryset.aggregate(
            t=Coalesce(Sum('total_amount'), 0, output_field=DecimalField())
        )['t'],
        'pending': queryset.aggregate(
            t=Coalesce(Sum('pending_count'), 0, output_field=DecimalField())
        )['t'],
    }

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': 'Income Sources',
        'income_sources': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'schedule_f': schedule_f},
        'schedule_f_lines': IncomeSource.SCHEDULE_F_LINES,
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'income/income_source_list.html', context)


def _crop_list(request, app, model):
    from crops.models import Crop, HarvestRecord

    q = request.GET.get('q', '')
    category = request.GET.get('category', '')
    perennial = request.GET.get('perennial', '')

    queryset = Crop.objects.annotate(
        season_count=Count('seasons', distinct=True),
        harvest_count=Count('seasons__harvests', distinct=True),
    ).order_by('name')

    if q:
        queryset = queryset.filter(
            Q(name__icontains=q) | Q(scientific_name__icontains=q) | Q(description__icontains=q)
        )
    if category:
        queryset = queryset.filter(category=category)
    if perennial == '1':
        queryset = queryset.filter(is_perennial=True)
    elif perennial == '0':
        queryset = queryset.filter(is_perennial=False)

    total_qty = HarvestRecord.objects.aggregate(
        t=Coalesce(Sum('quantity'), 0, output_field=DecimalField()))['t']
    stats = {
        'total_count': queryset.count(),
        'total_qty': str(total_qty),
        'perennial': queryset.filter(is_perennial=True).count(),
        'active_seasons': queryset.aggregate(t=Coalesce(Sum('season_count'), 0, output_field=DecimalField()))['t'],
    }

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': 'Crops',
        'crops': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'category': category, 'perennial': perennial},
        'categories': Crop.CropCategory.choices,
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'crops/crop_list.html', context)


def _crop_season_list(request, app, model):
    from crops.models import CropSeason, Crop
    from fields.models import Field

    q = request.GET.get('q', '')
    crop = request.GET.get('crop', '')
    field = request.GET.get('field', '')
    status = request.GET.get('status', '')

    queryset = scoped_queryset(CropSeason, request.user, CropSeason.objects.select_related('crop', 'field')).annotate(
        harvest_count=Count('harvests', distinct=True),
        total_harvested=Coalesce(Sum('harvests__quantity'), 0, output_field=DecimalField()),
    ).order_by('-planting_date')

    if q:
        queryset = queryset.filter(
            Q(crop__name__icontains=q) | Q(field__name__icontains=q) | Q(season_label__icontains=q)
        )
    if crop:
        queryset = queryset.filter(crop_id=crop)
    if field:
        queryset = queryset.filter(field_id=field)
    if status:
        queryset = queryset.filter(status=status)

    stats = {
        'total_count': queryset.count(),
        'growing': queryset.filter(status='GROWING').count(),
        'harvested': queryset.filter(status='HARVESTED').count(),
        'planned': queryset.filter(status='PLANNED').count(),
    }

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': 'Crop Seasons',
        'seasons': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'crop': crop, 'field': field, 'status': status},
        'crops': Crop.objects.order_by('name'),
        'fields': Field.objects.order_by('name'),
        'statuses': CropSeason.Status.choices,
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'crops/crop_season_list.html', context)


def _harvest_list(request, app, model):
    from crops.models import HarvestRecord, Crop
    from fields.models import Field

    q = request.GET.get('q', '')
    crop = request.GET.get('crop', '')
    field = request.GET.get('field', '')
    harvest_type = request.GET.get('harvest_type', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    queryset = scoped_queryset(HarvestRecord, request.user, HarvestRecord.objects.select_related(
        'crop_season__crop', 'crop_season__field'
    ).order_by('-harvest_date'))

    if q:
        queryset = queryset.filter(
            Q(crop_season__crop__name__icontains=q) | Q(notes__icontains=q)
        )
    if crop:
        queryset = queryset.filter(crop_season__crop_id=crop)
    if field:
        queryset = queryset.filter(crop_season__field_id=field)
    if harvest_type:
        queryset = queryset.filter(harvest_type=harvest_type)
    if date_from and date_to:
        queryset = queryset.filter(harvest_date__range=[date_from, date_to])
    elif date_from:
        queryset = queryset.filter(harvest_date__gte=date_from)
    elif date_to:
        queryset = queryset.filter(harvest_date__lte=date_to)

    stats = {
        'total_count': queryset.count(),
        'total_qty': str(queryset.aggregate(t=Coalesce(Sum('quantity'), 0, output_field=DecimalField()))['t']),
        'total_revenue': queryset.aggregate(
            t=Coalesce(Sum(F('sales__quantity') * F('sales__unit_price'), filter=Q(sales__is_waste=False)), 0, output_field=DecimalField()))['t'],
        'waste_qty': str(queryset.aggregate(t=Coalesce(Sum('waste_quantity'), 0, output_field=DecimalField()))['t']),
    }

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': 'Harvests',
        'harvests': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {
            'crop': crop,
            'field': field,
            'harvest_type': harvest_type,
            'date_from': date_from,
            'date_to': date_to,
        },
        'crops': Crop.objects.order_by('name'),
        'fields': Field.objects.order_by('name'),
        'harvest_types': HarvestRecord.HarvestType.choices,
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'crops/harvest_list.html', context)


def _processing_list(request, app, model):
    from crops.models import ProcessingStage, Crop, HarvestRecord

    q = request.GET.get('q', '')
    crop = request.GET.get('crop', '')
    harvest = request.GET.get('harvest', '')

    queryset = scoped_queryset(ProcessingStage, request.user, ProcessingStage.objects.select_related(
        'harvest__crop_season__crop', 'harvest__crop_season__field'
    ).order_by('harvest', 'sequence'))

    if q:
        queryset = queryset.filter(
            Q(name__icontains=q) | Q(harvest__crop_season__crop__name__icontains=q)
        )
    if crop:
        queryset = queryset.filter(harvest__crop_season__crop_id=crop)
    if harvest:
        queryset = queryset.filter(harvest_id=harvest)

    stats = {
        'total_count': queryset.count(),
        'total_cost': queryset.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t'],
        'total_added': queryset.aggregate(t=Coalesce(Sum('added_value'), 0, output_field=DecimalField()))['t'],
        'total_waste': str(queryset.aggregate(t=Coalesce(Sum('waste_quantity'), 0, output_field=DecimalField()))['t']),
    }

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': 'Processing Stages',
        'stages': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'crop': crop, 'harvest': harvest},
        'crops': Crop.objects.order_by('name'),
        'harvests': HarvestRecord.objects.select_related('crop_season__crop').order_by('-harvest_date')[:50],
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'crops/processing_list.html', context)


def _budget_list(request, app, model):
    from budgets.models import Budget

    q = request.GET.get('q', '')
    status = request.GET.get('status', '')
    farm = request.GET.get('farm', '')

    queryset = scoped_queryset(Budget, request.user, Budget.objects.select_related('farm_profile')).annotate(
        item_count=Count('items', distinct=True),
    ).order_by('-id')

    if q:
        queryset = queryset.filter(
            Q(name__icontains=q) | Q(farm_profile__farm_name__icontains=q)
        )
    if status:
        queryset = queryset.filter(status=status)
    if farm:
        queryset = queryset.filter(farm_profile_id=farm)

    budgets = list(queryset)
    for b in budgets:
        b.planned = b.total_planned
        b.spent = b.total_spent
        b.remaining_amt = b.planned - b.spent
        b.income_planned = b.total_income_planned
        b.funding_gap = b.income_planned - b.planned
        b.can_edit = accessible_farms(request.user, write_model='budgets.budget').filter(pk=b.farm_profile_id).exists() or request.user.is_superuser
        b.burn_pct = round((b.spent / b.planned) * 100, 1) if b.planned else 0

    if request.GET.get('export'):
        sections = [
            {'title': 'Budgets',
             'headers': ['Budget', 'Farm', 'Status', 'Start', 'End', 'Items',
                         'Planned Income', 'Expenditure', 'Funding Balance', 'Spent', 'Remaining', 'Burn %'],
             'rows': [[b.name, str(b.farm_profile) if b.farm_profile else '-',
                       b.get_status_display(),
                       str(b.start_date or '-'), str(b.end_date or '-'),
                       b.item_count, _M(b.income_planned), _M(b.planned), _M(b.funding_gap), _M(b.spent),
                       _M(b.remaining_amt), b.burn_pct] for b in budgets]},
        ]
        sections[0]['rows'].append([
            'Totals', '', '', '', '', sum(b.item_count for b in budgets),
            _M(sum((b.income_planned for b in budgets), Decimal('0'))),
            _M(sum((b.planned for b in budgets), Decimal('0'))),
            _M(sum((b.funding_gap for b in budgets), Decimal('0'))),
            _M(sum((b.spent for b in budgets), Decimal('0'))),
            _M(sum((b.remaining_amt for b in budgets), Decimal('0'))), ''])
        return _export_report(request, request.GET['export'], 'Budgets',
                              'budgets', sections, '', '')

    stats = {
        'total_count': len(budgets),
        'active_count': sum(1 for b in budgets if b.status == 'ACTIVE'),
        'total_planned': sum((b.planned for b in budgets), Decimal('0')),
        'total_spent': sum((b.spent for b in budgets), Decimal('0')),
    }

    paginator = Paginator(budgets, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    from accounts.models import FarmProfile
    context = {
        'app': app,
        'model': model,
        'model_name': 'Budgets',
        'budgets': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'status': status, 'farm': farm},
        'statuses': Budget.Status.choices,
        'farms': accessible_farms(request.user).order_by('farm_name'),
        'query_string': query_string,
        'export_query': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
        'can_create_budget': request.user.is_superuser or accessible_farms(request.user, write_model='budgets.budget').exists(),
    }
    return render(request, 'budgets/budget_list.html', context)


def _budget_item_list(request, app, model):
    from budgets.models import Budget, BudgetItem
    from crops.models import CropSeason
    from expenses.models import Category

    q = request.GET.get('q', '')
    budget = request.GET.get('budget', '')
    category = request.GET.get('category', '')
    crop_season = request.GET.get('crop_season', '')

    queryset = scoped_queryset(BudgetItem, request.user, BudgetItem.objects.select_related(
        'budget', 'category', 'crop_season'
    )).annotate(
        spent=Coalesce(Sum('expenses__amount'), 0, output_field=DecimalField()),
    ).order_by('budget__name', 'category__name')

    if q:
        queryset = queryset.filter(
            Q(budget__name__icontains=q) | Q(category__name__icontains=q)
            | Q(category__code__icontains=q) | Q(notes__icontains=q)
        )
    if budget:
        queryset = queryset.filter(budget_id=budget)
    if category:
        queryset = queryset.filter(category_id=category)
    if crop_season:
        queryset = queryset.filter(crop_season_id=crop_season)

    stats = {
        'total_count': queryset.count(),
        'total_planned': queryset.aggregate(
            t=Coalesce(Sum('planned_amount'), 0, output_field=DecimalField()))['t'],
        'total_spent': queryset.aggregate(
            t=Coalesce(Sum('expenses__amount'), 0, output_field=DecimalField()))['t'],
        'over_count': sum(1 for i in queryset if i.spent > i.planned_amount),
    }

    if request.GET.get('export'):
        rows = [[
            i.budget.name, i.category.code if i.category else '-',
            i.category.name if i.category else '-',
            str(i.crop_season) if i.crop_season else '-',
            _M(i.planned_amount), _M(i.fund_amount), _M(i.spent),
            _M(i.planned_amount - i.spent),
            'Yes' if i.spent > i.planned_amount else 'No',
        ] for i in queryset]
        sections = [{
            'title': 'Budget Items',
            'headers': ['Budget', 'Code', 'Category', 'Crop Season', 'Planned',
                        'Fund Pool', 'Spent', 'Remaining', 'Over Budget'],
            'rows': rows,
        }]
        return _export_report(request, request.GET['export'], 'Budget Items',
                              'budget_items', sections, '', '')

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': 'Budget Items',
        'items': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {
            'budget': budget,
            'category': category,
            'crop_season': crop_season,
        },
        'budgets': scoped_queryset(Budget, request.user).order_by('name'),
        'categories': Category.objects.order_by('name'),
        'seasons': scoped_queryset(CropSeason, request.user).select_related('crop', 'field').order_by('-planting_date')[:50],
        'query_string': query_string,
        'export_query': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'budgets/budget_item_list.html', context)


def _budget_code_list(request, app, model):
    from budgets.models import BudgetItem
    from expenses.models import Category, Expense

    q = request.GET.get('q', '')
    schedule_f = request.GET.get('schedule_f', '')

    farms = accessible_farms(request.user)
    planned_sq = (BudgetItem.objects.filter(category=OuterRef('pk'), budget__farm_profile__in=farms)
                  .values('category')
                  .annotate(s=Sum('planned_amount'))
                  .values('s'))
    spent_sq = (Expense.objects.filter(budget_item__category=OuterRef('pk'), farm_profile__in=farms)
                .values('budget_item__category')
                .annotate(s=Sum('amount'))
                .values('s'))

    queryset = Category.objects.select_related('schedule_f_line').annotate(
        item_count=Count('budget_items', filter=Q(budget_items__budget__farm_profile__in=farms), distinct=True),
        subcode_count=Count('subcodes', distinct=True),
        planned=Coalesce(Subquery(planned_sq, output_field=DecimalField()), Decimal('0'), output_field=DecimalField()),
        spent=Coalesce(Subquery(spent_sq, output_field=DecimalField()), Decimal('0'), output_field=DecimalField()),
    ).order_by('name')

    if q:
        queryset = queryset.filter(Q(name__icontains=q) | Q(code__icontains=q))
    if schedule_f:
        queryset = queryset.filter(schedule_f_line=schedule_f)

    stats = {
        'total_count': queryset.count(),
        'with_items': queryset.filter(item_count__gt=0).count(),
        'total_planned': queryset.aggregate(t=Coalesce(Sum('planned'), 0, output_field=DecimalField()))['t'],
        'total_spent': queryset.aggregate(t=Coalesce(Sum('spent'), 0, output_field=DecimalField()))['t'],
    }

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': 'Budget Codes',
        'codes': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'schedule_f': schedule_f},
        'schedule_f_lines': Category._meta.get_field('schedule_f_line').related_model.objects.values_list('code', 'name'),
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'budgets/budget_code_list.html', context)


def _expense_list(request, app, model):
    from crops.models import CropSeason
    from expenses.models import Expense, Category
    from fields.models import Field

    q = request.GET.get('q', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    category = request.GET.get('category', '')
    payment_method = request.GET.get('payment_method', '')
    crop_season = request.GET.get('crop_season', '')
    field = request.GET.get('field', '')
    status = request.GET.get('status', '')

    queryset = scoped_queryset(Expense, request.user, Expense.objects.select_related(
        'category', 'crop_season', 'field', 'farm_profile'
    ).order_by('-date', '-id'))

    if q:
        queryset = queryset.filter(
            Q(vendor__icontains=q) | Q(notes__icontains=q)
            | Q(category__name__icontains=q) | Q(category__code__icontains=q)
        )
    if date_from and date_to:
        queryset = queryset.filter(date__range=[date_from, date_to])
    elif date_from:
        queryset = queryset.filter(date__gte=date_from)
    elif date_to:
        queryset = queryset.filter(date__lte=date_to)
    if category:
        queryset = queryset.filter(category_id=category)
    if payment_method:
        queryset = queryset.filter(payment_method=payment_method)
    if crop_season:
        queryset = queryset.filter(crop_season_id=crop_season)
    if field:
        queryset = queryset.filter(field_id=field)
    if status:
        queryset = queryset.filter(reconciliation_status=status)

    stats = {
        'total_count': queryset.count(),
        'total_amount': queryset.aggregate(
            t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t'],
        'pending_count': queryset.filter(reconciliation_status='PENDING').count(),
        'reconciled_count': queryset.filter(reconciliation_status='RECONCILED').count(),
    }

    paginator = Paginator(queryset, 25)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    query_params = request.GET.copy()
    query_params.pop('page', None)
    query_string = query_params.urlencode()

    context = {
        'app': app,
        'model': model,
        'model_name': 'Expenses',
        'expenses': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {
            'date_from': date_from,
            'date_to': date_to,
            'category': category,
            'payment_method': payment_method,
            'crop_season': crop_season,
            'field': field,
            'status': status,
        },
        'categories': Category.objects.order_by('name'),
        'payment_methods': Expense.PaymentMethod.choices,
        'statuses': Expense.ReconciliationStatus.choices,
        'seasons': CropSeason.objects.select_related('crop', 'field').order_by('-planting_date')[:50],
        'fields': Field.objects.order_by('name'),
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'expenses/expense_list.html', context)


def _field_list(request, app, model):
    from fields.models import Field

    q = request.GET.get('q', '')
    soil = request.GET.get('soil', '')
    status = request.GET.get('status', '')

    queryset = scoped_queryset(Field, request.user, Field.objects.annotate(
        season_count=Count('seasons', distinct=True),
        parcel_count=Count('parcels', distinct=True),
    )).order_by('name')

    if q:
        queryset = queryset.filter(name__icontains=q)
    if soil:
        queryset = queryset.filter(soil_type=soil)
    if status == '1':
        queryset = queryset.filter(is_active=True)
    elif status == '0':
        queryset = queryset.filter(is_active=False)

    stats = {
        'total_count': queryset.count(),
        'total_acreage': queryset.aggregate(
            t=Coalesce(Sum('acreage'), 0, output_field=DecimalField()))['t'],
        'active': queryset.filter(is_active=True).count(),
        'in_use': queryset.filter(season_count__gt=0).count(),
    }

    paginator = Paginator(queryset, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    query_params = request.GET.copy()
    query_params.pop('page', None)

    context = {
        'app': app,
        'model': model,
        'model_name': 'Fields',
        'fields': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'soil': soil, 'status': status},
        'soil_types': Field.SoilType.choices,
        'query_string': query_params.urlencode(),
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'fields/field_list.html', context)


def _equipment_list(request, app, model):
    from equipment.models import Equipment

    q = request.GET.get('q', '')
    eq_type = request.GET.get('type', '')
    status = request.GET.get('status', '')

    queryset = scoped_queryset(Equipment, request.user, Equipment.objects.annotate(
        maintenance_total=Coalesce(Sum('maintenance_logs__cost'), 0, output_field=DecimalField()),
        usage_hours=Coalesce(Sum('usage_records__hours'), 0, output_field=DecimalField()),
    )).order_by('name')

    if q:
        queryset = queryset.filter(name__icontains=q)
    if eq_type:
        queryset = queryset.filter(equipment_type=eq_type)
    if status == '1':
        queryset = queryset.filter(is_active=True)
    elif status == '0':
        queryset = queryset.filter(is_active=False)

    stats = {
        'total_count': queryset.count(),
        'active': queryset.filter(is_active=True).count(),
        'asset_value': queryset.aggregate(
            t=Coalesce(Sum('current_value'), 0, output_field=DecimalField()))['t'],
        'maintenance_spend': queryset.aggregate(
            t=Coalesce(Sum('maintenance_logs__cost'), 0, output_field=DecimalField()))['t'],
        'hours': queryset.aggregate(
            t=Coalesce(Sum('hours_used'), 0, output_field=DecimalField()))['t'],
    }

    paginator = Paginator(queryset, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    query_params = request.GET.copy()
    query_params.pop('page', None)

    context = {
        'app': app,
        'model': model,
        'model_name': 'Equipment',
        'equipment': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'type': eq_type, 'status': status},
        'equipment_types': Equipment.EquipmentType.choices,
        'query_string': query_params.urlencode(),
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'equipment/equipment_list.html', context)


def _inventory_list(request, app, model):
    from inventory.models import InventoryItem

    q = request.GET.get('q', '')
    stock = request.GET.get('stock', '')

    queryset = scoped_queryset(InventoryItem, request.user, InventoryItem.objects.annotate(
        stock_value=Coalesce(F('quantity_on_hand') * F('cost_per_unit'), 0, output_field=DecimalField()),
    )).order_by('name')

    if q:
        queryset = queryset.filter(
            Q(name__icontains=q) | Q(supplier__icontains=q))
    if stock == 'low':
        queryset = queryset.filter(quantity_on_hand__lte=F('reorder_threshold'))
    elif stock == 'ok':
        queryset = queryset.filter(quantity_on_hand__gt=F('reorder_threshold'))

    stats = {
        'total_count': queryset.count(),
        'low_stock': queryset.filter(
            quantity_on_hand__lte=F('reorder_threshold')).count(),
        'stock_value': queryset.aggregate(
            t=Coalesce(Sum(F('quantity_on_hand') * F('cost_per_unit')), 0,
                       output_field=DecimalField()))['t'],
    }

    paginator = Paginator(queryset, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    query_params = request.GET.copy()
    query_params.pop('page', None)

    context = {
        'app': app,
        'model': model,
        'model_name': 'Inventory',
        'items': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'stock': stock},
        'query_string': query_params.urlencode(),
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'inventory/inventory_list.html', context)


def _worker_list(request, app, model):
    from labor.models import AttendanceRecord, Worker

    q = request.GET.get('q', '')
    status = request.GET.get('status', '')

    hours_sq = (AttendanceRecord.objects.filter(worker=OuterRef('pk'))
                .values('worker')
                .annotate(t=Sum('hours')).values('t'))
    queryset = scoped_queryset(Worker, request.user, Worker.objects.annotate(
        task_count=Count('tasks', distinct=True),
        hours_total=Coalesce(Subquery(hours_sq, output_field=DecimalField()), Value(0),
                             output_field=DecimalField()),
    )).order_by('name')

    if q:
        queryset = queryset.filter(
            Q(name__icontains=q) | Q(phone__icontains=q) | Q(tax_id__icontains=q))
    if status == '1':
        queryset = queryset.filter(is_active=True)
    elif status == '0':
        queryset = queryset.filter(is_active=False)

    month_start = datetime.date.today().replace(day=1)
    month_hours = AttendanceRecord.objects.filter(
        worker__farm_profile__in=accessible_farms(request.user),
        date__gte=month_start,
    ).aggregate(t=Coalesce(Sum('hours'), 0, output_field=DecimalField()))['t']
    rates = [w for w in queryset if w.hourly_rate]
    stats = {
        'total_count': queryset.count(),
        'active': queryset.filter(is_active=True).count(),
        'avg_rate': (sum((w.hourly_rate for w in rates), Decimal('0')) / len(rates)) if rates else Decimal('0'),
        'month_hours': month_hours,
    }

    paginator = Paginator(queryset, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    query_params = request.GET.copy()
    query_params.pop('page', None)

    context = {
        'app': app,
        'model': model,
        'model_name': 'Workers',
        'workers': page_obj,
        'page_obj': page_obj,
        'q': q,
        'filters': {'status': status},
        'query_string': query_params.urlencode(),
        'urls': _crud_urls(app, model),
        'stats': stats,
    }
    return render(request, 'labor/worker_list.html', context)


@login_required
def model_list(request, app, model):
    if (app, model) == ('fields', 'field'):
        return _field_list(request, app, model)
    if (app, model) == ('equipment', 'equipment'):
        return _equipment_list(request, app, model)
    if (app, model) == ('inventory', 'inventoryitem'):
        return _inventory_list(request, app, model)
    if (app, model) == ('labor', 'worker'):
        return _worker_list(request, app, model)
    if (app, model) == ('crops', 'sale'):
        return _sale_list(request, app, model)
    if (app, model) == ('income', 'income'):
        return _income_list(request, app, model)
    if (app, model) == ('income', 'incomesource'):
        return _income_source_list(request, app, model)
    if (app, model) == ('crops', 'crop'):
        return _crop_list(request, app, model)
    if (app, model) == ('crops', 'cropseason'):
        return _crop_season_list(request, app, model)
    if (app, model) == ('crops', 'harvestrecord'):
        return _harvest_list(request, app, model)
    if (app, model) == ('crops', 'processingstage'):
        return _processing_list(request, app, model)
    if (app, model) == ('budgets', 'budget'):
        return _budget_list(request, app, model)
    if (app, model) == ('budgets', 'budgetitem'):
        return _budget_item_list(request, app, model)
    if (app, model) == ('expenses', 'category'):
        return _budget_code_list(request, app, model)
    if (app, model) == ('expenses', 'expense'):
        return _expense_list(request, app, model)
    model_cls = _get_model(app, model)
    q = request.GET.get('q', '')

    if (app, model) in CUSTOM_LIST_DISPLAY:
        if (app, model) == ('income', 'income'):
            queryset = model_cls.objects.select_related('source', 'farm_profile').order_by('-date')
        elif (app, model) == ('income', 'incomesource'):
            queryset = model_cls.objects.all().order_by('name')
        elif (app, model) == ('accounts', 'farmmembership'):
            queryset = model_cls.objects.select_related('farm_profile', 'user').order_by('-pk')
        elif (app, model) == ('accounts', 'farmprofile'):
            queryset = model_cls.objects.select_related('default_currency').order_by('farm_name')
        elif (app, model) == ('expenses', 'scheduleline'):
            queryset = model_cls.objects.order_by('name', 'code')
        elif (app, model) == ('expenses', 'budgetsubcode'):
            queryset = model_cls.objects.select_related('category').order_by('category__name', 'code', 'name')
        elif (app, model) == ('crops', 'customer'):
            queryset = model_cls.objects.select_related('farm_profile').order_by('name')
        else:
            queryset = model_cls.objects.select_related('crop', 'field').order_by('-planting_date')
        display_fields = [model_cls._meta.get_field(name) for name in CUSTOM_LIST_DISPLAY[(app, model)]]
    else:
        queryset = model_cls.objects.all().order_by('-id')
        display_fields = _list_display(model_cls)

    queryset = _search_filter(scoped_queryset(model_cls, request.user, queryset), q)

    paginator = Paginator(queryset, 10)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    rows = [
        {
            'obj': obj,
            'cells': [_table_field(obj, f) for f in display_fields],
            'pk': obj.pk,
        }
        for obj in page_obj
    ]

    context = {
        'app': app,
        'model': model,
        'model_name': model_cls._meta.verbose_name_plural or model_cls._meta.verbose_name,
        'display_field_names': [f.verbose_name for f in display_fields],
        'rows': rows,
        'page_obj': page_obj,
        'q': q,
        'urls': _crud_urls(app, model),
        'stats': _compute_stats(app, model) if request.user.is_superuser else [],
        'is_modal': request.headers.get('X-Requested-With') == 'XMLHttpRequest' and request.GET.get('modal') == '1',
        'detail_url_name': DETAIL_URL_NAMES.get((app, model)),
    }
    return render(request, 'dashboard/list.html', context)


def _redirect_target(request, app, model):
    """Return the ?next= URL when it is a safe local path, else the model list."""
    nxt = request.GET.get('next', '')
    if nxt.startswith('/') and not nxt.startswith('//'):
        return nxt
    return _crud_urls(app, model)['list']


def _scope_form(form, user):
    for name, field in form.fields.items():
        if not hasattr(field, 'queryset') or field.queryset is None:
            continue
        related_model = field.queryset.model
        if form._meta.model._meta.label_lower == 'accounts.farmmembership' and name == 'user':
            continue
        if related_model._meta.label_lower == 'accounts.farmprofile':
            field.queryset = accessible_farms(user, write_model=form._meta.model._meta.label_lower)
        else:
            field.queryset = scoped_queryset(related_model, user, field.queryset)
    return form


@login_required
def model_create(request, app, model):
    if (app, model) == ('budgets', 'budget'):
        from budgets.views import budget_plan_edit_view
        return budget_plan_edit_view(request)
    model_cls = _get_model(app, model)
    label = model_cls._meta.label_lower
    if not request.user.is_superuser and (
            label in GLOBAL_CATALOGS or label == 'accounts.user'
            or (label != 'accounts.farmprofile'
                and not accessible_farms(request.user, write_model=label).exists())):
        raise PermissionDenied
    form_cls = _get_form_class(model_cls)
    if request.method == 'POST':
        form = _scope_form(form_cls(request.POST, request.FILES), request.user)
        if form.is_valid():
            try:
                if label == 'accounts.farmprofile' and not form.instance.pk:
                    if FarmProfile.objects.filter(user=request.user).exists():
                        raise ValidationError(
                            'You already have a farm profile. Edit it from the list instead.')
                    form.instance.user = request.user
                form.save()
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                target = _redirect_target(request, app, model)
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'ok': True, 'redirect': target})
                return HttpResponseRedirect(target)
    else:
        initial = {k: v for k, v in request.GET.items() if k != 'next'}
        form = _scope_form(form_cls(initial=initial), request.user)

    context = {
        'app': app,
        'model': model,
        'model_name': model_cls._meta.verbose_name,
        'form': form,
        'urls': _crud_urls(app, model),
        'action': 'Create',
    }
    return render(request, 'dashboard/form.html', context)


@login_required
def model_update(request, app, model, pk):
    if (app, model) == ('budgets', 'budget'):
        from budgets.views import budget_plan_edit_view
        return budget_plan_edit_view(request, pk=pk)
    model_cls = _get_model(app, model)
    obj = get_object_or_404(scoped_queryset(model_cls, request.user, write=True), pk=pk)
    is_waste = request.GET.get('waste') == '1'
    form_cls = _get_form_class(model_cls, waste=is_waste)
    if request.method == 'POST':
        form = _scope_form(form_cls(request.POST, request.FILES, instance=obj), request.user)
        if form.is_valid():
            try:
                form.save()
            except ValidationError as exc:
                form.add_error(None, exc)
            else:
                target = _redirect_target(request, app, model)
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'ok': True, 'redirect': target})
                return HttpResponseRedirect(target)
    else:
        form = _scope_form(form_cls(instance=obj), request.user)

    context = {
        'app': app,
        'model': model,
        'model_name': 'Waste' if is_waste else model_cls._meta.verbose_name,
        'form': form,
        'urls': _crud_urls(app, model, pk=pk),
        'action': 'Edit',
        'obj': obj,
        'waste': is_waste,
    }
    return render(request, 'dashboard/form.html', context)


@login_required
def model_delete(request, app, model, pk):
    model_cls = _get_model(app, model)
    obj = get_object_or_404(scoped_queryset(model_cls, request.user, write=True), pk=pk)
    context = {
        'app': app,
        'model': model,
        'model_name': model_cls._meta.verbose_name,
        'obj': obj,
        'urls': _crud_urls(app, model, pk=pk),
    }
    if request.method == 'POST':
        try:
            obj.delete()
        except ProtectedError as exc:
            counts = defaultdict(int)
            for related in exc.protected_objects:
                counts[related._meta] += 1
            context['protected_counts'] = [
                (str(meta.verbose_name if count == 1 else meta.verbose_name_plural), count)
                for meta, count in sorted(counts.items(), key=lambda item: item[0].label)
            ]
            return render(request, 'dashboard/confirm_delete.html', context, status=409)
        target = _redirect_target(request, app, model)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'ok': True, 'redirect': target})
        return HttpResponseRedirect(target)
    return render(request, 'dashboard/confirm_delete.html', context)


@login_required
def farm_settings(request):
    """Unified farm settings page: profile, team, users and currencies."""
    from accounts.models import Currency, FarmMembership, User

    farms = accessible_farms(request.user).select_related(
        'default_currency', 'user').order_by('farm_name')
    memberships = scoped_queryset(FarmMembership, request.user).select_related(
        'farm_profile', 'user').order_by('farm_profile__farm_name', 'user__username')
    member_ids = memberships.values_list('user_id', flat=True)
    owner_ids = farms.values_list('user_id', flat=True)
    if request.user.is_superuser:
        users = User.objects.all().order_by('username')
    else:
        users = User.objects.filter(
            Q(pk__in=member_ids) | Q(pk__in=owner_ids)).distinct().order_by('username')

    context = {
        'farms': farms,
        'memberships': memberships,
        'users': users,
        'currencies': Currency.objects.order_by('code'),
        'can_edit_farms': request.user.is_superuser or accessible_farms(
            request.user, write_model='accounts.farmprofile').exists(),
        'can_manage_team': request.user.is_superuser or accessible_farms(
            request.user, write_model='accounts.farmmembership').exists(),
        'can_manage_users': request.user.is_superuser,
        'can_manage_currencies': request.user.is_superuser,
    }
    return render(request, 'dashboard/farm_settings.html', context)


DONUT_PALETTE = ['#1b4332', '#2d6a4f', '#40916c', '#52b788', '#74c69d', '#95d5b2', '#b7e4c7']


def _report_range(request):
    """Parse ?date_from=&date_to= and return (from, to, queryset filter fn)."""
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    def in_range(qs, field):
        if date_from and date_to:
            return qs.filter(**{f'{field}__range': [date_from, date_to]})
        if date_from:
            return qs.filter(**{f'{field}__gte': date_from})
        if date_to:
            return qs.filter(**{f'{field}__lte': date_to})
        return qs

    return date_from, date_to, in_range


def _donut_segments(rows, limit=6):
    """Top-N rows + 'Other' bucket with SVG donut geometry. Rows need 'total'.

    Adds 'color', 'share', 'dash'/'dashoffset' for the ring, plus label
    positions ('px/py' dot, 'lx/ly' text, 'ex/ey' leader end, 'anchor')
    inside a 80x42 viewBox whose ring is centered at (40, 21).
    """
    top = list(rows[:limit])
    rest = rows[limit:]
    if rest:
        top.append({
            'label': 'Other',
            'total': sum((r['total'] for r in rest), Decimal('0')),
            'n': sum(r.get('n', 0) for r in rest),
        })
    total = sum((r['total'] for r in top), Decimal('0'))
    cumulative = 0.0
    for i, row in enumerate(top):
        share = float(row['total'] / total * 100) if total else 0.0
        row.setdefault('label', '-')
        row['share'] = round(share, 1)
        row['color'] = DONUT_PALETTE[i % len(DONUT_PALETTE)]
        row['dash'] = f'{share:.2f} {100 - share:.2f}'
        row['dashoffset'] = round(25 - cumulative, 2)

        # External label at the segment's mid-angle (0% at 12 o'clock).
        # viewBox is "0 -4 100 50"; ring center (50, 21), label radius 26.
        mid = cumulative + share / 2.0
        rad = math.radians(mid * 3.6)
        sx, sy = math.sin(rad), math.cos(rad)
        row['px'] = round(50 + 18.7 * sx, 2)
        row['py'] = round(21 - 18.7 * sy, 2)
        row['ex'] = round(50 + 23.5 * sx, 2)
        row['ey'] = round(21 - 23.5 * sy, 2)
        row['lx'] = round(50 + 26.5 * sx, 2)
        row['ly'] = round(21 - 26.5 * sy + 0.8, 2)
        row['anchor'] = 'middle' if abs(sx) < 0.35 else ('start' if sx > 0 else 'end')
        row['show_label'] = share >= 4
        cumulative += share
    return top


def _bar_scale(rows, key='total'):
    """Attach 'bar' (0-100) to each row proportional to the max value."""
    max_v = max((r[key] for r in rows), default=Decimal('0'))
    for r in rows:
        r['bar'] = round(float(r[key] / max_v * 100), 1) if max_v else 0
    return rows


def _monthly_chart(bucket_map):
    """{'YYYY-MM': Decimal} -> sorted [{'label', 'value', 'pct'}] scaled to max."""
    rows = [
        {
            'label': datetime.datetime.strptime(k, '%Y-%m').strftime('%b %Y'),
            'value': v,
        }
        for k, v in sorted(bucket_map.items())
    ]
    max_v = max((r['value'] for r in rows), default=Decimal('0'))
    for r in rows:
        r['pct'] = float(r['value'] / max_v * 100) if max_v else 0
    return rows


def _line_chart(rows):
    max_value = max((row['value'] for row in rows), default=Decimal('0'))
    count = len(rows)
    label_step = max(1, math.ceil(count / 12))
    for index, row in enumerate(rows):
        row['x'] = round(40 + 520 * index / (count - 1), 1) if count > 1 else 300
        row['y'] = round(178 - 135 * float(row['value'] / max_value), 1) if max_value else 178
        row['show_label'] = index % label_step == 0 or index == count - 1
    points = ' '.join(f"{row['x']},{row['y']}" for row in rows)
    return {'points': points, 'max': max_value}


class _M:
    """Marks a report-export cell as a money amount (converted to display currency)."""

    def __init__(self, v):
        self.v = v


def _export_letterhead(request, farm=None):
    """Farm identity for the export header: (name, contact line, logo path)."""
    farm = farm or getattr(request.user, 'farm_profile', None)
    if farm is None:
        farm = accessible_farms(request.user).select_related('default_currency').first()
    name = farm.farm_name if farm else 'FarmLedger Pro'
    contact = '  ·  '.join(
        p for p in ((farm.location or ''), (farm.phone or ''), (farm.email or ''))
        if p) if farm else ''
    logo_path = None
    if farm and farm.logo:
        try:
            logo_path = farm.logo.path
        except (ValueError, OSError):
            logo_path = None
    return name, contact, logo_path


def _export_report(request, fmt, title, filename, sections, date_from, date_to, farm=None):
    """Export report sections as CSV, XLSX or PDF.

    `sections` is a list of {'title', 'headers', 'rows'}. Cells may be str,
    numbers, or _M(value) for amounts that need currency conversion.
    """
    from farmledger.context_processors import currency as currency_ctx
    cur = currency_ctx(request)['currency']
    code = getattr(cur, 'code', 'USD')
    symbol = getattr(cur, 'symbol', '$')
    try:
        rate = float(cur.rate) / float(getattr(cur, 'input_rate', None) or cur.rate or 1)
    except (TypeError, ValueError):
        rate = 1.0
    period = f'{date_from or "Start"} to {date_to or "Today"}'
    farm_name, farm_contact, farm_logo = _export_letterhead(request, farm=farm)
    generated = f'{datetime.date.today():%d %b %Y}'

    def num_val(cell):
        is_money = isinstance(cell, _M)
        v = cell.v if is_money else cell
        if isinstance(v, bool) or not isinstance(v, (int, float, Decimal)):
            return v
        if isinstance(v, int) and not is_money:
            return v
        out = float(v) * rate if is_money else float(v)
        return round(out, 2)

    def text_val(cell):
        is_money = isinstance(cell, _M)
        v = cell.v if is_money else cell
        if v is None or v == '':
            return '-'
        if isinstance(v, (int, float, Decimal)):
            f = float(v)
            if is_money:
                return f'{symbol} {f * rate:,.2f}'
            return f'{f:,.2f}'.rstrip('0').rstrip('.')
        return str(v)

    if fmt == 'csv':
        import csv as _csv
        resp = HttpResponse(content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="{filename}.csv"'
        def csv_value(value):
            if isinstance(value, str) and value.strip() != '-' and value.lstrip().startswith(('=', '+', '-', '@')):
                return "'" + value
            return value

        w = _csv.writer(resp)
        w.writerow([csv_value(farm_name)])
        if farm_contact:
            w.writerow([csv_value(farm_contact)])
        w.writerow([csv_value(title)])
        w.writerow([f'Period: {period}', f'Currency: {code}', f'Generated: {generated}'])
        for sec in sections:
            w.writerow([])
            w.writerow([csv_value(sec['title'])])
            w.writerow([csv_value(h) for h in sec['headers']])
            for row in sec['rows']:
                w.writerow([csv_value(num_val(c)) for c in row])
        return resp

    if fmt == 'xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = 'Report'
        ws.sheet_properties.tabColor = '1B4332'
        ws.sheet_view.showGridLines = False

        ncols = max((len(s['headers']) for s in sections), default=2)
        thin = Side(style='thin', color='BFBFBF')
        box = Border(left=thin, right=thin, top=thin, bottom=thin)
        title_fill = PatternFill('solid', fgColor='1B4332')
        sub_fill = PatternFill('solid', fgColor='2D6A4F')
        band_fill = PatternFill('solid', fgColor='595959')
        head_fill = PatternFill('solid', fgColor='404040')
        shade_fill = PatternFill('solid', fgColor='F2F2F2')

        def fill_row(row_i, fill):
            for col in range(1, ncols + 1):
                ws.cell(row=row_i, column=col).fill = fill

        def band_row(text, fill, font, height=None):
            ws.append([text])
            r = ws.max_row
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
            if fill is not None:
                fill_row(r, fill)
            c = ws.cell(row=r, column=1)
            c.data_type = 's'
            c.font = font
            c.alignment = Alignment(horizontal='center', vertical='center')
            if height:
                ws.row_dimensions[r].height = height

        band_row(farm_name.upper(), title_fill,
                 Font(bold=True, size=16, color='FFFFFF'), height=32)
        if farm_contact:
            band_row(farm_contact, sub_fill,
                     Font(italic=True, size=9, color='FFFFFF'), height=15)
        band_row(title, None, Font(bold=True, size=13, color='1B4332'), height=22)
        band_row(
            f'Period: {period}     Currency: {code}     Generated: {generated}',
            shade_fill, Font(size=9, color='595959'), height=15)

        if farm_logo:
            try:
                from openpyxl.drawing.image import Image as _XLImage
                from PIL import Image as _PILImage
                img = _XLImage(farm_logo)
                with _PILImage.open(farm_logo) as pim:
                    w_px, h_px = pim.size
                img.height = 40
                img.width = max(1, int(w_px * 40 / h_px)) if h_px else 40
                ws.add_image(img, 'A1')
            except Exception:
                pass
        ws.append([])

        total_refs = {'income': [], 'expenditure': []}
        for sec in sections:
            band_row(sec['title'].upper(), band_fill,
                     Font(bold=True, size=10, color='FFFFFF'), height=16)

            ws.append([str(h) for h in sec['headers']]
                      + [''] * (ncols - len(sec['headers'])))
            r = ws.max_row
            for col in range(1, ncols + 1):
                c = ws.cell(row=r, column=col)
                c.font = Font(bold=True, size=9, color='FFFFFF')
                c.fill = head_fill
                c.border = box
                c.alignment = Alignment(horizontal='center', vertical='center')

            section_start = ws.max_row + 1
            section_rows = []
            kinds = sec.get('row_types', [])
            for i, row in enumerate(sec['rows']):
                kind = kinds[i] if i < len(kinds) else ''
                cells = [num_val(c) for c in row] + [''] * (ncols - len(row))
                ws.append(cells)
                r = ws.max_row
                section_rows.append((r, kind))
                for col, v in enumerate(cells, start=1):
                    c = ws.cell(row=r, column=col)
                    if isinstance(v, str):
                        c.data_type = 's'
                    c.border = box
                    c.font = Font(size=9, bold=kind in ('head', 'subtotal'),
                                  color='1D4ED8' if kind == 'recorded' else '222222')
                    c.alignment = Alignment(vertical='top', wrap_text=True,
                                            horizontal='right' if isinstance(v, (int, float)) else 'left')
                    if i % 2 or kind in ('head', 'subtotal'):
                        c.fill = shade_fill
                    if kind == 'unconfirmed' and col == sec.get('amount_column', -1) + 1:
                        c.fill = PatternFill('solid', fgColor='FFF2CC')
                    if isinstance(v, (int, float)):
                        c.number_format = '#,##0.00' if isinstance(v, float) else '#,##0'
            if 'amount_column' in sec:
                letter = get_column_letter(sec['amount_column'] + 1)
                subtotal_refs = []
                pending_head, detail_start = None, None
                for row_index, kind in section_rows:
                    if kind in ('head', 'subtotal'):
                        if pending_head is not None:
                            ws[f'{letter}{pending_head}'] = f'=SUM({letter}{detail_start}:{letter}{row_index - 1})' if detail_start < row_index else '=0'
                            subtotal_refs.append(f'{letter}{pending_head}')
                        if kind == 'head':
                            pending_head, detail_start = row_index, row_index + 1
                        else:
                            refs = subtotal_refs or [f'{letter}{r}' for r, k in section_rows if k not in ('head', 'subtotal')]
                            ws[f'{letter}{row_index}'] = '=SUM(' + ','.join(refs) + ')' if refs else '=0'
                            role = sec.get('total_role')
                            if role in total_refs:
                                total_refs[role].append(f'{letter}{row_index}')
                            pending_head = None
            if sec.get('formula_income_total'):
                ws.cell(section_start, 2).value = '=SUM(' + ','.join(total_refs['income']) + ')' if total_refs['income'] else '=0'
            if sec.get('formula_summary'):
                for offset, role in enumerate(('income', 'expenditure')):
                    ws.cell(section_start + offset, 2).value = '=SUM(' + ','.join(total_refs[role]) + ')' if total_refs[role] else '=0'
                ws.cell(section_start + 2, 2).value = f'=B{section_start}-B{section_start + 1}'
            ws.append([])

        for col in range(1, ncols + 1):
            letter = get_column_letter(col)
            width = max((len(str(c.value)) for c in ws[letter] if c.value is not None),
                        default=8)
            ws.column_dimensions[letter].width = min(width + 3, 45)
        ws.freeze_panes = f'A{5 if farm_contact else 4}'
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.orientation = 'landscape'
        ws.page_setup.paperSize = ws.PAPERSIZE_A4
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.print_title_rows = '1:4' if farm_contact else '1:3'
        ws.print_options.horizontalCentered = True
        ws.oddFooter.center.text = 'Page &P of &N'
        resp = HttpResponse(
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{filename}.xlsx"'
        wb.save(resp)
        return resp

    # PDF via reportlab — letterhead layout matching the Excel header
    from io import BytesIO
    from xml.sax.saxutils import escape
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        HRFlowable, Image as RLImage, Paragraph, SimpleDocTemplate,
        Spacer, Table, TableStyle)
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm, title=title)
    styles = getSampleStyleSheet()
    farm_style = ParagraphStyle(
        'FarmName', fontName='Helvetica-Bold', fontSize=19,
        textColor=colors.HexColor('#1b4332'), alignment=1, spaceAfter=2)
    contact_style = ParagraphStyle(
        'FarmContact', fontName='Helvetica', fontSize=8.5,
        textColor=colors.HexColor('#595959'), alignment=1)
    meta_style = ParagraphStyle(
        'ReportMeta', fontName='Helvetica-Oblique', fontSize=8.5,
        textColor=colors.HexColor('#595959'), alignment=1)
    title_style = ParagraphStyle(
        'ReportTitle', fontName='Helvetica-Bold', fontSize=14,
        textColor=colors.HexColor('#1b4332'), alignment=1, spaceBefore=2)

    story = []
    if farm_logo:
        try:
            from PIL import Image as _PILImage
            with _PILImage.open(farm_logo) as pim:
                lw, lh = pim.size
            logo_img = RLImage(farm_logo, height=13 * mm,
                               width=13 * mm * (lw / lh) if lh else 13 * mm)
            logo_img.hAlign = 'CENTER'
            story.append(logo_img)
            story.append(Spacer(1, 1.5 * mm))
        except Exception:
            pass
    story += [
        Paragraph(escape(farm_name.upper()), farm_style),
        Paragraph(escape(farm_contact or 'Farm management & reporting'), contact_style),
        Spacer(1, 2.5 * mm),
        HRFlowable(width='100%', thickness=1.1, color=colors.HexColor('#1b4332')),
        Spacer(1, 3 * mm),
        Paragraph(escape(title), title_style),
        Paragraph(
            f'Period: {period} &nbsp;·&nbsp; Currency: {code} &nbsp;·&nbsp; Generated: {generated}',
            meta_style),
        Spacer(1, 5 * mm),
    ]
    cell_style = ParagraphStyle('ExportCell', fontName='Helvetica', fontSize=7.5, leading=9)
    header_style = ParagraphStyle('ExportHeader', parent=cell_style, textColor=colors.white, fontName='Helvetica-Bold')
    for sec in sections:
        story.append(Paragraph(escape(sec['title']), styles['Heading3']))
        data = [[Paragraph(escape(str(h)), header_style) for h in sec['headers']]]
        kinds = sec.get('row_types', [])
        for index, row in enumerate(sec['rows']):
            kind = kinds[index] if index < len(kinds) else ''
            row_style = ParagraphStyle(
                f'ExportRow{index}', parent=cell_style,
                fontName='Helvetica-Bold' if kind in ('head', 'subtotal') else 'Helvetica',
                textColor=colors.HexColor('#1d4ed8') if kind == 'recorded' else colors.black)
            data.append([Paragraph(escape(text_val(c)), row_style) for c in row])
        weights = [0.12, 0.13, 0.045, 0.14, 0.05, 0.18, 0.13, 0.205] if 'amount_column' in sec else [1 / len(sec['headers'])] * len(sec['headers'])
        t = Table(data, colWidths=[doc.width * weight for weight in weights], repeatRows=1)
        for index, kind in enumerate(kinds, start=1):
            if kind in ('head', 'subtotal'):
                t.setStyle(TableStyle([('BACKGROUND', (0, index), (-1, index), colors.HexColor('#e1e8e3'))]))
            if kind == 'unconfirmed':
                col = sec.get('amount_column', 0)
                t.setStyle(TableStyle([('BACKGROUND', (col, index), (col, index), colors.HexColor('#fff2cc'))]))
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1b4332')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 7.5),
            ('GRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#cbd5e1')),
            ('VALIGN', (0, 1), (-1, -1), 'TOP'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))
        story.append(t)
        story.append(Spacer(1, 6 * mm))
    doc.build(story)
    resp = HttpResponse(buf.getvalue(), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}.pdf"'
    return resp


@login_required
def report_workspace(request, default_section='overview'):
    report_views = {
        'overview': ('Overview', reports),
        'sales': ('Sales', report_sales),
        'production': ('Production', report_production),
        'livestock': ('Livestock', report_livestock),
        'operations': ('Operations', report_operations),
    }
    active = request.GET.get('section', default_section)
    if active not in report_views:
        active = 'overview'
    if request.GET.get('export'):
        return report_views[active][1](request)
    panels = {}
    active_context = {}
    for key in [active, *[name for name in report_views if name != active]]:
        label, view = report_views[key]
        response = view(request)
        context = {
            **response.context_data,
            'report_base_template': 'reports/section_base.html',
            'report_workspace': True, 'report_section': key, 'active_report': active,
        }
        if key == active:
            active_context = response.context_data.copy()
        panels[key] = {'key': key, 'label': label,
                       'html': render_to_string(response.template_name, context, request=request)}
    return TemplateResponse(request, 'reports/workspace.html', {
        **active_context, 'active_report': active,
        'report_panels': [panels[key] for key in report_views],
    })


@login_required
def reports(request):
    from budgets.models import Budget
    from crops.models import CropSeason, HarvestRecord, Sale
    from equipment.models import Equipment
    from expenses.models import Expense
    from fields.models import Field
    from income.models import Income
    from labor.models import Task

    date_from, date_to, _in_range = _report_range(request)

    expenses = _in_range(
        scoped_queryset(Expense, request.user, Expense.objects.select_related('category')),
        'date')
    incomes = _in_range(
        scoped_queryset(Income, request.user, Income.objects.select_related('source')),
        'date')
    sales = _in_range(
        scoped_queryset(Sale, request.user,
                        Sale.objects.select_related('harvest__crop_season__crop')),
        'sale_date',
    ).filter(is_waste=False)

    total_expenses = expenses.aggregate(
        t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
    total_income = incomes.aggregate(
        t=Coalesce(Sum('amount'), 0, output_field=DecimalField()))['t']
    sales_revenue = sales.aggregate(
        t=Coalesce(Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()))['t']
    net_profit = total_income - total_expenses

    expense_by_category = list(
        expenses.values('category__name', 'category__code')
        .annotate(total=Sum('amount'), n=Count('id'))
        .order_by('-total')
    )
    for row in expense_by_category:
        row['label'] = row['category__name'] or 'Uncategorized'
    donut_rows = _donut_segments(expense_by_category)

    income_by_source = list(
        incomes.values('source__name')
        .annotate(total=Sum('amount'), n=Count('id'))
        .order_by('-total')
    )
    max_source = max((r['total'] for r in income_by_source), default=Decimal('0'))
    for row in income_by_source:
        row['share'] = round(float(row['total'] / total_income * 100), 1) if total_income else 0
        row['bar'] = round(float(row['total'] / max_source * 100), 1) if max_source else 0

    revenue_by_crop = list(
        sales.values('harvest__crop_season__crop__name')
        .annotate(
            qty=Coalesce(Sum('quantity'), 0, output_field=DecimalField()),
            total=Coalesce(
                Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()),
            n=Count('id'),
        )
        .order_by('-total')[:8]
    )
    max_crop = max((r['total'] for r in revenue_by_crop), default=Decimal('0'))
    for row in revenue_by_crop:
        row['bar'] = round(float(row['total'] / max_crop * 100), 1) if max_crop else 0
        row['share'] = round(
            float(row['total'] / sales_revenue * 100), 1) if sales_revenue else 0

    monthly = defaultdict(lambda: {'income': Decimal('0'), 'expense': Decimal('0')})
    for d, amt in incomes.values_list('date', 'amount'):
        monthly[d.strftime('%Y-%m')]['income'] += amt
    for d, amt in expenses.values_list('date', 'amount'):
        monthly[d.strftime('%Y-%m')]['expense'] += amt
    monthly_rows = [
        {
            'label': datetime.datetime.strptime(key, '%Y-%m').strftime('%b %Y'),
            'income': v['income'],
            'expense': v['expense'],
            'net': v['income'] - v['expense'],
        }
        for key, v in sorted(monthly.items())
    ]
    max_monthly = max(
        (max(r['income'], r['expense']) for r in monthly_rows), default=Decimal('0'))
    for row in monthly_rows:
        row['income_pct'] = float(row['income'] / max_monthly * 100) if max_monthly else 0
        row['expense_pct'] = float(row['expense'] / max_monthly * 100) if max_monthly else 0

    # Budgets: planned vs actual spend (spend respects the date filter)
    spent_by_budget = {
        row['budget_item__budget']: row['t']
        for row in expenses.values('budget_item__budget').annotate(t=Sum('amount'))
    }
    budget_rows = []
    for b in scoped_queryset(Budget, request.user).annotate(
        planned=Coalesce(Sum('items__planned_amount'), 0, output_field=DecimalField())
    ).order_by('-planned')[:8]:
        spent = spent_by_budget.get(b.pk, Decimal('0'))
        used = float(spent / b.planned * 100) if b.planned else 0
        budget_rows.append({
            'name': str(b),
            'status': b.get_status_display(),
            'planned': b.planned,
            'spent': spent,
            'remaining': b.planned - spent,
            'used_pct': round(used, 1),
            'bar': min(used, 100),
            'over': spent > b.planned,
        })

    ops = {
        'harvested': _in_range(
            scoped_queryset(HarvestRecord, request.user), 'harvest_date').aggregate(
            t=Coalesce(Sum('quantity'), 0, output_field=DecimalField()))['t'],
        'growing_seasons': scoped_queryset(CropSeason, request.user).filter(
            status='GROWING').count(),
        'fields': scoped_queryset(Field, request.user).filter(is_active=True).count(),
        'equipment': scoped_queryset(Equipment, request.user).filter(
            is_active=True).count(),
        'open_tasks': scoped_queryset(Task, request.user).exclude(
            status='COMPLETED').count(),
    }

    month_count = len(monthly_rows) or 1
    summary = {
        'months': len(monthly_rows),
        'avg_income': total_income / month_count,
        'avg_expense': total_expenses / month_count,
        'profitable_months': sum(1 for r in monthly_rows if r['net'] > 0),
    }

    # Financial position strip: what share of income is spent vs kept
    fin = {
        'exp_pct': min(round(float(total_expenses / total_income * 100), 1), 100)
        if total_income else (100 if total_expenses else 0),
        'net_pct': round(float(net_profit / total_income * 100), 1) if total_income else 0,
    }

    context = {
        'date_from': date_from,
        'date_to': date_to,
        'total_income': total_income,
        'total_expenses': total_expenses,
        'sales_revenue': sales_revenue,
        'net_profit': net_profit,
        'income_count': incomes.count(),
        'expense_count': expenses.count(),
        'sale_count': sales.count(),
        'expense_by_category': expense_by_category,
        'donut_rows': donut_rows,
        'income_by_source': income_by_source,
        'revenue_by_crop': revenue_by_crop,
        'monthly_rows': monthly_rows,
        'budget_rows': budget_rows,
        'ops': ops,
        'summary': summary,
        'fin': fin,
    }

    if request.GET.get('export'):
        sections = [
            {'title': 'Summary', 'headers': ['Metric', 'Value'], 'rows': [
                ['Total Income', _M(total_income)],
                ['Total Expenses', _M(total_expenses)],
                ['Net Profit/Loss', _M(net_profit)],
                ['Crop Sales Revenue', _M(sales_revenue)],
                ['Income Records', incomes.count()],
                ['Expense Records', expenses.count()],
                ['Harvested (kg)', ops['harvested']],
                ['Growing Seasons', ops['growing_seasons']],
                ['Active Fields', ops['fields']],
                ['Active Equipment', ops['equipment']],
                ['Open Tasks', ops['open_tasks']],
            ]},
            {'title': 'Monthly Cash Flow', 'headers': ['Month', 'Income', 'Expenses', 'Net'],
             'rows': [[r['label'], _M(r['income']), _M(r['expense']), _M(r['net'])]
                      for r in monthly_rows]},
            {'title': 'Expense Structure', 'headers': ['Budget Code', 'Category', 'Amount', 'Entries', 'Share %'],
             'rows': [[r['category__code'] or '-', r['label'], _M(r['total']), r['n'],
                       round(float(r['total'] / total_expenses * 100), 1) if total_expenses else 0]
                      for r in expense_by_category]},
            {'title': 'Income by Source', 'headers': ['Source', 'Amount', 'Entries', 'Share %'],
             'rows': [[r['source__name'] or 'Other', _M(r['total']), r['n'], r['share']]
                      for r in income_by_source]},
            {'title': 'Budget Performance', 'headers': ['Budget', 'Status', 'Planned', 'Spent', 'Remaining', 'Used %'],
             'rows': [[r['name'], r['status'], _M(r['planned']), _M(r['spent']),
                       _M(r['remaining']), r['used_pct']] for r in budget_rows]},
            {'title': 'Top Crops by Revenue', 'headers': ['Crop', 'Sales', 'Qty', 'Revenue', 'Share %'],
             'rows': [[r['harvest__crop_season__crop__name'] or '-', r['n'], r['qty'],
                       _M(r['total']), r['share']] for r in revenue_by_crop]},
        ]
        return _export_report(request, request.GET['export'], 'Farm Report - Overview',
                              'farm_overview_report', sections, date_from, date_to)

    return TemplateResponse(request, 'reports/overview.html', context)


@login_required
def report_sales(request):
    from crops.models import Crop, CropSeason, Sale

    date_from, date_to, in_range = _report_range(request)
    farm_ids = accessible_farms(request.user).values('pk')
    crop_options = list(Crop.objects.filter(
        seasons__field__farm_profile_id__in=farm_ids).distinct().order_by('name'))
    season_options = list(
        scoped_queryset(CropSeason, request.user)
        .select_related('crop', 'field').order_by('-planting_date', '-pk'))
    year_options = sorted(
        {d.year for d in scoped_queryset(Sale, request.user).dates('sale_date', 'year', order='DESC')}
        | {datetime.date.today().year}, reverse=True)
    selected_year = next((y for y in year_options if str(y) == request.GET.get('year')), None)
    selected_crop = next((c for c in crop_options if str(c.pk) == request.GET.get('crop')), None)
    selected_season = next((s for s in season_options if str(s.pk) == request.GET.get('season')), None)

    sales = in_range(
        scoped_queryset(Sale, request.user,
                        Sale.objects.select_related('harvest__crop_season__crop')),
        'sale_date')
    if selected_year is not None:
        sales = sales.filter(sale_date__year=selected_year)
    if selected_crop:
        sales = sales.filter(harvest__crop_season__crop=selected_crop)
    if selected_season:
        sales = sales.filter(harvest__crop_season=selected_season)
    latest_date = sales.order_by('-sale_date').values_list('sale_date', flat=True).first()
    if selected_year is not None:
        chart_year = selected_year
    elif latest_date:
        chart_year = latest_date.year
    elif date_to or date_from:
        chart_year = datetime.date.fromisoformat(date_to or date_from).year
    else:
        chart_year = datetime.date.today().year
    produce = sales.filter(is_waste=False)
    waste = sales.filter(is_waste=True)

    produce_list = list(produce.select_related(
        'harvest__crop_season__crop', 'processing_stage', 'inventory_item', 'customer'))

    def _sold_qty(sale):
        """Quantity converted to the sale's source unit (crop primary)."""
        if sale.harvest_id and sale.harvest.unit:
            return sale.quantity_in_unit(sale.harvest.unit)
        return sale.quantity

    gross = produce.aggregate(
        t=Coalesce(Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()))['t']
    qty_sold = sum((_sold_qty(s) for s in produce_list), Decimal('0'))
    order_count = len(produce_list)
    avg_order = gross / order_count if order_count else Decimal('0')
    waste_revenue = waste.aggregate(
        t=Coalesce(Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()))['t']
    waste_qty = waste.aggregate(
        t=Coalesce(Sum('quantity'), 0, output_field=DecimalField()))['t']
    total_receipts = gross + waste_revenue
    produce_pct = round(float(gross / total_receipts * 100), 1) if total_receipts else 0
    sales_mix = {
        'total': total_receipts,
        'produce_pct': produce_pct,
        'waste_pct': round(100 - produce_pct, 1) if total_receipts else 0,
    }

    monthly = {f'{chart_year}-{month:02d}': Decimal('0') for month in range(1, 13)}
    monthly_qty = monthly.copy()
    for s in produce_list:
        if s.sale_date and s.sale_date.year == chart_year:
            key = s.sale_date.strftime('%Y-%m')
            monthly[key] += s.total_amount
            monthly_qty[key] += _sold_qty(s)
    monthly_rows = _monthly_chart(monthly)
    monthly_qty_rows = _monthly_chart(monthly_qty)
    revenue_line = _line_chart(monthly_rows)
    quantity_line = _line_chart(monthly_qty_rows)
    for revenue, quantity in zip(monthly_rows, monthly_qty_rows):
        revenue['qty'] = quantity['value']

    crop_groups = list(
        produce.values('harvest__crop_season__crop__name')
        .annotate(
            total=Coalesce(
                Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()),
            n=Count('id'))
        .order_by('-total'))
    crop_qty = defaultdict(lambda: Decimal('0'))
    for s in produce_list:
        key = s.harvest.crop_season.crop.name if (
            s.harvest_id and s.harvest.crop_season_id and s.harvest.crop_season.crop_id
        ) else None
        crop_qty[key] += _sold_qty(s)
    for row in crop_groups:
        row['qty'] = crop_qty[row['harvest__crop_season__crop__name']]
    for row in crop_groups:
        row['label'] = row['harvest__crop_season__crop__name'] or '-'
    top_crops = _bar_scale(crop_groups[:8])
    for r in top_crops:
        r['share'] = round(float(r['total'] / gross * 100), 1) if gross else 0
    crop_donut = _donut_segments(crop_groups, limit=5)

    season_rows = list(
        produce.values(
            'harvest__crop_season_id', 'harvest__crop_season__crop__name',
            'harvest__crop_season__field__name', 'harvest__crop_season__planting_date',
            'harvest__crop_season__season_label',
        ).annotate(
            n=Count('id'),
            total=Coalesce(Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()),
        ).order_by('-total')
    )
    season_qty = defaultdict(lambda: Decimal('0'))
    for s in produce_list:
        if s.harvest_id and s.harvest.crop_season_id:
            season_qty[s.harvest.crop_season_id] += _sold_qty(s)
    for row in season_rows:
        row['qty'] = season_qty[row['harvest__crop_season_id']]
        row['crop'] = row['harvest__crop_season__crop__name'] or '-'
        row['season'] = row['harvest__crop_season__season_label'] or (
            f"{row['harvest__crop_season__field__name']} ({row['harvest__crop_season__planting_date'].year})"
        )
        row['share'] = round(float(row['total'] / gross * 100), 1) if gross else 0
    season_chart = _bar_scale(season_rows[:6])

    top_buyers = _bar_scale(list(
        produce.annotate(
                buyer_name=Coalesce('customer__name', 'buyer'),
                buyer_key=Lower(Coalesce('customer__name', 'buyer')))
        .exclude(buyer_name='').values('buyer_key')
        .annotate(
            buyer=Max('buyer_name'),
            n=Count('id'),
            total=Coalesce(
                Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()))
        .order_by('-total')[:8]))
    buyer_qty = defaultdict(lambda: Decimal('0'))
    for s in produce_list:
        name = s.customer.name if s.customer_id else s.buyer
        if name:
            buyer_qty[name.strip().lower()] += _sold_qty(s)
    for r in top_buyers:
        r['qty'] = buyer_qty[r['buyer_key'].strip().lower()]
    for r in top_buyers:
        r['label'] = r['buyer']
        r['share'] = round(float(r['total'] / gross * 100), 1) if gross else 0

    open_sales = [s for s in produce_list
                  if s.payment_status != Sale.PaymentStatus.PAID]
    receivables = sum((s.receivable for s in open_sales), Decimal('0'))
    receivable_count = len(open_sales)

    method_names = dict(Sale.PaymentMethod.choices)
    pay_rows = list(
        produce.values('payment_method')
        .annotate(total=Coalesce(
            Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()))
        .order_by('-total'))
    for r in pay_rows:
        r['label'] = method_names.get(r['payment_method'], r['payment_method'])
    pay_donut = _donut_segments(pay_rows)
    mix_donut = _donut_segments([
        {'label': 'Produce', 'total': gross},
        {'label': 'Recovered waste', 'total': waste_revenue},
    ]) if total_receipts else []

    context = {
        'date_from': date_from,
        'date_to': date_to,
        'year': str(selected_year) if selected_year is not None else '',
        'chart_year': chart_year,
        'year_options': year_options,
        'crop': str(selected_crop.pk) if selected_crop else '',
        'season': str(selected_season.pk) if selected_season else '',
        'crop_options': crop_options,
        'season_options': season_options,
        'show_sales_filters': True,
        'gross': gross,
        'qty_sold': qty_sold,
        'order_count': order_count,
        'avg_order': avg_order,
        'waste_revenue': waste_revenue,
        'waste_qty': waste_qty,
        'sales_mix': sales_mix,
        'monthly_rows': monthly_rows,
        'monthly_qty_rows': monthly_qty_rows,
        'revenue_line': revenue_line,
        'quantity_line': quantity_line,
        'top_crops': top_crops,
        'crop_donut': crop_donut,
        'season_rows': season_rows,
        'season_chart': season_chart,
        'top_buyers': top_buyers,
        'receivables': receivables,
        'receivable_count': receivable_count,
        'pay_donut': pay_donut,
        'mix_donut': mix_donut,
    }

    if request.GET.get('export'):
        sections = [
            {'title': 'Summary', 'headers': ['Metric', 'Value'], 'rows': [
                ['Gross Sales', _M(gross)],
                ['Trend Year', chart_year],
                ['Orders', order_count],
                ['Average Order Value', _M(avg_order)],
                ['Quantity Sold', qty_sold],
                ['Waste Revenue', _M(waste_revenue)],
                ['Waste Quantity', waste_qty],
                ['Receivables (unpaid + partial)', _M(receivables)],
                ['Unpaid Orders', receivable_count],
                ['Produce Share %', sales_mix['produce_pct']],
                ['Waste Share %', sales_mix['waste_pct']],
            ]},
            {'title': 'Monthly Sales', 'headers': ['Month', 'Revenue', 'Qty Sold'],
             'rows': [[r['label'], _M(r['value']), r['qty']] for r in monthly_rows]},
            {'title': 'Top-Selling Crops', 'headers': ['Crop', 'Orders', 'Qty Sold', 'Revenue', 'Share %'],
             'rows': [[r['label'], r['n'], r['qty'], _M(r['total']), r['share']]
                      for r in top_crops]},
            {'title': 'Crop Revenue Share', 'headers': ['Crop', 'Revenue', 'Share %'],
             'rows': [[r['label'], _M(r['total']), r['share']] for r in crop_donut]},
            {'title': 'Sales by Season and Crop',
             'headers': ['Season', 'Crop', 'Orders', 'Qty Sold', 'Revenue', 'Share %'],
             'rows': [[r['season'], r['crop'], r['n'], r['qty'], _M(r['total']), r['share']]
                      for r in season_rows]},
            {'title': 'Payment Methods', 'headers': ['Method', 'Amount', 'Share %'],
             'rows': [[r['label'], _M(r['total']), r['share']] for r in pay_donut]},
            {'title': 'Top Buyers', 'headers': ['Buyer', 'Orders', 'Qty', 'Revenue', 'Share %'],
             'rows': [[r['label'], r['n'], r['qty'], _M(r['total']), r['share']]
                      for r in top_buyers]},
        ]
        if selected_crop or selected_season or selected_year is not None:
            sections.insert(0, {
                'title': 'Selected Sales Filters', 'headers': ['Filter', 'Selection'],
                'rows': [
                    ['Year', selected_year if selected_year is not None else 'All years'],
                    ['Crop', selected_crop.name if selected_crop else 'All crops'],
                    ['Season', f'{selected_season.season_label or selected_season.field.name} '
                     f'({selected_season.planting_date.year}, #{selected_season.pk})'
                     if selected_season else 'All seasons'],
                ],
            })
        return _export_report(request, request.GET['export'], 'Sales Report',
                              'sales_report', sections, date_from, date_to)

    return TemplateResponse(request, 'reports/sales.html', context)


@login_required
def report_production(request):
    from crops.models import CropSeason, HarvestRecord, Sale
    from fields.models import Field

    date_from, date_to, in_range = _report_range(request)

    harvests = in_range(
        scoped_queryset(
            HarvestRecord, request.user,
            HarvestRecord.objects.select_related('crop_season__crop', 'crop_season__field')),
        'harvest_date')
    seasons = scoped_queryset(CropSeason, request.user)

    total_qty = harvests.aggregate(
        t=Coalesce(Sum('quantity'), 0, output_field=DecimalField()))['t']
    total_waste_qty = harvests.aggregate(
        t=Coalesce(Sum('waste_quantity'), 0, output_field=DecimalField()))['t']
    total_waste_value = harvests.aggregate(
        t=Coalesce(Sum('waste_value'), 0, output_field=DecimalField()))['t']
    harvest_count = harvests.count()
    season_count = seasons.count()
    growing = seasons.filter(status='GROWING').count()
    field_acreage = scoped_queryset(Field, request.user).filter(is_active=True).aggregate(
        t=Coalesce(Sum('acreage'), 0, output_field=DecimalField()))['t']

    monthly = defaultdict(lambda: Decimal('0'))
    for d, q in harvests.values_list('harvest_date', 'quantity'):
        monthly[d.strftime('%Y-%m')] += q
    monthly_rows = _monthly_chart(monthly)

    # Production per crop (harvested vs sold vs revenue)
    crop_rows = list(
        harvests.values('crop_season__crop__name')
        .annotate(
            qty=Coalesce(Sum('quantity'), 0, output_field=DecimalField()),
            waste=Coalesce(Sum('waste_quantity'), 0, output_field=DecimalField()),
            n=Count('id'))
        .order_by('-qty'))
    sales = in_range(scoped_queryset(Sale, request.user), 'sale_date').filter(is_waste=False)
    sold_map = {
        r['harvest__crop_season__crop__name']: r
        for r in sales.values('harvest__crop_season__crop__name').annotate(
            sold=Coalesce(Sum('quantity'), 0, output_field=DecimalField()),
            revenue=Coalesce(
                Sum(F('quantity') * F('unit_price')), 0, output_field=DecimalField()))
    }
    for r in crop_rows:
        r['label'] = r['crop_season__crop__name'] or '-'
        r['total'] = r['qty']
        s = sold_map.get(r['crop_season__crop__name'], {})
        r['sold'] = s.get('sold', Decimal('0'))
        r['revenue'] = s.get('revenue', Decimal('0'))
        r['sold_pct'] = min(float(r['sold'] / r['qty'] * 100), 100) if r['qty'] else 0
    _bar_scale(crop_rows)
    crop_donut = _donut_segments(crop_rows)

    sold_total = sum((r['sold'] for r in crop_rows), Decimal('0'))
    sold_rate = round(float(sold_total / total_qty * 100), 1) if total_qty else 0
    yield_per_acre = (total_qty / field_acreage) if field_acreage else Decimal('0')

    # Season pipeline: stages in agronomic order as a segmented strip
    status_names = dict(CropSeason.Status.choices)
    status_counts = {
        r['status']: r['total']
        for r in seasons.values('status').annotate(total=Count('id'))
    }
    status_total = sum(status_counts.values()) or 1
    pipeline_colors = {
        'PLANNED': '#b7c9c2', 'PLANTED': '#95d5b2', 'GROWING': '#52b788',
        'FLOWERING': '#40916c', 'HARVESTED': '#1b4332',
        'DORMANT': '#9aa5a1', 'CANCELLED': '#e07a5f',
    }
    pipeline = [
        {
            'label': status_names.get(s, s),
            'total': status_counts[s],
            'share': round(status_counts[s] / status_total * 100, 1),
            'color': pipeline_colors.get(s, '#74c69d'),
        }
        for s in ('PLANNED', 'PLANTED', 'GROWING', 'FLOWERING',
                  'HARVESTED', 'DORMANT', 'CANCELLED')
        if status_counts.get(s)
    ]

    field_rows = _bar_scale(list(
        harvests.values('crop_season__field__name', 'crop_season__field__acreage')
        .annotate(
            qty=Coalesce(Sum('quantity'), 0, output_field=DecimalField()),
            n=Count('id'))
        .order_by('-qty')[:8]), key='qty')
    for r in field_rows:
        r['label'] = r['crop_season__field__name'] or '-'
        acres = r['crop_season__field__acreage'] or Decimal('0')
        r['per_acre'] = round(r['qty'] / acres, 1) if acres else None

    context = {
        'date_from': date_from,
        'date_to': date_to,
        'total_qty': total_qty,
        'total_waste_qty': total_waste_qty,
        'total_waste_value': total_waste_value,
        'harvest_count': harvest_count,
        'season_count': season_count,
        'growing': growing,
        'field_acreage': field_acreage,
        'monthly_rows': monthly_rows,
        'crop_rows': crop_rows,
        'crop_donut': crop_donut,
        'pipeline': pipeline,
        'sold_total': sold_total,
        'sold_rate': sold_rate,
        'yield_per_acre': yield_per_acre,
        'field_rows': field_rows,
    }

    if request.GET.get('export'):
        sections = [
            {'title': 'Summary', 'headers': ['Metric', 'Value'], 'rows': [
                ['Harvested Quantity', total_qty],
                ['Harvest Records', harvest_count],
                ['Yield per Acre', yield_per_acre],
                ['Sold Quantity', sold_total],
                ['Sold Rate %', sold_rate],
                ['Waste Quantity', total_waste_qty],
                ['Waste Value', _M(total_waste_value)],
                ['Crop Seasons', season_count],
                ['Growing Seasons', growing],
                ['Active Field Acreage', field_acreage],
            ]},
            {'title': 'Monthly Harvest', 'headers': ['Month', 'Qty'],
             'rows': [[r['label'], r['value']] for r in monthly_rows]},
            {'title': 'Production per Crop', 'headers': ['Crop', 'Harvests', 'Qty', 'Waste', 'Sold', 'Sold %', 'Revenue'],
             'rows': [[r['label'], r['n'], r['qty'], r['waste'], r['sold'],
                       round(r['sold_pct'], 1), _M(r['revenue'])] for r in crop_rows]},
            {'title': 'Crop Structure', 'headers': ['Crop', 'Qty', 'Share %'],
             'rows': [[r['label'], r['total'], r['share']] for r in crop_donut]},
            {'title': 'Season Pipeline', 'headers': ['Status', 'Seasons', 'Share %'],
             'rows': [[r['label'], r['total'], r['share']] for r in pipeline]},
            {'title': 'Field Productivity', 'headers': ['Field', 'Harvests', 'Qty', 'Qty per Acre'],
             'rows': [[r['label'], r['n'], r['qty'], r['per_acre'] or '-'] for r in field_rows]},
        ]
        return _export_report(request, request.GET['export'], 'Production Report',
                              'production_report', sections, date_from, date_to)

    return TemplateResponse(request, 'reports/production.html', context)


@login_required
def report_livestock(request):
    from livestock.models import (
        AnimalFeedLog, AnimalGroup, AnimalHealthRecord, EggProductionRecord,
        MilkProductionRecord, PoultryBatch, PoultryFeedLog, PoultryHealthRecord,
    )

    date_from, date_to, in_range = _report_range(request)

    groups = list(
        scoped_queryset(AnimalGroup, request.user)
        .select_related('species', 'field').order_by('-count'))
    batches = list(
        scoped_queryset(PoultryBatch, request.user).order_by('-current_count'))

    total_animals = sum(g.count for g in groups)
    total_birds = sum(b.current_count for b in batches)
    active_groups = sum(1 for g in groups if g.is_active)

    milk = in_range(scoped_queryset(MilkProductionRecord, request.user), 'date')
    eggs = in_range(scoped_queryset(EggProductionRecord, request.user), 'date')
    health = in_range(scoped_queryset(AnimalHealthRecord, request.user), 'date')
    phealth = in_range(scoped_queryset(PoultryHealthRecord, request.user), 'date')
    feed = in_range(scoped_queryset(AnimalFeedLog, request.user), 'date')
    pfeed = in_range(scoped_queryset(PoultryFeedLog, request.user), 'date')

    milk_total = milk.aggregate(
        t=Coalesce(Sum('total_liters'), 0, output_field=DecimalField()))['t']
    milk_revenue = milk.aggregate(
        t=Coalesce(Sum(F('total_liters') * F('price_per_liter')), 0,
                     output_field=DecimalField()))['t']
    eggs_total = eggs.aggregate(
        t=Coalesce(Sum('eggs_collected'), 0, output_field=DecimalField()))['t']
    eggs_damaged = eggs.aggregate(
        t=Coalesce(Sum('damaged_eggs'), 0, output_field=DecimalField()))['t']
    health_cost = (
        health.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t']
        + phealth.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t'])
    feed_cost = (
        feed.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t']
        + pfeed.aggregate(t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t'])
    feed_kg = (
        feed.aggregate(t=Coalesce(Sum('quantity_kg'), 0, output_field=DecimalField()))['t']
        + pfeed.aggregate(t=Coalesce(Sum('quantity_kg'), 0, output_field=DecimalField()))['t'])

    milk_monthly_map = defaultdict(lambda: Decimal('0'))
    for d, l in milk.values_list('date', 'total_liters'):
        milk_monthly_map[d.strftime('%Y-%m')] += l
    milk_monthly = _monthly_chart(milk_monthly_map)
    eggs_monthly_map = defaultdict(lambda: Decimal('0'))
    for d, e in eggs.values_list('date', 'eggs_collected'):
        eggs_monthly_map[d.strftime('%Y-%m')] += e
    eggs_monthly = _monthly_chart(eggs_monthly_map)

    health_rows = [
        {'label': 'Vaccinations', 'total': health.filter(record_type='VACCINATION').count()
                 + phealth.filter(record_type='VACCINATION').count()},
        {'label': 'Treatments', 'total': health.filter(record_type='TREATMENT').count()
                 + phealth.filter(record_type='TREATMENT').count()},
        {'label': 'Other', 'total': health.exclude(
            record_type__in=('VACCINATION', 'TREATMENT')).count()
                 + phealth.exclude(record_type__in=('VACCINATION', 'TREATMENT')).count()},
    ]
    health_rows = [r for r in health_rows if r['total']]
    health_donut = _donut_segments(health_rows)

    context = {
        'date_from': date_from,
        'date_to': date_to,
        'total_animals': total_animals,
        'total_birds': total_birds,
        'active_groups': active_groups,
        'groups': groups[:10],
        'batches': batches[:10],
        'milk_total': milk_total,
        'milk_revenue': milk_revenue,
        'eggs_total': eggs_total,
        'eggs_damaged': eggs_damaged,
        'health_cost': health_cost,
        'feed_cost': feed_cost,
        'feed_kg': feed_kg,
        'milk_monthly': milk_monthly,
        'eggs_monthly': eggs_monthly,
        'health_donut': health_donut,
        'health_count': health.count() + phealth.count(),
    }

    if request.GET.get('export'):
        sections = [
            {'title': 'Summary', 'headers': ['Metric', 'Value'], 'rows': [
                ['Total Animals', total_animals],
                ['Total Birds', total_birds],
                ['Active Groups', active_groups],
                ['Milk (liters)', milk_total],
                ['Milk Revenue', _M(milk_revenue)],
                ['Eggs Collected', eggs_total],
                ['Damaged Eggs', eggs_damaged],
                ['Feed Used (kg)', feed_kg],
                ['Feed Cost', _M(feed_cost)],
                ['Health Cost', _M(health_cost)],
                ['Health Records', context['health_count']],
            ]},
            {'title': 'Monthly Milk', 'headers': ['Month', 'Liters'],
             'rows': [[r['label'], r['value']] for r in milk_monthly]},
            {'title': 'Monthly Eggs', 'headers': ['Month', 'Eggs'],
             'rows': [[r['label'], r['value']] for r in eggs_monthly]},
            {'title': 'Animal Groups', 'headers': ['Group', 'Species', 'Head Count', 'Purpose', 'Field', 'Active'],
             'rows': [[g.name, str(g.species) if g.species else '-', g.count,
                       g.get_purpose_display(), str(g.field) if g.field else '-',
                       'Yes' if g.is_active else 'No'] for g in groups]},
            {'title': 'Health Records by Type', 'headers': ['Type', 'Records', 'Share %'],
             'rows': [[r['label'], r['total'], r['share']] for r in health_donut]},
            {'title': 'Poultry Batches', 'headers': ['Batch', 'Type', 'Purpose', 'Birds', 'Mortality', 'Survival %', 'Status'],
             'rows': [[b.batch_name, b.get_bird_type_display(), b.get_purpose_display(),
                       b.current_count, b.mortality_count, round(float(b.survival_rate), 1),
                       b.get_status_display()] for b in batches]},
        ]
        return _export_report(request, request.GET['export'], 'Livestock Report',
                              'livestock_report', sections, date_from, date_to)

    return TemplateResponse(request, 'reports/livestock.html', context)


@login_required
def report_operations(request):
    from equipment.models import EquipmentUsage, MaintenanceLog
    from inventory.models import InventoryItem
    from labor.models import AttendanceRecord, PayrollRun, Task, Worker

    date_from, date_to, in_range = _report_range(request)

    attendance = in_range(
        scoped_queryset(AttendanceRecord, request.user,
                        AttendanceRecord.objects.select_related('worker')), 'date')
    usage = in_range(
        scoped_queryset(EquipmentUsage, request.user,
                        EquipmentUsage.objects.select_related('equipment')), 'date')
    maintenance = in_range(
        scoped_queryset(MaintenanceLog, request.user,
                        MaintenanceLog.objects.select_related('equipment')), 'date')
    payroll = in_range(scoped_queryset(PayrollRun, request.user), 'start_date')
    tasks = scoped_queryset(Task, request.user)
    inv_items = scoped_queryset(InventoryItem, request.user)

    total_hours = attendance.aggregate(
        t=Coalesce(Sum('hours'), 0, output_field=DecimalField()))['t']
    overtime = attendance.aggregate(
        t=Coalesce(Sum('overtime_hours'), 0, output_field=DecimalField()))['t']
    usage_hours = usage.aggregate(
        t=Coalesce(Sum('hours'), 0, output_field=DecimalField()))['t']
    maint_cost = maintenance.aggregate(
        t=Coalesce(Sum('cost'), 0, output_field=DecimalField()))['t']
    maint_count = maintenance.count()
    payroll_total = payroll.aggregate(
        t=Coalesce(Sum('total_amount'), 0, output_field=DecimalField()))['t']
    open_tasks = tasks.exclude(status='COMPLETED').exclude(status='CANCELLED').count()
    worker_count = scoped_queryset(Worker, request.user).filter(is_active=True).count()

    low_stock = [i for i in inv_items if i.is_low_stock]
    for i in low_stock:
        i.stock_val = i.quantity_on_hand * i.cost_per_unit
    inventory_value = sum(
        (i.quantity_on_hand * i.cost_per_unit for i in inv_items), Decimal('0'))

    status_names = dict(Task.TaskStatus.choices)
    task_rows = list(
        tasks.values('status').annotate(total=Count('id')).order_by('-total'))
    for r in task_rows:
        r['label'] = status_names.get(r['status'], r['status'])
    task_donut = _donut_segments(task_rows, limit=4)

    worker_rows = _bar_scale(list(
        attendance.values('worker__name', 'worker__hourly_rate')
        .annotate(
            total=Coalesce(Sum('hours'), 0, output_field=DecimalField()),
            ot=Coalesce(Sum('overtime_hours'), 0, output_field=DecimalField()))
        .order_by('-total')[:10]))
    for r in worker_rows:
        r['label'] = r['worker__name'] or '-'
        r['est_cost'] = r['total'] * (r['worker__hourly_rate'] or Decimal('0'))

    equip_rows = _bar_scale(list(
        usage.values('equipment__name')
        .annotate(total=Coalesce(Sum('hours'), 0, output_field=DecimalField()))
        .order_by('-total')[:8]))
    for r in equip_rows:
        r['label'] = r['equipment__name'] or '-'

    maint_rows = _bar_scale(list(
        maintenance.values('equipment__name')
        .annotate(
            total=Coalesce(Sum('cost'), 0, output_field=DecimalField()),
            n=Count('id'))
        .order_by('-total')[:8]))
    for r in maint_rows:
        r['label'] = r['equipment__name'] or '-'

    hours_monthly_map = defaultdict(lambda: Decimal('0'))
    for d, h in attendance.values_list('date', 'hours'):
        hours_monthly_map[d.strftime('%Y-%m')] += h
    hours_monthly = _monthly_chart(hours_monthly_map)

    context = {
        'date_from': date_from,
        'date_to': date_to,
        'total_hours': total_hours,
        'overtime': overtime,
        'usage_hours': usage_hours,
        'maint_cost': maint_cost,
        'maint_count': maint_count,
        'payroll_total': payroll_total,
        'open_tasks': open_tasks,
        'worker_count': worker_count,
        'low_stock': low_stock,
        'inventory_value': inventory_value,
        'task_donut': task_donut,
        'worker_rows': worker_rows,
        'equip_rows': equip_rows,
        'maint_rows': maint_rows,
        'hours_monthly': hours_monthly,
    }

    if request.GET.get('export'):
        sections = [
            {'title': 'Summary', 'headers': ['Metric', 'Value'], 'rows': [
                ['Labor Hours', total_hours],
                ['Overtime Hours', overtime],
                ['Equipment Hours', usage_hours],
                ['Maintenance Cost', _M(maint_cost)],
                ['Maintenance Records', maint_count],
                ['Payroll', _M(payroll_total)],
                ['Open Tasks', open_tasks],
                ['Active Workers', worker_count],
                ['Inventory Value', _M(inventory_value)],
                ['Low Stock Items', len(low_stock)],
            ]},
            {'title': 'Monthly Labor Hours', 'headers': ['Month', 'Hours'],
             'rows': [[r['label'], r['value']] for r in hours_monthly]},
            {'title': 'Worker Hours', 'headers': ['Worker', 'Hours', 'Overtime', 'Est. Cost'],
             'rows': [[r['label'], r['total'], r['ot'], _M(r['est_cost'])] for r in worker_rows]},
            {'title': 'Equipment Usage', 'headers': ['Equipment', 'Hours'],
             'rows': [[r['label'], r['total']] for r in equip_rows]},
            {'title': 'Maintenance by Equipment', 'headers': ['Equipment', 'Records', 'Cost'],
             'rows': [[r['label'], r['n'], _M(r['total'])] for r in maint_rows]},
            {'title': 'Tasks by Status', 'headers': ['Status', 'Tasks', 'Share %'],
             'rows': [[r['label'], r['total'], r['share']] for r in task_donut]},
            {'title': 'Low Stock', 'headers': ['Item', 'On Hand', 'Reorder At', 'Unit Cost', 'Value'],
             'rows': [[i.name, f'{i.quantity_on_hand} {i.unit}',
                       f'{i.reorder_threshold} {i.unit}', _M(i.cost_per_unit), _M(i.stock_val)]
                      for i in low_stock]},
        ]
        return _export_report(request, request.GET['export'], 'Operations Report',
                              'operations_report', sections, date_from, date_to)

    return TemplateResponse(request, 'reports/operations.html', context)
