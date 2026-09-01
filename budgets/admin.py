from django.contrib import admin

from .models import Budget, BudgetItem


class BudgetItemInline(admin.TabularInline):
    model = BudgetItem
    extra = 1


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ('name', 'farm_profile', 'status', 'start_date', 'end_date')
    list_filter = ('status', 'farm_profile')
    search_fields = ('name', 'farm_profile__farm_name')
    inlines = [BudgetItemInline]


@admin.register(BudgetItem)
class BudgetItemAdmin(admin.ModelAdmin):
    list_display = ('budget', 'category', 'crop_season', 'planned_amount', 'fund_amount')
    list_filter = ('budget', 'category', 'crop_season')
    search_fields = ('budget__name', 'category__name', 'crop_season__crop__name')
