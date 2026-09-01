from django.contrib import admin

from .models import AttendanceRecord, PayrollRun, Task, Worker


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 1


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    list_display = ('name', 'hourly_rate', 'tax_id', 'phone', 'is_active')
    list_filter = ('is_active', 'farm_profile')
    search_fields = ('name', 'tax_id', 'phone')


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ('name', 'status', 'crop_season', 'field', 'start_date', 'end_date')
    list_filter = ('status', 'crop_season', 'field')
    search_fields = ('name', 'description')
    filter_horizontal = ('workers',)


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ('worker', 'date', 'hours', 'overtime_hours', 'task')
    list_filter = ('date', 'worker', 'task')
    search_fields = ('worker__name', 'notes')


@admin.register(PayrollRun)
class PayrollRunAdmin(admin.ModelAdmin):
    list_display = ('farm_profile', 'start_date', 'end_date', 'status', 'total_amount')
    list_filter = ('status', 'farm_profile')
    search_fields = ('farm_profile__farm_name',)
