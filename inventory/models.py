from django.conf import settings
from django.db import models


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


class InventoryTransaction(models.Model):
    class TransactionType(models.TextChoices):
        PURCHASE = 'PURCHASE', 'Purchase'
        USAGE = 'USAGE', 'Usage'
        ADJUSTMENT = 'ADJUSTMENT', 'Adjustment'

    item = models.ForeignKey(
        InventoryItem,
        on_delete=models.CASCADE,
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
    crop_season = models.ForeignKey(
        'crops.CropSeason',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inventory_transactions',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.get_transaction_type_display()} - {self.item.name} - {self.quantity}'


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
