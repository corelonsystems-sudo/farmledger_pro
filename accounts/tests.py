import shutil

from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from django.conf import settings
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

    def test_all_sidebar_linked_lists_render(self):
        """Every model linked in the sidebar resolves to a working list page."""
        linked = [
            ('livestock', 'animalspecies'), ('livestock', 'animalgroup'),
            ('livestock', 'animalrecord'), ('livestock', 'animalhealthrecord'),
            ('livestock', 'breedingrecord'), ('livestock', 'animalfeedlog'),
            ('livestock', 'milkproductionrecord'), ('livestock', 'poultrybatch'),
            ('livestock', 'eggproductionrecord'), ('livestock', 'poultryfeedlog'),
            ('livestock', 'poultryhealthrecord'),
            ('fields', 'field'), ('fields', 'landparcel'),
            ('equipment', 'equipment'), ('equipment', 'maintenancelog'),
            ('equipment', 'equipmentusage'),
            ('inventory', 'inventoryitem'), ('inventory', 'inventorytransaction'),
            ('inventory', 'inventoryalert'),
            ('labor', 'worker'), ('labor', 'task'),
            ('labor', 'attendancerecord'), ('labor', 'payrollrun'),
            ('budgets', 'budget'), ('budgets', 'budgetitem'),
            ('budgets', 'budgetincomeplan'),
            ('integrations', 'bankaccount'), ('integrations', 'banktransaction'),
        ]
        self.client.force_login(self.owner)
        for app, model in linked:
            with self.subTest(model=f'{app}.{model}'):
                response = self.client.get(reverse('model_list', args=[app, model]))
                self.assertEqual(response.status_code, 200)


class SidebarRoleAccessTests(TestCase):
    """Sidebar links are filtered by the user's effective farm role."""

    PAGE = '/dashboard/fields/field/'
    # Markers are href="..." so the currency form's `next` hidden input and
    # other URL echoes can't produce false positives.
    OPS = ('href="/dashboard/fields/field/"', 'href="/dashboard/crops/cropseason/"',
           'href="/dashboard/livestock/animalrecord/"', 'href="/dashboard/labor/task/"')
    FINANCE = ('href="/dashboard/expenses/expense/"', 'href="/dashboard/income/income/"',
               'href="/dashboard/budgets/budget/"', 'href="/reports/"')

    def setUp(self):
        self.owner = User.objects.create_user(username='owner', password='pw')
        self.manager = User.objects.create_user(username='manager', password='pw')
        self.worker = User.objects.create_user(username='worker', password='pw')
        self.accountant = User.objects.create_user(username='acct', password='pw')
        self.farm = FarmProfile.objects.create(user=self.owner, farm_name='Farm A')
        Field.objects.create(farm_profile=self.farm, name='North', acreage=2)
        FarmMembership.objects.create(
            farm_profile=self.farm, user=self.manager, role=User.Role.MANAGER)
        FarmMembership.objects.create(
            farm_profile=self.farm, user=self.worker, role=User.Role.WORKER)
        FarmMembership.objects.create(
            farm_profile=self.farm, user=self.accountant, role=User.Role.ACCOUNTANT)

    def test_owner_sees_everything(self):
        self.client.force_login(self.owner)
        response = self.client.get(self.PAGE)
        for marker in self.OPS + self.FINANCE + (
                'href="/dashboard/labor/payrollrun/"', 'href="/dashboard/settings/"'):
            self.assertContains(response, marker)

    def test_manager_sees_everything(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.PAGE)
        for marker in self.OPS + self.FINANCE + (
                'href="/dashboard/labor/payrollrun/"', 'href="/dashboard/settings/"'):
            self.assertContains(response, marker)

    def test_worker_sees_ops_only(self):
        self.client.force_login(self.worker)
        response = self.client.get(self.PAGE)
        for marker in self.OPS:
            self.assertContains(response, marker)
        for marker in self.FINANCE + (
                'href="/dashboard/labor/payrollrun/"', 'href="/dashboard/settings/"'):
            self.assertNotContains(response, marker)
        # read-only: no add/edit affordances on the page either
        self.assertNotContains(response, 'New Field')

    def test_accountant_sees_finance_only(self):
        self.client.force_login(self.accountant)
        response = self.client.get(self.PAGE)
        for marker in self.FINANCE:
            self.assertContains(response, marker)
        for marker in self.OPS + (
                'href="/dashboard/labor/payrollrun/"', 'href="/dashboard/settings/"'):
            self.assertNotContains(response, marker)

    def test_farmless_user_sees_settings_only(self):
        fresh = User.objects.create_user(username='fresh', password='pw')
        self.client.force_login(fresh)
        response = self.client.get(self.PAGE)
        self.assertContains(response, 'href="/dashboard/settings/"')
        for marker in self.OPS + self.FINANCE:
            self.assertNotContains(response, marker)


class AuditLogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.client.force_login(self.user)

    def test_dashboard_write_creates_audit_entry_with_user(self):
        from audit.models import AuditLog
        AuditLog.objects.all().delete()
        self.client.post(reverse('model_create', args=['fields', 'field']), {
            'name': 'Audited Field', 'acreage': '4', 'farm_profile': self.farm.pk,
            'soil_type': 'LOAM', 'is_active': 'on',
        })
        entry = AuditLog.objects.filter(action_type='CREATE').latest('timestamp')
        self.assertEqual(entry.user, self.user)
        self.assertEqual(entry.content_type.model, 'field')
        self.assertIsNotNone(entry.ip_address)

    def test_update_and_delete_are_audited(self):
        from audit.models import AuditLog
        field = Field.objects.create(
            farm_profile=self.farm, name='F', acreage=1)
        AuditLog.objects.all().delete()
        field.name = 'F2'
        field.save()
        field.delete()
        actions = list(AuditLog.objects.values_list('action_type', flat=True))
        self.assertEqual(actions, ['DELETE', 'UPDATE'])


class GlobalCatalogReadOnlyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        FarmProfile.objects.create(user=self.user, farm_name='Farm A')

    def test_owner_sees_no_write_actions_on_catalogs(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('model_list', args=['crops', 'crop']))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_write'])

    def test_superuser_keeps_catalog_write_actions(self):
        superuser = User.objects.create_superuser(
            username='admin', password='pw', email='a@x.com')
        self.client.force_login(superuser)
        response = self.client.get(reverse('model_list', args=['crops', 'crop']))
        self.assertTrue(response.context['can_write'])


class ApiCoverageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_new_endpoints_respond(self):
        for name in ['api_landparcel_list', 'api_harvest_list', 'api_sale_list',
                     'api_customer_list', 'api_income_list', 'api_budget_list',
                     'api_budgetitem_list', 'api_incomeplan_list', 'api_inventorytxn_list',
                     'api_inventoryalert_list', 'api_maintenance_list',
                     'api_usage_list', 'api_task_list',
                     'api_attendance_list', 'api_payroll_list',
                     'api_payrollline_list']:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)

    def test_unauthenticated_is_rejected(self):
        self.client.force_authenticate(None)
        response = self.client.get(reverse('api_sale_list'))
        self.assertIn(response.status_code, (401, 403))


