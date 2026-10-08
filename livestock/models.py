from django.db import models


# ============================================================================
# ANIMAL REARING (Cattle, Goats, Sheep, Pigs, etc.)
# ============================================================================

class AnimalSpecies(models.Model):
    """Species/breed registry: Cattle, Goats, Sheep, Pigs, etc."""
    class SpeciesType(models.TextChoices):
        CATTLE = 'CATTLE', 'Cattle'
        GOAT = 'GOAT', 'Goat'
        SHEEP = 'SHEEP', 'Sheep'
        PIG = 'PIG', 'Pig'
        HORSE = 'HORSE', 'Horse'
        DONKEY = 'DONKEY', 'Donkey'
        RABBIT = 'RABBIT', 'Rabbit'
        OTHER = 'OTHER', 'Other'

    name = models.CharField(max_length=100, unique=True, help_text='e.g. Holstein, Boer, Dorper')
    species_type = models.CharField(
        max_length=20,
        choices=SpeciesType.choices,
        default=SpeciesType.OTHER,
    )
    breed_origin = models.CharField(max_length=100, blank=True, default='')
    average_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, default=0, blank=True)
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Animal Species'

    def __str__(self):
        return f'{self.name} ({self.get_species_type_display()})'


class AnimalGroup(models.Model):
    """A herd/flock of animals tracked as a group (e.g. "Dairy Cows", "Breeding Goats")."""
    class Purpose(models.TextChoices):
        DAIRY = 'DAIRY', 'Dairy'
        BEEF = 'BEEF', 'Beef/Meat'
        BREEDING = 'BREEDING', 'Breeding'
        DRAFT = 'DRAFT', 'Draft/Work'
        FIBER = 'FIBER', 'Fiber/Wool'
        PET = 'PET', 'Pet'
        MIXED = 'MIXED', 'Mixed'

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='animal_groups',
    )
    name = models.CharField(max_length=200, help_text='e.g. "Milking Herd", "Breeding Goats"')
    species = models.ForeignKey(
        AnimalSpecies,
        on_delete=models.PROTECT,
        related_name='groups',
    )
    purpose = models.CharField(
        max_length=20,
        choices=Purpose.choices,
        default=Purpose.MIXED,
    )
    field = models.ForeignKey(
        'fields.Field',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='animal_groups',
        help_text='Grazing field/pen if applicable',
    )
    count = models.PositiveIntegerField(default=0, help_text='Number of animals in the group')
    date_established = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.name} ({self.count} {self.species.name})'


class AnimalRecord(models.Model):
    """Individual animal tracking (for valuable/breeding animals)."""
    class Gender(models.TextChoices):
        MALE = 'M', 'Male'
        FEMALE = 'F', 'Female'
        CASTRATED = 'C', 'Castrated'

    class HealthStatus(models.TextChoices):
        HEALTHY = 'HEALTHY', 'Healthy'
        SICK = 'SICK', 'Sick'
        TREATED = 'TREATED', 'Under Treatment'
        QUARANTINED = 'QUARANTINED', 'Quarantined'
        DECEASED = 'DECEASED', 'Deceased'

    group = models.ForeignKey(
        AnimalGroup,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='individual_animals',
    )
    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='animals',
    )
    tag_number = models.CharField(max_length=50, unique=True, help_text='Ear tag / ID number')
    name = models.CharField(max_length=100, blank=True, default='')
    species = models.ForeignKey(
        AnimalSpecies,
        on_delete=models.PROTECT,
        related_name='individuals',
    )
    gender = models.CharField(max_length=1, choices=Gender.choices, default=Gender.FEMALE)
    birth_date = models.DateField(null=True, blank=True)
    acquisition_date = models.DateField(null=True, blank=True, help_text='When purchased/acquired')
    acquisition_cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, blank=True)
    current_weight_kg = models.DecimalField(max_digits=8, decimal_places=2, default=0, blank=True)
    health_status = models.CharField(
        max_length=20,
        choices=HealthStatus.choices,
        default=HealthStatus.HEALTHY,
    )
    parent_male = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='offspring_as_sire', limit_choices_to={'gender': 'M'},
    )
    parent_female = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='offspring_as_dam', limit_choices_to={'gender': 'F'},
    )
    is_sold = models.BooleanField(default=False)
    sale_date = models.DateField(null=True, blank=True)
    sale_price = models.DecimalField(max_digits=12, decimal_places=2, default=0, blank=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.tag_number} - {self.name or self.species.name}'

    @property
    def age_days(self):
        if not self.birth_date:
            return None
        from datetime import date
        return (date.today() - self.birth_date).days

    @property
    def is_breeding_stock(self):
        return self.gender in ('M', 'F') and not self.is_sold and self.health_status != 'DECEASED'


