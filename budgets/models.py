from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction


class FigureType(models.TextChoices):
    RECORDED = 'RECORDED', 'Recorded'
    ESTIMATE = 'ESTIMATE', 'Estimate'
    UNCONFIRMED = 'UNCONFIRMED', 'Unconfirmed'


def save_coded_line(instance, prefix, *args, **kwargs):
    with transaction.atomic():
        Budget.objects.select_for_update().get(pk=instance.budget_id)
        if not instance.line_code:
            index = 1
            codes = set(type(instance).objects.filter(budget_id=instance.budget_id).values_list('line_code', flat=True))
            while f'{prefix}-{index:03d}' in codes:
                index += 1
            instance.line_code = f'{prefix}-{index:03d}'
            if kwargs.get('update_fields') is not None:
                kwargs['update_fields'] = set(kwargs['update_fields']) | {'line_code'}
        return models.Model.save(instance, *args, **kwargs)


class Budget(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        ACTIVE = 'ACTIVE', 'Active'
        CLOSED = 'CLOSED', 'Closed'

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='budgets',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=200, blank=True, default='')
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({'end_date': 'End date cannot be before the start date.'})
        if self.pk and self.farm_profile_id:
            from expenses.models import Expense
            if Expense.objects.filter(budget_item__budget_id=self.pk).exclude(farm_profile_id=self.farm_profile_id).exists():
                raise ValidationError({'farm_profile': 'A budget with posted expenses cannot be moved to another farm.'})

    def __str__(self):
        return self.name or f'Budget #{self.pk}'

    @property
    def total_planned(self):
        return sum(
            (item.planned_amount for item in self.items.all()),
            0,
        )

    @property
    def total_spent(self):
        from expenses.models import Expense
        item_ids = self.items.values_list('pk', flat=True)
        return Expense.objects.filter(budget_item__in=item_ids).aggregate(
            total=models.Sum('amount')
        )['total'] or 0

    @property
    def remaining(self):
        return self.total_planned - self.total_spent

    @property
    def total_income_planned(self):
        return sum((line.planned_amount for line in self.income_plans.all()), Decimal('0'))

    @property
    def funding_balance(self):
        return self.total_income_planned - self.total_planned


class BudgetItem(models.Model):
    budget = models.ForeignKey(
        Budget,
        on_delete=models.CASCADE,
        related_name='items',
    )
    category = models.ForeignKey(
        'expenses.Category',
        on_delete=models.PROTECT,
        related_name='budget_items',
    )
    crop_season = models.ForeignKey(
        'crops.CropSeason',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='budget_items',
    )
    line_code = models.CharField(max_length=50, blank=True, default='')
    cost_centre = models.CharField(max_length=100, default='General')
    subcode = models.ForeignKey(
        'expenses.BudgetSubCode', on_delete=models.PROTECT,
        null=True, blank=True, related_name='budget_items',
        verbose_name='Head / activity')
    head = models.CharField(max_length=150, blank=True, default='')
    schedule_line = models.ForeignKey(
        'expenses.ScheduleLine', on_delete=models.PROTECT,
        null=True, blank=True, related_name='budget_items',
        verbose_name='Line item')
    reference = models.CharField(max_length=20, blank=True, default='')
    line_name = models.CharField(max_length=200, blank=True, default='')
    figure_type = models.CharField(max_length=20, choices=FigureType.choices, default=FigureType.ESTIMATE)
    planned_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    fund_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text='Total money available for this code in this budget.',
    )
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['cost_centre', 'head', 'line_code', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['budget', 'line_code'], condition=~models.Q(line_code=''), name='unique_budget_line_code'),
            models.CheckConstraint(check=models.Q(planned_amount__gte=0), name='budget_line_amount_nonnegative'),
            models.CheckConstraint(check=~models.Q(figure_type='UNCONFIRMED') | models.Q(planned_amount=0), name='budget_unconfirmed_amount_zero'),
        ]

    def __str__(self):
        return f'{self.line_code or self.category.code} - {self.line_name or self.category.name} ({self.budget})'

    def clean(self):
        errors = {}
        if self.subcode_id:
            if self.category_id and self.subcode.category_id != self.category_id:
                errors['subcode'] = 'The subcode must belong to the selected budget code.'
            else:
                self.head = self.subcode.name
        if not self.schedule_line_id and self.subcode_id and not self.line_name:
            self.line_name = self.subcode.name
        if self.schedule_line_id:
            self.line_name = self.schedule_line.name
            if (self.schedule_line.subcode_id
                    and self.schedule_line.subcode_id != self.subcode_id):
                errors['schedule_line'] = 'This schedule line is linked to a different subcode.'
        elif not self.line_name:
            errors['subcode'] = 'Pick a subcode - its linked schedule lines become the line items automatically.'
        if self.planned_amount is not None and self.planned_amount < 0:
            errors['planned_amount'] = 'Planned amount cannot be negative.'
        if self.figure_type == FigureType.UNCONFIRMED and self.planned_amount:
            errors['planned_amount'] = 'Unconfirmed lines must have a blank or zero amount.'
        if self.budget_id and self.crop_season_id and self.budget.farm_profile_id != self.crop_season.field.farm_profile_id:
            errors['crop_season'] = 'The crop season must belong to the budget farm.'
        if self.pk:
            original = BudgetItem.objects.get(pk=self.pk)
            if (original.budget_id != self.budget_id or original.category_id != self.category_id) and original.expenses.exists():
                errors['category'] = 'The budget and budget code cannot be changed on a line with posted expenses.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.subcode_id:
            self.head = self.subcode.name
        if self.schedule_line_id:
            self.line_name = self.schedule_line.name
        return save_coded_line(self, 'EXP', *args, **kwargs)

    @property
    def spent_amount(self):
        from expenses.models import Expense
        return Expense.objects.filter(budget_item=self).aggregate(
            total=models.Sum('amount')
        )['total'] or 0

    @property
    def remaining(self):
        return self.planned_amount - self.spent_amount


