from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import FarmProfile, User
from .models import Equipment, MaintenanceLog
from datetime import date


class EquipmentListPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        tractor = Equipment.objects.create(
            farm_profile=self.farm, name='Tractor 1', equipment_type='TRACTOR',
            purchase_cost=Decimal('20000'), current_value=Decimal('15000'),
            hours_used=Decimal('120'))
        MaintenanceLog.objects.create(
            equipment=tractor, date=date(2025, 3, 1),
            description='Oil change', cost=Decimal('150'))
        Equipment.objects.create(
            farm_profile=FarmProfile.objects.create(
                user=User.objects.create_user(username='other', password='pw'),
                farm_name='Farm B'),
            name='Hidden Harvester', equipment_type='HARVESTER')
        self.url = reverse('model_list', args=['equipment', 'equipment'])

    def test_page_renders_with_stats_and_scoping(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Tractor 1')
        self.assertNotContains(response, 'Hidden Harvester')
        stats = response.context['stats']
        self.assertEqual(stats['total_count'], 1)
        self.assertEqual(stats['asset_value'], Decimal('15000'))
        self.assertEqual(stats['maintenance_spend'], Decimal('150'))
        row = response.context['equipment'][0]
        self.assertEqual(row.maintenance_total, Decimal('150'))

    def test_type_filter_applies(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url, {'type': 'HARVESTER'})
        self.assertEqual(response.context['stats']['total_count'], 0)
        response = self.client.get(self.url, {'type': 'TRACTOR'})
        self.assertEqual(response.context['stats']['total_count'], 1)
