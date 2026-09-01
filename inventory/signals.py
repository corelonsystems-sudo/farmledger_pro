from decimal import Decimal

from django.core.mail import mail_managers
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import InventoryAlert, InventoryItem, InventoryTransaction


@receiver(post_save, sender=InventoryTransaction)
def handle_inventory_transaction(sender, instance, created, **kwargs):
    """Handle inventory transaction side effects.

    - PURCHASE: auto-creates a linked expense record and increases stock.
    - USAGE: decreases stock and sends low stock email + creates alert if below threshold.
    - ADJUSTMENT: directly sets stock to the adjustment value.
    """
    if not created:
        return

    item = instance.item

    if instance.transaction_type == InventoryTransaction.TransactionType.PURCHASE:
        item.quantity_on_hand += instance.quantity
        item.save(update_fields=['quantity_on_hand', 'updated_at'])

        from expenses.models import Category, Expense
        category, _ = Category.objects.get_or_create(
            name='Supplies',
            defaults={'schedule_f_line': 'part1_supplies'},
        )
        total_amount = instance.quantity * instance.unit_cost
        expense = Expense.objects.create(
            farm_profile=item.farm_profile,
            category=category,
            amount=total_amount,
            date=instance.date,
            vendor=item.supplier,
            payment_method=Expense.PaymentMethod.OTHER,
            notes=f'Auto-created from inventory purchase: {item.name}',
            crop_season=instance.crop_season,
        )
        instance.expense = expense
        instance.save(update_fields=['expense'])

    elif instance.transaction_type == InventoryTransaction.TransactionType.USAGE:
        item.quantity_on_hand -= instance.quantity
        item.save(update_fields=['quantity_on_hand', 'updated_at'])

        if item.quantity_on_hand <= item.reorder_threshold:
            InventoryAlert.objects.create(
                item=item,
                message=f'Low stock alert: {item.name} is at {item.quantity_on_hand} {item.unit} '
                        f'(reorder threshold: {item.reorder_threshold} {item.unit})',
            )
            subject = f'Low Stock Alert: {item.name}'
            message = (
                f'Item: {item.name}\n'
                f'Current stock: {item.quantity_on_hand} {item.unit}\n'
                f'Reorder threshold: {item.reorder_threshold} {item.unit}\n'
                f'Supplier: {item.supplier}\n'
            )
            try:
                mail_managers(subject, message)
            except Exception:
                pass

    elif instance.transaction_type == InventoryTransaction.TransactionType.ADJUSTMENT:
        item.quantity_on_hand = instance.quantity
        item.save(update_fields=['quantity_on_hand', 'updated_at'])
