from django.core.mail import mail_managers
from django.db.models import F
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import InventoryAlert, InventoryItem, InventoryTransaction


@receiver(post_save, sender=InventoryTransaction)
def handle_inventory_transaction(sender, instance, created, **kwargs):
    """Handle inventory transaction side effects.

    - PURCHASE: posts a linked expense on the selected budget item and
      increases stock.
    - USAGE: decreases stock and sends low stock email + creates alert if
      below threshold.
    - ADJUSTMENT: directly sets stock to the adjustment value.

    Everything runs inside the transaction started by
    ``InventoryTransaction.save()`` so a failed expense or stock write rolls
    back the whole posting. Updates are ignored to avoid double-posting.
    """
    if not created:
        return

    # Lock the item row so concurrent transactions serialize correctly.
    item = InventoryItem.objects.select_for_update().get(pk=instance.item_id)

    if instance.transaction_type == InventoryTransaction.TransactionType.PURCHASE:
        from expenses.models import Expense
        budget_item = instance.budget_item  # guaranteed present by clean()
        expense = Expense.objects.create(
            farm_profile=item.farm_profile,
            budget_item=budget_item,
            category=budget_item.category,
            amount=instance.quantity * instance.unit_cost,
            date=instance.date,
            vendor=item.supplier,
            payment_method=Expense.PaymentMethod.OTHER,
            notes=f'Auto-created from inventory purchase: {item.name}',
            crop_season=instance.crop_season,
        )
        item.quantity_on_hand = F('quantity_on_hand') + instance.quantity
        item.save(update_fields=['quantity_on_hand', 'updated_at'])
        InventoryTransaction.objects.filter(pk=instance.pk).update(expense=expense)
        instance.expense = expense

    elif instance.transaction_type == InventoryTransaction.TransactionType.USAGE:
        item.quantity_on_hand = F('quantity_on_hand') - instance.quantity
        item.save(update_fields=['quantity_on_hand', 'updated_at'])
        item.refresh_from_db(fields=['quantity_on_hand'])

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
