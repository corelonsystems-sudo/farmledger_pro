import logging
from decimal import Decimal

from celery import shared_task
from django.core.mail import mail_managers

from .models import Budget

logger = logging.getLogger(__name__)


@shared_task
def check_budget_alerts():
    """Daily Celery task that checks if any budget category has actual spending
    above 90% of the planned amount and sends an email alert.
    """
    from .queries import get_budget_variance_report

    results = get_budget_variance_report()
    alerts = []

    for item in results:
        planned = Decimal(item['planned_amount'])
        actual = Decimal(item['actual_amount'])
        if planned > 0:
            burn_rate = (actual / planned) * Decimal('100')
            if burn_rate >= Decimal('90'):
                alerts.append(item)

    if alerts:
        subject = f'Budget Alert: {len(alerts)} category(ies) above 90% of planned budget'
        message_lines = ['The following budget categories have exceeded 90% of their planned amount:\n']
        for a in alerts:
            message_lines.append(
                f"- Crop: {a['crop_season_name']}\n"
                f"  Category: {a['category']}\n"
                f"  Planned: ${a['planned_amount']}\n"
                f"  Actual: ${a['actual_amount']}\n"
                f"  Variance: ${a['variance']} ({a['variance_percentage']}%)\n"
            )
        message = '\n'.join(message_lines)
        try:
            mail_managers(subject, message)
        except Exception as e:
            logger.error(f'Failed to send budget alert email: {e}')

    logger.info(f'Budget alert check complete. {len(alerts)} alert(s) found.')
    return {'alerts': len(alerts)}
