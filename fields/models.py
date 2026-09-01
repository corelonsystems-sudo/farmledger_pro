from django.conf import settings
from django.db import models


class Field(models.Model):
    class SoilType(models.TextChoices):
        CLAY = 'CLAY', 'Clay'
        LOAM = 'LOAM', 'Loam'
        SANDY = 'SANDY', 'Sandy'
        SILT = 'SILT', 'Silt'
        PEATY = 'PEATY', 'Peaty'
        CHALKY = 'CHALKY', 'Chalky'

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='fields',
    )
    name = models.CharField(max_length=200)
    acreage = models.DecimalField(max_digits=10, decimal_places=2)
    soil_type = models.CharField(
        max_length=20,
        choices=SoilType.choices,
        default=SoilType.LOAM,
    )
    gps_coordinates = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class LandParcel(models.Model):
    class LandType(models.TextChoices):
        OWNED = 'OWNED', 'Owned'
        LEASED = 'LEASED', 'Leased'
        RENTED = 'RENTED', 'Rented'

    field = models.ForeignKey(
        Field,
        on_delete=models.CASCADE,
        related_name='parcels',
    )
    land_type = models.CharField(
        max_length=20,
        choices=LandType.choices,
        default=LandType.OWNED,
    )
    lease_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    lease_start = models.DateField(null=True, blank=True)
    lease_end = models.DateField(null=True, blank=True)
    owner_name = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.field.name} - {self.get_land_type_display()}'
