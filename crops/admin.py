from django.contrib import admin

from .models import Crop, CropSeason, HarvestRecord, ProcessingStage, Sale


class CropSeasonInline(admin.TabularInline):
    model = CropSeason
    extra = 1


class HarvestRecordInline(admin.TabularInline):
    model = HarvestRecord
    extra = 1


@admin.register(Crop)
class CropAdmin(admin.ModelAdmin):
    list_display = ('name', 'scientific_name', 'category', 'default_unit', 'secondary_unit', 'growing_season_days')
    list_filter = ('category',)
    search_fields = ('name', 'scientific_name')


@admin.register(CropSeason)
class CropSeasonAdmin(admin.ModelAdmin):
    list_display = ('crop', 'field', 'planting_date', 'expected_harvest_date', 'status')
    list_filter = ('status', 'field', 'crop')
    search_fields = ('crop__name', 'variety', 'field__name')
    inlines = [HarvestRecordInline]


class ProcessingStageInline(admin.TabularInline):
    model = ProcessingStage
    extra = 1


class SaleInline(admin.TabularInline):
    model = Sale
    extra = 1


@admin.register(HarvestRecord)
class HarvestRecordAdmin(admin.ModelAdmin):
    list_display = ('crop_season', 'harvest_date', 'harvest_type', 'quantity', 'unit', 'quality_grade')
    list_filter = ('harvest_date', 'crop_season__crop')
    search_fields = ('crop_season__crop__name',)
    inlines = [ProcessingStageInline, SaleInline]


@admin.register(ProcessingStage)
class ProcessingStageAdmin(admin.ModelAdmin):
    list_display = ('harvest', 'name', 'sequence', 'start_date', 'duration_days', 'end_date', 'cost', 'added_value', 'input_quantity', 'output_quantity', 'output_unit')
    list_filter = ('harvest__crop_season__crop',)
    search_fields = ('name', 'harvest__crop_season__crop__name')
    ordering = ('harvest', 'sequence')


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ('harvest', 'sale_date', 'quantity', 'unit', 'unit_price', 'buyer', 'payment_method')
    list_filter = ('sale_date', 'payment_method', 'harvest__crop_season__crop')
    search_fields = ('buyer', 'harvest__crop_season__crop__name')
