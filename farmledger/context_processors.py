from accounts.access import FINANCIAL_MODELS, accessible_farms
from accounts.models import Currency, FarmProfile, User


def _system_logo_url(user):
    """Logo shown in the app chrome: user's farm logo first, else the
    static system logo at static/system-logo.svg."""
    if not user.is_authenticated:
        return None
    farm = accessible_farms(user).first()
    if farm and farm.logo:
        try:
            return farm.logo.url
        except (ValueError, OSError):
            pass
    return None


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


def nav_access(request):
    """Sidebar visibility flags derived from the user's effective farm roles.

    Mirrors the write matrix in accounts.access.accessible_farms:
    owners/managers see everything, accountants get finance + reports,
    workers get field operations (no payroll/finance). A user with no
    accessible farm still sees Farm Settings so they can create a profile.
    """
    user = request.user
    if not user.is_authenticated:
        return {'nav_access': {}}
    if user.is_superuser:
        owns_farm = True
        roles = {User.Role.OWNER}
    else:
        owns_farm = FarmProfile.objects.filter(user=user).exists()
        roles = set(user.farm_memberships.values_list('role', flat=True))
        if owns_farm:
            roles.add(User.Role.OWNER)

    manage = bool(roles & {User.Role.OWNER, User.Role.MANAGER})
    ops = manage or User.Role.WORKER in roles
    finance = manage or User.Role.ACCOUNTANT in roles
    write_roles = {User.Role.OWNER, User.Role.MANAGER}
    fin_write_roles = write_roles | {User.Role.ACCOUNTANT}

    def can_write(label):
        return bool(roles & (
            fin_write_roles if label in FINANCIAL_MODELS else write_roles))

    return {'nav_access': {
        'reports': finance,
        'crops': ops,
        'animals': ops,
        'operations': ops,
        'payroll': manage,
        'finance': finance,
        'settings': manage or not accessible_farms(user).exists(),
        'w_sale': can_write('crops.sale'),
        'w_harvest': can_write('crops.harvestrecord'),
        'w_season': can_write('crops.cropseason'),
        'w_expense': can_write('expenses.expense'),
        'w_income': can_write('income.income'),
        'w_task': can_write('labor.task'),
        'w_farm': can_write('accounts.farmprofile') or not owns_farm,
    },
        'system_logo_url': _system_logo_url(user),
    }
