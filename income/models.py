import uuid

from django.db import models


class IncomeSource(models.Model):
    SCHEDULE_F_LINES = [
        ('part1_sales', 'Sales of livestock, produce, and other items'),
        ('part1_cooperatives', 'Cooperative distributions'),
        ('part1_agricultural_program', 'Agricultural program payments'),
        ('part1_crop_insurance', 'Crop insurance proceeds'),
        ('part1_custom_hire', 'Custom hire (machine work) income'),
        ('part1_other', 'Other farm income'),
    ]

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(
        max_length=20,
        unique=True,
        blank=True,
        default='',
        help_text='Short code identifying this income source (e.g. INC1, INC2).',
    )
    schedule_f_line = models.CharField(
        max_length=50,
        choices=SCHEDULE_F_LINES,
        default='part1_other',
    )
    description = models.TextField(blank=True, default='')

    class Meta:
        verbose_name = 'Income Source'
        verbose_name_plural = 'Income Sources'
        ordering = ['name']

    def __str__(self):
        return self.name


class Income(models.Model):
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
        related_name='incomes',
    )
    source = models.ForeignKey(
        IncomeSource,
        on_delete=models.PROTECT,
        related_name='incomes',
        null=True,
        blank=True,
    )
    crop_season = models.ForeignKey(
        'crops.CropSeason',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='incomes',
    )
    field = models.ForeignKey(
        'fields.Field',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='incomes',
    )
    equipment = models.ForeignKey(
        'equipment.Equipment',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='incomes',
    )
    budget_item = models.ForeignKey(
        'budgets.BudgetItem',
        on_delete=models.SET_NULL,
        related_name='incomes',
        help_text='Link income to a budget item for tracking.',
        null=True,
        blank=True,
    )
    sale = models.ForeignKey(
        'crops.Sale',
        on_delete=models.CASCADE,
        related_name='income_records',
        help_text='Auto-generated income linked to this sale; removed when the sale is deleted.',
        null=True,
        blank=True,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    customer = models.CharField(max_length=200, blank=True, default='')
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    receipt_image = models.ImageField(upload_to='income_receipts/', null=True, blank=True)
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
        source_name = self.source.name if self.source else 'No Source'
        if self.sale:
            return f'{source_name} - {self.amount} - {self.date} (Sale #{self.sale.id})'
        return f'{source_name} - {self.amount} - {self.date}'

    def clean(self):
        from django.core.exceptions import ValidationError
        errors = {}
        if self.amount is not None and self.amount < 0:
            errors['amount'] = 'Amount cannot be negative.'
        if (self.budget_item_id and self.farm_profile_id
                and self.budget_item.budget.farm_profile_id
                and self.budget_item.budget.farm_profile_id != self.farm_profile_id):
            errors['budget_item'] = 'The budget item must belong to the same farm.'
        if errors:
            raise ValidationError(errors)
