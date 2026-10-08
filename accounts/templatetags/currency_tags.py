from decimal import Decimal, InvalidOperation

from django import template


register = template.Library()


@register.filter
def sub(value, arg):
    """Subtract arg from value."""
    try:
        return Decimal(value) - Decimal(arg)
    except (TypeError, ValueError, InvalidOperation):
        return Decimal('0')


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


@register.filter
def compact(value):
    """Abbreviate a number for tight spaces: 398000 -> '398.0K'."""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    abs_v = abs(v)
    formatted = f'{abs_v:.0f}'
    for div, suffix in ((1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if abs_v >= div:
            formatted = f'{abs_v / div:.1f}{suffix}'
            break
    return f'-{formatted}' if v < 0 else formatted


@register.filter
def as_currency_compact(value, currency):
    """Like as_currency but abbreviates large amounts (e.g. 'UGX 8.0M') for tight spaces."""
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

    formatted = compact(abs(converted))
    if converted < 0:
        return f'-{symbol} {formatted}'
    return f'{symbol} {formatted}'
