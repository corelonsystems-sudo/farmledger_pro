from django.conf import settings
from django.db import models


class Worker(models.Model):
    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='workers',
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='worker_profile',
    )
    name = models.CharField(max_length=200)
    hourly_rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_id = models.CharField(max_length=50, blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Task(models.Model):
    class TaskStatus(models.TextChoices):
        ASSIGNED = 'ASSIGNED', 'Assigned'
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    workers = models.ManyToManyField(Worker, related_name='tasks')
    crop_season = models.ForeignKey(
        'crops.CropSeason',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
    )
    field = models.ForeignKey(
        'fields.Field',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='tasks',
    )
    status = models.CharField(
        max_length=20,
        choices=TaskStatus.choices,
        default=TaskStatus.ASSIGNED,
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class AttendanceRecord(models.Model):
    worker = models.ForeignKey(
        Worker,
        on_delete=models.CASCADE,
        related_name='attendance_records',
    )
    task = models.ForeignKey(
        Task,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='attendance_records',
    )
    date = models.DateField()
    hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    overtime_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.worker.name} - {self.date} - {self.hours}h'

    @property
    def total_pay(self):
        regular = self.hours * self.worker.hourly_rate
        overtime = self.overtime_hours * self.worker.hourly_rate * 2
        return regular + overtime


class PayrollRun(models.Model):
    class PayrollStatus(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        COMPLETED = 'COMPLETED', 'Completed'
        FAILED = 'FAILED', 'Failed'

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='payroll_runs',
    )
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(
        max_length=20,
        choices=PayrollStatus.choices,
        default=PayrollStatus.PENDING,
    )
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    budget_item = models.ForeignKey(
        'budgets.BudgetItem',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='payroll_runs',
        help_text='Budget item payroll wages are posted against.')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Payroll {self.start_date} to {self.end_date} - {self.status}'

    def generate_from_attendance(self):
        """(Re)build per-worker lines from attendance records in the pay period
        and update ``total_amount``. No-op for completed runs."""
        from django.db import transaction
        from django.db.models import DecimalField, Sum
        from django.db.models.functions import Coalesce

        if self.status == self.PayrollStatus.COMPLETED:
            return 0
        rows = (
            AttendanceRecord.objects
            .filter(worker__farm_profile=self.farm_profile,
                    date__range=(self.start_date, self.end_date))
            .values('worker')
            .annotate(hours=Coalesce(Sum('hours'), 0, output_field=DecimalField()),
                      overtime=Coalesce(Sum('overtime_hours'), 0, output_field=DecimalField()))
        )
        total = 0
        with transaction.atomic():
            self.lines.all().delete()
            for row in rows:
                worker = Worker.objects.get(pk=row['worker'])
                amount = (row['hours'] * worker.hourly_rate
                          + row['overtime'] * worker.hourly_rate * 2)
                PayrollLine.objects.create(
                    payroll_run=self, worker=worker,
                    hours=row['hours'], overtime_hours=row['overtime'],
                    amount=amount)
                total += amount
            self.total_amount = total
            self.save(update_fields=['total_amount', 'updated_at'])
        return total


class PayrollLine(models.Model):
    payroll_run = models.ForeignKey(
        PayrollRun,
        on_delete=models.CASCADE,
        related_name='lines',
    )
    worker = models.ForeignKey(
        Worker,
        on_delete=models.PROTECT,
        related_name='payroll_lines',
    )
    hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    overtime_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    budget_item = models.ForeignKey(
        'budgets.BudgetItem',
        on_delete=models.PROTECT,
        null=True, blank=True,
        related_name='payroll_lines',
        help_text='Budget item this pay is posted against.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['payroll_run', 'worker'], name='unique_payroll_line_worker'),
        ]
        ordering = ['worker__name']

    def __str__(self):
        return f'{self.worker.name} - {self.amount}'
