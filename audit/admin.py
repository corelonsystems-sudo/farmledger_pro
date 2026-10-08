from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('action_type', 'content_type', 'object_id', 'user', 'timestamp', 'ip_address')
    list_filter = ('action_type', 'content_type', 'timestamp')
    search_fields = ('object_id', 'user__username', 'changed_fields')
    date_hierarchy = 'timestamp'
    readonly_fields = (
        'content_type', 'object_id', 'action_type', 'changed_fields',
        'user', 'timestamp', 'ip_address',
    )

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
