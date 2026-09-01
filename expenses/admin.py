from django.contrib import admin

from .models import Category, Expense


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'schedule_f_line')
    list_filter = ('schedule_f_line',)
    search_fields = ('name', 'code', 'description')


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('budget_item', 'amount', 'date', 'vendor', 'farm_profile')
    list_filter = ('budget_item', 'date', 'farm_profile')
    search_fields = ('vendor', 'notes', 'budget_item__category__name', 'offline_uuid')
    date_hierarchy = 'date'
    filter_horizontal = ()
