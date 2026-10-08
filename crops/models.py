from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F


def convert_quantity(quantity, from_unit, to_unit, crop):
    """Convert a quantity between a crop's primary/secondary unit pair."""
    if not from_unit or not to_unit or from_unit == to_unit:
        return quantity
    if crop and crop.secondary_unit and crop.secondary_per_primary:
        if from_unit == crop.secondary_unit and to_unit == crop.default_unit:
            return quantity * crop.secondary_per_primary
        if from_unit == crop.default_unit and to_unit == crop.secondary_unit:
            return quantity / crop.secondary_per_primary
    raise ValidationError({
        'unit': f"Cannot convert '{from_unit}' to '{to_unit}'. "
                'Configure matching primary/secondary units on the crop.',
    })


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
        on_delete=models.PROTECT,
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
    expected_yield = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        help_text='Expected harvest quantity in the crop primary unit (for planned vs actual yield).')
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
        crop_name = self.crop.name if self.crop_id else 'Unspecified crop'
        if self.is_perennial_season and self.season_label:
            return f'{crop_name} - {self.season_label} ({self.planting_date.year})'
        return f'{crop_name} - {self.field.name} ({self.planting_date.year})'

    def clean(self):
        errors = {}
        if self.expected_harvest_date and self.expected_harvest_date < self.planting_date:
            errors['expected_harvest_date'] = 'Expected harvest cannot be before the planting date.'
        if self.actual_harvest_date and self.actual_harvest_date < self.planting_date:
            errors['actual_harvest_date'] = 'Actual harvest cannot be before the planting date.'
        if not self.crop_id and self.status != self.Status.PLANNED:
            errors['crop'] = 'A crop is required once the season leaves the Planned stage.'
        if self.expected_yield is not None and self.expected_yield < 0:
            errors['expected_yield'] = 'Expected yield cannot be negative.'
        if (self.field_id and self.planting_date and not self.is_perennial_season
                and self.status != self.Status.CANCELLED):
            # Perennial cycles legitimately overlap on the same field.
            from datetime import date as _date
            mine_end = self.actual_harvest_date or self.expected_harvest_date or _date.max
            overlapping = CropSeason.objects.filter(
                field_id=self.field_id, is_perennial_season=False,
            ).exclude(pk=self.pk).exclude(status=self.Status.CANCELLED)
            for other in overlapping:
                other_end = other.actual_harvest_date or other.expected_harvest_date or _date.max
                if self.planting_date <= other_end and other.planting_date <= mine_end:
                    errors['planting_date'] = f'This season overlaps with "{other}" on the same field.'
                    break
        if errors:
            raise ValidationError(errors)

    @property
    def yield_per_acre(self):
        if self.field_id and self.field.acreage:
            return self.total_harvest_quantity / self.field.acreage
        return None

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
        on_delete=models.PROTECT,
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

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0), name='harvest_quantity_positive'),
            models.CheckConstraint(
                check=models.Q(waste_quantity__gte=0), name='harvest_waste_qty_nonnegative'),
            models.CheckConstraint(
                check=models.Q(waste_value__gte=0), name='harvest_waste_value_nonnegative'),
            models.CheckConstraint(
                check=models.Q(moisture_content__gte=0) & models.Q(moisture_content__lte=100),
                name='harvest_moisture_0_100'),
        ]

    def __str__(self):
        crop = self.crop_season.crop
        crop_name = crop.name if crop else 'Unspecified crop'
        return f'{crop_name} harvest - {self.harvest_date}'

    def clean(self):
        errors = {}
        if self.crop_season_id and self.crop_season.planting_date \
                and self.harvest_date < self.crop_season.planting_date:
            errors['harvest_date'] = (
                'Harvest date cannot be before the season planting date.')
        crop = self.crop_season.crop if self.crop_season_id else None
        if crop and self.unit:
            allowed = {unit for unit, _ in crop.get_unit_choices()}
            if allowed and self.unit not in allowed:
                errors['unit'] = (
                    f"Unit '{self.unit}' is not configured for {crop.name}. "
                    f"Use: {', '.join(sorted(allowed))}.")
        if errors:
            raise ValidationError(errors)

    @property
    def total_revenue(self):
        return sum((sale.total_amount for sale in self.sales.all()), Decimal('0'))

    @property
    def quantity_sold(self):
        return sum(
            (sale.quantity_in_unit(self.unit) for sale in self.sales.all() if not sale.is_waste),
            Decimal('0'),
        )

    @property
    def quantity_unsold(self):
        return self.quantity - self.quantity_sold

    @property
    def quantity_sold_raw(self):
        """Quantity sold straight off the harvest, with no processing stage attached."""
        return sum(
            (sale.quantity_in_unit(self.unit) for sale in self.sales.all()
             if sale.processing_stage_id is None and not sale.is_waste),
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
        on_delete=models.PROTECT,
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
        constraints = [
            models.UniqueConstraint(
                fields=['harvest', 'sequence'], name='unique_stage_sequence_per_harvest'),
            models.CheckConstraint(
                check=models.Q(cost__gte=0), name='stage_cost_nonnegative'),
            models.CheckConstraint(
                check=models.Q(added_value__gte=0), name='stage_added_value_nonnegative'),
            models.CheckConstraint(
                check=models.Q(input_quantity__gte=0), name='stage_input_qty_nonnegative'),
            models.CheckConstraint(
                check=models.Q(output_quantity__isnull=True) | models.Q(output_quantity__gte=0),
                name='stage_output_qty_nonnegative'),
            models.CheckConstraint(
                check=models.Q(waste_quantity__gte=0), name='stage_waste_qty_nonnegative'),
            models.CheckConstraint(
                check=models.Q(waste_value__gte=0), name='stage_waste_value_nonnegative'),
        ]

    def __str__(self):
        return f'{self.name} - {self.harvest}'

    @property
    def end_date(self):
        if self.start_date and self.duration_days:
            from datetime import timedelta
            return self.start_date + timedelta(days=self.duration_days)
        return None

    def clean(self):
        errors = {}
        if self.sequence is not None and self.sequence < 1:
            errors['sequence'] = 'Sequence must be 1 or higher.'
        if (self.start_date and self.harvest_id and self.harvest.harvest_date
                and self.start_date < self.harvest.harvest_date):
            errors['start_date'] = 'Processing cannot start before the harvest date.'
        if self.harvest_id and self.input_quantity:
            # Stage input draws from the raw harvest (first stage) or the
            # previous stage's output, less whatever was already sold from
            # that source - it cannot exceed the remaining balance.
            harvest = self.harvest
            crop = harvest.crop_season.crop if harvest.crop_season_id else None
            previous = (ProcessingStage.objects
                        .filter(harvest_id=self.harvest_id, sequence__lt=self.sequence or 1)
                        .exclude(pk=self.pk).order_by('-sequence').first())
            if previous is None:
                source_qty = harvest.quantity
                source_unit = harvest.unit
                sold_qs = harvest.sales.filter(is_waste=False, processing_stage__isnull=True)
                source_label = 'the raw harvest'
            else:
                source_qty = previous.output_quantity
                source_unit = previous.output_unit or harvest.unit
                sold_qs = previous.sales.filter(is_waste=False)
                source_label = f'stage {previous.sequence}. {previous.name}'
            if source_qty is not None:
                consumed = Decimal('0')
                for s in sold_qs:
                    try:
                        consumed += convert_quantity(s.quantity, s.unit, source_unit, crop)
                    except ValidationError:
                        consumed += s.quantity
                available = source_qty - consumed
                if self.input_quantity > available:
                    errors['input_quantity'] = (
                        f'Input exceeds the {available} {source_unit} available '
                        f'from {source_label} ({consumed} already sold).')
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)


