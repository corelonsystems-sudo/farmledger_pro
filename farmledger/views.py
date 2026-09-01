from django.apps import apps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import FieldError
from django.core.paginator import Paginator
from django.db.models import Count, F, Q, Sum, Value, DecimalField
from django.db.models.functions import Coalesce
from django.forms import modelform_factory
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from urllib.parse import quote
from collections import defaultdict
from decimal import Decimal

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
}

# Override generic dashboard list columns for specific models.
CUSTOM_LIST_DISPLAY = {
    ('crops', 'cropseason'): ['crop', 'field', 'planting_date', 'status'],
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

    if key == ('crops', 'crop'):
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
    crop = request.GET.get('crop', '')
    waste = request.GET.get('waste', '')
    is_waste = waste == '1'

    queryset = Sale.objects.select_related(
        'harvest__crop_season__crop', 'harvest__crop_season__field', 'processing_stage'
    ).order_by('-sale_date')
    queryset = _search_filter(queryset, q)

    if date_from and date_to:
        queryset = queryset.filter(sale_date__range=[date_from, date_to])
    elif date_from:
        queryset = queryset.filter(sale_date__gte=date_from)
    elif date_to:
        queryset = queryset.filter(sale_date__lte=date_to)

    if buyer:
        queryset = queryset.filter(buyer__icontains=buyer)

    if payment_method:
        queryset = queryset.filter(payment_method=payment_method)

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
        stats = [
            {'label': 'Waste Sales', 'value': queryset.count()},
            {'label': 'Waste Quantity', 'value': str(total_waste_qty)},
            {'label': 'Waste Revenue', 'value': total_waste_rev},
        ]
    else:
        stats = _compute_stats(app, model)

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
            'crop': crop,
        },
        'crops': Crop.objects.order_by('name'),
        'payment_methods': Sale.PaymentMethod.choices,
        'query_string': query_string,
        'urls': _crud_urls(app, model),
        'is_waste': is_waste,
        'create_label': create_label,
        'view_label': 'Waste Details' if is_waste else 'Sale Details',
        'stats': stats,
    }
    return render(request, 'crops/sale_list.html', context)


@login_required
def model_list(request, app, model):
    if (app, model) == ('crops', 'sale'):
        return _sale_list(request, app, model)
    model_cls = _get_model(app, model)
    q = request.GET.get('q', '')

    if (app, model) in CUSTOM_LIST_DISPLAY:
        queryset = model_cls.objects.select_related('crop', 'field').order_by('-planting_date')
        display_fields = [model_cls._meta.get_field(name) for name in CUSTOM_LIST_DISPLAY[(app, model)]]
    else:
        queryset = model_cls.objects.all().order_by('-id')
        display_fields = _list_display(model_cls)

    queryset = _search_filter(queryset, q)

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
        'stats': _compute_stats(app, model),
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


@login_required
def model_create(request, app, model):
    model_cls = _get_model(app, model)
    form_cls = _get_form_class(model_cls)
    if request.method == 'POST':
        form = form_cls(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            target = _redirect_target(request, app, model)
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': True, 'redirect': target})
            return HttpResponseRedirect(target)
    else:
        initial = {k: v for k, v in request.GET.items() if k != 'next'}
        form = form_cls(initial=initial)

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
    model_cls = _get_model(app, model)
    obj = get_object_or_404(model_cls, pk=pk)
    is_waste = request.GET.get('waste') == '1'
    form_cls = _get_form_class(model_cls, waste=is_waste)
    if request.method == 'POST':
        form = form_cls(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            form.save()
            target = _redirect_target(request, app, model)
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'ok': True, 'redirect': target})
            return HttpResponseRedirect(target)
    else:
        form = form_cls(instance=obj)

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
    obj = get_object_or_404(model_cls, pk=pk)
    if request.method == 'POST':
        obj.delete()
        target = _redirect_target(request, app, model)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'ok': True, 'redirect': target})
        return HttpResponseRedirect(target)
    context = {
        'app': app,
        'model': model,
        'model_name': model_cls._meta.verbose_name,
        'obj': obj,
        'urls': _crud_urls(app, model, pk=pk),
    }
    return render(request, 'dashboard/confirm_delete.html', context)
