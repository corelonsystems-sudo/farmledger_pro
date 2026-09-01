from decimal import Decimal

from django.db import models


class Crop(models.Model):
    class CropCategory(models.TextChoices):
        GRAIN = 'GRAIN', 'Grain'
        OILSEED = 'OILSEED', 'Oilseed'
        FORAGE = 'FORAGE', 'Forage'
        VEGETABLE = 'VEGETABLE', 'Vegetable'
        FRUIT = 'FRUIT', 'Fruit'
        ROOT = 'ROOT', 'Root/Tuber'
        LEGUME = 'LEGUME', 'Legume'
        BEVERAGE = 'BEVERAGE', 'Beverage (Coffee/Tea)'
        OTHER = 'OTHER', 'Other'

    name = models.CharField(max_length=100, unique=True)
    scientific_name = models.CharField(max_length=200, blank=True, default='')
    category = models.CharField(
        max_length=20,
        choices=CropCategory.choices,
        default=CropCategory.OTHER,
    )
    default_unit = models.CharField(max_length=20, default='kg')
    secondary_unit = models.CharField(max_length=20, blank=True, default='')
    secondary_per_primary = models.DecimalField(
        max_digits=14,
        decimal_places=4,
        default=1,
        blank=True,
        help_text='How many primary units make up one secondary unit (e.g., 100 if 1 bag = 100 kg).',
    )
    growing_season_days = models.PositiveIntegerField(default=0, blank=True)
    is_perennial = models.BooleanField(
        default=False,
        help_text='Perennial crops are planted once and harvested seasonally over many years (e.g. coffee, tea, fruit trees).',
    )
    perennial_lifespan_years = models.PositiveIntegerField(
        default=0, blank=True,
        help_text='Expected productive lifespan in years (for perennial crops).',
    )
    harvest_frequency = models.CharField(
        max_length=50, blank=True, default='',
        help_text='How often it is harvested, e.g. "Annual", "Biannual", "Every 3 months".',
    )
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_unit_choices(self):
        choices = [(self.default_unit, self.default_unit)] if self.default_unit else []
        if self.secondary_unit and self.secondary_unit != self.default_unit:
            choices.append((self.secondary_unit, self.secondary_unit))
        return choices


class CropSeason(models.Model):
    class Status(models.TextChoices):
        PLANNED = 'PLANNED', 'Planned'
        PLANTED = 'PLANTED', 'Planted'
        GROWING = 'GROWING', 'Growing'
        FLOWERING = 'FLOWERING', 'Flowering'
        HARVESTED = 'HARVESTED', 'Harvested'
        DORMANT = 'DORMANT', 'Dormant'
        CANCELLED = 'CANCELLED', 'Cancelled'

    field = models.ForeignKey(
        'fields.Field',
        on_delete=models.CASCADE,
        related_name='crop_seasons',
    )
    crop = models.ForeignKey(
        Crop,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='seasons',
    )
    variety = models.CharField(max_length=100, blank=True, default='')
    planting_date = models.DateField()
    expected_harvest_date = models.DateField(null=True, blank=True)
    actual_harvest_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
    )
    # Perennial-specific fields
    is_perennial_season = models.BooleanField(
        default=False,
        help_text='Check if this is a seasonal harvest cycle of a perennial crop (e.g. coffee harvest season).',
    )
    season_label = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Label for perennial harvest cycles, e.g. "2025 Main Harvest", "2025 Fly Crop".',
    )
    perennial_established_date = models.DateField(
        null=True, blank=True,
        help_text='When the perennial crop was originally planted (for multi-year crops).',
    )
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        if self.is_perennial_season and self.season_label:
            return f'{self.crop.name} - {self.season_label} ({self.planting_date.year})'
        return f'{self.crop.name} - {self.field.name} ({self.planting_date.year})'

    @property
    def total_revenue(self):
        return sum(
            (harvest.total_revenue for harvest in self.harvests.all()),
            Decimal('0'),
        )

    @property
    def total_expenses(self):
        from expenses.models import Expense
        return Decimal(
            Expense.objects.filter(crop_season=self).aggregate(
                total=models.Sum('amount')
            )['total'] or 0
        )

    @property
    def net_profit(self):
        return self.total_revenue - self.total_expenses

    @property
    def total_harvest_quantity(self):
        return sum(
            (harvest.quantity for harvest in self.harvests.all()),
            Decimal('0'),
        )