class BudgetIncomePlan(models.Model):
    class FundingType(models.TextChoices):
        CAPITAL = 'CAPITAL', 'Capital'
        LOAN = 'LOAN', 'Loan'
        REVENUE = 'REVENUE', 'Revenue'

    budget = models.ForeignKey(Budget, on_delete=models.CASCADE, related_name='income_plans')
    line_code = models.CharField(max_length=50, blank=True, default='')
    funding_type = models.CharField(max_length=20, choices=FundingType.choices, default=FundingType.REVENUE)
    schedule_line = models.ForeignKey(
        'expenses.ScheduleLine', on_delete=models.PROTECT,
        null=True, blank=True, related_name='budget_income_plans')
    name = models.CharField(max_length=200, blank=True, default='')
    planned_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    figure_type = models.CharField(max_length=20, choices=FigureType.choices, default=FigureType.ESTIMATE)
    notes = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['funding_type', 'line_code', 'pk']
        verbose_name = 'Budget Income Source'
        verbose_name_plural = 'Budget Income Sources'
        constraints = [
            models.UniqueConstraint(fields=['budget', 'line_code'], condition=~models.Q(line_code=''), name='unique_budget_income_code'),
            models.CheckConstraint(check=models.Q(planned_amount__gte=0), name='budget_income_amount_nonnegative'),
            models.CheckConstraint(check=~models.Q(figure_type='UNCONFIRMED') | models.Q(planned_amount=0), name='budget_income_unconfirmed_zero'),
        ]

    def __str__(self):
        return f'{self.line_code} - {self.name}'

    def clean(self):
        if self.schedule_line_id:
            self.name = self.schedule_line.name
        if not self.name:
            raise ValidationError({'schedule_line': 'Pick the schedule line for this income item.'})
        if self.planned_amount is not None and self.planned_amount < 0:
            raise ValidationError({'planned_amount': 'Planned income cannot be negative.'})
        if self.figure_type == FigureType.UNCONFIRMED and self.planned_amount:
            raise ValidationError({'planned_amount': 'Unconfirmed sources must have a blank or zero amount.'})

    def save(self, *args, **kwargs):
        if self.schedule_line_id:
            self.name = self.schedule_line.name
        return save_coded_line(self, 'INC', *args, **kwargs)
