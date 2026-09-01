from django.contrib import admin

from .models import Equipment, EquipmentUsage, MaintenanceLog


class MaintenanceLogInline(admin.TabularInline):
    model = MaintenanceLog
    extra = 1


class EquipmentUsageInline(admin.TabularInline):
    model = EquipmentUsage
    extra = 1


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'equipment_type', 'purchase_cost', 'current_value', 'hours_used', 'is_active', 'cost_per_hour')
    list_filter = ('equipment_type', 'is_active', 'farm_profile')
    search_fields = ('name',)
    inlines = [MaintenanceLogInline, EquipmentUsageInline]


@admin.register(MaintenanceLog)
class MaintenanceLogAdmin(admin.ModelAdmin):
    list_display = ('equipment', 'date', 'cost', 'performed_by')
    list_filter = ('date', 'equipment')
    search_fields = ('equipment__name', 'description', 'performed_by')


@admin.register(EquipmentUsage)
class EquipmentUsageAdmin(admin.ModelAdmin):
    list_display = ('equipment', 'date', 'hours', 'crop_season', 'field')
    list_filter = ('date', 'equipment')
    search_fields = ('equipment__name', 'description')
