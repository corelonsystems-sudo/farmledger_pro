from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import HarvestRecord


@receiver(post_save, sender=HarvestRecord)
def calculate_net_profit_on_harvest_save(sender, instance, created, **kwargs):
    """Auto-calculate net profit by subtracting all linked expenses from total revenue.

    The net_profit property on HarvestRecord and CropSeason is calculated on the fly
    from the database. This signal ensures the crop season status is updated when
    a harvest record is saved, and logs the calculated profit.
    """
    crop_season = instance.crop_season
    net = instance.net_profit

    if created and crop_season.status == crop_season.Status.GROWING:
        crop_season.status = crop_season.Status.HARVESTED
        crop_season.actual_harvest_date = instance.harvest_date
        crop_season.save(update_fields=['status', 'actual_harvest_date', 'updated_at'])
