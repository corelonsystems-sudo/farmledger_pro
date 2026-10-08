from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import FarmProfile, User
from .models import Field


class FieldListPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        Field.objects.create(
            farm_profile=self.farm, name='North Field', acreage=Decimal('10'),
            soil_type='CLAY')
        Field.objects.create(
            farm_profile=FarmProfile.objects.create(
                user=User.objects.create_user(username='other', password='pw'),
                farm_name='Farm B'),
            name='Hidden Field', acreage=Decimal('5'))
        self.url = reverse('model_list', args=['fields', 'field'])

    def test_page_renders_with_stats_and_scoping(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'North Field')
        self.assertNotContains(response, 'Hidden Field')
        self.assertEqual(response.context['stats']['total_count'], 1)
        self.assertEqual(response.context['stats']['total_acreage'], Decimal('10'))

    def test_soil_and_status_filters_apply(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url, {'soil': 'SANDY'})
        self.assertEqual(response.context['stats']['total_count'], 0)
        response = self.client.get(self.url, {'status': '0'})
        self.assertEqual(response.context['stats']['total_count'], 0)
        response = self.client.get(self.url, {'status': '1'})
        self.assertEqual(response.context['stats']['total_count'], 1)
