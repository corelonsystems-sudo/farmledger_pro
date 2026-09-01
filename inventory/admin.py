from django.contrib import admin

from .models import InventoryAlert, InventoryItem, InventoryTransaction


class InventoryTransactionInline(admin.TabularInline):
    model = InventoryTransaction
    extra = 1


@admin.register(InventoryItem)
class InventoryItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'unit', 'quantity_on_hand', 'reorder_threshold', 'cost_per_unit', 'supplier', 'is_low_stock')
    list_filter = ('supplier', 'farm_profile')
    search_fields = ('name', 'supplier')
    inlines = [InventoryTransactionInline]


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    list_display = ('item', 'transaction_type', 'quantity', 'unit_cost', 'date', 'crop_season')
    list_filter = ('transaction_type', 'date', 'item')
    search_fields = ('item__name', 'notes')


@admin.register(InventoryAlert)
class InventoryAlertAdmin(admin.ModelAdmin):
    list_display = ('item', 'message', 'is_resolved', 'created_at')
    list_filter = ('is_resolved',)
    search_fields = ('item__name', 'message')
