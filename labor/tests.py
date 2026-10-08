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
