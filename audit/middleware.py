from .mixin import _thread_locals


class AuditMiddleware:
    """Store the current user and IP in thread-local storage so the audit
    signal handlers can attribute changes to whoever made the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        _thread_locals.audit_user = user if getattr(user, 'is_authenticated', False) else None
        _thread_locals.audit_ip = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[0].strip() \
            or request.META.get('REMOTE_ADDR')
        try:
            return self.get_response(request)
        finally:
            _thread_locals.audit_user = None
            _thread_locals.audit_ip = None
