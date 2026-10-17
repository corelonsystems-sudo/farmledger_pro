from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import FarmProfile, User
from .models import (
    AnimalGroup, AnimalHealthRecord, AnimalRecord, AnimalSpecies,
    BreedingRecord, EggProductionRecord, MilkProductionRecord, PoultryBatch,
)


class LivestockListPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        other_farm = FarmProfile.objects.create(
            user=User.objects.create_user(username='other', password='pw'),
            farm_name='Farm B')
        self.species = AnimalSpecies.objects.create(name='Holstein', species_type='CATTLE')
        self.group = AnimalGroup.objects.create(
            farm_profile=self.farm, name='Milking Herd', species=self.species,
            purpose='DAIRY', count=12)
        self.other_group = AnimalGroup.objects.create(
            farm_profile=other_farm, name='Hidden Herd', species=self.species,
            count=30)
        self.animal = AnimalRecord.objects.create(
            farm_profile=self.farm, tag_number='A-001', species=self.species,
            group=self.group, gender='F', health_status='SICK',
            current_weight_kg=Decimal('400'))
        AnimalRecord.objects.create(
            farm_profile=other_farm, tag_number='X-999', species=self.species,
            health_status='HEALTHY')
        self.batch = PoultryBatch.objects.create(
            farm_profile=self.farm, batch_name='Layers 01', bird_type='CHICKEN_LAYER',
            purpose='EGGS', start_date=date(2025, 1, 1),
            initial_count=100, current_count=95, mortality_count=5)
        self.client.force_login(self.user)

    def url(self, model):
        return reverse('model_list', args=['livestock', model])

    def test_species_page_shows_counts(self):
        response = self.client.get(self.url('animalspecies'))
        self.assertEqual(response.status_code, 200)
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Animal Groups'], 2)
        self.assertEqual(stats['Animals Tracked'], 2)
        self.assertContains(response, 'Holstein')

    def test_groups_are_farm_scoped(self):
        response = self.client.get(self.url('animalgroup'))
        self.assertContains(response, 'Milking Herd')
        self.assertNotContains(response, 'Hidden Herd')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Total Head'], 12)

    def test_animal_records_show_health_stats(self):
        response = self.client.get(self.url('animalrecord'))
        self.assertContains(response, 'A-001')
        self.assertNotContains(response, 'X-999')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Sick / In Treatment'], 1)
        self.assertEqual(stats['Animals'], 1)

    def test_health_records_page(self):
        AnimalHealthRecord.objects.create(
            animal=self.animal, record_type='VACCINATION',
            date=date(2025, 5, 1), description='FMD vaccine',
            cost=Decimal('50'), next_due_date=date(2099, 1, 1))
        response = self.client.get(self.url('animalhealthrecord'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'A-001')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Upcoming Due'], 1)
        self.assertEqual(stats['Total Cost'], Decimal('50'))

    def test_poultry_batch_page(self):
        response = self.client.get(self.url('poultrybatch'))
        self.assertContains(response, 'Layers 01')
        self.assertContains(response, '5.0%')  # mortality rate
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Live Birds'], 95)

    def test_egg_production_page(self):
        EggProductionRecord.objects.create(
            batch=self.batch, date=date.today(), eggs_collected=80,
            damaged_eggs=4, price_per_egg=Decimal('0.3'))
        response = self.client.get(self.url('eggproductionrecord'))
        self.assertEqual(response.status_code, 200)
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['Eggs Collected'], 80)
        self.assertEqual(stats['This Month'], 80)

    def test_milk_page(self):
        MilkProductionRecord.objects.create(
            group=self.group, date=date.today(), morning_liters=Decimal('20'),
            evening_liters=Decimal('15'), price_per_liter=Decimal('1.5'))
        response = self.client.get(self.url('milkproductionrecord'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '35')
        stats = {s['label']: s['value'] for s in response.context['stats']}
        self.assertEqual(stats['This Month'], '35 L')


class AnimalRecordDetailTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(user=self.user, farm_name='Farm A')
        other_farm = FarmProfile.objects.create(
            user=User.objects.create_user(username='other', password='pw'),
            farm_name='Farm B')
        species = AnimalSpecies.objects.create(name='Goat', species_type='GOATS')
        self.animal = AnimalRecord.objects.create(
            farm_profile=self.farm, tag_number='G-001', name='Bella',
            species=species, gender='F', health_status='HEALTHY')
        self.other_animal = AnimalRecord.objects.create(
            farm_profile=other_farm, tag_number='X-1', species=species)
        AnimalHealthRecord.objects.create(
            animal=self.animal, record_type='CHECKUP',
            date=date(2025, 6, 1), description='Routine check', cost=Decimal('30'))
        BreedingRecord.objects.create(
            farm_profile=self.farm, dam=self.animal, species=species,
            mating_date=date(2025, 3, 1), status='PREGNANT', offspring_count=0)
        self.client.force_login(self.user)

    def test_detail_page_renders(self):
        response = self.client.get(
            reverse('animalrecord_detail', args=[self.animal.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Bella')
        self.assertContains(response, 'Routine check')
        self.assertContains(response, 'Pregnant')

    def test_detail_is_farm_scoped(self):
        response = self.client.get(
            reverse('animalrecord_detail', args=[self.other_animal.pk]))
        self.assertEqual(response.status_code, 404)
