from django.contrib import admin

from .models import Field, LandParcel


class LandParcelInline(admin.TabularInline):
    model = LandParcel
    extra = 1


@admin.register(Field)
class FieldAdmin(admin.ModelAdmin):
    list_display = ('name', 'farm_profile', 'acreage', 'soil_type', 'is_active')
    list_filter = ('soil_type', 'is_active', 'farm_profile')
    search_fields = ('name', 'farm_profile__farm_name')
    inlines = [LandParcelInline]


@admin.register(LandParcel)
class LandParcelAdmin(admin.ModelAdmin):
    list_display = ('field', 'land_type', 'lease_cost', 'lease_start', 'lease_end')
    list_filter = ('land_type',)
    search_fields = ('field__name', 'owner_name')
