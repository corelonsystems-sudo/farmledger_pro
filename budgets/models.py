from django.db import models


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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

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
    planned_amount = models.DecimalField(max_digits=12, decimal_places=2)
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
        unique_together = ('budget', 'category')

    def __str__(self):
        return f'{self.budget.name} - {self.category.name} - ${self.planned_amount}'

    @property
    def spent_amount(self):
        from expenses.models import Expense
        return Expense.objects.filter(budget_item=self).aggregate(
            total=models.Sum('amount')
        )['total'] or 0

    @property
    def remaining(self):
        return self.planned_amount - self.spent_amount