class Customer(models.Model):
    """A farm-scoped customer catalog so sales group by a consistent buyer name."""

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='customers',
    )
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=40, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(
                fields=['farm_profile', 'name'], name='unique_customer_name_per_farm'),
        ]
        verbose_name = 'Customer'

    def __str__(self):
        return self.name


class Sale(models.Model):
    class PaymentMethod(models.TextChoices):
        CASH = 'CASH', 'Cash'
        BANK = 'BANK', 'Bank Transfer'
        MOBILE = 'MOBILE', 'Mobile Money'
        CREDIT = 'CREDIT', 'Credit'
        OTHER = 'OTHER', 'Other'

    class PaymentStatus(models.TextChoices):
        PAID = 'PAID', 'Paid'
        PARTIAL = 'PARTIAL', 'Partially paid'
        UNPAID = 'UNPAID', 'Unpaid'

    harvest = models.ForeignKey(
        HarvestRecord,
        on_delete=models.PROTECT,
        related_name='sales',
        null=True,
        blank=True,
        help_text='Required for produce/waste sales; leave empty for inventory-only sales.',
    )
    processing_stage = models.ForeignKey(
        ProcessingStage,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sales',
        help_text='Set if the produce sold came out of a specific processing stage.',
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='sales',
        help_text='Pick a registered customer to fill the buyer name consistently.',
    )
    inventory_item = models.ForeignKey(
        'inventory.InventoryItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sales',
        help_text='Set if the sale is for a general inventory item (not crop-specific).',
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
    payment_status = models.CharField(
        max_length=10,
        choices=PaymentStatus.choices,
        default=PaymentStatus.PAID,
        help_text='Track whether the buyer has paid, for receivables reporting.',
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
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0), name='sale_quantity_positive'),
            models.CheckConstraint(
                check=models.Q(unit_price__gte=0), name='sale_unit_price_nonnegative'),
        ]

    def __str__(self):
        return f'Sale {self.quantity}{self.unit} - {self.sale_date}'

    @property
    def total_amount(self):
        return self.quantity * self.unit_price

    def clean(self):
        errors = {}
        if self.customer_id:
            self.buyer = self.customer.name
        if not self.harvest_id and not self.inventory_item_id:
            errors['harvest'] = 'A sale must reference a harvest or an inventory item.'
        if self.quantity is not None and self.quantity <= 0:
            errors['quantity'] = 'Quantity must be greater than zero.'
        if self.unit_price is not None and self.unit_price < 0:
            errors['unit_price'] = 'Unit price cannot be negative.'
        if (self.sale_date and self.harvest_id and self.harvest.harvest_date
                and self.sale_date < self.harvest.harvest_date):
            errors['sale_date'] = 'Sale date cannot be before the harvest date.'
        if (self.processing_stage_id and self.harvest_id
                and self.processing_stage.harvest_id != self.harvest_id):
            errors['processing_stage'] = (
                'The processing stage must belong to the selected harvest.')
        if self.inventory_item_id:
            if self.processing_stage_id:
                errors['processing_stage'] = (
                    'An inventory sale cannot also draw from a processing stage.')
            if self.is_waste:
                errors['is_waste'] = 'Inventory items do not have a waste source.'
        if errors:
            raise ValidationError(errors)
        self._check_availability()

    def _sale_crop(self):
        if self.harvest_id and self.harvest.crop_season_id:
            return self.harvest.crop_season.crop
        return None

    def _convert_to_unit(self, quantity, from_unit, to_unit):
        """Convert a quantity between units using the crop's configured
        primary/secondary unit pair."""
        return convert_quantity(quantity, from_unit, to_unit, self._sale_crop())

    def quantity_in_unit(self, target_unit):
        """This sale's quantity expressed in ``target_unit``.

        Falls back to the raw quantity when the unit pair cannot be
        converted (e.g. no crop configured), so displays never break.
        """
        try:
            return convert_quantity(self.quantity, self.unit, target_unit, self._sale_crop())
        except ValidationError:
            return self.quantity

    def _sold_against(self, queryset, source_unit):
        """Total quantity already sold from a source, normalized to source_unit."""
        return sum(
            (self._convert_to_unit(s.quantity, s.unit, source_unit)
             for s in queryset.exclude(pk=self.pk)),
            Decimal('0'),
        )

    def _check_availability(self):
        if not self.quantity or self.quantity <= 0:
            return

        if self.inventory_item_id:
            # Inventory sales decrement quantity_on_hand at save time, so the
            # stored stock is the source of truth. When updating, release this
            # sale's previous quantity before comparing.
            item = self.inventory_item
            if self.unit != item.unit:
                raise ValidationError({
                    'unit': f"Sale unit '{self.unit}' does not match the "
                            f"inventory unit '{item.unit}'."})
            remaining = item.quantity_on_hand
            if self.pk:
                old = Sale.objects.filter(pk=self.pk).values(
                    'quantity', 'inventory_item_id').first()
                if old and old['inventory_item_id'] == item.pk:
                    remaining += old['quantity']
            if self.quantity > remaining:
                raise ValidationError({
                    'quantity': f'Cannot sell {self.quantity}{self.unit}. Only '
                                f'{remaining}{item.unit} available in inventory '
                                f'for {item.name}.'})
            return

        if self.processing_stage_id:
            stage = self.processing_stage
            source_unit = stage.output_unit or (self.harvest.unit if self.harvest_id else self.unit)
            if self.is_waste:
                available = stage.waste_quantity or Decimal('0')
                sold = self._sold_against(stage.sales.filter(is_waste=True), source_unit)
            else:
                available = stage.output_quantity or Decimal('0')
                sold = self._sold_against(stage.sales.filter(is_waste=False), source_unit)
            requested = self._convert_to_unit(self.quantity, self.unit, source_unit)
            if requested > available - sold:
                raise ValidationError({
                    'quantity': f'Cannot sell {requested}{source_unit}. Only '
                                f'{available - sold}{source_unit} available from {stage.name}.'})
            return

        if self.harvest_id:
            harvest = self.harvest
            if self.is_waste:
                available = harvest.waste_quantity or Decimal('0')
                sold = self._sold_against(
                    harvest.sales.filter(is_waste=True, processing_stage__isnull=True),
                    harvest.unit)
            else:
                first_stage = harvest.processing_stages.order_by('sequence').first()
                processed = first_stage.input_quantity if first_stage else Decimal('0')
                available = harvest.quantity - processed
                sold = self._sold_against(
                    harvest.sales.filter(is_waste=False, processing_stage__isnull=True),
                    harvest.unit)
            requested = self._convert_to_unit(self.quantity, self.unit, harvest.unit)
            if requested > available - sold:
                raise ValidationError({
                    'quantity': f'Cannot sell {requested}{harvest.unit}. Only '
                                f'{available - sold}{harvest.unit} available from harvest.'})

    def save(self, *args, **kwargs):
        # Atomic, locked save: re-validates availability and applies inventory
        # stock changes so concurrent sales cannot oversell a source.
        with transaction.atomic():
            old = None
            if self.pk:
                old = Sale.objects.select_for_update().filter(pk=self.pk).first()
            if self.harvest_id:
                HarvestRecord.objects.select_for_update().filter(pk=self.harvest_id).exists()
            if self.inventory_item_id:
                from inventory.models import InventoryItem
                self.inventory_item = InventoryItem.objects.select_for_update().get(
                    pk=self.inventory_item_id)
            self.clean()
            self._apply_inventory_delta(old)
            super().save(*args, **kwargs)

    def _apply_inventory_delta(self, old):
        if not self.inventory_item_id and not (old and old.inventory_item_id):
            return
        from inventory.models import InventoryItem
        if old and old.inventory_item_id:
            # Release the quantity previously deducted (old item, in case the
            # inventory item changed on update).
            InventoryItem.objects.filter(pk=old.inventory_item_id).update(
                quantity_on_hand=F('quantity_on_hand') + old.quantity)
        if self.inventory_item_id:
            updated = InventoryItem.objects.filter(
                pk=self.inventory_item_id,
                quantity_on_hand__gte=self.quantity,
            ).update(quantity_on_hand=F('quantity_on_hand') - self.quantity)
            if not updated:
                raise ValidationError({
                    'quantity': f'Insufficient stock for {self.inventory_item.name}.'})
            self.inventory_item.refresh_from_db(fields=['quantity_on_hand'])

    def delete(self, *args, **kwargs):
        # Restore inventory stock, then delete. Linked auto-generated income is
        # removed by the CASCADE on Income.sale.
        with transaction.atomic():
            if self.inventory_item_id:
                from inventory.models import InventoryItem
                InventoryItem.objects.filter(pk=self.inventory_item_id).update(
                    quantity_on_hand=F('quantity_on_hand') + self.quantity)
            return super().delete(*args, **kwargs)
