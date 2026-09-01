"""Custom ModelForm classes for the dashboard popup forms.

Each form defines field ordering, widget types, and help text to give
the dashboard a polished, user-friendly look instead of the raw
auto-generated modelform_factory output.
"""
from django import forms

from accounts.models import FarmProfile, User
from budgets.models import Budget, BudgetItem
from crops.models import Crop, CropSeason, HarvestRecord, ProcessingStage, Sale
from equipment.models import Equipment, EquipmentUsage, MaintenanceLog
from expenses.models import Category, Expense
from fields.models import Field, LandParcel
from integrations.models import BankAccount, BankTransaction
from inventory.models import InventoryAlert, InventoryItem, InventoryTransaction
from labor.models import AttendanceRecord, PayrollRun, Task, Worker
from livestock.models import (
    AnimalFeedLog,
    AnimalGroup,
    AnimalHealthRecord,
    AnimalRecord,
    AnimalSpecies,
    BreedingRecord,
    EggProductionRecord,
    MilkProductionRecord,
    PoultryBatch,
    PoultryFeedLog,
    PoultryHealthRecord,
)


# -- Accounts --

class FarmProfileForm(forms.ModelForm):
    class Meta:
        model = FarmProfile
        fields = ['user', 'farm_name', 'location', 'acreage', 'tax_id', 'fiscal_year_start', 'phone', 'email', 'logo']
        widgets = {
            'farm_name': forms.TextInput(attrs={'placeholder': 'e.g. Green Valley Farm'}),
            'location': forms.TextInput(attrs={'placeholder': 'City, State, Country'}),
            'acreage': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'tax_id': forms.TextInput(attrs={'placeholder': 'Tax ID / EIN'}),
            'fiscal_year_start': forms.DateInput(attrs={'type': 'date'}),
            'phone': forms.TextInput(attrs={'placeholder': '+1 555-000-0000'}),
            'email': forms.EmailInput(attrs={'placeholder': 'farm@example.com'}),
        }
        labels = {
            'farm_name': 'Farm Name',
            'tax_id': 'Tax ID / EIN',
        }


class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'role', 'phone', 'is_active']
        widgets = {
            'username': forms.TextInput(attrs={'placeholder': 'username'}),
            'email': forms.EmailInput(attrs={'placeholder': 'user@example.com'}),
            'phone': forms.TextInput(attrs={'placeholder': '+1 555-000-0000'}),
        }


# -- Fields --

class FieldForm(forms.ModelForm):
    class Meta:
        model = Field
        fields = ['farm_profile', 'name', 'acreage', 'soil_type', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. North 40'}),
            'acreage': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
        }
        labels = {'farm_profile': 'Farm'}


class LandParcelForm(forms.ModelForm):
    class Meta:
        model = LandParcel
        fields = ['field', 'land_type', 'lease_cost', 'lease_start', 'lease_end', 'owner_name']
        widgets = {
            'lease_cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'lease_start': forms.DateInput(attrs={'type': 'date'}),
            'lease_end': forms.DateInput(attrs={'type': 'date'}),
            'owner_name': forms.TextInput(attrs={'placeholder': 'Landlord name (if leased/rented)'}),
        }


# -- Crops --

class CropForm(forms.ModelForm):
    class Meta:
        model = Crop
        fields = ['name', 'scientific_name', 'category', 'default_unit', 'secondary_unit',
                  'secondary_per_primary', 'growing_season_days', 'is_perennial', 'perennial_lifespan_years',
                  'harvest_frequency', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Maize, Coffee, Soybeans'}),
            'scientific_name': forms.TextInput(attrs={'placeholder': 'e.g. Zea mays'}),
            'default_unit': forms.TextInput(attrs={'placeholder': 'kg, lbs, tons...'}),
            'secondary_unit': forms.TextInput(attrs={'placeholder': 'bags, crates, bunches...'}),
            'secondary_per_primary': forms.NumberInput(attrs={'step': '0.0001', 'placeholder': '1'}),
            'harvest_frequency': forms.TextInput(attrs={'placeholder': 'e.g. Annual, Biannual, Every 3 months'}),
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Crop description...'}),
        }
        labels = {
            'default_unit': 'Primary unit',
            'secondary_unit': 'Secondary unit',
            'secondary_per_primary': 'Primary units per secondary unit',
            'is_perennial': 'Perennial crop (planted once, harvested seasonally)',
        }


