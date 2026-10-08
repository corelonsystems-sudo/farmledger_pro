from django.contrib import admin

from .models import (
    AnimalFeedLog,
    AnimalGroup,
    AnimalHealthRecord,
    AnimalRecord,
    AnimalSpecies,
    BreedingRecord,
    EggProductionRecord,
    MilkProductionRecord,
    PoultryBatch,
    PoultryFeedLog,
    PoultryHealthRecord,
)


class AnimalHealthRecordInline(admin.TabularInline):
    model = AnimalHealthRecord
    extra = 1


class AnimalRecordInline(admin.TabularInline):
    model = AnimalRecord
    extra = 1


@admin.register(AnimalSpecies)
class AnimalSpeciesAdmin(admin.ModelAdmin):
    list_display = ('name', 'species_type', 'average_weight_kg')
    list_filter = ('species_type',)
    search_fields = ('name', 'breed_origin')


@admin.register(AnimalGroup)
class AnimalGroupAdmin(admin.ModelAdmin):
    list_display = ('name', 'farm_profile', 'species', 'purpose', 'count', 'is_active')
    list_filter = ('purpose', 'is_active', 'species__species_type')
    search_fields = ('name', 'farm_profile__farm_name')
    inlines = [AnimalRecordInline]


@admin.register(AnimalRecord)
class AnimalRecordAdmin(admin.ModelAdmin):
    list_display = ('tag_number', 'name', 'species', 'gender', 'health_status', 'is_sold')
    list_filter = ('gender', 'health_status', 'is_sold', 'species__species_type')
    search_fields = ('tag_number', 'name', 'species__name')
    inlines = [AnimalHealthRecordInline]


@admin.register(AnimalHealthRecord)
class AnimalHealthRecordAdmin(admin.ModelAdmin):
    list_display = ('animal', 'record_type', 'date', 'cost', 'veterinarian')
    list_filter = ('record_type', 'date')
    search_fields = ('animal__tag_number', 'description')


@admin.register(BreedingRecord)
class BreedingRecordAdmin(admin.ModelAdmin):
    list_display = ('species', 'dam', 'sire', 'mating_date', 'status', 'offspring_count')
    list_filter = ('status', 'species')
    search_fields = ('dam__tag_number', 'sire__tag_number')


@admin.register(AnimalFeedLog)
class AnimalFeedLogAdmin(admin.ModelAdmin):
    list_display = ('group', 'date', 'feed_type', 'quantity_kg', 'cost')
    list_filter = ('date', 'feed_type')
    search_fields = ('group__name',)


@admin.register(MilkProductionRecord)
class MilkProductionRecordAdmin(admin.ModelAdmin):
    list_display = ('group', 'date', 'morning_liters', 'evening_liters', 'total_liters', 'price_per_liter')
    list_filter = ('date',)
    search_fields = ('group__name',)


class EggProductionRecordInline(admin.TabularInline):
    model = EggProductionRecord
    extra = 1


class PoultryFeedLogInline(admin.TabularInline):
    model = PoultryFeedLog
    extra = 1


class PoultryHealthRecordInline(admin.TabularInline):
    model = PoultryHealthRecord
    extra = 1


@admin.register(PoultryBatch)
class PoultryBatchAdmin(admin.ModelAdmin):
    list_display = ('batch_name', 'farm_profile', 'bird_type', 'purpose', 'current_count', 'status')
    list_filter = ('bird_type', 'purpose', 'status')
    search_fields = ('batch_name', 'farm_profile__farm_name', 'breed')
    inlines = [EggProductionRecordInline, PoultryFeedLogInline, PoultryHealthRecordInline]


@admin.register(EggProductionRecord)
class EggProductionRecordAdmin(admin.ModelAdmin):
    list_display = ('batch', 'date', 'eggs_collected', 'damaged_eggs', 'saleable_eggs', 'price_per_egg')
    list_filter = ('date',)
    search_fields = ('batch__batch_name',)


@admin.register(PoultryFeedLog)
class PoultryFeedLogAdmin(admin.ModelAdmin):
    list_display = ('batch', 'date', 'feed_type', 'quantity_kg', 'cost')
    list_filter = ('date', 'feed_type')
    search_fields = ('batch__batch_name',)


@admin.register(PoultryHealthRecord)
class PoultryHealthRecordAdmin(admin.ModelAdmin):
    list_display = ('batch', 'record_type', 'date', 'cost', 'birds_affected')
    list_filter = ('record_type', 'date')
    search_fields = ('batch__batch_name', 'description')
