from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import HarvestRecord, ProcessingStage, Sale
from .services import sync_processing_expense, sync_sale_income


@receiver(post_save, sender=HarvestRecord)
def calculate_net_profit_on_harvest_save(sender, instance, created, **kwargs):
    """Update the crop season status when a harvest record is created."""
    crop_season = instance.crop_season

    if created and crop_season.status not in (
            crop_season.Status.HARVESTED, crop_season.Status.CANCELLED):
        crop_season.status = crop_season.Status.HARVESTED
        crop_season.actual_harvest_date = instance.harvest_date
        crop_season.save(update_fields=['status', 'actual_harvest_date', 'updated_at'])


@receiver(post_save, sender=Sale)
def sync_income_from_sale(sender, instance, **kwargs):
    """Keep the auto-generated income record in sync with the sale.

    Runs on create and update; deletion is handled by the CASCADE on
    Income.sale, so no orphaned income is left behind.
    """
    sync_sale_income(instance)


@receiver(post_save, sender=ProcessingStage)
def sync_expense_from_stage(sender, instance, **kwargs):
    """Post the stage cost to the expense ledger.

    Runs on create and update; deletion is handled by the CASCADE on
    Expense.processing_stage.
    """
    sync_processing_expense(instance)