class DatabaseBackupTests(TestCase):
    """Settings > Database tab: SQLite file export and validated import.

    The test runner uses an in-memory SQLite database, so the views are pointed
    at a real temporary database file via _sqlite_db_path."""

    def setUp(self):
        import shutil
        import sqlite3
        import tempfile
        import unittest.mock
        from pathlib import Path
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_file = Path(self.tmpdir.name) / 'test-db.sqlite3'
        con = sqlite3.connect(str(self.db_file))
        con.execute('CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app VARCHAR, name VARCHAR, applied VARCHAR)')
        con.execute('CREATE TABLE accounts_user (id INTEGER PRIMARY KEY, password VARCHAR, username VARCHAR UNIQUE)')
        con.execute("INSERT INTO accounts_user (password, username) VALUES ('x', 'seeded')")
        con.execute('PRAGMA journal_mode=DELETE')
        con.commit()
        con.close()
        patcher = unittest.mock.patch(
            'farmledger.views._sqlite_db_path', return_value=self.db_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmpdir.cleanup)
        self.admin = User.objects.create_user(
            username='dbadmin', password='pw', is_superuser=True)
        self.staff_farm = FarmProfile.objects.create(
            user=User.objects.create_user(username='plain', password='pw'),
            farm_name='Plain Farm')
        self.client.force_login(self.admin)

    def test_settings_shows_database_tab_for_superuser(self):
        response = self.client.get(reverse('farm_settings'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'tab-database')
        self.assertContains(response, 'Download database file')
        self.assertContains(response, 'test-db.sqlite3')

    def test_export_downloads_sqlite_file(self):
        response = self.client.get(reverse('database_export'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/octet-stream')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertTrue(response['Content-Disposition'].endswith('.sqlite3"'))
        body = b''.join(response.streaming_content)
        self.assertTrue(body.startswith(b'SQLite format 3\x00'))

    def test_export_forbidden_for_non_superuser(self):
        self.client.force_login(self.staff_farm.user)
        response = self.client.get(reverse('database_export'))
        self.assertEqual(response.status_code, 403)

    def _valid_db_file(self):
        import os
        import sqlite3
        import tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        fd, path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE django_migrations (id INTEGER PRIMARY KEY, app VARCHAR, name VARCHAR, applied VARCHAR)')
        con.execute('CREATE TABLE accounts_user (id INTEGER PRIMARY KEY, password VARCHAR, username VARCHAR UNIQUE)')
        con.execute('PRAGMA journal_mode=DELETE')
        con.commit()
        con.close()
        with open(path, 'rb') as f:
            data = f.read()
        os.unlink(path)
        return SimpleUploadedFile('import.sqlite3', data)

    def test_import_rejects_non_sqlite_file(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        response = self.client.post(
            reverse('database_import'),
            {'dbfile': SimpleUploadedFile('bad.txt', b'not a database'),
             'confirm': '1'})
        self.assertRedirects(
            response, reverse('farm_settings') + '?tab=database',
            fetch_redirect_response=False)
        response = self.client.get(reverse('farm_settings') + '?tab=database')
        self.assertContains(response, 'not an SQLite database')

    def test_import_requires_confirmation(self):
        response = self.client.post(
            reverse('database_import'), {'dbfile': self._valid_db_file()})
        self.assertRedirects(
            response, reverse('farm_settings') + '?tab=database',
            fetch_redirect_response=False)
        response = self.client.get(reverse('farm_settings') + '?tab=database')
        self.assertContains(response, 'Tick the confirmation box')

    def test_import_rejects_foreign_database(self):
        import os
        import sqlite3
        import tempfile
        from django.core.files.uploadedfile import SimpleUploadedFile
        fd, path = tempfile.mkstemp(suffix='.sqlite3')
        os.close(fd)
        con = sqlite3.connect(path)
        con.execute('CREATE TABLE unrelated (id INTEGER PRIMARY KEY)')
        con.commit()
        con.close()
        with open(path, 'rb') as f:
            upload = SimpleUploadedFile('foreign.sqlite3', f.read())
        os.unlink(path)
        response = self.client.post(
            reverse('database_import'), {'dbfile': upload, 'confirm': '1'})
        response = self.client.get(reverse('farm_settings') + '?tab=database')
        self.assertContains(response, 'missing table(s)')

    def test_import_swaps_database_and_keeps_backup(self):
        import shutil
        import sqlite3
        from pathlib import Path
        from django.conf import settings
        response = self.client.post(
            reverse('database_import'),
            {'dbfile': self._valid_db_file(), 'confirm': '1'})
        self.assertRedirects(
            response, reverse('farm_settings') + '?tab=database',
            fetch_redirect_response=False)
        # The uploaded file replaced the live one (empty user table).
        con = sqlite3.connect(str(self.db_file))
        count = con.execute('SELECT COUNT(*) FROM accounts_user').fetchone()[0]
        con.close()
        self.assertEqual(count, 0)
        # A timestamped backup of the previous database exists.
        backups = list((Path(settings.BASE_DIR) / 'backups').glob('pre-import-*.sqlite3'))
        self.assertEqual(len(backups), 1)
        bcon = sqlite3.connect(str(backups[0]))
        bcount = bcon.execute('SELECT COUNT(*) FROM accounts_user').fetchone()[0]
        bcon.close()
        self.assertEqual(bcount, 1)  # the seeded user
        shutil.rmtree(Path(settings.BASE_DIR) / 'backups')


class SystemLogoConfigTests(TestCase):
    """The system logo is available as a static asset and the sidebar brand
    falls back to it when the current farm has no uploaded logo."""

    def setUp(self):
        self.user = User.objects.create_user(username='logo-check', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Logo Farm')
        self.client.force_login(self.user)

    def test_system_logo_static_file_is_configured(self):
        from django.conf import settings
        from django.contrib.staticfiles import finders
        assert settings.STATIC_URL == '/static/'
        path = finders.find('system-logo.png')
        assert path is not None and 'system-logo.png' in path, 'PNG system logo not discoverable via staticfiles finders'

    def test_sidebar_brand_uses_static_logo_without_farm_logo(self):
        response = self.client.get(reverse('model_list', kwargs={'app': 'crops', 'model': 'sale'}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'src="{settings.STATIC_URL}system-logo.png"')

    def test_farm_logo_upload_url_is_served_in_debug(self):
        from django.conf import settings
        self.assertEqual(settings.MEDIA_URL, '/media/')
        self.assertEqual(settings.MEDIA_ROOT.name, 'media')

    def test_login_page_brand_shows_png_logo(self):
        page = Client().get(reverse('login'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, settings.STATIC_URL + 'system-logo.png')
