from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.access import scoped_queryset
from accounts.models import FarmMembership, FarmProfile, User
from fields.models import Field

# Create your tests here.


class LoginPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='farm-manager', password='test-password')

    def test_protected_page_uses_app_login(self):
        response = self.client.get(reverse('report_sales'))
        self.assertRedirects(response, '/login/?next=/reports/sales/', fetch_redirect_response=False)
        login = self.client.get('/login/')
        self.assertContains(login, 'FarmLedger Pro')
        self.assertContains(login, 'name="username"')
        self.assertContains(login, 'name="password"')
        self.assertContains(login, 'csrfmiddlewaretoken')

    def test_login_redirects_to_requested_page(self):
        response = self.client.post('/login/', {
            'username': self.user.username, 'password': 'test-password',
            'next': reverse('report_sales'),
        })
        self.assertRedirects(response, reverse('report_sales'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('report_sales')).status_code, 200)

    def test_invalid_login_and_external_redirect(self):
        invalid = self.client.post('/login/', {
            'username': self.user.username, 'password': 'wrong-password',
        })
        self.assertEqual(invalid.status_code, 200)
        self.assertFalse('_auth_user_id' in self.client.session)
        response = self.client.post('/login/', {
            'username': self.user.username, 'password': 'test-password',
            'next': 'https://example.com/elsewhere',
        })
        self.assertRedirects(response, '/', fetch_redirect_response=False)

    def test_dashboard_topbar_preserves_currency_and_logout_controls(self):
        self.client.force_login(self.user)
        response = self.client.get('/')
        for text in ['aria-label="Breadcrumb"', 'topbar-user', 'topbar-currency-select',
                     'aria-label="Display currency"', 'farm-manager', 'Sign out',
                     'action="/set-currency/"', 'action="/logout/"', 'csrfmiddlewaretoken']:
            self.assertContains(response, text)

    def test_sign_out_requires_post(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get('/logout/').status_code, 405)
        response = self.client.post('/logout/')
        self.assertRedirects(response, '/login/', fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_admin_remains_restricted_to_staff(self):
        self.client.force_login(self.user)
        response = self.client.get('/admin/')
        self.assertRedirects(response, '/admin/login/?next=/admin/', fetch_redirect_response=False)

    def test_login_form_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        data = {'username': self.user.username, 'password': 'test-password'}
        self.assertEqual(client.post('/login/', data).status_code, 403)
        login = client.get('/login/')
        data['csrfmiddlewaretoken'] = login.cookies['csrftoken'].value
        self.assertRedirects(client.post('/login/', data), '/', fetch_redirect_response=False)


class FarmAccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='password')
        self.manager = User.objects.create_user(username='manager', password='password')
        self.worker = User.objects.create_user(username='worker', password='password')
        self.other = User.objects.create_user(username='other', password='password')
        self.farm = FarmProfile.objects.create(user=self.owner, farm_name='Owner Farm')
        self.other_farm = FarmProfile.objects.create(user=self.other, farm_name='Other Farm')
        self.field = Field.objects.create(farm_profile=self.farm, name='North', acreage=2)
        self.other_field = Field.objects.create(farm_profile=self.other_farm, name='South', acreage=2)
        FarmMembership.objects.create(farm_profile=self.farm, user=self.manager, role=User.Role.MANAGER)
        FarmMembership.objects.create(farm_profile=self.farm, user=self.worker, role=User.Role.WORKER)

    def test_membership_limits_read_and_write(self):
        self.assertEqual(list(scoped_queryset(Field, self.manager)), [self.field])
        self.assertEqual(list(scoped_queryset(Field, self.manager, write=True)), [self.field])
        self.assertFalse(scoped_queryset(Field, self.worker, write=True).exists())

    def test_dashboard_refuses_cross_farm_changes(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(f'/dashboard/fields/field/{self.other_field.pk}/edit/').status_code, 404)
        self.assertNotContains(self.client.get('/dashboard/fields/field/'), 'South')

    def test_api_is_scoped_and_user_roles_not_editable(self):
        client = APIClient()
        client.force_authenticate(user=self.manager)
        self.assertNotContains(client.get('/api/fields/'), 'South')
        self.assertEqual(client.patch(
            f'/api/fields/{self.other_field.pk}/', {'name': 'Changed'}, format='json',
        ).status_code, 404)
        self.assertEqual(client.patch(
            f'/api/users/{self.owner.pk}/', {'is_staff': True}, format='json',
        ).status_code, 403)

    def test_reports_only_include_accessible_farms(self):
        from datetime import date
        from decimal import Decimal
        from crops.models import Crop, CropSeason, HarvestRecord, Sale
        from expenses.models import Category, Expense
        from budgets.models import Budget, BudgetItem

        crop = Crop.objects.create(name='Maize')
        season = CropSeason.objects.create(
            field=self.field, crop=crop, planting_date=date(2025, 1, 1))
        harvest = HarvestRecord.objects.create(
            crop_season=season, harvest_date=date(2025, 3, 1), quantity=Decimal('10'))
        Sale.objects.create(
            harvest=harvest, sale_date=date(2025, 4, 1),
            quantity=Decimal('5'), unit_price=Decimal('10'))

        other_crop = Crop.objects.create(name='SecretCrop')
        other_season = CropSeason.objects.create(
            field=self.other_field, crop=other_crop, planting_date=date(2025, 1, 1))
        other_harvest = HarvestRecord.objects.create(
            crop_season=other_season, harvest_date=date(2025, 3, 1), quantity=Decimal('99'))
        Sale.objects.create(
            harvest=other_harvest, sale_date=date(2025, 4, 1),
            quantity=Decimal('50'), unit_price=Decimal('999'))

        self.client.force_login(self.manager)
        response = self.client.get('/reports/sales/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['gross'], Decimal('50'))
        self.assertEqual(response.context['order_count'], 1)
        self.assertNotContains(response, 'SecretCrop')

    def test_field_with_season_history_cannot_be_deleted(self):
        from datetime import date
        from decimal import Decimal
        from crops.models import Crop, CropSeason, HarvestRecord

        crop = Crop.objects.create(name='Beans')
        season = CropSeason.objects.create(
            field=self.field, crop=crop, planting_date=date(2025, 1, 1))
        HarvestRecord.objects.create(
            crop_season=season, harvest_date=date(2025, 3, 1), quantity=Decimal('10'))

        self.client.force_login(self.owner)
        response = self.client.post(f'/dashboard/fields/field/{self.field.pk}/delete/')
        self.assertEqual(response.status_code, 409)
        self.assertTrue(Field.objects.filter(pk=self.field.pk).exists())
