from decimal import Decimal

from django.db import models


class Equipment(models.Model):
    class EquipmentType(models.TextChoices):
        TRACTOR = 'TRACTOR', 'Tractor'
        HARVESTER = 'HARVESTER', 'Harvester'
        TRUCK = 'TRUCK', 'Truck'
        IRRIGATION = 'IRRIGATION', 'Irrigation'
        SPRAYER = 'SPRAYER', 'Sprayer'
        OTHER = 'OTHER', 'Other'

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='equipment',
    )
    name = models.CharField(max_length=200)
    equipment_type = models.CharField(
        max_length=20,
        choices=EquipmentType.choices,
        default=EquipmentType.OTHER,
    )
    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    current_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    hours_used = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    @property
    def total_maintenance_cost(self):
        total = self.maintenance_logs.aggregate(
            total=models.Sum('cost')
        )['total']
        return Decimal(total or 0)

    @property
    def depreciation(self):
        return self.purchase_cost - self.current_value

    @property
    def cost_per_hour(self):
        """Cost per hour = (total maintenance cost + depreciation) / total hours used."""
        if self.hours_used and self.hours_used > 0:
            return (self.total_maintenance_cost + self.depreciation) / self.hours_used
        return Decimal('0')


class MaintenanceLog(models.Model):
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        related_name='maintenance_logs',
    )
    date = models.DateField()
    description = models.TextField()
    cost = models.DecimalField(max_digits=12, decimal_places=2)
    performed_by = models.CharField(max_length=200, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.equipment.name} - {self.date} - {self.cost}'


class EquipmentUsage(models.Model):
    equipment = models.ForeignKey(
        Equipment,
        on_delete=models.CASCADE,
        related_name='usage_records',
    )
    crop_season = models.ForeignKey(
        'crops.CropSeason',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='equipment_usage',
    )
    field = models.ForeignKey(
        'fields.Field',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='equipment_usage',
    )
    date = models.DateField()
    hours = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.equipment.name} - {self.date} - {self.hours}h'
