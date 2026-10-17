from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import FarmProfile, User
from .models import Field, LandParcel


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


class LandParcelListPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        field = Field.objects.create(
            farm_profile=self.farm, name='North', acreage=Decimal('10'))
        LandParcel.objects.create(
            field=field, land_type='LEASED', lease_cost=Decimal('500'),
            owner_name='Landlord One')
        other_field = Field.objects.create(
            farm_profile=FarmProfile.objects.create(
                user=User.objects.create_user(username='other', password='pw'),
                farm_name='Farm B'),
            name='South', acreage=Decimal('3'))
        LandParcel.objects.create(
            field=other_field, land_type='OWNED', owner_name='Hidden Owner')
        self.url = reverse('model_list', args=['fields', 'landparcel'])

    def test_page_renders_scoped_with_stats(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Landlord One')
        self.assertNotContains(response, 'Hidden Owner')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Parcels'], 1)
        self.assertEqual(stats['Lease Costs'], Decimal('500'))

    def test_tenure_filter_applies(self):
        self.client.force_login(self.user)
        response = self.client.get(self.url, {'type': 'OWNED'})
        self.assertNotContains(response, 'Landlord One')


class FieldAndParcelDetailTests(TestCase):
    def setUp(self):
        from datetime import date, timedelta
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        other_farm = FarmProfile.objects.create(
            user=User.objects.create_user(username='other', password='pw'))
        self.field = Field.objects.create(
            farm_profile=self.farm, name='North Field', acreage=Decimal('10'))
        self.parcel = LandParcel.objects.create(
            field=self.field, land_type='LEASED', lease_cost=Decimal('500'),
            owner_name='Landlord One',
            lease_start=date(2025, 1, 1),
            lease_end=date.today() + timedelta(days=20))
        self.other_field = Field.objects.create(
            farm_profile=other_farm, name='Hidden Field', acreage=Decimal('1'))
        self.other_parcel = LandParcel.objects.create(
            field=self.other_field, land_type='OWNED')
        self.client.force_login(self.user)

    def test_field_detail_page(self):
        response = self.client.get(reverse('field_detail', args=[self.field.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'North Field')
        self.assertContains(response, 'Landlord One')

    def test_parcel_detail_shows_lease_status(self):
        response = self.client.get(
            reverse('landparcel_detail', args=[self.parcel.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Expiring')

    def test_detail_pages_are_farm_scoped(self):
        self.assertEqual(self.client.get(
            reverse('field_detail', args=[self.other_field.pk])).status_code, 404)
        self.assertEqual(self.client.get(
            reverse('landparcel_detail', args=[self.other_parcel.pk])).status_code, 404)

    def test_lease_expiry_pills_on_list(self):
        from datetime import date, timedelta
        LandParcel.objects.create(
            field=self.field, land_type='LEASED', owner_name='Old Lease',
            lease_end=date.today() - timedelta(days=10))
        response = self.client.get(reverse('model_list', args=['fields', 'landparcel']))
        self.assertContains(response, 'Expiring')
        self.assertContains(response, 'Expired')


class FieldListCropsColumnTests(TestCase):
    def setUp(self):
        from datetime import date
        from crops.models import Crop, CropSeason
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.field = Field.objects.create(
            farm_profile=self.farm, name='North', acreage=Decimal('10'))
        crop = Crop.objects.create(name='Maize T', default_unit='kg')
        crop2 = Crop.objects.create(name='Beans T', default_unit='kg')
        CropSeason.objects.create(
            field=self.field, crop=crop, planting_date=date(2024, 1, 1),
            actual_harvest_date=date(2024, 6, 30))
        CropSeason.objects.create(
            field=self.field, crop=crop2, planting_date=date(2025, 1, 1))
        self.client.force_login(self.user)

    def test_crops_column_lists_crops_per_season(self):
        response = self.client.get(reverse('model_list', args=['fields', 'field']))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Beans T')
        self.assertContains(response, '2025')
        self.assertContains(response, 'Maize T')
        self.assertContains(response, '2024')


class SeasonFormFieldCropsTests(TestCase):
    def setUp(self):
        from datetime import date
        from crops.models import Crop, CropSeason
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.field = Field.objects.create(
            farm_profile=self.farm, name='North', acreage=Decimal('10'))
        crop = Crop.objects.create(name='Maize T', default_unit='kg')
        self.season = CropSeason.objects.create(
            field=self.field, crop=crop, planting_date=date(2024, 1, 1),
            actual_harvest_date=date(2024, 6, 30))
        self.client.force_login(self.user)

    def test_season_form_shows_field_crops_map(self):
        url = reverse('model_create', args=['crops', 'cropseason'])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'fieldSeasonsData')
        self.assertContains(response, '\"crop\": \"Maize T\"')
        self.assertContains(response, 'fieldCropsPanel')

    def test_season_edit_excludes_self_from_map(self):
        url = reverse('model_update', args=['crops', 'cropseason', self.season.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'fieldSeasonsData')
        self.assertNotContains(response, '\"crop\": \"Maize T\"')


class FieldCropBreakdownTests(TestCase):
    def setUp(self):
        from datetime import date
        from crops.models import Crop, CropSeason, HarvestRecord
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        self.field = Field.objects.create(
            farm_profile=self.farm, name='North Field', acreage=Decimal('10'))
        maize = Crop.objects.create(name='Maize', default_unit='kg')
        beans = Crop.objects.create(name='Beans', default_unit='kg')
        s1 = CropSeason.objects.create(
            field=self.field, crop=maize, planting_date=date(2025, 1, 1),
            actual_harvest_date=date(2025, 6, 1))
        CropSeason.objects.create(
            field=self.field, crop=maize, planting_date=date(2025, 7, 1),
            actual_harvest_date=date(2025, 12, 1))
        CropSeason.objects.create(
            field=self.field, crop=beans, planting_date=date(2026, 1, 1))
        HarvestRecord.objects.create(
            crop_season=s1, harvest_date=date(2025, 5, 15),
            quantity=Decimal('80'), unit='kg')
        self.client.force_login(self.user)

    def test_field_detail_shows_crop_counts(self):
        response = self.client.get(reverse('field_detail', args=[self.field.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Crops Grown')
        body = response.content.decode()
        block = body.split('Crops Grown', 1)[1].split('Crop Seasons', 1)[0]
        self.assertIn('Maize', block)
        self.assertIn('Beans', block)
        # Maize has 2 seasons, Beans has 1; maize shows its 80 kg harvest.
        self.assertIn('80 kg', block)
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Crops Grown'], 2)
