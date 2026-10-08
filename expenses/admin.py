from django.contrib import admin

from .models import BudgetSubCode, Category, Expense, ScheduleLine


@admin.register(BudgetSubCode)
class BudgetSubCodeAdmin(admin.ModelAdmin):
    list_display = ('category', 'code', 'name')
    list_filter = ('category',)
    search_fields = ('code', 'name', 'description', 'category__name')


@admin.register(ScheduleLine)
class ScheduleLineAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'subcode', 'description')
    list_filter = ('subcode__category',)
    search_fields = ('code', 'name', 'description')

    def get_readonly_fields(self, request, obj=None):
        return ('code',) if obj else ()


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'schedule_f_line')
    list_filter = ('schedule_f_line',)
    search_fields = ('name', 'code', 'description')


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ('budget_item', 'schedule_line', 'amount', 'date', 'vendor', 'farm_profile')
    list_filter = ('budget_item', 'date', 'farm_profile')
    search_fields = ('vendor', 'notes', 'budget_item__category__name', 'offline_uuid')
    date_hierarchy = 'date'
    filter_horizontal = ()
