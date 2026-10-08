from django.test import TestCase

# Create your tests here.


class IncomeSourceHeaderTests(TestCase):
    def setUp(self):
        from accounts.models import FarmProfile, User
        from income.models import IncomeSource
        self.user = User.objects.create_user(username='source-owner', password='test-password')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Source Test Farm',
                                               location='Header location', phone='+256 700 123456', email='farm@example.com')
        self.source = IncomeSource.objects.create(name='Crop Sales', code='CS')
        self.client.force_login(self.user)

    def test_header_matches_green_card_and_respects_permissions(self):
        from django.urls import reverse
        from accounts.models import FarmMembership, User
        url = reverse('income_source_detail', kwargs={'pk': self.source.pk})
        response = self.client.get(url)
        self.assertContains(response, 'Back to Income Sources')
        header = response.content.decode().split('<header class="source-toolbar"', 1)[1].split('</header>', 1)[0]
        for text in [self.source.name, self.source.code, 'Record Income', 'Print']:
            self.assertIn(text, header)
        for text in [self.farm.farm_name, self.farm.location, self.farm.phone, self.farm.email]:
            self.assertNotIn(text, header)
        self.assertNotIn('Edit Source', header)
        worker = User.objects.create_user(username='source-worker')
        FarmMembership.objects.create(user=worker, farm_profile=self.farm, role='WORKER')
        self.client.force_login(worker)
        response = self.client.get(url)
        header = response.content.decode().split('<header class="source-toolbar"', 1)[1].split('</header>', 1)[0]
        self.assertNotIn('Record Income', header)
        self.assertNotIn('Edit Source', header)
        admin = User.objects.create_superuser(username='source-admin', password='test-password')
        self.client.force_login(admin)
        self.assertContains(self.client.get(url), 'Edit Source')

    def test_income_totals_and_filters_remain_farm_scoped(self):
        from datetime import date
        from django.urls import reverse
        from accounts.models import FarmProfile, User
        from income.models import Income
        for amount, status in [(10, 'PENDING'), (20, 'RECONCILED')]:
            Income.objects.create(farm_profile=self.farm, source=self.source,
                                  amount=amount, date=date(2026, 1, 1), reconciliation_status=status)
        private_farm = FarmProfile.objects.create(user=User.objects.create_user(username='private-source'),
                                                  farm_name='Private Source Farm', email='private@example.com')
        Income.objects.create(farm_profile=private_farm, source=self.source,
                              amount=999, date=date(2026, 1, 1), customer='Private Customer')
        url = reverse('income_source_detail', kwargs={'pk': self.source.pk})
        response = self.client.get(url)
        self.assertEqual(response.context['total_count'], 2)
        self.assertEqual(response.context['total_amount'], 30)
        self.assertNotContains(response, 'Private Customer')
        self.assertNotContains(response, private_farm.email)
        filtered = self.client.get(url, {'status': 'RECONCILED'})
        self.assertEqual(filtered.context['total_count'], 1)
        self.assertEqual(filtered.context['total_amount'], 20)