class AnimalHealthRecord(models.Model):
    """Vaccinations, treatments, vet visits."""
    class RecordType(models.TextChoices):
        VACCINATION = 'VACCINATION', 'Vaccination'
        TREATMENT = 'TREATMENT', 'Treatment'
        CHECKUP = 'CHECKUP', 'Check-up'
        SURGERY = 'SURGERY', 'Surgery'
        DEWORMING = 'DEWORMING', 'Deworming'
        OTHER = 'OTHER', 'Other'

    animal = models.ForeignKey(
        AnimalRecord,
        on_delete=models.CASCADE,
        related_name='health_records',
    )
    record_type = models.CharField(
        max_length=20,
        choices=RecordType.choices,
        default=RecordType.CHECKUP,
    )
    date = models.DateField()
    description = models.TextField(help_text='What was done?')
    medication = models.CharField(max_length=200, blank=True, default='')
    dosage = models.CharField(max_length=100, blank=True, default='')
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, blank=True)
    veterinarian = models.CharField(max_length=200, blank=True, default='')
    next_due_date = models.DateField(null=True, blank=True, help_text='Next vaccination/treatment due')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.animal.tag_number} - {self.record_type} - {self.date}'


class BreedingRecord(models.Model):
    """Breeding/mating records and pregnancy tracking."""
    class Status(models.TextChoices):
        PLANNED = 'PLANNED', 'Planned'
        MATED = 'MATED', 'Mated'
        PREGNANT = 'PREGNANT', 'Pregnant'
        DELIVERED = 'DELIVERED', 'Delivered'
        FAILED = 'FAILED', 'Failed'

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='breeding_records',
    )
    dam = models.ForeignKey(
        AnimalRecord, on_delete=models.SET_NULL, null=True,
        related_name='breeding_as_dam', limit_choices_to={'gender': 'F'},
    )
    sire = models.ForeignKey(
        AnimalRecord, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='breeding_as_sire', limit_choices_to={'gender': 'M'},
    )
    species = models.ForeignKey(
        AnimalSpecies, on_delete=models.PROTECT,
        related_name='breeding_records',
    )
    mating_date = models.DateField()
    expected_delivery_date = models.DateField(null=True, blank=True)
    actual_delivery_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PLANNED,
    )
    offspring_count = models.PositiveIntegerField(default=0, blank=True)
    offspring_alive = models.PositiveIntegerField(default=0, blank=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.species.name} - {self.mating_date} - {self.status}'


class AnimalFeedLog(models.Model):
    """Feed consumption records for animal groups."""
    group = models.ForeignKey(
        AnimalGroup,
        on_delete=models.CASCADE,
        related_name='feed_logs',
    )
    date = models.DateField()
    feed_type = models.CharField(max_length=100, help_text='e.g. Hay, Grain Mix, Pasture')
    quantity_kg = models.DecimalField(max_digits=10, decimal_places=2)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, blank=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.group.name} - {self.feed_type} - {self.date}'


class MilkProductionRecord(models.Model):
    """Daily milk production for dairy animals."""
    group = models.ForeignKey(
        AnimalGroup,
        on_delete=models.CASCADE,
        related_name='milk_records',
    )
    date = models.DateField()
    morning_liters = models.DecimalField(max_digits=8, decimal_places=2, default=0, blank=True)
    evening_liters = models.DecimalField(max_digits=8, decimal_places=2, default=0, blank=True)
    total_liters = models.DecimalField(max_digits=8, decimal_places=2, default=0, blank=True)
    fat_content_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0, blank=True)
    price_per_liter = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    buyer = models.CharField(max_length=200, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.group.name} - {self.date} - {self.total_liters}L'

    def save(self, *args, **kwargs):
        if not self.total_liters:
            self.total_liters = self.morning_liters + self.evening_liters
        super().save(*args, **kwargs)


# ============================================================================
# POULTRY MANAGEMENT
# ============================================================================

