"""Auto-sync services for the labor domain.

Posts one auto-generated ``Expense`` per ``PayrollLine`` so wages reach
the expense ledger, budget actuals and reports. The run's ``budget_item``
is carried onto every expense (line-level overrides are honored when set).
Deleting a line removes its expense via ``on_delete=CASCADE``; zero-amount
lines remove a stale expense instead.
"""

from django.db import transaction


def _labor_category():
    from expenses.models import Category, ScheduleLine
    sched_line, _ = ScheduleLine.objects.get_or_create(
        code='part1_labor_hired', defaults={'name': 'Labor hired (less employment credits)'})
    category, _ = Category.objects.get_or_create(
        code='LABOR', defaults={
            'name': 'Labor', 'schedule_f_line': sched_line,
            'description': 'Wages paid to farm workers'})
    return category


def sync_payroll_expense(line):
    """Keep exactly one auto-generated Expense per payroll line."""
    from expenses.models import Expense

    run = line.payroll_run
    with transaction.atomic():
        expense = Expense.objects.filter(payroll_line=line).first()
        if not line.amount or line.amount <= 0:
            if expense is not None:
                expense.delete()
            return

        budget_item = line.budget_item or run.budget_item
        category = budget_item.category if budget_item else _labor_category()
        if not budget_item:
            from expenses.services import default_budget_item
            budget_item = default_budget_item(run.farm_profile, category)
        synced = {
            'farm_profile': run.farm_profile,
            'category': category,
            'budget_item': budget_item,
            'amount': line.amount,
            'date': run.end_date,
            'vendor': f'Payroll: {line.worker.name}',
            'payment_method': Expense.PaymentMethod.BANK_TRANSFER,
            'notes': f'Payroll {run.start_date} to {run.end_date}. '
                     f'Hours: {line.hours}, Overtime: {line.overtime_hours}',
        }
        if expense is None:
            Expense.objects.create(payroll_line=line, **synced)
        else:
            for attr, value in synced.items():
                setattr(expense, attr, value)
            expense.save(update_fields=[*synced.keys(), 'updated_at'])


def sync_payroll_run_expenses(run):
    """Post expenses for every line in a payroll run."""
    for line in run.lines.select_related('worker', 'payroll_run', 'budget_item'):
        sync_payroll_expense(line)
