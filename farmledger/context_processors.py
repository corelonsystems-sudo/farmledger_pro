from accounts.models import Currency, FarmProfile


def currency(request):
    """Expose the active currency and available currencies to templates."""
    default = None
    if request.user.is_authenticated:
        try:
            default = request.user.farm_profile.default_currency
        except (AttributeError, FarmProfile.DoesNotExist):
            pass
    if default is None:
        default = Currency.objects.filter(is_default=True).first()
    if default is None:
        default = Currency(
            code='USD',
            name='US Dollar',
            symbol='$',
            rate=1,
        )

    selected_id = request.session.get('display_currency')
    if selected_id:
        try:
            active = Currency.objects.get(pk=int(selected_id))
        except (ValueError, Currency.DoesNotExist, TypeError):
            active = default
    else:
        active = default

    active.input_rate = default.rate
    return {
        'currency': active,
        'default_currency': default,
        'currencies': Currency.objects.order_by('code'),
    }
