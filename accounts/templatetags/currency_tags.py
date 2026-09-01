from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def as_currency(value, currency):
    """Convert a value in the default/input currency to the active currency and format it."""
    if currency:
        symbol = getattr(currency, 'symbol', '$')
        try:
            target_rate = float(currency.rate)
        except (TypeError, ValueError, InvalidOperation):
            target_rate = 1.0
        try:
            input_rate = float(getattr(currency, 'input_rate', target_rate))
        except (TypeError, ValueError, InvalidOperation):
            input_rate = target_rate
    else:
        symbol = '$'
        target_rate = 1.0
        input_rate = 1.0

    try:
        converted = float(value or 0) * (target_rate / input_rate) if input_rate else float(value or 0)
    except (TypeError, ValueError, InvalidOperation):
        converted = 0.0

    formatted = f'{abs(converted):,.2f}'
    if converted < 0:
        return f'-{symbol} {formatted}'
    return f'{symbol} {formatted}'
