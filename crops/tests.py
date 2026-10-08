from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import FarmProfile, User
from crops.models import Crop, CropSeason, HarvestRecord, ProcessingStage, Sale
from fields.models import Field
from income.models import Income
from inventory.models import InventoryItem

# Create your tests here.


class ReportsWorkspaceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='report-workspace', password='test-password')
        FarmProfile.objects.create(user=self.user, farm_name='Workspace Farm')
        self.client.force_login(self.user)

    def test_all_reports_are_hidden_tab_sections_on_one_page(self):
        response = self.client.get(reverse('reports'))
        self.assertContains(response, 'aria-label="Report sections"')
        body = response.content.decode()
        self.assertRegex(body, r'id="tab-report-overview"[^>]*aria-selected="true"')
        for section in ['sales', 'production', 'livestock', 'operations']:
            self.assertRegex(body, rf'id="report-{section}"[^>]*hidden')
        self.assertEqual(body.lower().count('<!doctype html>'), 1)
        import re
        ids = re.findall(r'\bid="([^"]+)"', body)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertContains(response, 'Monthly Cash Flow')
        self.assertContains(response, 'Sales by Season and Crop')

    def test_legacy_routes_select_correct_workspace_tab(self):
        for section in ['sales', 'production', 'livestock', 'operations']:
            with self.subTest(section=section):
                response = self.client.get(reverse(f'report_{section}'))
                self.assertEqual(response.context['active_report'], section)
                self.assertRegex(response.content.decode(), rf'id="tab-report-{section}"[^>]*aria-selected="true"')

    def test_sales_filter_and_export_links_keep_the_selected_section(self):
        response = self.client.get(reverse('reports'), {'section': 'sales', 'year': '2026'})
        self.assertEqual(response.context['active_report'], 'sales')
        self.assertEqual(response.context['year'], '2026')
        self.assertContains(response, 'name="section" value="sales"')
        self.assertContains(response, 'section=sales')
        self.assertContains(response, 'year=2026')

    def test_hidden_sections_do_not_expose_another_farms_records(self):
        other = User.objects.create_user(username='hidden-private-farm')
        farm = FarmProfile.objects.create(user=other, farm_name='Private Workspace Farm')
        field = Field.objects.create(farm_profile=farm, name='Private Workspace Field', acreage=2)
        season = CropSeason.objects.create(field=field, crop=Crop.objects.create(name='Private Workspace Crop'),
                                          planting_date=date(2026, 1, 1))
        harvest = HarvestRecord.objects.create(crop_season=season, harvest_date=date(2026, 2, 1), quantity=10)
        Sale.objects.create(harvest=harvest, sale_date=date(2026, 2, 2), quantity=2,
                            unit_price=50, buyer='Private Workspace Buyer')
        response = self.client.get(reverse('reports'))
        for text in ['Private Workspace Farm', 'Private Workspace Field', 'Private Workspace Crop', 'Private Workspace Buyer']:
            self.assertNotContains(response, text)

    def test_currency_control_preserves_report_selection_and_filters(self):
        selected = self.client.get(reverse('reports'), {'section': 'sales', 'year': '2026'})
        self.assertContains(selected, 'value="/reports/?section=sales&amp;year=2026"')
        redirect = self.client.post(reverse('set_currency'), {'next': '/reports/?section=sales&year=2026'})
        self.assertRedirects(redirect, '/reports/?section=sales&year=2026', fetch_redirect_response=False)

    def test_every_section_has_the_redesigned_header_filters_and_kpis(self):
        import re
        body = self.client.get(reverse('reports')).content.decode()
        self.assertEqual(body.count('class="report-header"'), 5)
        self.assertEqual(body.count('class="filter-panel"'), 5)
        self.assertNotIn('class="page-head"', body)
        for key, title in [('overview', 'Farm Report'), ('sales', 'Sales Report'),
                           ('production', 'Production Report'), ('livestock', 'Livestock Report'),
                           ('operations', 'Operations Report')]:
            section = body.split(f'id="report-{key}"', 1)[1].split('</section>', 1)[0]
            self.assertIn(f'<h2>{title}</h2>', section)
            self.assertIn('Export', section)
            self.assertIn('Print', section)
            if key == 'sales':
                self.assertIn('class="sales-line sales-line-combined"', section)
                self.assertIn('Crop Revenue Share', section)
                self.assertIn('Payment Methods', section)
                self.assertIn('Sales Mix', section)
                self.assertNotIn('sales-metric', section)
            else:
                self.assertEqual(section.count('class="hero-stat"'), 4)
            self.assertEqual(len(re.findall(r'class="(?:table-card|ops-row|report-grid)', section)) > 0, True)

    def test_invalid_section_defaults_to_overview(self):
        response = self.client.get(reverse('reports'), {'section': 'unknown'})
        self.assertEqual(response.context['active_report'], 'overview')

    def test_every_tab_exports_in_all_formats(self):
        for section in ['overview', 'sales', 'production', 'livestock', 'operations']:
            for fmt in ['csv', 'xlsx', 'pdf']:
                with self.subTest(section=section, format=fmt):
                    response = self.client.get(reverse('reports'), {'section': section, 'export': fmt})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(f'.{fmt}', response['Content-Disposition'])
                    if section == 'sales' and fmt == 'csv':
                        self.assertIn('Gross Sales', response.content.decode())


class SalesReportLayoutTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username='sales-report', password='test-password')
        self.client.force_login(user)
        farm = FarmProfile.objects.create(user=user, farm_name='Test farm')
        field = Field.objects.create(farm_profile=farm, name='North', acreage=Decimal('2'))
        self.seasons = []
        for i, (name, quantity, price, buyer, waste) in enumerate((
            ('Coffee', '10', '10', 'Buyer A', False),
            ('Maize', '5', '10', 'Buyer B', False),
            ('Coffee', '5', '5', 'Buyer A', True),
        )):
            crop, _ = Crop.objects.get_or_create(name=name)
            # Each season needs its own field: overlapping non-perennial
            # seasons on one field are now rejected by validation.
            season_field = field if i == 0 else Field.objects.create(
                farm_profile=farm, name=f'North-{i}', acreage=Decimal('2'))
            season = CropSeason.objects.create(field=season_field, crop=crop, planting_date=date(2025, 1, 1))
            self.seasons.append(season)
            harvest = HarvestRecord.objects.create(
                crop_season=season, harvest_date=date(2025, 2, 1), quantity=Decimal('20'),
                waste_quantity=Decimal('10') if waste else Decimal('0'),
            )
            Sale.objects.create(
                harvest=harvest, sale_date=date(2025, 3, 1),
                quantity=Decimal(quantity), unit_price=Decimal(price),
                buyer=buyer, is_waste=waste,
            )
        self.url = reverse('report_sales')

    def test_sales_mix_and_crop_ranking_respect_period(self):
        response = self.client.get(self.url, {'date_from': '2025-01-01', 'date_to': '2025-12-31'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sales Mix')
        self.assertContains(response, 'Revenue by Crop')
        page = response.content.decode()
        self.assertLess(page.index('Monthly Sales Revenue'), page.index('Revenue by Crop'))
        self.assertLess(page.index('Revenue by Crop'), page.index('Sales by Season and Crop'))
        self.assertEqual(response.context['gross'], Decimal('150'))
        self.assertEqual(response.context['sales_mix']['produce_pct'], 85.7)
        self.assertEqual(response.context['sales_mix']['waste_pct'], 14.3)
        self.assertEqual([r['share'] for r in response.context['top_crops']], [66.7, 33.3])
        self.assertEqual(response.context['top_buyers'][0]['share'], 66.7)

        empty = self.client.get(self.url, {'date_from': '2026-01-01'})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.context['sales_mix']['total'], Decimal('0'))

    def test_season_and_crop_filters_apply_to_sales_and_exports(self):
        season = self.seasons[0]
        Sale.objects.create(
            harvest=HarvestRecord.objects.get(crop_season=season),
            sale_date=date(2025, 3, 2), quantity=Decimal('1'),
            unit_price=Decimal('10'), buyer='Buyer A',
        )
        params = {'crop': str(season.crop_id), 'season': str(season.pk),
                  'date_from': '2025-01-01', 'date_to': '2025-12-31'}
        response = self.client.get(self.url, params)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sales by Season and Crop')
        self.assertContains(response, 'name="crop"')
        self.assertContains(response, 'name="season"')
        self.assertEqual(response.context['gross'], Decimal('110'))
        self.assertEqual(response.context['waste_revenue'], Decimal('0'))
        self.assertEqual(len(response.context['season_rows']), 1)
        self.assertEqual(response.context['season_rows'][0]['crop'], 'Coffee')
        self.assertEqual(response.context['season_rows'][0]['n'], 2)
        self.assertEqual(response.context['season_rows'][0]['total'], Decimal('110'))
        self.assertIn(f'crop={season.crop_id}&amp;season={season.pk}', response.content.decode())

        crop_only = self.client.get(self.url, {'crop': str(season.crop_id)})
        self.assertEqual(crop_only.context['gross'], Decimal('110'))
        self.assertEqual(crop_only.context['waste_revenue'], Decimal('25'))
        self.assertEqual(len(crop_only.context['season_rows']), 1)

        season_only = self.client.get(self.url, {'season': str(self.seasons[1].pk)})
        self.assertEqual(season_only.context['gross'], Decimal('50'))
        self.assertEqual(season_only.context['top_crops'][0]['label'], 'Maize')

        exported = self.client.get(self.url, {**params, 'export': 'csv'})
        self.assertContains(exported, 'Sales by Season and Crop')
        self.assertContains(exported, 'Coffee')
        self.assertNotContains(exported, 'Maize')

    def test_statistical_charts_follow_sales_filters(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Monthly Quantity Sold')
        self.assertContains(response, 'Season Revenue')
        self.assertContains(response, 'Crop Revenue Share')
        self.assertEqual(response.context['chart_year'], 2025)
        self.assertEqual(len(response.context['monthly_qty_rows']), 12)
        self.assertEqual(response.context['monthly_qty_rows'][2]['value'], Decimal('15'))
        self.assertEqual([r['total'] for r in response.context['season_chart']],
                         [Decimal('100'), Decimal('50')])
        self.assertEqual([r['share'] for r in response.context['crop_donut']], [66.7, 33.3])

        filtered = self.client.get(self.url, {'season': str(self.seasons[1].pk)})
        self.assertEqual(filtered.context['monthly_qty_rows'][2]['value'], Decimal('5'))
        self.assertEqual(len(filtered.context['season_chart']), 1)
        self.assertEqual(filtered.context['crop_donut'][0]['label'], 'Maize')

        empty = self.client.get(self.url, {'date_from': '2026-01-01'})
        self.assertEqual(empty.context['chart_year'], 2026)
        self.assertEqual(len(empty.context['monthly_qty_rows']), 12)
        self.assertTrue(all(r['value'] == 0 for r in empty.context['monthly_qty_rows']))
        self.assertEqual(empty.context['crop_donut'], [])

    def test_monthly_line_graphs_include_zero_sale_months(self):
        jan_harvest = HarvestRecord.objects.create(
            crop_season=self.seasons[0], harvest_date=date(2025, 1, 4),
            quantity=Decimal('5'))
        Sale.objects.create(
            harvest=jan_harvest,
            sale_date=date(2025, 1, 5), quantity=Decimal('2'),
            unit_price=Decimal('20'), buyer='Buyer A',
        )
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['monthly_rows']), 12)
        self.assertEqual([r['value'] for r in response.context['monthly_rows'][:3]],
                         [Decimal('40'), Decimal('0'), Decimal('150')])
        self.assertEqual([r['value'] for r in response.context['monthly_qty_rows'][:3]],
                         [Decimal('2'), Decimal('0'), Decimal('15')])
        self.assertTrue(all(r['value'] == 0 for r in response.context['monthly_rows'][3:]))
        self.assertEqual(response.context['monthly_rows'][0]['x'], 40)
        self.assertEqual(response.context['monthly_rows'][-1]['x'], 560)
        self.assertContains(response, 'Dec 2025')
        self.assertEqual(response.context['monthly_rows'][1]['y'], 178)
        self.assertNotEqual(response.context['monthly_rows'][0]['y'],
                            response.context['monthly_qty_rows'][0]['y'])
        self.assertContains(response, 'class="sales-line sales-line-combined"', count=1)
        self.assertContains(response, 'class="line-path revenue"', count=1)
        self.assertContains(response, 'class="line-path quantity"', count=1)
        self.assertContains(response, 'left axis')
        self.assertContains(response, 'right axis')
        sales_html = next(panel['html'] for panel in response.context['report_panels'] if panel['key'] == 'sales')
        self.assertIn('class="chart-group"', sales_html)
        self.assertIn('class="bar solo"', sales_html)
        self.assertContains(self.client.get(self.url, {'export': 'csv'}), 'Feb 2025')

        single_month = self.client.get(self.url, {'date_from': '2025-03-01'})
        self.assertEqual(len(single_month.context['monthly_rows']), 12)
        self.assertEqual(single_month.context['monthly_rows'][2]['value'], Decimal('150'))
        self.assertEqual(single_month.context['monthly_qty_rows'][2]['y'], 43.0)
        self.assertEqual(single_month.context['monthly_rows'][0]['y'], 178)
        self.assertContains(single_month, 'class="sales-line sales-line-combined"', count=1)

    def test_year_selection_filters_report_and_keeps_twelve_months(self):
        # Sequential rotation on the same field is fine once the earlier
        # season has an end date before the next planting.
        season_2024 = CropSeason.objects.create(
            field=self.seasons[0].field, crop=self.seasons[0].crop,
            planting_date=date(2024, 1, 1),
            expected_harvest_date=date(2024, 12, 31))
        harvest_2024 = HarvestRecord.objects.create(
            crop_season=season_2024, harvest_date=date(2024, 5, 1),
            quantity=Decimal('10'))
        Sale.objects.create(
            harvest=harvest_2024,
            sale_date=date(2024, 6, 1), quantity=Decimal('1'),
            unit_price=Decimal('50'), buyer='Buyer A',
        )
        response = self.client.get(self.url, {'year': '2024'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['chart_year'], 2024)
        self.assertEqual(response.context['gross'], Decimal('50'))
        self.assertEqual(len(response.context['monthly_rows']), 12)
        self.assertEqual(response.context['monthly_rows'][5]['value'], Decimal('50'))
        self.assertEqual(response.context['monthly_rows'][11]['value'], Decimal('0'))
        self.assertContains(response, 'name="year"')
        self.assertIn('year=2024', response.content.decode())
        exported = self.client.get(self.url, {'year': '2024', 'export': 'csv'})
        self.assertContains(exported, 'Jun 2024')
        self.assertNotContains(exported, 'Mar 2025')

    def test_exports_remain_available(self):
        for fmt in ('csv', 'xlsx', 'pdf'):
            with self.subTest(fmt=fmt):
                response = self.client.get(self.url, {
                    'export': fmt, 'date_from': '2025-01-01',
                    'crop': str(self.seasons[0].crop_id),
                    'season': str(self.seasons[0].pk),
                })
                self.assertEqual(response.status_code, 200)
                self.assertIn(f'sales_report.{fmt}', response['Content-Disposition'])
                self.assertTrue(response.content)
                if fmt == 'csv':
                    self.assertContains(response, 'Month,Revenue,Qty Sold')
                    self.assertNotContains(response, 'Maize')


class SaleConsistencyTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username='seller', password='pw')
        farm = FarmProfile.objects.create(user=user, farm_name='Farm A')
        field = Field.objects.create(farm_profile=farm, name='F1', acreage=Decimal('2'))
        self.crop = Crop.objects.create(
            name='Beans', default_unit='kg',
            secondary_unit='bag', secondary_per_primary=Decimal('50'))
        self.season = CropSeason.objects.create(
            field=field, crop=self.crop, planting_date=date(2025, 1, 1))
        self.harvest = HarvestRecord.objects.create(
            crop_season=self.season, harvest_date=date(2025, 6, 1),
            quantity=Decimal('100'), unit='kg', waste_quantity=Decimal('10'))
        self.item = InventoryItem.objects.create(
            farm_profile=farm, name='Grain sacks', unit='kg',
            quantity_on_hand=Decimal('50'))

    def test_sale_creates_updates_and_deletes_linked_income(self):
        sale = Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('10'), unit_price=Decimal('5'), buyer='Coop')
        income = Income.objects.get(sale=sale)
        self.assertEqual(income.amount, Decimal('50'))
        self.assertEqual(income.customer, 'Coop')
        self.assertEqual(income.crop_season, self.season)

        sale.quantity = Decimal('20')
        sale.buyer = 'Trader'
        sale.save()
        income.refresh_from_db()
        self.assertEqual(Income.objects.filter(sale=sale).count(), 1)
        self.assertEqual(income.amount, Decimal('100'))
        self.assertEqual(income.customer, 'Trader')

        sale.delete()
        self.assertEqual(Income.objects.filter(sale_id=sale.pk).count(), 0)

    def test_overselling_harvest_is_rejected(self):
        Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('90'), unit_price=Decimal('1'))
        with self.assertRaises(ValidationError):
            Sale.objects.create(
                harvest=self.harvest, sale_date=date(2025, 7, 2),
                quantity=Decimal('20'), unit_price=Decimal('1'))

    def test_waste_sales_are_limited_by_waste_quantity(self):
        Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('8'), unit_price=Decimal('1'), is_waste=True)
        with self.assertRaises(ValidationError):
            Sale.objects.create(
                harvest=self.harvest, sale_date=date(2025, 7, 2),
                quantity=Decimal('5'), unit_price=Decimal('1'), is_waste=True)

    def test_processing_stage_must_belong_to_sale_harvest(self):
        other_field = Field.objects.create(
            farm_profile=self.season.field.farm_profile, name='South',
            acreage=Decimal('1'))
        other_season = CropSeason.objects.create(
            field=other_field, crop=self.crop, planting_date=date(2025, 1, 2))
        other_harvest = HarvestRecord.objects.create(
            crop_season=other_season, harvest_date=date(2025, 6, 2),
            quantity=Decimal('40'))
        stage = ProcessingStage.objects.create(
            harvest=other_harvest, name='Milling', sequence=1,
            input_quantity=Decimal('40'), output_quantity=Decimal('38'),
            output_unit='kg')
        with self.assertRaises(ValidationError):
            Sale.objects.create(
                harvest=self.harvest, processing_stage=stage,
                sale_date=date(2025, 7, 1), quantity=Decimal('5'),
                unit_price=Decimal('2'))

    def test_secondary_unit_sale_converts_to_primary(self):
        # 1 bag = 50 kg, so two bag sales should exhaust the 100 kg harvest.
        Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('1'), unit='bag', unit_price=Decimal('100'))
        with self.assertRaises(ValidationError):
            Sale.objects.create(
                harvest=self.harvest, sale_date=date(2025, 7, 2),
                quantity=Decimal('2'), unit='bag', unit_price=Decimal('100'))

    def test_inventory_sale_decrements_and_restores_stock(self):
        sale = Sale.objects.create(
            harvest=self.harvest, inventory_item=self.item,
            sale_date=date(2025, 7, 1), quantity=Decimal('20'),
            unit='kg', unit_price=Decimal('3'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, Decimal('30'))

        sale.quantity = Decimal('25')
        sale.save()
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, Decimal('25'))

        sale.delete()
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, Decimal('50'))

    def test_inventory_oversell_is_rejected(self):
        with self.assertRaises(ValidationError):
            Sale.objects.create(
                harvest=self.harvest, inventory_item=self.item,
                sale_date=date(2025, 7, 1), quantity=Decimal('60'),
                unit='kg', unit_price=Decimal('3'))


