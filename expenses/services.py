"""Shared helpers for the expenses domain."""


def default_budget_item(farm_profile, category):
    """Resolve (or create) a BudgetItem on the farm's active budget for
    ``category``.

    Auto-generated expenses must always land on a budget so actuals are
    never unlinked. When the source record carries no ``budget_item``, the
    expense is attached to an 'AUTO-<category.code>' item on the farm's
    ACTIVE budget (latest otherwise; a catch-all budget is created when
    the farm has none).
    """
    from budgets.models import Budget, BudgetItem

    budget = (Budget.objects.filter(
                farm_profile=farm_profile, status=Budget.Status.ACTIVE)
              .order_by('-start_date', '-pk').first())
    if budget is None:
        budget = (Budget.objects.filter(farm_profile=farm_profile)
                  .order_by('-start_date', '-pk').first())
    if budget is None:
        from datetime import date
        today = date.today()
        budget = Budget.objects.create(
            farm_profile=farm_profile,
            name=f'Operations Budget {today.year}',
            start_date=date(today.year, 1, 1),
            end_date=date(today.year, 12, 31),
            status=Budget.Status.ACTIVE,
            notes='Auto-created to catch expenses posted without an explicit '
                  'budget item.')

    item, _ = BudgetItem.objects.get_or_create(
        budget=budget, line_code=f'AUTO-{category.code}',
        defaults={
            'category': category,
            'cost_centre': 'Auto-posted',
            'line_name': category.name,
            'planned_amount': 0,
        })
    return item
