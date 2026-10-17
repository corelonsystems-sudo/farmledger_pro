from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import FarmProfile, User
from .models import AttendanceRecord, Worker


class WorkerListPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        worker = Worker.objects.create(
            farm_profile=self.farm, name='Jane Doe', phone='0700',
            hourly_rate=Decimal('10'))
        AttendanceRecord.objects.create(
            worker=worker, date=date.today(), hours=Decimal('8'))
        Worker.objects.create(
            farm_profile=FarmProfile.objects.create(
                user=User.objects.create_user(username='other', password='pw'),
                farm_name='Farm B'),
            name='Hidden Hand')
        self.url = reverse('model_list', args=['labor', 'worker'])

    def test_page_renders_with_stats_and_scoping(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Jane Doe')
        self.assertNotContains(response, 'Hidden Hand')
        stats = response.context['stats']
        self.assertEqual(stats['total_count'], 1)
        self.assertEqual(stats['avg_rate'], Decimal('10'))
        self.assertEqual(stats['month_hours'], Decimal('8'))
        row = response.context['workers'][0]
        self.assertEqual(row.hours_total, Decimal('8'))


class LaborPagesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.worker = Worker.objects.create(
            farm_profile=self.farm, name='Jane Doe', hourly_rate=Decimal('10'))
        from .models import PayrollRun, Task
        self.task = Task.objects.create(
            name='Weeding', status='IN_PROGRESS',
            start_date=date(2025, 4, 1), end_date=date(2025, 4, 5))
        self.task.workers.add(self.worker)
        AttendanceRecord.objects.create(
            worker=self.worker, task=self.task, date=date.today(),
            hours=Decimal('8'), overtime_hours=Decimal('2'))
        PayrollRun.objects.create(
            farm_profile=self.farm, start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 30), status='PENDING',
            total_amount=Decimal('1200'))
        self.client.force_login(self.user)

    def test_tasks_page(self):
        response = self.client.get(reverse('model_list', args=['labor', 'task']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Weeding')
        self.assertContains(response, 'Jane Doe')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['In Progress'], 1)

    def test_attendance_page(self):
        response = self.client.get(
            reverse('model_list', args=['labor', 'attendancerecord']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Jane Doe')
        self.assertContains(response, '8h')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        # 8h * 10 + 2h * 10 * 2 = 120
        self.assertEqual(stats['Month Pay'], Decimal('120'))

    def test_payroll_page(self):
        response = self.client.get(
            reverse('model_list', args=['labor', 'payrollrun']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pending')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Pending'], 1)


class PayrollWorkflowTests(TestCase):
    def setUp(self):
        from .models import PayrollRun, Task
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.other_farm = FarmProfile.objects.create(
            user=User.objects.create_user(username='other', password='pw'),
            farm_name='Farm B')
        self.worker = Worker.objects.create(
            farm_profile=self.farm, name='Jane Doe', hourly_rate=Decimal('10'))
        other_worker = Worker.objects.create(
            farm_profile=self.other_farm, name='Hidden Hand', hourly_rate=Decimal('9'))
        AttendanceRecord.objects.create(
            worker=self.worker, date=date(2025, 4, 10),
            hours=Decimal('8'), overtime_hours=Decimal('2'))
        AttendanceRecord.objects.create(
            worker=self.worker, date=date(2025, 4, 11), hours=Decimal('7'))
        AttendanceRecord.objects.create(  # outside period
            worker=self.worker, date=date(2025, 5, 2), hours=Decimal('9'))
        AttendanceRecord.objects.create(  # other farm
            worker=other_worker, date=date(2025, 4, 10), hours=Decimal('8'))
        self.run = PayrollRun.objects.create(
            farm_profile=self.farm, start_date=date(2025, 4, 1),
            end_date=date(2025, 4, 30), status='PENDING')
        self.client.force_login(self.user)

    def test_generate_from_attendance(self):
        total = self.run.generate_from_attendance()
        # 15h * 10 + 2h * 10 * 2 = 190
        self.assertEqual(total, Decimal('190'))
        self.run.refresh_from_db()
        self.assertEqual(self.run.total_amount, Decimal('190'))
        line = self.run.lines.get()
        self.assertEqual(line.worker, self.worker)
        self.assertEqual(line.hours, Decimal('15'))
        self.assertEqual(line.overtime_hours, Decimal('2'))
        self.assertEqual(line.amount, Decimal('190'))

    def test_duplicate_worker_line_rejected(self):
        from django.db import IntegrityError
        from .models import PayrollLine
        PayrollLine.objects.create(
            payroll_run=self.run, worker=self.worker, amount=Decimal('1'))
        with self.assertRaises(IntegrityError):
            PayrollLine.objects.create(
                payroll_run=self.run, worker=self.worker, amount=Decimal('2'))

    def test_completed_run_not_regenerated(self):
        self.run.generate_from_attendance()
        self.run.status = 'COMPLETED'
        self.run.save()
        AttendanceRecord.objects.create(
            worker=self.worker, date=date(2025, 4, 15), hours=Decimal('40'))
        self.assertEqual(self.run.generate_from_attendance(), 0)
        self.run.refresh_from_db()
        self.assertEqual(self.run.total_amount, Decimal('190'))

    def test_generate_and_complete_endpoints(self):
        url = reverse('payroll_generate', args=[self.run.pk])
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.run.refresh_from_db()
        self.assertEqual(self.run.total_amount, Decimal('190'))
        response = self.client.post(reverse('payroll_complete', args=[self.run.pk]))
        self.assertEqual(response.status_code, 302)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, 'COMPLETED')

    def test_payroll_detail_page(self):
        self.run.generate_from_attendance()
        response = self.client.get(reverse('payrollrun_detail', args=[self.run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Jane Doe')
        self.assertContains(response, '15h')


class TaskActionTests(TestCase):
    def setUp(self):
        from .models import Task
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.worker = Worker.objects.create(farm_profile=self.farm, name='Jane')
        self.task = Task.objects.create(name='Weeding', status='ASSIGNED')
        self.task.workers.add(self.worker)
        other_worker = Worker.objects.create(
            farm_profile=FarmProfile.objects.create(
                user=User.objects.create_user(username='o2', password='pw')),
            name='Hidden')
        self.other_task = Task.objects.create(name='Hidden task', status='ASSIGNED')
        self.other_task.workers.add(other_worker)
        self.client.force_login(self.user)

    def test_task_detail_page(self):
        response = self.client.get(reverse('task_detail', args=[self.task.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Weeding')
        self.assertContains(response, 'Jane')

    def test_advance_status(self):
        response = self.client.post(reverse('task_advance', args=[self.task.pk]))
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'IN_PROGRESS')
        self.client.post(reverse('task_advance', args=[self.task.pk]))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'COMPLETED')

    def test_advance_is_farm_scoped(self):
        response = self.client.post(
            reverse('task_advance', args=[self.other_task.pk]))
        self.assertEqual(response.status_code, 404)
        self.other_task.refresh_from_db()
        self.assertEqual(self.other_task.status, 'ASSIGNED')

    def test_worker_detail_page(self):
        AttendanceRecord.objects.create(
            worker=self.worker, date=date(2025, 4, 1), hours=Decimal('8'))
        response = self.client.get(reverse('worker_detail', args=[self.worker.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Jane')
        self.assertContains(response, 'Weeding')
