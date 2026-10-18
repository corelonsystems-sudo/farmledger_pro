from datetime import date
from decimal import Decimal

from django.apps import apps
from django.test import TestCase
from django.urls import reverse

from accounts.models import FarmProfile, User
from budgets.models import Budget, BudgetItem
from expenses.models import Category, Expense

# Create your tests here.


class ScheduleLineTests(TestCase):
    def setUp(self):
        from django.apps import apps
        self.model = apps.get_model('expenses', 'ScheduleLine')
        self.admin = User.objects.create_superuser(username='schedule-admin', password='test-password')
        self.client.force_login(self.admin)
        self.list_url = reverse('model_list', kwargs={'app': 'expenses', 'model': 'scheduleline'})
        self.create_url = reverse('model_create', kwargs={'app': 'expenses', 'model': 'scheduleline'})

    def test_create_line_and_select_it_in_budget_code_form(self):
        response = self.client.post(self.create_url, {
            'code': 'irrigation', 'name': 'Irrigation infrastructure', 'description': 'Water systems',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        line = self.model.objects.get(code='irrigation')
        form_url = reverse('model_create', kwargs={'app': 'expenses', 'model': 'category'})
        self.assertContains(self.client.get(form_url), 'Irrigation infrastructure')
        response = self.client.post(form_url, {
            'code': 'IRR', 'name': 'Irrigation', 'schedule_f_line': line.pk, 'description': '',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(response.json()['ok'])
        category = Category.objects.get(code='IRR')
        self.assertEqual(category.schedule_f_line_id, line.pk)
        self.assertEqual(category.get_schedule_f_line_display(), line.name)
        self.assertContains(self.client.get(self.list_url), 'Budget Schedule Lines')
        self.assertContains(self.client.get(self.list_url, {'q': 'irrigation'}), 'Irrigation infrastructure')
        codes_url = reverse('model_list', kwargs={'app': 'expenses', 'model': 'category'})
        filtered = self.client.get(codes_url, {'schedule_f': line.code})
        self.assertContains(filtered, 'Irrigation infrastructure')
        self.assertEqual(filtered.context['page_obj'].paginator.count, 1)
        self.assertContains(self.client.get(self.list_url), self.list_url)

    def test_edit_code_updates_line_and_linked_codes(self):
        line = self.model.objects.get(code='part1_other')
        category = Category.objects.create(name='Test code', code='TEST', schedule_f_line=line)
        edit_url = reverse('model_update', kwargs={
            'app': 'expenses', 'model': 'scheduleline', 'pk': line.pk,
        })
        response = self.client.post(edit_url, {
            'code': 'part1_renamed', 'name': 'Other costs', 'description': '',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(response.json()['ok'])
        line.refresh_from_db()
        self.assertEqual(line.code, 'part1_renamed')
        # The linked budget code still points at the same schedule line.
        category.refresh_from_db()
        self.assertEqual(category.schedule_f_line, line)
        self.assertEqual(category.get_schedule_f_line_display(), 'Other costs')
        delete_url = reverse('model_delete', kwargs={
            'app': 'expenses', 'model': 'scheduleline', 'pk': line.pk,
        })
        self.assertEqual(self.client.post(delete_url).status_code, 409)
        self.assertTrue(self.model.objects.filter(pk=line.pk).exists())

    def test_seeded_choices_and_duplicate_codes(self):
        self.assertTrue(self.model.objects.filter(code='part1_seeds_plants', name='Seeds and plants').exists())
        response = self.client.post(self.create_url, {
            'code': 'part1_other', 'name': 'Duplicate', 'description': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'already exists')

    def test_non_admin_can_read_but_cannot_modify_catalog(self):
        user = User.objects.create_user(username='schedule-reader', password='test-password')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        self.assertEqual(self.client.post(self.create_url, {'code': 'x', 'name': 'X'}).status_code, 403)
        line = self.model.objects.first()
        edit_url = reverse('model_update', kwargs={
            'app': 'expenses', 'model': 'scheduleline', 'pk': line.pk,
        })
        self.assertEqual(self.client.post(edit_url, {'name': 'Changed'}).status_code, 404)


class BudgetSubCodeTests(TestCase):
    def setUp(self):
        self.model = apps.get_model('expenses', 'BudgetSubCode')
        self.admin = User.objects.create_superuser(username='subcode-admin', password='test-password')
        self.client.force_login(self.admin)
        self.category = Category.objects.create(name='Seed Purchases', code='SEED')
        self.other_category = Category.objects.create(name='Feed Purchases', code='FEED')
        self.list_url = reverse('model_list', kwargs={'app': 'expenses', 'model': 'budgetsubcode'})
        self.create_url = reverse('model_create', kwargs={'app': 'expenses', 'model': 'budgetsubcode'})

    def test_create_subcode_belongs_to_a_code_and_lists_it(self):
        response = self.client.post(self.create_url, {
            'category': self.category.pk, 'code': 'SEED-A', 'name': 'Hybrid maize seed',
            'description': 'Certified seed',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(response.json()['ok'])
        sub = self.model.objects.get(name='Hybrid maize seed')
        self.assertEqual(sub.category, self.category)
        page = self.client.get(self.list_url)
        self.assertContains(page, 'Budget Subcodes')
        self.assertContains(page, 'SEED-A')
        self.assertContains(page, 'Seed Purchases')

    def test_code_and_name_must_be_unique_within_the_parent_code(self):
        self.model.objects.create(category=self.category, code='A', name='Sub A')
        duplicate_code = self.client.post(self.create_url, {
            'category': self.category.pk, 'code': 'A', 'name': 'Another',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertContains(duplicate_code, 'already exists')
        duplicate_name = self.client.post(self.create_url, {
            'category': self.category.pk, 'code': 'B', 'name': 'Sub A',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertContains(duplicate_name, 'already exists')
        self.assertEqual(self.model.objects.count(), 1)
        allowed = self.client.post(self.create_url, {
            'category': self.other_category.pk, 'code': 'A', 'name': 'Sub A',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertTrue(allowed.json()['ok'])

    def test_subcodes_show_on_the_code_detail_and_codes_list(self):
        self.model.objects.create(category=self.category, code='A', name='Sub A')
        self.model.objects.create(category=self.category, code='B', name='Sub B')
        detail = self.client.get(reverse('budget_code_detail', kwargs={'pk': self.category.pk}))
        self.assertContains(detail, 'Budget Subcodes')
        self.assertContains(detail, '2 subcodes')
        self.assertContains(detail, 'Sub A')
        self.assertContains(detail, 'Sub B')
        codes_page = self.client.get(reverse('model_list', kwargs={'app': 'expenses', 'model': 'category'}))
        self.assertContains(codes_page, 'Subcodes')

    def test_non_admin_can_read_but_cannot_modify_subcodes(self):
        user = User.objects.create_user(username='subcode-reader', password='test-password')
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.list_url).status_code, 200)
        self.assertEqual(self.client.post(self.create_url, {
            'category': self.category.pk, 'name': 'Nope',
        }).status_code, 403)
        sub = self.model.objects.create(category=self.category, code='A', name='Sub A')
        edit_url = reverse('model_update', kwargs={
            'app': 'expenses', 'model': 'budgetsubcode', 'pk': sub.pk,
        })
        self.assertEqual(self.client.post(edit_url, {
            'category': self.category.pk, 'name': 'Changed',
        }).status_code, 404)


class CategoryDeleteTests(TestCase):
    def setUp(self):
        user = User.objects.create_superuser(username='delete-test', password='test-password')
        self.client.force_login(user)
        self.category = Category.objects.create(name='Insurance')
        self.url = reverse('model_delete', kwargs={
            'app': 'expenses', 'model': 'category', 'pk': self.category.pk,
        })

    def test_protected_category_delete_in_modal(self):
        farm = FarmProfile.objects.create(user=User.objects.get(username='delete-test'), farm_name='Test farm')
        item = BudgetItem.objects.create(
            budget=Budget.objects.create(name='Test budget'), category=self.category,
            planned_amount=Decimal('5000.00'),
        )
        expense = Expense.objects.create(
            farm_profile=farm, category=self.category, budget_item=item,
            amount=Decimal('3500.00'), date=date(2025, 1, 15),
        )

        response = self.client.post(f'{self.url}?modal=1', HTTP_X_REQUESTED_WITH='XMLHttpRequest')

        self.assertEqual(response.status_code, 409)
        self.assertContains(response, 'Cannot delete this Budget Code', status_code=409)
        self.assertContains(response, '1 expense', status_code=409)
        self.assertContains(response, '1 budget item', status_code=409)
        self.assertNotContains(response, 'Yes, Delete', status_code=409)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())
        self.assertTrue(Expense.objects.filter(pk=expense.pk).exists())
        self.assertTrue(BudgetItem.objects.filter(pk=item.pk).exists())

    def test_protected_category_delete_without_modal(self):
        BudgetItem.objects.create(
            budget=Budget.objects.create(name='Test budget'), category=self.category,
            planned_amount=Decimal('100.00'),
        )
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, '1 budget item', status_code=409)
        self.assertTrue(Category.objects.filter(pk=self.category.pk).exists())

    def test_unreferenced_category_can_be_deleted(self):
        response = self.client.post(self.url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertFalse(Category.objects.filter(pk=self.category.pk).exists())