class CropSeasonForm(forms.ModelForm):
    class Meta:
        model = CropSeason
        fields = ['field', 'crop', 'variety', 'planting_date', 'expected_harvest_date',
                  'actual_harvest_date', 'status', 'is_perennial_season', 'season_label',
                  'perennial_established_date', 'notes']
        widgets = {
            'variety': forms.TextInput(attrs={'placeholder': 'e.g. Pioneer P1197AM'}),
            'planting_date': forms.DateInput(attrs={'type': 'date'}),
            'expected_harvest_date': forms.DateInput(attrs={'type': 'date'}),
            'actual_harvest_date': forms.DateInput(attrs={'type': 'date'}),
            'season_label': forms.TextInput(attrs={'placeholder': 'e.g. "2025 Main Harvest", "Fly Crop"'}),
            'perennial_established_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Notes about this season...'}),
        }
        labels = {
            'is_perennial_season': 'Perennial harvest cycle (e.g. coffee/tea harvest season)',
        }


class ProcessingStageSelect(forms.Select):
    """Select that tags each stage option with its harvest id for client-side filtering."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        instance = getattr(value, 'instance', None)
        if instance is not None:
            option['attrs']['data-harvest'] = instance.harvest_id
        return option


def _scoped_crop_id(form, crop_lookup):
    """Resolve the crop this form is scoped to, from GET initial or the edited instance."""
    crop_id = form.initial.get('crop')
    if crop_id:
        return crop_id
    if form.instance and form.instance.pk:
        return crop_lookup(form.instance)
    return None


class HarvestRecordForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        crop_id = _scoped_crop_id(self, lambda obj: obj.crop_season.crop_id)
        if crop_id:
            self.fields['crop_season'].queryset = CropSeason.objects.filter(
                crop_id=crop_id
            ).select_related('crop', 'field')

        crop = None
        if crop_id:
            try:
                crop = Crop.objects.get(pk=crop_id)
            except Crop.DoesNotExist:
                crop = None
        if crop:
            current = self.initial.get('unit') or (self.instance.unit if self.instance.pk else crop.default_unit)
            choices = crop.get_unit_choices()
            if current and current not in [c[0] for c in choices]:
                choices.append((current, current))
            self.fields['unit'] = forms.ChoiceField(
                choices=choices, required=False, initial=current, widget=forms.Select
            )

    class Meta:
        model = HarvestRecord
        fields = ['crop_season', 'harvest_date', 'harvest_type', 'quantity', 'unit',
                  'waste_name', 'waste_quantity', 'waste_value', 'quality_grade', 'moisture_content', 'notes']
        widgets = {
            'harvest_date': forms.DateInput(attrs={'type': 'date'}),
            'quantity': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'unit': forms.TextInput(attrs={'placeholder': 'kg, lbs, tons...'}),
            'waste_name': forms.TextInput(attrs={'placeholder': 'e.g., husk, bran'}),
            'waste_quantity': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'waste_value': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'quality_grade': forms.TextInput(attrs={'placeholder': 'e.g. Grade A, Premium, Standard'}),
            'moisture_content': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class ProcessingStageForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        crop_id = _scoped_crop_id(self, lambda obj: obj.harvest.crop_season.crop_id)
        if not crop_id:
            harvest_id = self.initial.get('harvest') or (self.instance.harvest_id if self.instance.pk else None)
            if harvest_id:
                try:
                    harvest = HarvestRecord.objects.select_related('crop_season__crop').get(pk=harvest_id)
                    crop_id = harvest.crop_season.crop_id
                except (HarvestRecord.DoesNotExist, ValueError, TypeError):
                    crop_id = None
        if crop_id:
            self.fields['harvest'].queryset = HarvestRecord.objects.filter(
                crop_season__crop_id=crop_id
            ).select_related('crop_season__crop')
        harvest_id = self.initial.get('harvest')
        if harvest_id and not self.initial.get('sequence'):
            last = ProcessingStage.objects.filter(
                harvest_id=harvest_id
            ).order_by('-sequence').first()
            self.initial['sequence'] = (last.sequence + 1) if last else 1

        crop = None
        if crop_id:
            try:
                crop = Crop.objects.get(pk=crop_id)
            except Crop.DoesNotExist:
                crop = None
        if crop:
            current = self.initial.get('output_unit') or (self.instance.output_unit if self.instance.pk else crop.secondary_unit or crop.default_unit)
            choices = crop.get_unit_choices()
            if current and current not in [c[0] for c in choices]:
                choices.append((current, current))
            self.fields['output_unit'] = forms.ChoiceField(
                choices=choices, required=False, initial=current, widget=forms.Select
            )

    class Meta:
        model = ProcessingStage
        fields = ['harvest', 'sequence', 'name', 'start_date', 'duration_days', 'cost',
                  'added_value', 'input_quantity', 'output_quantity', 'output_unit',
                  'waste_name', 'waste_quantity', 'waste_value', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Drying, Hulling, Packaging'}),
            'sequence': forms.NumberInput(attrs={'min': '1'}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'duration_days': forms.NumberInput(attrs={'min': '0'}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'added_value': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'input_quantity': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'output_quantity': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'output_unit': forms.TextInput(attrs={'placeholder': 'kg, bags, litres...'}),
            'waste_name': forms.TextInput(attrs={'placeholder': 'e.g., husk, bran'}),
            'waste_quantity': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'waste_value': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class HarvestRecordWasteForm(forms.ModelForm):
    class Meta:
        model = HarvestRecord
        fields = ['waste_name', 'waste_quantity', 'waste_value']
        widgets = {
            'waste_name': forms.TextInput(attrs={'placeholder': 'e.g., husk, bran'}),
            'waste_quantity': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'waste_value': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
        }
        labels = {
            'waste_name': 'Waste product name',
            'waste_quantity': 'Waste quantity',
            'waste_value': 'Waste value / loss',
        }


class ProcessingStageWasteForm(forms.ModelForm):
    class Meta:
        model = ProcessingStage
        fields = ['waste_name', 'waste_quantity', 'waste_value']
        widgets = {
            'waste_name': forms.TextInput(attrs={'placeholder': 'e.g., husk, bran'}),
            'waste_quantity': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'waste_value': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
        }
        labels = {
            'waste_name': 'Waste product name',
            'waste_quantity': 'Waste quantity',
            'waste_value': 'Waste value / loss',
        }


class SaleForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        crop_id = _scoped_crop_id(self, lambda obj: obj.harvest.crop_season.crop_id)
        if crop_id:
            self.fields['harvest'].queryset = HarvestRecord.objects.filter(
                crop_season__crop_id=crop_id
            ).select_related('crop_season__crop')

        harvest_id = self.initial.get('harvest') or (
            self.instance.harvest_id if self.instance and self.instance.pk else None
        )
        if not harvest_id:
            if crop_id:
                self.fields['processing_stage'].queryset = ProcessingStage.objects.filter(
                    harvest__crop_season__crop_id=crop_id
                ).select_related('harvest')
            return

        harvest = HarvestRecord.objects.filter(pk=harvest_id).first()
        stages = ProcessingStage.objects.filter(harvest_id=harvest_id).order_by('sequence')
        self.fields['processing_stage'].queryset = stages

        if self.instance and self.instance.pk:
            return

        # `raw=1` means the caller is explicitly selling unprocessed produce,
        # so do not fall back to a processing stage.
        # `waste=1` means the caller is explicitly selling waste material.
        sell_raw = str(self.initial.get('raw', '')).lower() in ('1', 'true', 'yes')
        sell_waste = str(self.initial.get('waste', '')).lower() in ('1', 'true', 'yes')

        # Respect an explicitly preselected processing stage (e.g. a waste sale
        # from a specific stage). For normal produce sales, default to the
        # latest stage; for raw/harvest waste sales, leave the stage empty.
        selected_stage_id = self.initial.get('processing_stage')
        if selected_stage_id:
            latest_stage = stages.filter(pk=selected_stage_id).first()
        else:
            latest_stage = None if (sell_raw or sell_waste) else stages.last()
        if latest_stage and not self.initial.get('processing_stage'):
            self.initial['processing_stage'] = latest_stage.pk

        if sell_waste and not self.instance.pk:
            self.initial['is_waste'] = True

        if not self.initial.get('quantity'):
            if sell_waste:
                if latest_stage:
                    self.initial['quantity'] = latest_stage.waste_quantity
                elif harvest:
                    self.initial['quantity'] = harvest.waste_quantity
            else:
                if latest_stage and latest_stage.output_quantity:
                    self.initial['quantity'] = latest_stage.output_quantity
                elif harvest:
                    self.initial['quantity'] = harvest.quantity_raw_remaining

        if sell_waste and not self.initial.get('unit_price'):
            if latest_stage:
                self.initial['unit_price'] = latest_stage.waste_value
            elif harvest:
                self.initial['unit_price'] = harvest.waste_value

        # Build unit dropdown from the related crop
        crop = None
        if harvest and harvest.crop_season and harvest.crop_season.crop:
            crop = harvest.crop_season.crop
        if not crop and crop_id:
            try:
                crop = Crop.objects.get(pk=crop_id)
            except Crop.DoesNotExist:
                crop = None
        if crop:
            unit = (latest_stage.output_unit if latest_stage else '') or (harvest.unit if harvest else '') or crop.default_unit
            current = self.initial.get('unit') or (self.instance.unit if self.instance.pk else '') or unit or crop.default_unit
            if unit and not self.initial.get('unit'):
                self.initial['unit'] = unit
            choices = crop.get_unit_choices()
            if current and current not in [c[0] for c in choices]:
                choices.append((current, current))
            self.fields['unit'] = forms.ChoiceField(
                choices=choices, required=False, initial=current, widget=forms.Select
            )
        else:
            if not self.initial.get('unit'):
                unit = (latest_stage.output_unit if latest_stage else '') or (harvest.unit if harvest else '')
                if unit:
                    self.initial['unit'] = unit

    class Meta:
        model = Sale
        fields = ['harvest', 'processing_stage', 'is_waste', 'sale_date', 'quantity', 'unit',
                  'unit_price', 'buyer', 'payment_method', 'notes']
        widgets = {
            'processing_stage': ProcessingStageSelect(),
            'is_waste': forms.CheckboxInput(),
            'sale_date': forms.DateInput(attrs={'type': 'date'}),
            'quantity': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'unit': forms.TextInput(attrs={'placeholder': 'kg, lbs, tons...'}),
            'unit_price': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'buyer': forms.TextInput(attrs={'placeholder': 'Buyer name'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


# -- Expenses --

class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'code', 'schedule_f_line', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Seed Purchases'}),
            'code': forms.TextInput(attrs={'placeholder': 'e.g. 100'}),
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Category description...'}),
        }


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense
        fields = ['farm_profile', 'budget_item', 'amount', 'date', 'vendor', 'notes']
        widgets = {
            'amount': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'date': forms.DateInput(attrs={'type': 'date'}),
            'vendor': forms.TextInput(attrs={'placeholder': 'Vendor / supplier name'}),
            'notes': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Notes...'}),
        }
        labels = {
            'farm_profile': 'Farm',
            'budget_item': 'Budget Item (required)',
        }

    def clean_budget_item(self):
        budget_item = self.cleaned_data.get('budget_item')
        if not budget_item:
            raise forms.ValidationError('Every expense must be attached to a budget item.')
        return budget_item

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.budget_item_id:
            instance.category = instance.budget_item.category
        if commit:
            instance.save()
        return instance


# -- Inventory --

class InventoryItemForm(forms.ModelForm):
    class Meta:
        model = InventoryItem
        fields = ['farm_profile', 'name', 'unit', 'quantity_on_hand', 'reorder_threshold',
                  'cost_per_unit', 'supplier']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Fertilizer NPK 15-15-15'}),
            'unit': forms.TextInput(attrs={'placeholder': 'kg, bags, liters...'}),
            'quantity_on_hand': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'reorder_threshold': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'cost_per_unit': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'supplier': forms.TextInput(attrs={'placeholder': 'Supplier name'}),
        }
        labels = {'farm_profile': 'Farm'}


class InventoryTransactionForm(forms.ModelForm):
    class Meta:
        model = InventoryTransaction
        fields = ['item', 'transaction_type', 'quantity', 'unit_cost', 'date',
                  'expense', 'crop_season', 'notes']
        widgets = {
            'quantity': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'unit_cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class InventoryAlertForm(forms.ModelForm):
    class Meta:
        model = InventoryAlert
        fields = ['item', 'message', 'is_resolved']
        widgets = {
            'message': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Alert details...'}),
        }


# -- Equipment --

class EquipmentForm(forms.ModelForm):
    class Meta:
        model = Equipment
        fields = ['farm_profile', 'name', 'equipment_type', 'purchase_date', 'purchase_cost',
                  'current_value', 'hours_used', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. John Deere 5075E'}),
            'purchase_date': forms.DateInput(attrs={'type': 'date'}),
            'purchase_cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'current_value': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'hours_used': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
        }
        labels = {'farm_profile': 'Farm'}


class MaintenanceLogForm(forms.ModelForm):
    class Meta:
        model = MaintenanceLog
        fields = ['equipment', 'date', 'description', 'cost', 'performed_by']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'What was done?'}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'performed_by': forms.TextInput(attrs={'placeholder': 'Technician / shop name'}),
        }


class EquipmentUsageForm(forms.ModelForm):
    class Meta:
        model = EquipmentUsage
        fields = ['equipment', 'crop_season', 'field', 'date', 'hours', 'description']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'hours': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'What was it used for?'}),
        }


# -- Labor --

class WorkerForm(forms.ModelForm):
    class Meta:
        model = Worker
        fields = ['farm_profile', 'user', 'name', 'hourly_rate', 'tax_id', 'phone', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Full name'}),
            'hourly_rate': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'tax_id': forms.TextInput(attrs={'placeholder': 'SSN / Tax ID'}),
            'phone': forms.TextInput(attrs={'placeholder': '+1 555-000-0000'}),
        }
        labels = {
            'farm_profile': 'Farm',
            'user': 'Linked User (optional)',
        }


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ['name', 'description', 'workers', 'crop_season', 'field',
                  'status', 'start_date', 'end_date']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Irrigate North Field'}),
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Task details...'}),
            'workers': forms.CheckboxSelectMultiple(),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }


class AttendanceRecordForm(forms.ModelForm):
    class Meta:
        model = AttendanceRecord
        fields = ['worker', 'task', 'date', 'hours', 'overtime_hours', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'hours': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'overtime_hours': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'notes': forms.Textarea(attrs={'rows': 2}),
        }


class PayrollRunForm(forms.ModelForm):
    class Meta:
        model = PayrollRun
        fields = ['farm_profile', 'start_date', 'end_date', 'status', 'total_amount']
        widgets = {
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
            'total_amount': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
        }
        labels = {'farm_profile': 'Farm'}


# -- Budgets --

class BudgetForm(forms.ModelForm):
    class Meta:
        model = Budget
        fields = ['farm_profile', 'name', 'start_date', 'end_date', 'status']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. 2024 Spring Planting Budget'}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'end_date': forms.DateInput(attrs={'type': 'date'}),
        }
        labels = {'farm_profile': 'Farm'}


class BudgetItemForm(forms.ModelForm):
    class Meta:
        model = BudgetItem
        fields = ['budget', 'category', 'crop_season', 'planned_amount', 'fund_amount', 'notes']
        widgets = {
            'planned_amount': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'fund_amount': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'notes': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Notes about this budget line...'}),
        }


# -- Integrations --

class BankAccountForm(forms.ModelForm):
    class Meta:
        model = BankAccount
        fields = ['farm_profile', 'name', 'bank_name', 'account_number_last4',
                  'routing_number', 'account_type', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Operating Account'}),
            'bank_name': forms.TextInput(attrs={'placeholder': 'e.g. First National Bank'}),
            'account_number_last4': forms.TextInput(attrs={'placeholder': 'Last 4 digits'}),
            'routing_number': forms.TextInput(attrs={'placeholder': 'Routing number'}),
            'account_type': forms.TextInput(attrs={'placeholder': 'checking / savings'}),
        }
        labels = {'farm_profile': 'Farm'}


class BankTransactionForm(forms.ModelForm):
    class Meta:
        model = BankTransaction
        fields = ['bank_account', 'date', 'description', 'amount', 'transaction_id',
                  'matched_expense', 'is_reconciled', 'is_flagged']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.TextInput(attrs={'placeholder': 'Transaction description'}),
            'amount': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'transaction_id': forms.TextInput(attrs={'placeholder': 'Bank transaction ID'}),
        }


# ============================================================================
# LIVESTOCK (Animal Rearing + Poultry)
# ============================================================================

class AnimalSpeciesForm(forms.ModelForm):
    class Meta:
        model = AnimalSpecies
        fields = ['name', 'species_type', 'breed_origin', 'average_weight_kg', 'description']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Holstein, Boer, Dorper'}),
            'breed_origin': forms.TextInput(attrs={'placeholder': 'e.g. Netherlands, South Africa'}),
            'average_weight_kg': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'description': forms.Textarea(attrs={'rows': 3}),
        }


class AnimalGroupForm(forms.ModelForm):
    class Meta:
        model = AnimalGroup
        fields = ['farm_profile', 'name', 'species', 'purpose', 'field', 'count',
                  'date_established', 'is_active', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. "Milking Herd", "Breeding Goats"'}),
            'date_established': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {'farm_profile': 'Farm'}


class AnimalRecordForm(forms.ModelForm):
    class Meta:
        model = AnimalRecord
        fields = ['group', 'farm_profile', 'tag_number', 'name', 'species', 'gender',
                  'birth_date', 'acquisition_date', 'acquisition_cost', 'current_weight_kg',
                  'health_status', 'parent_male', 'parent_female', 'is_sold', 'sale_date',
                  'sale_price', 'notes']
        widgets = {
            'tag_number': forms.TextInput(attrs={'placeholder': 'Ear tag / ID number'}),
            'name': forms.TextInput(attrs={'placeholder': 'Animal name (optional)'}),
            'birth_date': forms.DateInput(attrs={'type': 'date'}),
            'acquisition_date': forms.DateInput(attrs={'type': 'date'}),
            'acquisition_cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'current_weight_kg': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'sale_date': forms.DateInput(attrs={'type': 'date'}),
            'sale_price': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'farm_profile': 'Farm',
            'parent_male': 'Sire (father)',
            'parent_female': 'Dam (mother)',
        }


class AnimalHealthRecordForm(forms.ModelForm):
    class Meta:
        model = AnimalHealthRecord
        fields = ['animal', 'record_type', 'date', 'description', 'medication', 'dosage',
                  'cost', 'veterinarian', 'next_due_date']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3, 'placeholder': 'What was done?'}),
            'medication': forms.TextInput(attrs={'placeholder': 'e.g. Penicillin, Vaccine'}),
            'dosage': forms.TextInput(attrs={'placeholder': 'e.g. 10ml IM'}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'veterinarian': forms.TextInput(attrs={'placeholder': 'Vet name'}),
            'next_due_date': forms.DateInput(attrs={'type': 'date'}),
        }


class BreedingRecordForm(forms.ModelForm):
    class Meta:
        model = BreedingRecord
        fields = ['farm_profile', 'dam', 'sire', 'species', 'mating_date',
                  'expected_delivery_date', 'actual_delivery_date', 'status',
                  'offspring_count', 'offspring_alive', 'notes']
        widgets = {
            'mating_date': forms.DateInput(attrs={'type': 'date'}),
            'expected_delivery_date': forms.DateInput(attrs={'type': 'date'}),
            'actual_delivery_date': forms.DateInput(attrs={'type': 'date'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {
            'farm_profile': 'Farm',
            'dam': 'Dam (mother)',
            'sire': 'Sire (father)',
        }


class AnimalFeedLogForm(forms.ModelForm):
    class Meta:
        model = AnimalFeedLog
        fields = ['group', 'date', 'feed_type', 'quantity_kg', 'cost', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'feed_type': forms.TextInput(attrs={'placeholder': 'e.g. Hay, Grain Mix, Pasture'}),
            'quantity_kg': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class MilkProductionRecordForm(forms.ModelForm):
    class Meta:
        model = MilkProductionRecord
        fields = ['group', 'date', 'morning_liters', 'evening_liters', 'total_liters',
                  'fat_content_pct', 'price_per_liter', 'buyer', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'morning_liters': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'evening_liters': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'total_liters': forms.NumberInput(attrs={'step': '0.01', 'placeholder': 'Auto-calculated'}),
            'fat_content_pct': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'price_per_liter': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'buyer': forms.TextInput(attrs={'placeholder': 'Buyer name (optional)'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class PoultryBatchForm(forms.ModelForm):
    class Meta:
        model = PoultryBatch
        fields = ['farm_profile', 'batch_name', 'bird_type', 'purpose', 'breed',
                  'start_date', 'initial_count', 'current_count', 'mortality_count',
                  'sold_count', 'status', 'housing', 'notes']
        widgets = {
            'batch_name': forms.TextInput(attrs={'placeholder': 'e.g. "Layer Batch 2025-01"'}),
            'breed': forms.TextInput(attrs={'placeholder': 'e.g. Rhode Island Red, Cobb 500'}),
            'start_date': forms.DateInput(attrs={'type': 'date'}),
            'housing': forms.TextInput(attrs={'placeholder': 'Coop/pen name'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }
        labels = {'farm_profile': 'Farm'}


class EggProductionRecordForm(forms.ModelForm):
    class Meta:
        model = EggProductionRecord
        fields = ['batch', 'date', 'eggs_collected', 'damaged_eggs', 'saleable_eggs',
                  'price_per_egg', 'buyer', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'price_per_egg': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'buyer': forms.TextInput(attrs={'placeholder': 'Buyer name (optional)'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class PoultryFeedLogForm(forms.ModelForm):
    class Meta:
        model = PoultryFeedLog
        fields = ['batch', 'date', 'feed_type', 'quantity_kg', 'cost', 'notes']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'feed_type': forms.TextInput(attrs={'placeholder': 'e.g. Starter, Grower, Layer Mash'}),
            'quantity_kg': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'notes': forms.Textarea(attrs={'rows': 3}),
        }


class PoultryHealthRecordForm(forms.ModelForm):
    class Meta:
        model = PoultryHealthRecord
        fields = ['batch', 'record_type', 'date', 'description', 'medication',
                  'cost', 'birds_affected', 'next_due_date']
        widgets = {
            'date': forms.DateInput(attrs={'type': 'date'}),
            'description': forms.Textarea(attrs={'rows': 3}),
            'medication': forms.TextInput(attrs={'placeholder': 'e.g. Newcastle Vaccine'}),
            'cost': forms.NumberInput(attrs={'step': '0.01', 'placeholder': '0.00'}),
            'next_due_date': forms.DateInput(attrs={'type': 'date'}),
        }


# -- Registry: maps (app_label, model_name) -> Form class --

FORM_REGISTRY = {
    ('accounts', 'farmprofile'): FarmProfileForm,
    ('accounts', 'user'): UserForm,
    ('fields', 'field'): FieldForm,
    ('fields', 'landparcel'): LandParcelForm,
    ('crops', 'crop'): CropForm,
    ('crops', 'cropseason'): CropSeasonForm,
    ('crops', 'harvestrecord'): HarvestRecordForm,
    ('crops', 'processingstage'): ProcessingStageForm,
    ('crops', 'sale'): SaleForm,
    ('expenses', 'category'): CategoryForm,
    ('expenses', 'expense'): ExpenseForm,
    ('inventory', 'inventoryitem'): InventoryItemForm,
    ('inventory', 'inventorytransaction'): InventoryTransactionForm,
    ('inventory', 'inventoryalert'): InventoryAlertForm,
    ('equipment', 'equipment'): EquipmentForm,
    ('equipment', 'maintenancelog'): MaintenanceLogForm,
    ('equipment', 'equipmentusage'): EquipmentUsageForm,
    ('labor', 'worker'): WorkerForm,
    ('labor', 'task'): TaskForm,
    ('labor', 'attendancerecord'): AttendanceRecordForm,
    ('labor', 'payrollrun'): PayrollRunForm,
    ('budgets', 'budget'): BudgetForm,
    ('budgets', 'budgetitem'): BudgetItemForm,
    ('integrations', 'bankaccount'): BankAccountForm,
    ('integrations', 'banktransaction'): BankTransactionForm,
    # Livestock
    ('livestock', 'animalspecies'): AnimalSpeciesForm,
    ('livestock', 'animalgroup'): AnimalGroupForm,
    ('livestock', 'animalrecord'): AnimalRecordForm,
    ('livestock', 'animalhealthrecord'): AnimalHealthRecordForm,
    ('livestock', 'breedingrecord'): BreedingRecordForm,
    ('livestock', 'animalfeedlog'): AnimalFeedLogForm,
    ('livestock', 'milkproductionrecord'): MilkProductionRecordForm,
    ('livestock', 'poultrybatch'): PoultryBatchForm,
    ('livestock', 'eggproductionrecord'): EggProductionRecordForm,
    ('livestock', 'poultryfeedlog'): PoultryFeedLogForm,
    ('livestock', 'poultryhealthrecord'): PoultryHealthRecordForm,
}

WASTE_FORM_REGISTRY = {
    ('crops', 'harvestrecord'): HarvestRecordWasteForm,
    ('crops', 'processingstage'): ProcessingStageWasteForm,
}
