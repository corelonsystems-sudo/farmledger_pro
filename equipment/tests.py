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


class EquipmentLogListPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.equipment = Equipment.objects.create(
            farm_profile=self.farm, name='Tractor 1')
        MaintenanceLog.objects.create(
            equipment=self.equipment, date=date(2025, 3, 1),
            description='Oil change', cost=Decimal('150'), performed_by='Sam')
        from .models import EquipmentUsage
        EquipmentUsage.objects.create(
            equipment=self.equipment, date=date(2025, 3, 2),
            hours=Decimal('4'), description='Ploughing')
        self.client.force_login(self.user)

    def test_maintenance_page(self):
        response = self.client.get(
            reverse('model_list', args=['equipment', 'maintenancelog']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Oil change')
        self.assertContains(response, 'Sam')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Total Spend'], Decimal('150'))
        self.assertEqual(stats['Equipment Serviced'], 1)

    def test_usage_page(self):
        response = self.client.get(
            reverse('model_list', args=['equipment', 'equipmentusage']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ploughing')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Total Hours'], '4h')


class EquipmentSyncAndDetailTests(TestCase):
    def setUp(self):
        from .models import EquipmentUsage
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.equipment = Equipment.objects.create(
            farm_profile=self.farm, name='Baler')
        MaintenanceLog.objects.create(
            equipment=self.equipment, date=date(2025, 2, 1),
            description='Belt replaced', cost=Decimal('80'))
        self.usage = EquipmentUsage.objects.create(
            equipment=self.equipment, date=date(2025, 2, 2), hours=Decimal('5'))
        self.client.force_login(self.user)

    def test_hours_used_syncs_from_usage(self):
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.hours_used, Decimal('5'))
        from .models import EquipmentUsage
        extra = EquipmentUsage.objects.create(
            equipment=self.equipment, date=date(2025, 2, 3), hours=Decimal('2.5'))
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.hours_used, Decimal('7.5'))
        extra.delete()
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.hours_used, Decimal('5'))

    def test_equipment_detail_page(self):
        response = self.client.get(
            reverse('equipment_detail', args=[self.equipment.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Baler')
        self.assertContains(response, 'Belt replaced')
        self.assertContains(response, '5h')


class EquipmentExpenseSyncTests(TestCase):
    """Maintenance logs and equipment purchases auto-post Expense rows."""

    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')

    def test_maintenance_posts_expense(self):
        from expenses.models import Expense
        eq = Equipment.objects.create(farm_profile=self.farm, name='Tractor')
        log = MaintenanceLog.objects.create(
            equipment=eq, date=date(2026, 1, 10),
            description='Oil change', cost=Decimal('500'))
        expense = Expense.objects.get(maintenance_log=log)
        self.assertEqual(expense.amount, Decimal('500'))
        self.assertEqual(expense.date, date(2026, 1, 10))
        self.assertEqual(expense.equipment, eq)
        self.assertEqual(expense.farm_profile, self.farm)
        self.assertEqual(expense.category.name, 'Equipment Maintenance')
        self.assertEqual(
            expense.category.schedule_f_line.code, 'part1_repairs_maintenance')

    def test_maintenance_update_and_delete_sync(self):
        from expenses.models import Expense
        eq = Equipment.objects.create(farm_profile=self.farm, name='Sprayer')
        log = MaintenanceLog.objects.create(
            equipment=eq, date=date(2026, 1, 10),
            description='Filter', cost=Decimal('100'))
        log.cost = Decimal('250')
        log.save()
        self.assertEqual(Expense.objects.get(maintenance_log=log).amount, Decimal('250'))
        log.delete()
        self.assertFalse(Expense.objects.exists())

    def test_zero_cost_removes_expense(self):
        from expenses.models import Expense
        eq = Equipment.objects.create(farm_profile=self.farm, name='Pump')
        log = MaintenanceLog.objects.create(
            equipment=eq, date=date(2026, 1, 10),
            description='Checkup', cost=Decimal('80'))
        log.cost = Decimal('0')
        log.save()
        self.assertFalse(Expense.objects.filter(maintenance_log=log).exists())

    def test_purchase_posts_expense(self):
        from expenses.models import Expense
        eq = Equipment.objects.create(
            farm_profile=self.farm, name='Harvester',
            purchase_date=date(2026, 1, 5), purchase_cost=Decimal('30000'))
        expense = Expense.objects.get(equipment_purchase=eq)
        self.assertEqual(expense.amount, Decimal('30000'))
        self.assertEqual(expense.date, date(2026, 1, 5))
        self.assertEqual(expense.category.schedule_f_line.code, 'part1_depreciation')

    def test_leased_purchase_posts_no_expense(self):
        from expenses.models import Expense
        eq = Equipment.objects.create(
            farm_profile=self.farm, name='Rented truck',
            ownership=Equipment.Ownership.LEASED, purchase_cost=Decimal('5000'))
        self.assertFalse(Expense.objects.filter(equipment_purchase=eq).exists())

    def test_purchase_edit_syncs(self):
        from expenses.models import Expense
        eq = Equipment.objects.create(
            farm_profile=self.farm, name='Trailer', purchase_cost=Decimal('9000'))
        eq.purchase_cost = Decimal('12000')
        eq.save()
        self.assertEqual(
            Expense.objects.get(equipment_purchase=eq).amount, Decimal('12000'))


class EquipmentNewFieldsTests(TestCase):
    def setUp(self):
        from .models import EquipmentUsage
        from labor.models import Worker
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.worker = Worker.objects.create(farm_profile=self.farm, name='John')
        self.eq = Equipment.objects.create(
            farm_profile=self.farm, name='Tractor',
            opening_hours=Decimal('100'))
        EquipmentUsage.objects.create(
            equipment=self.eq, date=date(2026, 1, 10),
            hours=Decimal('6'), operator=self.worker,
            fuel_liters=Decimal('20'), fuel_cost=Decimal('120'))
        self.client.force_login(self.user)

    def test_opening_hours_plus_usage(self):
        self.eq.refresh_from_db()
        self.assertEqual(self.eq.hours_used, Decimal('106'))

    def test_detail_shows_new_fields(self):
        MaintenanceLog.objects.create(
            equipment=self.eq, date=date(2026, 1, 5),
            description='Service', cost=Decimal('200'),
            next_due_date=date(2020, 1, 1))
        response = self.client.get(
            reverse('equipment_detail', args=[self.eq.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Service overdue')
        self.assertContains(response, 'John')
        self.assertContains(response, '20L')
        self.assertTrue(response.context['service_overdue'])