class HarvestRecord(models.Model):
    class HarvestType(models.TextChoices):
        MAIN = 'MAIN', 'Main Harvest'
        SECONDARY = 'SECONDARY', 'Secondary Harvest'
        THINNING = 'THINNING', 'Thinning'
        GLEANING = 'GLEANING', 'Gleaning'

    crop_season = models.ForeignKey(
        CropSeason,
        on_delete=models.CASCADE,
        related_name='harvests',
    )
    harvest_date = models.DateField()
    harvest_type = models.CharField(
        max_length=20,
        choices=HarvestType.choices,
        default=HarvestType.MAIN,
    )
    quantity = models.DecimalField(max_digits=12, decimal_places=2, help_text='Quantity harvested (not sold).')
    unit = models.CharField(max_length=20, default='kg')
    waste_name = models.CharField(max_length=100, blank=True, default='', help_text='Name/type of waste produced (e.g., husk, bran).')
    waste_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), blank=True, help_text='Quantity of waste produced during harvest.')
    waste_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), blank=True, help_text='Estimated value or loss of the waste.')
    quality_grade = models.CharField(max_length=20, blank=True, default='', help_text='e.g. Grade A, Premium, Standard')
    moisture_content = models.DecimalField(max_digits=5, decimal_places=2, default=0, blank=True, help_text='Percentage moisture content')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.crop_season.crop.name} harvest - {self.harvest_date}'

    @property
    def total_revenue(self):
        return sum((sale.total_amount for sale in self.sales.all()), Decimal('0'))

    @property
    def quantity_sold(self):
        return sum((sale.quantity for sale in self.sales.all() if not sale.is_waste), Decimal('0'))

    @property
    def quantity_unsold(self):
        return self.quantity - self.quantity_sold

    @property
    def quantity_sold_raw(self):
        """Quantity sold straight off the harvest, with no processing stage attached."""
        return sum(
            (sale.quantity for sale in self.sales.all() if sale.processing_stage_id is None and not sale.is_waste),
            Decimal('0'),
        )

    @property
    def quantity_raw_remaining(self):
        """Harvested produce still in its raw form and not yet sold or sent to processing.

        Only the first processing stage draws from the raw harvest; subsequent
        stages draw from the previous stage's output, so we only subtract the
        first stage's input_quantity from the raw total.
        """
        first_stage = self.processing_stages.order_by('sequence').first()
        processed = first_stage.input_quantity if first_stage else Decimal('0')
        return self.quantity - self.quantity_sold_raw - processed

    @property
    def net_profit(self):
        from expenses.models import Expense
        total_expenses = Decimal(
            Expense.objects.filter(crop_season=self.crop_season).aggregate(
                total=models.Sum('amount')
            )['total'] or 0
        )
        return self.total_revenue - total_expenses


class ProcessingStage(models.Model):
    harvest = models.ForeignKey(
        HarvestRecord,
        on_delete=models.CASCADE,
        related_name='processing_stages',
    )
    name = models.CharField(max_length=100)
    sequence = models.PositiveIntegerField(default=1)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    added_value = models.DecimalField(
        max_digits=12, decimal_places=2, default=0,
        help_text='Additional value created at this stage.',
    )
    start_date = models.DateField(null=True, blank=True, help_text='Date processing for this stage begins.')
    duration_days = models.PositiveIntegerField(default=0, blank=True, help_text='Number of days the stage is expected to take.')
    input_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=0, blank=True, help_text='Quantity of raw produce used in this stage.')
    output_quantity = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    output_unit = models.CharField(max_length=20, blank=True, default='')
    waste_name = models.CharField(max_length=100, blank=True, default='', help_text='Name/type of waste produced (e.g., husk, bran).')
    waste_quantity = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), blank=True, help_text='Quantity of waste produced during processing.')
    waste_value = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0'), blank=True, help_text='Estimated value or loss of the waste.')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['harvest', 'sequence']

    def __str__(self):
        return f'{self.name} - {self.harvest}'

    @property
    def end_date(self):
        if self.start_date and self.duration_days:
            from datetime import timedelta
            return self.start_date + timedelta(days=self.duration_days)
        return None


class Sale(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Cash'
        BANK = 'BANK', 'Bank Transfer'
        MOBILE = 'MOBILE', 'Mobile Money'
        CREDIT = 'CREDIT', 'Credit'
        OTHER = 'OTHER', 'Other'

    harvest = models.ForeignKey(
        HarvestRecord,
        on_delete=models.CASCADE,
        related_name='sales',
    )
    processing_stage = models.ForeignKey(
        ProcessingStage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales',
        help_text='Set if the produce sold came out of a specific processing stage.',
    )
    sale_date = models.DateField()
    quantity = models.DecimalField(max_digits=12, decimal_places=2)
    unit = models.CharField(max_length=20, default='kg')
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    buyer = models.CharField(max_length=200, blank=True, default='')
    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    is_waste = models.BooleanField(
        default=False,
        help_text='Check if this sale is for waste material from the harvest or stage.',
    )
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-sale_date', '-id']

    def __str__(self):
        return f'Sale {self.quantity}{self.unit} - {self.sale_date}'

    @property
    def total_amount(self):
        return self.quantity * self.unit_price
