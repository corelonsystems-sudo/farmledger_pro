from decimal import Decimal

from django.db.models import F, Sum, Value, DecimalField
from django.db.models.functions import Coalesce

from expenses.models import Expense
from .models import Budget


def get_budget_variance_report(crop_season_id=None):
    """Build a budget variance report using annotate + Sum.

    Joins budget records with actual expense totals and calculates
    variance and variance percentage on the fly (no stored model).
    """
    qs = Budget.objects.select_related('crop_season', 'category')

    if crop_season_id:
        qs = qs.filter(crop_season_id=crop_season_id)

    qs = qs.annotate(
        actual_amount=Coalesce(
            Sum(
                'category__expenses__amount',
                filter=models_Q_expenses_for_crop_season(crop_season_id),
            ),
            Value(Decimal('0')),
            output_field=DecimalField(max_digits=12, decimal_places=2),
        ),
    )

    results = []
    for budget in qs:
        actual = budget.actual_amount or Decimal('0')
        variance = budget.planned_amount - actual
        if budget.planned_amount and budget.planned_amount > 0:
            variance_pct = (variance / budget.planned_amount) * Decimal('100')
        else:
            variance_pct = Decimal('0')

        results.append({
            'id': budget.id,
            'crop_season': budget.crop_season.id,
            'crop_season_name': str(budget.crop_season),
            'category': budget.category.name,
            'category_id': budget.category.id,
            'planned_amount': str(budget.planned_amount),
            'actual_amount': str(actual),
            'variance': str(variance),
            'variance_percentage': str(variance_pct),
        })

    return results


def models_Q_expenses_for_crop_season(crop_season_id):
    """Return a Q object filtering expenses by crop season if specified."""
    from django.db.models import Q
    if crop_season_id:
        return Q(expenses__crop_season_id=crop_season_id)
    return Q()
