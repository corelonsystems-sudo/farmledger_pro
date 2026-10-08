from django.contrib import admin

from .models import Budget, BudgetIncomePlan, BudgetItem


class BudgetItemInline(admin.TabularInline):
    model = BudgetItem
    extra = 1


class BudgetIncomeInline(admin.TabularInline):
    model = BudgetIncomePlan
    extra = 1


@admin.register(BudgetIncomePlan)
class BudgetIncomeAdmin(admin.ModelAdmin):
    list_display = ('budget', 'line_code', 'funding_type', 'name', 'planned_amount', 'figure_type')
    list_filter = ('budget', 'funding_type', 'figure_type')
    search_fields = ('line_code', 'name', 'budget__name')


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ('name', 'farm_profile', 'status', 'start_date', 'end_date')
    list_filter = ('status', 'farm_profile')
    search_fields = ('name', 'farm_profile__farm_name')
    inlines = [BudgetIncomeInline, BudgetItemInline]


@admin.register(BudgetItem)
class BudgetItemAdmin(admin.ModelAdmin):
    list_display = ('budget', 'line_code', 'cost_centre', 'head', 'line_name', 'category', 'planned_amount', 'figure_type')
    list_filter = ('budget', 'category', 'crop_season')
    search_fields = ('budget__name', 'category__name', 'crop_season__crop__name')
