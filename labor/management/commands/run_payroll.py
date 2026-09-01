import os
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

from expenses.models import Category, Expense
from labor.models import AttendanceRecord, PayrollRun, Worker


class Command(BaseCommand):
    help = 'Run payroll for a date range. Aggregates attendance records, creates a PayrollRun, ' \
           'auto-generates expense records per worker, and saves pay stub PDFs.'

    def add_arguments(self, parser):
        parser.add_argument('--start-date', type=str, required=True, help='Start date (YYYY-MM-DD)')
        parser.add_argument('--end-date', type=str, required=True, help='End date (YYYY-MM-DD)')
        parser.add_argument(
            '--farm-profile-id', type=int, default=None,
            help='Farm profile ID. If not specified, runs for all farm profiles.',
        )

    def handle(self, *args, **options):
        from datetime import date as date_type

        try:
            start_date = date_type.fromisoformat(options['start_date'])
            end_date = date_type.fromisoformat(options['end_date'])
        except ValueError:
            raise CommandError('Dates must be in YYYY-MM-DD format.')

        if start_date > end_date:
            raise CommandError('Start date must be before or equal to end date.')

        farm_profile_id = options.get('farm_profile_id')

        from accounts.models import FarmProfile
        if farm_profile_id:
            farm_profiles = FarmProfile.objects.filter(id=farm_profile_id)
        else:
            farm_profiles = FarmProfile.objects.all()

        if not farm_profiles.exists():
            raise CommandError('No farm profiles found.')

        total_runs = 0
        total_pay_stubs = 0

        for farm in farm_profiles:
            self.stdout.write(self.style.HTTP_INFO(f'Processing farm: {farm.farm_name}'))

            payroll_run = PayrollRun.objects.create(
                farm_profile=farm,
                start_date=start_date,
                end_date=end_date,
                status=PayrollRun.PayrollStatus.PENDING,
            )

            workers = Worker.objects.filter(farm_profile=farm, is_active=True)
            grand_total = Decimal('0')

            labor_category, _ = Category.objects.get_or_create(
                name='Labor',
                defaults={'schedule_f_line': 'part1_labor_hired'},
            )

            with transaction.atomic():
                for worker in workers:
                    records = AttendanceRecord.objects.filter(
                        worker=worker,
                        date__gte=start_date,
                        date__lte=end_date,
                    )

                    if not records.exists():
                        continue

                    total_hours = sum((r.hours for r in records), Decimal('0'))
                    total_overtime = sum((r.overtime_hours for r in records), Decimal('0'))
                    regular_pay = total_hours * worker.hourly_rate
                    overtime_pay = total_overtime * worker.hourly_rate * 2
                    total_pay = regular_pay + overtime_pay
                    grand_total += total_pay

                    crop_season = None
                    first_record = records.first()
                    if first_record and first_record.task and first_record.task.crop_season:
                        crop_season = first_record.task.crop_season

                    expense = Expense.objects.create(
                        farm_profile=farm,
                        category=labor_category,
                        crop_season=crop_season,
                        amount=total_pay,
                        date=end_date,
                        vendor=f'Payroll: {worker.name}',
                        payment_method=Expense.PaymentMethod.BANK_TRANSFER,
                        notes=f'Payroll {start_date} to {end_date}. '
                              f'Regular: {total_hours}h, Overtime: {total_overtime}h',
                    )

                    pay_stub_dir = os.path.join(settings.MEDIA_ROOT, 'paystubs', str(payroll_run.id))
                    os.makedirs(pay_stub_dir, exist_ok=True)
                    pay_stub_path = os.path.join(pay_stub_dir, f'{worker.name.replace(" ", "_")}_paystub.pdf')

                    self._generate_pay_stub_pdf(
                        pay_stub_path,
                        worker=worker,
                        start_date=start_date,
                        end_date=end_date,
                        total_hours=total_hours,
                        total_overtime=total_overtime,
                        regular_pay=regular_pay,
                        overtime_pay=overtime_pay,
                        total_pay=total_pay,
                        farm_name=farm.farm_name,
                    )

                    total_pay_stubs += 1
                    self.stdout.write(
                        f'  Worker: {worker.name} | Hours: {total_hours} | '
                        f'Overtime: {total_overtime} | Pay: ${total_pay} | '
                        f'Pay stub: {pay_stub_path}'
                    )

                payroll_run.total_amount = grand_total
                payroll_run.status = PayrollRun.PayrollStatus.COMPLETED
                payroll_run.save()

                total_runs += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  Payroll run completed: ${grand_total} total for {farm.farm_name}'
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'\nDone. {total_runs} payroll run(s) completed, {total_pay_stubs} pay stub(s) generated.'
            )
        )

    def _generate_pay_stub_pdf(self, path, worker, start_date, end_date,
                               total_hours, total_overtime, regular_pay,
                               overtime_pay, total_pay, farm_name):
        c = canvas.Canvas(path, pagesize=letter)
        width, height = letter

        styles = getSampleStyleSheet()

        y = height - 1 * inch
        c.setFont('Helvetica-Bold', 16)
        c.drawString(1 * inch, y, f'{farm_name} - Pay Stub')
        y -= 0.4 * inch

        c.setFont('Helvetica', 10)
        c.drawString(1 * inch, y, f'Worker: {worker.name}')
        y -= 0.2 * inch
        c.drawString(1 * inch, y, f'Tax ID: {worker.tax_id or "N/A"}')
        y -= 0.2 * inch
        c.drawString(1 * inch, y, f'Pay Period: {start_date} to {end_date}')
        y -= 0.2 * inch
        c.drawString(1 * inch, y, f'Hourly Rate: ${worker.hourly_rate}/hr')
        y -= 0.4 * inch

        c.line(1 * inch, y, width - 1 * inch, y)
        y -= 0.3 * inch

        c.drawString(1 * inch, y, 'Earnings Breakdown:')
        y -= 0.25 * inch
        c.drawString(1.2 * inch, y, f'Regular Hours: {total_hours}    Regular Pay: ${regular_pay}')
        y -= 0.2 * inch
        c.drawString(1.2 * inch, y, f'Overtime Hours: {total_overtime}    Overtime Pay: ${overtime_pay}')
        y -= 0.3 * inch

        c.setFont('Helvetica-Bold', 12)
        c.drawString(1 * inch, y, f'Total Pay: ${total_pay}')

        c.showPage()
        c.save()
