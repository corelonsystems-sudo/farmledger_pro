from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import FarmMembership, FarmProfile, User
from crops.models import (
    Crop, CropSeason, Customer, HarvestRecord, Invoice, InvoiceItem,
    InvoicePayment, ProcessingStage, Sale,
)
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


class SalePaymentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm X')
        other_farm = FarmProfile.objects.create(
            user=User.objects.create_user(username='other', password='pw'))
        field = Field.objects.create(farm_profile=self.farm, name='F1', acreage=Decimal('2'))
        other_field = Field.objects.create(farm_profile=other_farm, name='F9', acreage=Decimal('2'))
        crop = Crop.objects.create(name='Beans Y', default_unit='kg')
        season = CropSeason.objects.create(
            field=field, crop=crop, planting_date=date(2025, 1, 1))
        other_season = CropSeason.objects.create(
            field=other_field, crop=crop, planting_date=date(2025, 1, 1))
        self.harvest = HarvestRecord.objects.create(
            crop_season=season, harvest_date=date(2025, 6, 1),
            quantity=Decimal('100'), unit='kg')
        other_harvest = HarvestRecord.objects.create(
            crop_season=other_season, harvest_date=date(2025, 6, 1),
            quantity=Decimal('50'), unit='kg')
        self.sale = Sale.objects.create(
            harvest=self.harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('10'), unit='kg', unit_price=Decimal('10'),
            payment_status='UNPAID')
        self.other_sale = Sale.objects.create(
            harvest=other_harvest, sale_date=date(2025, 7, 1),
            quantity=Decimal('5'), unit='kg', unit_price=Decimal('2'),
            payment_status='UNPAID')
        self.client.force_login(self.user)
        self.url = reverse('sale_record_payment', args=[self.sale.pk])

    def test_partial_payment_marks_partial(self):
        response = self.client.post(self.url, {'amount': '40'})
        self.assertEqual(response.status_code, 302)
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.amount_received, Decimal('40'))
        self.assertEqual(self.sale.payment_status, 'PARTIAL')
        self.assertEqual(self.sale.receivable, Decimal('60'))

    def test_full_payment_marks_paid(self):
        self.client.post(self.url, {'amount': '100'})
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.payment_status, 'PAID')
        self.assertEqual(self.sale.amount_received, Decimal('100'))
        self.assertEqual(self.sale.receivable, Decimal('0'))

    def test_overpayment_rejected(self):
        response = self.client.post(self.url, {'amount': '500'})
        self.assertEqual(response.status_code, 200)  # form error
        self.sale.refresh_from_db()
        self.assertEqual(self.sale.amount_received, Decimal('0'))

    def test_payment_is_farm_scoped(self):
        response = self.client.post(
            reverse('sale_record_payment', args=[self.other_sale.pk]),
            {'amount': '5'})
        self.assertEqual(response.status_code, 404)