class PoultryBatch(models.Model):
    """A batch/flock of poultry birds (chickens, ducks, turkeys, etc.)."""
    class BirdType(models.TextChoices):
        CHICKEN_LAYER = 'CHICKEN_LAYER', 'Chicken (Layer)'
        CHICKEN_BROILER = 'CHICKEN_BROILER', 'Chicken (Broiler)'
        CHICKEN_DUAL = 'CHICKEN_DUAL', 'Chicken (Dual Purpose)'
        DUCK = 'DUCK', 'Duck'
        TURKEY = 'TURKEY', 'Turkey'
        GUINEA_FOWL = 'GUINEA_FOWL', 'Guinea Fowl'
        QUAIL = 'QUAIL', 'Quail'
        OTHER = 'OTHER', 'Other'

    class Purpose(models.TextChoices):
        EGGS = 'EGGS', 'Egg Production'
        MEAT = 'MEAT', 'Meat Production'
        DUAL = 'DUAL', 'Dual Purpose'
        BREEDING = 'BREEDING', 'Breeding'

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        SOLD = 'SOLD', 'Sold Out'
        CULLED = 'CULLED', 'Culled'
        DECEASED = 'DECEASED', 'Lost'

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='poultry_batches',
    )
    batch_name = models.CharField(max_length=200, help_text='e.g. "Layer Batch 2025-01"')
    bird_type = models.CharField(
        max_length=20,
        choices=BirdType.choices,
        default=BirdType.CHICKEN_LAYER,
    )
    purpose = models.CharField(
        max_length=20,
        choices=Purpose.choices,
        default=Purpose.EGGS,
    )
    breed = models.CharField(max_length=100, blank=True, default='', help_text='e.g. Rhode Island Red, Cobb 500')
    start_date = models.DateField(help_text='Date batch was started (chicks placed)')
    initial_count = models.PositiveIntegerField(default=0, help_text='Number of birds at start')
    current_count = models.PositiveIntegerField(default=0, help_text='Current live bird count')
    mortality_count = models.PositiveIntegerField(default=0, blank=True)
    sold_count = models.PositiveIntegerField(default=0, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    housing = models.CharField(max_length=200, blank=True, default='', help_text='Coop/pen name')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.batch_name} ({self.get_bird_type_display()})'

    @property
    def mortality_rate(self):
        if self.initial_count > 0:
            return (self.mortality_count / self.initial_count) * 100
        return 0

    @property
    def survival_rate(self):
        return 100 - self.mortality_rate


class EggProductionRecord(models.Model):
    """Daily egg production records for layer batches."""
    batch = models.ForeignKey(
        PoultryBatch,
        on_delete=models.CASCADE,
        related_name='egg_records',
    )
    date = models.DateField()
    eggs_collected = models.PositiveIntegerField(default=0)
    damaged_eggs = models.PositiveIntegerField(default=0, blank=True)
    saleable_eggs = models.PositiveIntegerField(default=0, blank=True)
    price_per_egg = models.DecimalField(max_digits=10, decimal_places=2, default=0, blank=True)
    buyer = models.CharField(max_length=200, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.batch.batch_name} - {self.date} - {self.eggs_collected} eggs'

    def save(self, *args, **kwargs):
        if not self.saleable_eggs:
            self.saleable_eggs = self.eggs_collected - self.damaged_eggs
        super().save(*args, **kwargs)

    @property
    def revenue(self):
        return self.saleable_eggs * self.price_per_egg

    @property
    def lay_rate(self):
        """Percentage of birds laying."""
        if self.batch.current_count > 0:
            return (self.eggs_collected / self.batch.current_count) * 100
        return 0


class PoultryFeedLog(models.Model):
    """Feed consumption for poultry batches."""
    batch = models.ForeignKey(
        PoultryBatch,
        on_delete=models.CASCADE,
        related_name='feed_logs',
    )
    date = models.DateField()
    feed_type = models.CharField(max_length=100, help_text='e.g. Starter, Grower, Layer Mash')
    quantity_kg = models.DecimalField(max_digits=10, decimal_places=2)
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, blank=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.batch.batch_name} - {self.feed_type} - {self.date}'

    @property
    def feed_per_bird(self):
        if self.batch.current_count > 0:
            return self.quantity_kg / self.batch.current_count
        return 0


class PoultryHealthRecord(models.Model):
    """Vaccinations and health treatments for poultry batches."""
    class RecordType(models.TextChoices):
        VACCINATION = 'VACCINATION', 'Vaccination'
        TREATMENT = 'TREATMENT', 'Treatment'
        SUPPLEMENT = 'SUPPLEMENT', 'Supplement/Vitamin'
        CULLING = 'CULLING', 'Culling'
        OTHER = 'OTHER', 'Other'

    batch = models.ForeignKey(
        PoultryBatch,
        on_delete=models.CASCADE,
        related_name='health_records',
    )
    record_type = models.CharField(
        max_length=20,
        choices=RecordType.choices,
        default=RecordType.VACCINATION,
    )
    date = models.DateField()
    description = models.TextField()
    medication = models.CharField(max_length=200, blank=True, default='')
    cost = models.DecimalField(max_digits=12, decimal_places=2, default=0, blank=True)
    birds_affected = models.PositiveIntegerField(default=0, blank=True)
    next_due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.batch.batch_name} - {self.record_type} - {self.date}'
