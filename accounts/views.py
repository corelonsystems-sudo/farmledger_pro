from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LogoutView
from django.shortcuts import redirect


class PostOnlyLogoutView(LogoutView):
    http_method_names = ['post', 'options']


@login_required
def set_currency_view(request):
    next_url = request.POST.get('next', request.GET.get('next', '/'))
    currency_pk = request.POST.get('currency')
    if currency_pk:
        request.session['display_currency'] = currency_pk
    return redirect(next_url)
