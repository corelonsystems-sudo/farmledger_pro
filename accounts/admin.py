from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Currency, User, FarmProfile, FarmMembership


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'symbol', 'rate', 'is_default')
    list_editable = ('rate', 'is_default')


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('username', 'email', 'role', 'is_staff', 'is_active')
    list_filter = ('role', 'is_staff', 'is_active')
    search_fields = ('username', 'email', 'phone')
    fieldsets = BaseUserAdmin.fieldsets + (
        ('Farm Ledger', {'fields': ('role', 'phone')}),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Farm Ledger', {'fields': ('role', 'phone')}),
    )


@admin.register(FarmProfile)
class FarmProfileAdmin(admin.ModelAdmin):
    list_display = ('farm_name', 'user', 'location', 'acreage', 'tax_id', 'phone', 'email')
    list_filter = ('location',)
    search_fields = ('farm_name', 'user__username', 'tax_id', 'phone', 'email')


@admin.register(FarmMembership)
class FarmMembershipAdmin(admin.ModelAdmin):
    list_display = ('farm_profile', 'user', 'role')
    list_filter = ('farm_profile', 'role')
    search_fields = ('farm_profile__farm_name', 'user__username')
