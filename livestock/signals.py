from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import (AnimalFeedLog, AnimalHealthRecord, AnimalRecord,
                     PoultryFeedLog, PoultryHealthRecord)
from .services import (sync_animal_acquisition_expense,
                       sync_animal_feed_expense, sync_animal_health_expense,
                       sync_poultry_feed_expense, sync_poultry_health_expense)


@receiver(post_save, sender=AnimalHealthRecord)
def animal_health_saved(sender, instance, **kwargs):
    sync_animal_health_expense(instance)


@receiver(post_save, sender=AnimalFeedLog)
def animal_feed_saved(sender, instance, **kwargs):
    sync_animal_feed_expense(instance)


@receiver(post_save, sender=PoultryFeedLog)
def poultry_feed_saved(sender, instance, **kwargs):
    sync_poultry_feed_expense(instance)


@receiver(post_save, sender=PoultryHealthRecord)
def poultry_health_saved(sender, instance, **kwargs):
    sync_poultry_health_expense(instance)


@receiver(post_save, sender=AnimalRecord)
def animal_saved(sender, instance, **kwargs):
    sync_animal_acquisition_expense(instance)