class CropDetailChartTests(TestCase):
    """Crop detail renders the SVG revenue/cost line chart, the quantity donut
    and the stages-of-production flow from a harvest -> stage -> sale chain."""

    def setUp(self):
        self.user = User.objects.create_user(username='chart-owner', password='pw')
        farm = FarmProfile.objects.create(user=self.user, farm_name='Chart Farm')
        field = Field.objects.create(farm_profile=farm, name='C1', acreage=Decimal('1'))
        self.crop = Crop.objects.create(name='Coffee', default_unit='kg')
        season = CropSeason.objects.create(field=field, crop=self.crop, planting_date=date(2025, 1, 1))
        self.harvest = HarvestRecord.objects.create(
            crop_season=season, harvest_date=date(2025, 6, 1), quantity=Decimal('100'), unit='kg')
        self.stage = ProcessingStage.objects.create(
            harvest=self.harvest, name='Drying', sequence=1,
            input_quantity=Decimal('60'), output_quantity=Decimal('50'), output_unit='kg',
            cost=Decimal('200'))
        Sale.objects.create(harvest=self.harvest, sale_date=date(2025, 7, 1),
                            quantity=Decimal('30'), unit_price=Decimal('10'))
        Sale.objects.create(harvest=self.harvest, processing_stage=self.stage,
                            sale_date=date(2025, 7, 5), quantity=Decimal('20'), unit_price=Decimal('25'))
        self.client.force_login(self.user)

    def test_charts_render_from_production_chain(self):
        response = self.client.get(reverse('crop_detail', kwargs={'pk': self.crop.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'chart.js')
        # Default view: sold vs in-store across the months of the latest active year.
        chart = response.context['line_chart']
        self.assertEqual(len(chart['rows']), 12)
        self.assertEqual(chart['rows'][6]['sold'], Decimal('50'))      # Jul: 30 raw + 20 processed
        self.assertEqual(chart['rows'][5]['in_store'], Decimal('90'))  # Jun: 100 - 10 lost
        self.assertEqual(chart['rows'][6]['in_store'], Decimal('40'))  # Jul: 100 - 50 sold - 10 lost
        self.assertEqual(chart['rows'][11]['in_store'], Decimal('40'))
        self.assertContains(response, 'class="burn-line"')
        self.assertContains(response, 'chart-filters')

        segs = {s['label']: s['total'] for s in response.context['qty_segments']}
        self.assertEqual(segs['Raw Sold'], Decimal('30'))
        self.assertEqual(segs['Processed Sold'], Decimal('20'))
        self.assertEqual(segs['Processed in Store'], Decimal('30'))
        self.assertEqual(segs['Raw in Store'], Decimal('10'))
        self.assertEqual(segs['Depreciated'], Decimal('10'))
        self.assertContains(response, 'class="donut"')

        flow = response.context['stage_flow']
        self.assertEqual([r['label'] for r in flow], ['Harvested (raw)', '1. Drying'])
        raw, drying = flow
        self.assertEqual((raw['qty'], raw['sold'], raw['in_store'], raw['passed_on']),
                         (Decimal('100'), Decimal('30'), Decimal('10'), Decimal('60')))
        self.assertEqual((drying['qty'], drying['sold'], drying['in_store'], drying['lost']),
                         (Decimal('50'), Decimal('20'), Decimal('30'), Decimal('10')))
        self.assertContains(response, 'Stages of Production')

    def test_chart_filters_narrow_the_series(self):
        url = reverse('crop_detail', kwargs={'pk': self.crop.pk})
        # Stage filter: only the stage's processed sale counts as sold.
        resp = self.client.get(url, {'stage': 'Drying'})
        chart = resp.context['line_chart']
        self.assertEqual(chart['rows'][6]['sold'], Decimal('20'))
        self.assertEqual(chart['rows'][6]['in_store'], Decimal('70'))  # 100 - 20 sold - 10 lost
        # Season filter with no harvests in it -> no chart.
        other_field = Field.objects.create(
            farm_profile=FarmProfile.objects.get(user=self.user), name='C2',
            acreage=Decimal('1'))
        empty_season = CropSeason.objects.create(
            field=other_field, crop=self.crop, planting_date=date(2025, 3, 1))
        resp = self.client.get(url, {'season': empty_season.pk})
        self.assertIsNone(resp.context['line_chart'])
        # Unknown year falls back to the latest active year.
        resp = self.client.get(url, {'year': '1999'})
        self.assertEqual(resp.context['chart_filters']['year'], '2025')

    def test_empty_crop_shows_placeholders(self):
        bare = Crop.objects.create(name='Bare', default_unit='kg')
        response = self.client.get(reverse('crop_detail', kwargs={'pk': bare.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['line_chart'])
        self.assertEqual(response.context['qty_segments'], [])
        self.assertEqual(response.context['stage_flow'], [])
        self.assertContains(response, 'No dated activity for this selection')


class InvoiceFlowTests(TestCase):
    """Invoices: create/edit with items, payments, printable docs, sale raise."""

    def setUp(self):
        from inventory.models import InventoryItem
        self.user = User.objects.create_user(username='inv-owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Invoice Farm')
        self.customer = Customer.objects.create(farm_profile=self.farm, name='Kampala Traders')
        self.item = InventoryItem.objects.create(
            farm_profile=self.farm, name='Grain sacks', unit='kg',
            quantity_on_hand=Decimal('500'))
        self.sale = Sale.objects.create(
            inventory_item=self.item, sale_date=date(2026, 2, 1),
            quantity=Decimal('100'), unit='kg', unit_price=Decimal('2000'),
            customer=self.customer, payment_status=Sale.PaymentStatus.PARTIAL,
            amount_received=Decimal('100000'))
        self.client.force_login(self.user)

    def _payload(self):
        return {
            'farm_profile': self.farm.pk, 'customer': self.customer.pk,
            'buyer': '', 'date': '2026-02-05', 'due_date': '2026-03-05',
            'payment_terms': 'Net 30', 'reference': '', 'tax_percent': '0',
            'discount': '0', 'payment_details': '',
            'status': 'ISSUED', 'notes': 'Pay within 30 days',
            'items-TOTAL_FORMS': '1', 'items-INITIAL_FORMS': '0',
            'items-0-description': 'Grain sacks', 'items-0-detail': '',
            'items-0-quantity': '100',
            'items-0-unit': 'kg', 'items-0-unit_price': '2000',
            'items-0-unit_cost': '900',
        }

    def test_create_invoice_with_items_and_numbering(self):
        response = self.client.post(reverse('invoice_create'), self._payload())
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(customer=self.customer, sale=None)
        # INV-00001 is the sale's automatic invoice; the manual one follows.
        self.assertEqual(invoice.number, 'INV-00002')
        self.assertEqual(invoice.buyer, 'Kampala Traders')
        self.assertEqual(invoice.total, Decimal('200000'))
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertRedirects(response, reverse('invoice_detail', kwargs={'pk': invoice.pk}))

    def test_payment_updates_status_and_receipt_renders(self):
        invoice = Invoice.objects.create(
            farm_profile=self.farm, customer=self.customer, date=date(2026, 2, 5),
            status=Invoice.Status.ISSUED)
        InvoiceItem.objects.create(
            invoice=invoice, description='Grain', quantity=Decimal('10'),
            unit='kg', unit_price=Decimal('1000'))
        response = self.client.post(
            reverse('invoice_record_payment', kwargs={'pk': invoice.pk}),
            {'amount': '4000', 'date': '2026-02-10', 'method': 'CASH',
             'reference': 'RC1', 'notes': ''})
        self.assertEqual(invoice.payments.count(), 1)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PARTIAL)
        payment = invoice.payments.first()
        # Overpayment is rejected
        response = self.client.post(
            reverse('invoice_record_payment', kwargs={'pk': invoice.pk}),
            {'amount': '99999', 'date': '2026-02-11', 'method': 'CASH'})
        self.assertEqual(invoice.payments.count(), 1)
        # Printable receipt downloads as a PDF document
        page = self.client.get(reverse(
            'invoice_payment_receipt', kwargs={'pk': invoice.pk, 'payment_pk': payment.pk}))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page['Content-Type'], 'application/pdf')
        self.assertIn('RCP-', page['Content-Disposition'])
        self.assertTrue(page.content.startswith(b'%PDF-'))

    def test_invoice_detail_is_printable_document(self):
        invoice = Invoice.objects.create(
            farm_profile=self.farm, customer=self.customer, date=date(2026, 2, 5),
            status=Invoice.Status.ISSUED, notes='Terms apply')
        InvoiceItem.objects.create(
            invoice=invoice, description='Grain', quantity=Decimal('10'),
            unit='kg', unit_price=Decimal('1000'))
        page = self.client.get(reverse('invoice_detail', kwargs={'pk': invoice.pk}))
        self.assertContains(page, 'INVOICE')
        self.assertContains(page, 'Invoice Farm')
        self.assertContains(page, 'Kampala Traders')
        self.assertContains(page, 'Download PDF')
        self.assertEqual(page.context['balance'], Decimal('10000'))

    def test_invoice_list_shows_rows_and_stats(self):
        invoice = Invoice.objects.create(
            farm_profile=self.farm, customer=self.customer, date=date(2026, 2, 5),
            status=Invoice.Status.ISSUED)
        InvoiceItem.objects.create(
            invoice=invoice, description='Grain', quantity=Decimal('10'),
            unit='kg', unit_price=Decimal('1000'))
        page = self.client.get(reverse('invoice_list'))
        self.assertContains(page, 'INV-')
        self.assertContains(page, 'Outstanding')

    def test_raise_invoice_from_sale_reuses_existing(self):
        response = self.client.post(reverse('sale_generate_invoice', kwargs={'pk': self.sale.pk}))
        invoice = Invoice.objects.first()
        self.assertRedirects(response, reverse('invoice_detail', kwargs={'pk': invoice.pk}))
        self.assertEqual(invoice.items.first().description, 'Grain sacks')
        self.assertEqual(invoice.payments.count(), 1)  # sale collection carried over
        self.assertEqual(invoice.status, Invoice.Status.PARTIAL)
        # Raising again redirects to the existing invoice
        response = self.client.post(reverse('sale_generate_invoice', kwargs={'pk': self.sale.pk}))
        self.assertRedirects(response, reverse('invoice_detail', kwargs={'pk': invoice.pk}))
        self.assertEqual(Invoice.objects.count(), 1)

    def test_sale_receipt_renders(self):
        page = self.client.get(reverse('sale_receipt', kwargs={'pk': self.sale.pk}))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page['Content-Type'], 'application/pdf')
        self.assertIn('sale-receipt-', page['Content-Disposition'])
        self.assertTrue(page.content.startswith(b'%PDF-'))

    def test_worker_cannot_create_invoice(self):
        worker = User.objects.create_user(username='inv-worker', password='pw')
        FarmMembership.objects.create(
            farm_profile=self.farm, user=worker, role=User.Role.WORKER)
        self.client.force_login(worker)
        self.assertEqual(self.client.get(reverse('invoice_create')).status_code, 403)
        self.assertEqual(self.client.get(reverse('invoice_list')).status_code, 200)
        self.assertContains(self.client.get(reverse('invoice_list')), 'Kampala Traders')


class SaleAutoInvoiceTests(TestCase):
    """Every sale automatically gets an invoice that mirrors it."""

    def setUp(self):
        from inventory.models import InventoryItem
        self.user = User.objects.create_user(username='auto-inv', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Auto Farm')
        self.customer = Customer.objects.create(farm_profile=self.farm, name='Auto Buyer')
        self.item = InventoryItem.objects.create(
            farm_profile=self.farm, name='Sacks', unit='kg', quantity_on_hand=Decimal('500'))

    def _make_sale(self, **kwargs):
        defaults = dict(
            inventory_item=self.item, sale_date=date(2026, 2, 1),
            quantity=Decimal('50'), unit='kg', unit_price=Decimal('1000'),
            customer=self.customer, payment_status=Sale.PaymentStatus.PAID)
        defaults.update(kwargs)
        return Sale.objects.create(**defaults)

    def test_sale_creates_auto_invoice(self):
        sale = self._make_sale()
        invoice = sale.invoices.get()
        self.assertEqual(invoice.number, 'INV-00001')
        self.assertEqual(invoice.buyer, 'Auto Buyer')
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        line = invoice.items.get()
        self.assertEqual(line.description, 'Sacks')
        self.assertEqual(line.total, Decimal('50000'))
        self.assertEqual(invoice.payments.get().amount, Decimal('50000'))

    def test_sale_update_mirrors_invoice(self):
        sale = self._make_sale(payment_status=Sale.PaymentStatus.PARTIAL,
                               amount_received=Decimal('20000'))
        invoice = sale.invoices.get()
        self.assertEqual(invoice.status, Invoice.Status.PARTIAL)
        sale.quantity = Decimal('80')
        sale.amount_received = Decimal('40000')
        sale.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.items.get().quantity, Decimal('80'))
        self.assertEqual(invoice.total, Decimal('80000'))
        self.assertEqual(invoice.payments.get().amount, Decimal('40000'))
        self.assertEqual(invoice.status, Invoice.Status.PARTIAL)

    def test_partial_then_full_payment_flows_to_invoice(self):
        sale = self._make_sale(payment_status=Sale.PaymentStatus.UNPAID)
        invoice = sale.invoices.get()
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(invoice.payments.count(), 0)
        sale.payment_status = Sale.PaymentStatus.PAID
        sale.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertEqual(invoice.payments.get().amount, Decimal('50000'))

    def test_deleting_sale_removes_invoice(self):
        sale = self._make_sale()
        self.assertEqual(Invoice.objects.count(), 1)
        sale.delete()
        self.assertEqual(Invoice.objects.count(), 0)

    def test_manual_invoice_survives_sale_deletion(self):
        self._make_sale()
        manual = Invoice.objects.create(
            farm_profile=self.farm, customer=self.customer, buyer='Walk-in',
            date=date(2026, 2, 2), status=Invoice.Status.ISSUED)
        InvoiceItem.objects.create(
            invoice=manual, description='Direct order', quantity=Decimal('5'),
            unit='kg', unit_price=Decimal('900'))
        self.assertEqual(Invoice.objects.count(), 2)
        Sale.objects.all().delete()
        self.assertEqual(list(Invoice.objects.all()), [manual])


class InvoiceDocumentTests(TestCase):
    """Invoice document layout fields (terms, reference, tax, discount) + PDF."""

    def setUp(self):
        self.user = User.objects.create_user(username='doc-owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Doc Farm')
        self.customer = Customer.objects.create(farm_profile=self.farm, name='Doc Buyer')
        self.client.force_login(self.user)

    def _invoice(self, **kwargs):
        defaults = dict(
            farm_profile=self.farm, customer=self.customer, date=date(2026, 2, 5),
            status=Invoice.Status.ISSUED, payment_terms='Net 30',
            reference='PO-0077', tax_percent=Decimal('18'), discount=Decimal('100'))
        defaults.update(kwargs)
        invoice = Invoice.objects.create(**defaults)
        InvoiceItem.objects.create(
            invoice=invoice, description='Grain', detail='Grade A, cleaned',
            quantity=Decimal('10'), unit='kg', unit_price=Decimal('1000'),
            unit_cost=Decimal('600'))
        return invoice

    def test_tax_discount_and_total_due(self):
        invoice = self._invoice()
        self.assertEqual(invoice.subtotal, Decimal('10000'))
        self.assertEqual(invoice.tax_amount, Decimal('1800'))
        self.assertEqual(invoice.total, Decimal('11700'))  # 10000 + 1800 tax - 100 discount
        self.assertEqual(invoice.balance, Decimal('11700'))

    def test_detail_page_shows_document_fields(self):
        invoice = self._invoice()
        page = self.client.get(reverse('invoice_detail', kwargs={'pk': invoice.pk}))
        body = page.content.decode()
        for marker in ('Billing Summary', 'Payment Progress', 'Invoice Items',
                       'INVOICE ITEMS', 'Grade A, cleaned', 'Invoice Item Summary',
                       'Revenue vs Production Cost', 'Download PDF', 'polyline',
                       'budget-toolbar', 'PAYMENTS'):
            self.assertIn(marker, body)
        self.assertNotIn('INVOICE DOCUMENT', body)
        self.assertNotIn('BILLED TO', body)
        self.assertEqual(page.context['tax_amount'], Decimal('1800'))
        self.assertEqual(page.context['total_production_cost'], Decimal('6000'))
        self.assertEqual(page.context['chart_rows'][0]['production_cost_pct'], 60)

    def test_pdf_download(self):
        invoice = self._invoice()
        response = self.client.get(reverse('invoice_pdf', kwargs={'pk': invoice.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('INV-', response['Content-Disposition'])
        self.assertTrue(response.content.startswith(b'%PDF-'))


class InvoicePaymentIncomeTests(TestCase):
    """Invoice payments book Income rows (sale-linked invoices excluded)."""

    def setUp(self):
        self.user = User.objects.create_user(username='pay-income', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Pay Farm')
        self.customer = Customer.objects.create(farm_profile=self.farm, name='Pay Buyer')
        self.invoice = Invoice.objects.create(
            farm_profile=self.farm, customer=self.customer, buyer='Pay Buyer',
            date=date(2026, 3, 1), status=Invoice.Status.ISSUED)

    def _pay(self, **kwargs):
        defaults = dict(invoice=self.invoice, amount=Decimal('5000'),
                        date=date(2026, 3, 5), method=InvoicePayment.Method.BANK)
        defaults.update(kwargs)
        return InvoicePayment.objects.create(**defaults)

    def test_payment_creates_income(self):
        payment = self._pay()
        income = Income.objects.get(invoice_payment=payment)
        self.assertEqual(income.amount, Decimal('5000'))
        self.assertEqual(income.date, date(2026, 3, 5))
        self.assertEqual(income.farm_profile, self.farm)
        self.assertEqual(income.source.name, 'Invoice Payments')
        self.assertEqual(income.payment_method, Income.PaymentMethod.BANK_TRANSFER)
        self.assertEqual(income.customer_ref, self.customer)

    def test_payment_update_syncs_income(self):
        payment = self._pay()
        payment.amount = Decimal('7500')
        payment.method = InvoicePayment.Method.CASH
        payment.save()
        income = Income.objects.get(invoice_payment=payment)
        self.assertEqual(income.amount, Decimal('7500'))
        self.assertEqual(income.payment_method, Income.PaymentMethod.CASH)
        self.assertEqual(Income.objects.filter(invoice_payment=payment).count(), 1)

    def test_payment_delete_removes_income(self):
        payment = self._pay()
        self.assertTrue(Income.objects.filter(invoice_payment=payment).exists())
        payment.delete()
        self.assertFalse(Income.objects.filter(invoice_payment=payment).exists())

    def test_sale_linked_invoice_payment_skipped(self):
        """Payments on a sale's auto invoice do not double-book income."""
        item = InventoryItem.objects.create(
            farm_profile=self.farm, name='Beans', unit='kg',
            quantity_on_hand=Decimal('100'))
        sale = Sale.objects.create(
            inventory_item=item, sale_date=date(2026, 3, 2),
            quantity=Decimal('10'), unit='kg', unit_price=Decimal('100'),
            buyer='Sale Buyer', amount_received=Decimal('0'))
        inv = Invoice.objects.get(sale=sale)  # auto-created by the sale signal
        before = Income.objects.count()
        self._pay(invoice=inv)
        self.assertEqual(Income.objects.count(), before)


class ProformaInvoiceTests(TestCase):
    """Proforma invoices: numbering, no payments, PDF wording, conversion."""

    def setUp(self):
        self.user = User.objects.create_user(username='proforma-owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Quote Farm')
        self.customer = Customer.objects.create(farm_profile=self.farm, name='Quote Buyer')
        self.client.force_login(self.user)

    def _proforma(self):
        pro = Invoice.objects.create(
            farm_profile=self.farm, customer=self.customer, kind=Invoice.Kind.PROFORMA,
            date=date(2026, 4, 1), due_date=date(2026, 4, 30),
            status=Invoice.Status.ISSUED, tax_percent=Decimal('18'))
        InvoiceItem.objects.create(
            invoice=pro, description='Maize', quantity=Decimal('5'), unit='bag',
            unit_price=Decimal('100000'), unit_cost=Decimal('60000'))
        return pro

    def test_proforma_number_uses_pro_prefix(self):
        pro = self._proforma()
        self.assertTrue(pro.number.startswith('PRO-'))
        inv = Invoice.objects.create(
            farm_profile=self.farm, customer=self.customer, date=date(2026, 4, 1))
        self.assertTrue(inv.number.startswith('INV-'))

    def test_proforma_rejects_payments(self):
        pro = self._proforma()
        response = self.client.post(
            reverse('invoice_record_payment', kwargs={'pk': pro.pk}),
            {'amount': '1000', 'date': '2026-04-02', 'method': 'CASH'})
        self.assertRedirects(response, reverse('invoice_detail', kwargs={'pk': pro.pk}))
        self.assertEqual(pro.payments.count(), 0)
        pro.refresh_from_db()
        self.assertEqual(pro.status, Invoice.Status.ISSUED)

    def test_proforma_pdf_is_labelled_and_not_fiscal(self):
        pro = self._proforma()
        response = self.client.get(reverse('invoice_pdf', kwargs={'pk': pro.pk}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF-'))
        from pypdf import PdfReader
        from io import BytesIO
        text = PdfReader(BytesIO(response.content)).pages[0].extract_text()
        self.assertIn('PROFORMA INVOICE', text)
        self.assertIn('NOT A TAX INVOICE', text)
        self.assertIn('ESTIMATED TOTAL', text)
        self.assertIn('Valid until', text)
        self.assertNotIn('FISCAL VERIFICATION', text)
        self.assertNotIn('Balance due', text)

    def test_convert_creates_linked_invoice_with_items(self):
        pro = self._proforma()
        response = self.client.post(reverse('invoice_convert', kwargs={'pk': pro.pk}))
        pro.refresh_from_db()
        inv = pro.converted_invoice
        self.assertIsNotNone(inv)
        self.assertRedirects(response, reverse('invoice_detail', kwargs={'pk': inv.pk}))
        self.assertEqual(inv.kind, Invoice.Kind.INVOICE)
        self.assertTrue(inv.number.startswith('INV-'))
        self.assertEqual(inv.status, Invoice.Status.ISSUED)
        self.assertEqual(inv.items.count(), 1)
        self.assertEqual(inv.total, pro.total)
        self.assertEqual(inv.tax_percent, Decimal('18'))
        # Converting again is a no-op that returns the same invoice.
        self.client.post(reverse('invoice_convert', kwargs={'pk': pro.pk}))
        pro.refresh_from_db()
        self.assertEqual(pro.converted_invoice_id, inv.pk)
        self.assertEqual(Invoice.objects.filter(kind=Invoice.Kind.INVOICE).count(), 1)

    def test_list_filters_by_kind_and_excludes_proformas_from_billing(self):
        pro = self._proforma()
        Invoice.objects.create(
            farm_profile=self.farm, customer=self.customer, date=date(2026, 4, 1),
            status=Invoice.Status.ISSUED)
        page = self.client.get(reverse('invoice_list') + '?kind=PROFORMA')
        self.assertEqual([r['pk'] for r in page.context['rows']], [pro.pk])
        page = self.client.get(reverse('invoice_list'))
        stats = {s['label']: s['value'] for s in page.context['stats']}
        self.assertEqual(stats['Invoices'], 1)
        self.assertEqual(stats['Proformas'], 1)
        self.assertEqual(stats['Total Billed'], Decimal('0'))
        self.assertContains(page, 'Convert to Invoice')
        self.assertContains(page, 'New Proforma')
