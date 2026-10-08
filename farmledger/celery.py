import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'farmledger.settings')

app = Celery('farmledger')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

app.conf.beat_schedule = {
    'check-budget-alerts-daily': {
        'task': 'budgets.tasks.check_budget_alerts',
        'schedule': crontab(hour=8, minute=0),
    },
}
