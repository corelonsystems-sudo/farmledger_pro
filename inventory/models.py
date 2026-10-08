from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction


class InventoryItem(models.Model):
    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='inventory_items',
    )
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=20, default='unit')
    quantity_on_hand = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    reorder_threshold = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cost_per_unit = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    supplier = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def is_low_stock(self):
        return self.quantity_on_hand <= self.reorder_threshold

    def can_sell_quantity(self, quantity):
        """Check if enough quantity is available for sale."""
        return self.quantity_on_hand >= quantity


class InventoryTransaction(models.Model):
    class TransactionType(models.TextChoices):
        PURCHASE = 'PURCHASE', 'Purchase'
        USAGE = 'USAGE', 'Usage'
        ADJUSTMENT = 'ADJUSTMENT', 'Adjustment'

    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.PROTECT,
        related_name='transactions',
    )
    transaction_type = models.CharField(
        max_length=20,
        choices=TransactionType.choices,
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    date = models.DateField()
    notes = models.TextField(blank=True, default='')
    expense = models.ForeignKey(
        'expenses.Expense',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_transactions',
    )
    budget_item = models.ForeignKey(
        'budgets.BudgetItem',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_transactions',
        help_text='Budget line a purchase is posted to; required for purchases.',
    )
    crop_season = models.ForeignKey(
        'crops.CropSeason',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_transactions',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gte=0), name='inv_txn_quantity_nonnegative'),
            models.CheckConstraint(
                check=models.Q(unit_cost__gte=0), name='inv_txn_unit_cost_nonnegative'),
        ]

    def __str__(self):
        return f'{self.get_transaction_type_display()} - {self.item.name} - {self.quantity}'

    def clean(self):
        errors = {}
        if self.transaction_type in (
                self.TransactionType.PURCHASE, self.TransactionType.USAGE):
            if not self.quantity or self.quantity <= 0:
                errors['quantity'] = 'Quantity must be greater than zero.'
        if self.unit_cost is not None and self.unit_cost < 0:
            errors['unit_cost'] = 'Unit cost cannot be negative.'
        if self.transaction_type == self.TransactionType.PURCHASE:
            if not self.budget_item_id:
                errors['budget_item'] = (
                    'Purchases must be linked to a budget item so the expense '
                    'can be posted to a budget line.')
            elif (self.item_id and self.budget_item.budget.farm_profile_id
                    and self.budget_item.budget.farm_profile_id != self.item.farm_profile_id):
                errors['budget_item'] = (
                    'The budget item must belong to the same farm as the inventory item.')
        if self.transaction_type == self.TransactionType.USAGE and self.item_id:
            if self.quantity and self.quantity > self.item.quantity_on_hand:
                errors['quantity'] = (
                    f'Only {self.item.quantity_on_hand} {self.item.unit} of '
                    f'{self.item.name} in stock.')
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        # Validate and post inside one transaction so a failed expense or
        # stock update rolls back the transaction row as well.
        with transaction.atomic():
            self.clean()
            super().save(*args, **kwargs)


class InventoryAlert(models.Model):
    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
        related_name='alerts',
    )
    message = models.TextField()
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'Alert: {self.item.name} - {self.created_at}'