class CropIntegrityTests(TestCase):
    """Regression tests for seasons, harvests, processing and sales rules."""

    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm X')
        self.field = Field.objects.create(farm_profile=self.farm, name='F1', acreage=Decimal('2'))
        self.crop = Crop.objects.create(
            name='Beans X', default_unit='kg',
            secondary_unit='bag', secondary_per_primary=Decimal('50'))
        self.season = CropSeason.objects.create(
            field=self.field, crop=self.crop, planting_date=date(2025, 1, 1),
            expected_yield=Decimal('150'))
        self.harvest = HarvestRecord.objects.create(
            crop_season=self.season, harvest_date=date(2025, 6, 1),
            quantity=Decimal('100'), unit='kg')
        self.item = InventoryItem.objects.create(
            farm_profile=self.farm, name='Fertilizer', unit='kg',
            quantity_on_hand=Decimal('50'))

    # -- Sale date / PROTECT -------------------------------------------------

    def test_sale_date_before_harvest_is_rejected(self):
        with self.assertRaises(ValidationError):
            Sale.objects.create(
                harvest=self.harvest, sale_date=date(2025, 5, 1),
                quantity=Decimal('1'), unit='kg', unit_price=Decimal('2'))

    def test_processing_stage_delete_is_protected_by_sales(self):
        from django.db.models.deletion import ProtectedError
        stage = ProcessingStage.objects.create(
            harvest=self.harvest, name='Drying', sequence=1,
            input_quantity=Decimal('50'), output_quantity=Decimal('45'),
            output_unit='kg')
        Sale.objects.create(
            harvest=self.harvest, processing_stage=stage,
            sale_date=date(2025, 7, 1), quantity=Decimal('10'),
            unit='kg', unit_price=Decimal('4'))
        with self.assertRaises(ProtectedError):
            stage.delete()

    # -- Unit normalization --------------------------------------------------

    def test_harvest_quantities_are_normalized_to_primary_unit(self):
        Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('1'), unit='bag', unit_price=Decimal('100'))
        self.harvest.refresh_from_db()
        self.assertEqual(self.harvest.quantity_sold, Decimal('50'))
        self.assertEqual(self.harvest.quantity_unsold, Decimal('50'))

    # -- Processing stage validation -----------------------------------------

    def test_stage_input_cannot_exceed_raw_harvest(self):
        with self.assertRaises(ValidationError):
            ProcessingStage.objects.create(
                harvest=self.harvest, name='Drying', sequence=1,
                input_quantity=Decimal('150'))

    def test_stage_input_cannot_exceed_prior_output(self):
        ProcessingStage.objects.create(
            harvest=self.harvest, name='Drying', sequence=1,
            input_quantity=Decimal('50'), output_quantity=Decimal('40'),
            output_unit='kg')
        with self.assertRaises(ValidationError):
            ProcessingStage.objects.create(
                harvest=self.harvest, name='Milling', sequence=2,
                input_quantity=Decimal('60'))

    def test_stage_input_accounts_for_already_sold_source(self):
        stage = ProcessingStage.objects.create(
            harvest=self.harvest, name='Drying', sequence=1,
            input_quantity=Decimal('50'), output_quantity=Decimal('40'),
            output_unit='kg')
        Sale.objects.create(
            harvest=self.harvest, processing_stage=stage,
            sale_date=date(2025, 7, 1), quantity=Decimal('30'),
            unit='kg', unit_price=Decimal('4'))
        with self.assertRaises(ValidationError):
            ProcessingStage.objects.create(
                harvest=self.harvest, name='Milling', sequence=2,
                input_quantity=Decimal('20'))

    def test_duplicate_stage_sequence_is_rejected(self):
        from django.db import transaction as db_transaction
        from django.db import IntegrityError
        ProcessingStage.objects.create(
            harvest=self.harvest, name='Drying', sequence=1,
            input_quantity=Decimal('10'))
        with self.assertRaises(IntegrityError):
            with db_transaction.atomic():
                ProcessingStage.objects.create(
                    harvest=self.harvest, name='Milling', sequence=1,
                    input_quantity=Decimal('10'))

    def test_stage_start_before_harvest_is_rejected(self):
        with self.assertRaises(ValidationError):
            ProcessingStage.objects.create(
                harvest=self.harvest, name='Drying', sequence=1,
                start_date=date(2025, 5, 1), input_quantity=Decimal('10'))

    # -- Processing cost -> Expense sync --------------------------------------

    def test_processing_cost_syncs_to_expense(self):
        from expenses.models import Expense
        stage = ProcessingStage.objects.create(
            harvest=self.harvest, name='Drying', sequence=1,
            input_quantity=Decimal('50'), cost=Decimal('200'),
            start_date=date(2025, 6, 10), duration_days=2)
        expense = Expense.objects.get(processing_stage=stage)
        self.assertEqual(expense.amount, Decimal('200'))
        self.assertEqual(expense.crop_season, self.season)
        self.assertEqual(expense.farm_profile, self.farm)

        stage.cost = Decimal('250')
        stage.save()
        expense.refresh_from_db()
        self.assertEqual(expense.amount, Decimal('250'))
        self.assertEqual(Expense.objects.filter(processing_stage=stage).count(), 1)

        stage.cost = Decimal('0')
        stage.save()
        self.assertEqual(Expense.objects.filter(processing_stage=stage).count(), 0)

    # -- Inventory-only sales --------------------------------------------------

    def test_inventory_sale_without_harvest_creates_income(self):
        sale = Sale.objects.create(
            inventory_item=self.item, sale_date=date(2025, 7, 1),
            quantity=Decimal('10'), unit='kg', unit_price=Decimal('3'),
            buyer='Depot')
        self.assertIsNone(sale.harvest_id)
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, Decimal('40'))
        income = Income.objects.get(sale=sale)
        self.assertEqual(income.amount, Decimal('30'))
        self.assertEqual(income.farm_profile, self.farm)
        self.assertIsNone(income.crop_season)

    def test_sale_needs_a_source(self):
        with self.assertRaises(ValidationError):
            Sale.objects.create(
                sale_date=date(2025, 7, 1), quantity=Decimal('1'),
                unit='kg', unit_price=Decimal('2'))

    def test_inventory_sale_scoped_to_inventory_farm(self):
        from accounts.access import scoped_queryset
        Sale.objects.create(
            inventory_item=self.item, sale_date=date(2025, 7, 1),
            quantity=Decimal('10'), unit='kg', unit_price=Decimal('3'))
        self.assertIn(
            self.item,
            scoped_queryset(InventoryItem, self.user, InventoryItem.objects.all()))
        other = User.objects.create_user(username='outsider', password='pw')
        self.assertEqual(
            scoped_queryset(Sale, other, Sale.objects.all()).count(), 0)

    # -- Season workflow -------------------------------------------------------

    def test_harvest_advances_season_from_any_preharvest_status(self):
        for status in ['PLANNED', 'PLANTED', 'FLOWERING']:
            with self.subTest(status=status):
                field = Field.objects.create(
                    farm_profile=self.farm, name=f'F-{status}', acreage=Decimal('1'))
                season = CropSeason.objects.create(
                    field=field, crop=self.crop, planting_date=date(2025, 1, 1),
                    status=status)
                HarvestRecord.objects.create(
                    crop_season=season, harvest_date=date(2025, 6, 1),
                    quantity=Decimal('10'))
                season.refresh_from_db()
                self.assertEqual(season.status, 'HARVESTED')
                self.assertEqual(season.actual_harvest_date, date(2025, 6, 1))

    def test_overlapping_seasons_on_same_field_are_rejected(self):
        second = CropSeason(
            field=self.field, crop=self.crop, planting_date=date(2025, 3, 1))
        with self.assertRaises(ValidationError):
            second.full_clean()

    def test_perennial_seasons_may_overlap(self):
        self.season.is_perennial_season = True
        self.season.save()
        other = CropSeason(
            field=self.field, crop=self.crop, planting_date=date(2025, 3, 1),
            is_perennial_season=True)
        other.full_clean()  # must not raise

    def test_expected_yield_and_yield_per_acre(self):
        self.assertEqual(self.season.yield_per_acre, Decimal('50'))
        season2 = CropSeason.objects.create(
            field=Field.objects.create(farm_profile=self.farm, name='F2', acreage=Decimal('4')),
            crop=self.crop, planting_date=date(2025, 1, 1), expected_yield=Decimal('10'))
        HarvestRecord.objects.create(
            crop_season=season2, harvest_date=date(2025, 6, 1), quantity=Decimal('20'))
        self.assertEqual(season2.yield_per_acre, Decimal('5'))

    # -- Customers / payments ---------------------------------------------------

    def test_customer_selection_fills_buyer(self):
        from crops.models import Customer
        customer = Customer.objects.create(
            farm_profile=self.farm, name='Kampala Traders')
        sale = Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('5'), unit='kg', unit_price=Decimal('4'),
            customer=customer)
        self.assertEqual(sale.buyer, 'Kampala Traders')

    def test_receivables_show_in_sales_report(self):
        self.client.force_login(self.user)
        Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('10'), unit='kg', unit_price=Decimal('5'),
            payment_status='UNPAID')
        response = self.client.get(reverse('report_sales'))
        self.assertEqual(response.context['receivables'], Decimal('50'))
        self.assertEqual(response.context['receivable_count'], 1)

    # -- Expense schedule line ----------------------------------------------------

    def test_expense_schedule_line_must_match_budget_item_subcode(self):
        from budgets.models import Budget, BudgetItem
        from expenses.models import BudgetSubCode, Category, Expense, ScheduleLine
        category = Category.objects.create(name='Crop ops', code='COP')
        sub_a = BudgetSubCode.objects.create(category=category, code='COP-A', name='Weeding')
        sub_b = BudgetSubCode.objects.create(category=category, code='COP-B', name='Pests')
        line_a = ScheduleLine.objects.create(code='weed', name='Weed control', subcode=sub_a)
        budget = Budget.objects.create(name='Plan', farm_profile=self.farm)
        item = BudgetItem.objects.create(
            budget=budget, category=category, subcode=sub_b,
            planned_amount=Decimal('100'))
        expense = Expense(
            farm_profile=self.farm, category=category, budget_item=item,
            schedule_line=line_a, amount=Decimal('10'), date=date(2025, 7, 1))
        with self.assertRaises(ValidationError):
            expense.full_clean()
        expense.schedule_line = ScheduleLine.objects.create(
            code='pest', name='Pest control', subcode=sub_b)
        expense.full_clean()  # no error now

    # -- Amount received / receivables -----------------------------------------

    def test_amount_received_bounds_are_validated(self):
        with self.assertRaises(ValidationError):
            Sale.objects.create(
                harvest=self.harvest, sale_date=date(2025, 7, 1),
                quantity=Decimal('5'), unit='kg', unit_price=Decimal('4'),
                payment_status='PARTIAL', amount_received=Decimal('-1'))
        with self.assertRaises(ValidationError):
            Sale.objects.create(
                harvest=self.harvest, sale_date=date(2025, 7, 1),
                quantity=Decimal('5'), unit='kg', unit_price=Decimal('4'),
                payment_status='PARTIAL', amount_received=Decimal('21'))

    def test_payment_status_keeps_amount_received_consistent(self):
        paid = Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('5'), unit='kg', unit_price=Decimal('4'),
            payment_status='PAID')
        self.assertEqual(paid.amount_received, Decimal('20'))
        self.assertEqual(paid.receivable, Decimal('0'))

        unpaid = Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('5'), unit='kg', unit_price=Decimal('4'),
            payment_status='UNPAID', amount_received=Decimal('10'))
        self.assertEqual(unpaid.amount_received, Decimal('0'))
        self.assertEqual(unpaid.receivable, Decimal('20'))

    def test_partial_sale_receivable_is_the_balance(self):
        self.client.force_login(self.user)
        sale = Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('10'), unit='kg', unit_price=Decimal('5'),
            payment_status='PARTIAL', amount_received=Decimal('30'))
        self.assertEqual(sale.receivable, Decimal('20'))
        response = self.client.get(reverse('report_sales'))
        self.assertEqual(response.context['receivables'], Decimal('20'))
        self.assertEqual(response.context['receivable_count'], 1)

    # -- Overlap enforced on save() --------------------------------------------

    def test_overlap_rejected_on_direct_create(self):
        with self.assertRaises(ValidationError):
            CropSeason.objects.create(
                field=self.field, crop=self.crop,
                planting_date=date(2025, 3, 1))

    # -- Stage budget item -> expense ------------------------------------------

    def test_stage_budget_item_flows_into_generated_expense(self):
        from budgets.models import Budget, BudgetItem
        from expenses.models import BudgetSubCode, Category, Expense
        category = Category.objects.create(name='Processing ops', code='PRO')
        subcode = BudgetSubCode.objects.create(
            category=category, code='PRO-A', name='Milling')
        budget = Budget.objects.create(name='Plan', farm_profile=self.farm)
        item = BudgetItem.objects.create(
            budget=budget, category=category, subcode=subcode,
            planned_amount=Decimal('1000'))
        stage = ProcessingStage.objects.create(
            harvest=self.harvest, name='Milling', sequence=1,
            input_quantity=Decimal('50'), cost=Decimal('200'),
            budget_item=item)
        expense = Expense.objects.get(processing_stage=stage)
        self.assertEqual(expense.budget_item, item)

    def test_stage_budget_item_must_match_stage_farm(self):
        from budgets.models import Budget, BudgetItem
        from expenses.models import Category
        other_farm = FarmProfile.objects.create(
            user=User.objects.create_user(username='other', password='pw'),
            farm_name='Other farm')
        category = Category.objects.create(name='Ops', code='OPS')
        budget = Budget.objects.create(name='Plan', farm_profile=other_farm)
        item = BudgetItem.objects.create(
            budget=budget, category=category, planned_amount=Decimal('10'))
        with self.assertRaises(ValidationError):
            ProcessingStage.objects.create(
                harvest=self.harvest, name='Milling', sequence=1,
                input_quantity=Decimal('10'), budget_item=item)

    # -- Customer history --------------------------------------------------------

    def test_customer_history_view(self):
        from crops.models import Customer
        self.client.force_login(self.user)
        customer = Customer.objects.create(
            farm_profile=self.farm, name='Kampala Traders')
        Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('5'), unit='kg', unit_price=Decimal('4'),
            customer=customer, payment_status='PARTIAL',
            amount_received=Decimal('5'))
        response = self.client.get(reverse('customer_detail', args=[customer.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total_spend'], Decimal('20'))
        self.assertEqual(response.context['outstanding'], Decimal('15'))
        other = User.objects.create_user(username='outsider2', password='pw')
        self.client.force_login(other)
        self.assertEqual(
            self.client.get(
                reverse('customer_detail', args=[customer.pk])).status_code, 404)
