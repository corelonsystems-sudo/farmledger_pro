from django.contrib import admin

from .models import Income, IncomeSource


@admin.register(IncomeSource)
class IncomeSourceAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'schedule_f_line')
    list_filter = ('schedule_f_line',)
    search_fields = ('name', 'code', 'description')


@admin.register(Income)
class IncomeAdmin(admin.ModelAdmin):
    list_display = ('source', 'amount', 'date', 'customer', 'farm_profile', 'sale')
    list_filter = ('source', 'date', 'farm_profile', 'sale')
    search_fields = ('customer', 'notes', 'source__name', 'offline_uuid')
    date_hierarchy = 'date'
    filter_horizontal = ()
