import uuid

from django.db import models


class ScheduleLine(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=150)
    subcode = models.ForeignKey(
        'BudgetSubCode', on_delete=models.PROTECT,
        null=True, blank=True, related_name='schedule_lines',
        verbose_name='Linked Subcode',
        help_text='Expenditure lines pick this schedule line under its subcode.')
    description = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['name', 'code']
        verbose_name = 'Budget Schedule Line'
        verbose_name_plural = 'Budget Schedule Lines'

    def __str__(self):
        return f'{self.code} - {self.name}'

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.pk and ScheduleLine.objects.filter(pk=self.pk).exclude(code=self.code).exists():
            raise ValidationError({'code': 'The identifier cannot be changed after creation.'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class Category(models.Model):
    SCHEDULE_F_LINES = [
        ('part1_cartruck', 'Car and truck expenses'),
        ('part1_chemicals', 'Chemicals'),
        ('part1_conservation', 'Conservation expenses'),
        ('part1_custom_hire', 'Custom hire (machine work)'),
        ('part1_depreciation', 'Depreciation'),
        ('part1_employee_benefits', 'Employee benefit programs'),
        ('part1_feed', 'Feed'),
        ('part1_fertilizer', 'Fertilizer and lime'),
        ('part1_freight_trucking', 'Freight and trucking'),
        ('part1_gasoline_fuel', 'Gasoline, fuel, and oil'),
        ('part1_insurance', 'Insurance (other than health)'),
        ('part1_interest', 'Interest'),
        ('part1_labor_hired', 'Labor hired (less employment credits)'),
        ('part1_pension', 'Pension and profit-sharing plans'),
        ('part1_rent_lease_equipment', 'Rent or lease vehicles, machinery, equipment'),
        ('part1_rent_lease_land', 'Rent or lease land, animals'),
        ('part1_repairs_maintenance', 'Repairs and maintenance'),
        ('part1_seeds_plants', 'Seeds and plants'),
        ('part1_storage_warehousing', 'Storage and warehousing'),
        ('part1_supplies', 'Supplies purchased'),
        ('part1_taxes', 'Taxes'),
        ('part1_utilities', 'Utilities'),
        ('part1_veterinary', 'Veterinary, breeding, and medicine'),
        ('part1_other', 'Other or unspecified'),
    ]

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        default='',
        help_text='Short code identifying this budget code (e.g. 100, 200).',
    )
    schedule_f_line = models.ForeignKey(
        ScheduleLine,
        to_field='code',
        db_column='schedule_f_line',
        on_delete=models.PROTECT,
        related_name='budget_codes',
        default='part1_other',
        verbose_name='Budget Schedule Line',
    )
    description = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Budget Code'
        verbose_name_plural = 'Budget Codes'

    def __str__(self):
        return self.name

    def get_schedule_f_line_display(self):
        return self.schedule_f_line.name


class BudgetSubCode(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name='subcodes',
        verbose_name='Budget Code',
    )
    name = models.CharField(max_length=100)
    code = models.CharField(
        max_length=20,
        blank=True,
        default='',
        help_text='Short subcode within the parent code (e.g. 100-A).',
    )
    description = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['category__name', 'code', 'name']
        verbose_name = 'Budget Subcode'
        verbose_name_plural = 'Budget Subcodes'
        constraints = [
            models.UniqueConstraint(
                fields=['category', 'name'], name='unique_subcode_name_per_code',
                violation_error_message='A subcode with this name already exists for this budget code.'),
            models.UniqueConstraint(
                fields=['category', 'code'], condition=~models.Q(code=''),
                name='unique_subcode_code_per_code',
                violation_error_message='A subcode with this code already exists for this budget code.'),
        ]

    def __str__(self):
        label = f'{self.code} - {self.name}' if self.code else self.name
        return f'{self.category.name} / {label}'


class Expense(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Cash'
        CHECK = 'CHECK', 'Check'
        CARD = 'CARD', 'Card'
        BANK_TRANSFER = 'BANK_TRANSFER', 'Bank Transfer'
        OTHER = 'OTHER', 'Other'

    class ReconciliationStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        MATCHED = 'MATCHED', 'Matched'
        RECONCILED = 'RECONCILED', 'Reconciled'
        FLAGGED = 'FLAGGED', 'Flagged'

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='expenses',
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name='expenses',
    )
    crop_season = models.ForeignKey(
        'crops.CropSeason',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses',
    )
    field = models.ForeignKey(
        'fields.Field',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses',
    )
    equipment = models.ForeignKey(
        'equipment.Equipment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses',
    )
    budget_item = models.ForeignKey(
        'budgets.BudgetItem',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='expenses',
        help_text='Every expense must be linked to a budget item.',
    )
    schedule_line = models.ForeignKey(
        ScheduleLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='expenses',
        verbose_name='Schedule line',
        help_text='Optional line item under the budget item subcode.',
    )
    processing_stage = models.OneToOneField(
        'crops.ProcessingStage',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='expense',
        help_text='Auto-generated expense for this processing stage cost.',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    vendor = models.CharField(max_length=200, blank=True, default='')
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    receipt_image = models.ImageField(upload_to='receipts/', null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    reconciliation_status = models.CharField(
        max_length=20,
        choices=ReconciliationStatus.choices,
        default=ReconciliationStatus.PENDING,
    )
    offline_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.category.name} - {self.amount} - {self.date}'

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        if not self.budget_item_id and not self.processing_stage_id:
            errors['budget_item'] = 'Every expense must be linked to a budget item.'
        if self.amount is not None and self.amount < 0:
            errors['amount'] = 'Amount cannot be negative.'
        if (self.budget_item_id and self.farm_profile_id
                and self.budget_item.budget.farm_profile_id
                and self.budget_item.budget.farm_profile_id != self.farm_profile_id):
            errors['budget_item'] = 'The budget item must belong to the same farm.'
        if (self.schedule_line_id and self.budget_item_id
                and self.budget_item.subcode_id
                and self.schedule_line.subcode_id != self.budget_item.subcode_id):
            errors['schedule_line'] = 'This schedule line belongs to a different subcode than the budget item.'
        if errors:
            raise ValidationError(errors)
