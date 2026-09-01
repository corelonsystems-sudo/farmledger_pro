from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from accounts.models import FarmProfile
from budgets.models import Budget
from crops.models import Crop, CropSeason, HarvestRecord, Sale
from equipment.models import Equipment, MaintenanceLog
from expenses.models import Category, Expense
from fields.models import Field, LandParcel
from inventory.models import InventoryItem
from labor.models import AttendanceRecord, Task, Worker

User = get_user_model()

PRE_SEED_CATEGORIES = [
    ('Seed', 'part1_seeds_plants'),
    ('Feed', 'part1_feed'),
    ('Fertilizer', 'part1_fertilizer'),
    ('Fuel', 'part1_gasoline_fuel'),
    ('Chemicals', 'part1_chemicals'),
    ('Equipment Repair', 'part1_repairs_maintenance'),
    ('Labor', 'part1_labor_hired'),
    ('Rent', 'part1_rent_lease_land'),
    ('Insurance', 'part1_insurance'),
    ('Supplies', 'part1_supplies'),
]


class Command(BaseCommand):
    help = 'Seed the database with sample farm data for immediate demo.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.HTTP_INFO('Seeding farm data...'))

        # --- Create Owner User ---
        owner, created = User.objects.get_or_create(
            username='farmowner',
            defaults={
                'email': 'owner@farmledger.pro',
                'role': User.Role.OWNER,
                'is_staff': True,
                'is_superuser': True,
            },
        )
        if created:
            owner.set_password('demo1234')
            owner.save()
            self.stdout.write(f'  Created owner user: {owner.username}')
        else:
            self.stdout.write(f'  Owner user already exists: {owner.username}')

        # --- Create Manager User ---
        manager, _ = User.objects.get_or_create(
            username='farmmanager',
            defaults={
                'email': 'manager@farmledger.pro',
                'role': User.Role.MANAGER,
                'is_staff': True,
            },
        )
        if created:
            manager.set_password('demo1234')
            manager.save()

        # --- Create Farm Profile ---
        farm, created = FarmProfile.objects.get_or_create(
            user=owner,
            defaults={
                'farm_name': 'Green Valley Farm',
                'location': 'Iowa, USA',
                'acreage': Decimal('500.00'),
                'tax_id': 'TAX-123456789',
                'fiscal_year_start': date(2025, 1, 1),
            },
        )
        if created:
            self.stdout.write(f'  Created farm profile: {farm.farm_name}')
        else:
            self.stdout.write(f'  Farm profile already exists: {farm.farm_name}')

        # --- Create Categories ---
        categories = {}
        for name, schedule_f_line in PRE_SEED_CATEGORIES:
            cat, created = Category.objects.get_or_create(
                name=name,
                defaults={'schedule_f_line': schedule_f_line},
            )
            categories[name] = cat
        self.stdout.write(f'  Created {len(categories)} expense categories')

        # --- Create Fields ---
        field1, _ = Field.objects.get_or_create(
            farm_profile=farm,
            name='North Field',
            defaults={
                'acreage': Decimal('120.00'),
                'soil_type': Field.SoilType.LOAM,
                'gps_coordinates': {'lat': 41.878, 'lng': -93.097},
            },
        )
        field2, _ = Field.objects.get_or_create(
            farm_profile=farm,
            name='South Field',
            defaults={
                'acreage': Decimal('80.00'),
                'soil_type': Field.SoilType.CLAY,
                'gps_coordinates': {'lat': 41.875, 'lng': -93.095},
            },
        )
        field3, _ = Field.objects.get_or_create(
            farm_profile=farm,
            name='East Pasture',
            defaults={
                'acreage': Decimal('200.00'),
                'soil_type': Field.SoilType.SANDY,
                'gps_coordinates': {'lat': 41.880, 'lng': -93.090},
            },
        )
        self.stdout.write(f'  Created 3 fields')

        # --- Create Land Parcels ---
        LandParcel.objects.get_or_create(
            field=field2,
            land_type=LandParcel.LandType.LEASED,
            defaults={
                'lease_cost': Decimal('8000.00'),
                'lease_start': date(2025, 1, 1),
                'lease_end': date(2025, 12, 31),
                'owner_name': 'John Anderson',
            },
        )

        # --- Create Crops ---
        corn, _ = Crop.objects.get_or_create(
            name='Corn',
            defaults={'category': Crop.CropCategory.GRAIN, 'scientific_name': 'Zea mays', 'default_unit': 'kg'},
        )
        soybeans, _ = Crop.objects.get_or_create(
            name='Soybeans',
            defaults={'category': Crop.CropCategory.OILSEED, 'scientific_name': 'Glycine max', 'default_unit': 'kg'},
        )
        alfalfa, _ = Crop.objects.get_or_create(
            name='Alfalfa',
            defaults={'category': Crop.CropCategory.FORAGE, 'scientific_name': 'Medicago sativa', 'default_unit': 'kg'},
        )

        # --- Create Crop Seasons ---
        season1, _ = CropSeason.objects.get_or_create(
            field=field1,
            crop=corn,
            planting_date=date(2025, 4, 15),
            defaults={
                'variety': 'Pioneer P1197',
                'expected_harvest_date': date(2025, 10, 15),
                'status': CropSeason.Status.HARVESTED,
                'actual_harvest_date': date(2025, 10, 10),
            },
        )
        season2, _ = CropSeason.objects.get_or_create(
            field=field2,
            crop=soybeans,
            planting_date=date(2025, 5, 1),
            defaults={
                'variety': 'Asgrow AG24X4',
                'expected_harvest_date': date(2025, 10, 25),
                'status': CropSeason.Status.HARVESTED,
                'actual_harvest_date': date(2025, 10, 20),
            },
        )
        season3, _ = CropSeason.objects.get_or_create(
            field=field3,
            crop=alfalfa,
            planting_date=date(2025, 3, 20),
            defaults={
                'expected_harvest_date': date(2025, 9, 15),
                'status': CropSeason.Status.GROWING,
            },
        )
        self.stdout.write(f'  Created 3 crop seasons')

        # --- Create Harvest Records ---
        harvest1, _ = HarvestRecord.objects.get_or_create(
            crop_season=season1,
            harvest_date=date(2025, 10, 10),
            defaults={
                'quantity': Decimal('18000'),
                'unit': 'kg',
            },
        )
        harvest2, _ = HarvestRecord.objects.get_or_create(
            crop_season=season2,
            harvest_date=date(2025, 10, 20),
            defaults={
                'quantity': Decimal('12000'),
                'unit': 'kg',
            },
        )
        self.stdout.write(f'  Created 2 harvest records')

        # --- Create Sales ---
        Sale.objects.get_or_create(
            harvest=harvest1,
            sale_date=date(2025, 10, 12),
            defaults={
                'quantity': Decimal('18000'),
                'unit': 'kg',
                'unit_price': Decimal('0.18'),
                'buyer': 'Iowa Grain Co.',
            },
        )
        Sale.objects.get_or_create(
            harvest=harvest2,
            sale_date=date(2025, 10, 22),
            defaults={
                'quantity': Decimal('12000'),
                'unit': 'kg',
                'unit_price': Decimal('0.55'),
                'buyer': 'Soybean Traders LLC',
            },
        )
        self.stdout.write(f'  Created 2 sales')

        # --- Create Equipment ---
        tractor, _ = Equipment.objects.get_or_create(
            farm_profile=farm,
            name='John Deere 8R 410',
            defaults={
                'equipment_type': Equipment.EquipmentType.TRACTOR,
                'purchase_date': date(2022, 3, 15),
                'purchase_cost': Decimal('250000.00'),
                'current_value': Decimal('200000.00'),
                'hours_used': Decimal('1500.00'),
            },
        )
        harvester, _ = Equipment.objects.get_or_create(
            farm_profile=farm,
            name='Case IH 8250',
            defaults={
                'equipment_type': Equipment.EquipmentType.HARVESTER,
                'purchase_date': date(2021, 8, 1),
                'purchase_cost': Decimal('180000.00'),
                'current_value': Decimal('130000.00'),
                'hours_used': Decimal('800.00'),
            },
        )
        truck, _ = Equipment.objects.get_or_create(
            farm_profile=farm,
            name='Ford F-350',
            defaults={
                'equipment_type': Equipment.EquipmentType.TRUCK,
                'purchase_date': date(2023, 1, 10),
                'purchase_cost': Decimal('65000.00'),
                'current_value': Decimal('55000.00'),
                'hours_used': Decimal('600.00'),
            },
        )
        self.stdout.write(f'  Created 3 equipment records')

        # --- Create Maintenance Logs ---
        MaintenanceLog.objects.get_or_create(
            equipment=tractor,
            date=date(2025, 6, 1),
            defaults={
                'description': 'Oil change and filter replacement',
                'cost': Decimal('450.00'),
                'performed_by': 'John Deere Service Center',
            },
        )
        MaintenanceLog.objects.get_or_create(
            equipment=harvester,
            date=date(2025, 7, 15),
            defaults={
                'description': 'Header repair and belt replacement',
                'cost': Decimal('1200.00'),
                'performed_by': 'Case IH Dealer',
            },
        )

        # --- Create Expenses ---
        expense_data = [
            (categories['Seed'], Decimal('5500.00'), date(2025, 4, 10), 'Pioneer Seeds', season1, field1),
            (categories['Fertilizer'], Decimal('3200.00'), date(2025, 4, 12), 'Nutrien Ag Solutions', season1, field1),
            (categories['Fuel'], Decimal('1800.00'), date(2025, 4, 20), 'Local Fuel Co.', season1, field1),
            (categories['Chemicals'], Decimal('2100.00'), date(2025, 5, 15), 'Crop Protection Inc.', season1, field1),
            (categories['Equipment Repair'], Decimal('450.00'), date(2025, 6, 1), 'John Deere Service', season1, field1, tractor),
            (categories['Seed'], Decimal('3800.00'), date(2025, 4, 28), 'Asgrow Seeds', season2, field2),
            (categories['Fertilizer'], Decimal('2400.00'), date(2025, 5, 2), 'Nutrien Ag Solutions', season2, field2),
            (categories['Fuel'], Decimal('1200.00'), date(2025, 5, 10), 'Local Fuel Co.', season2, field2),
            (categories['Rent'], Decimal('8000.00'), date(2025, 1, 5), 'John Anderson', season2, field2),
            (categories['Insurance'], Decimal('3500.00'), date(2025, 1, 15), 'Farm Bureau Insurance', None, None),
            (categories['Equipment Repair'], Decimal('1200.00'), date(2025, 7, 15), 'Case IH Dealer', None, None, harvester),
            (categories['Fuel'], Decimal('900.00'), date(2025, 3, 25), 'Local Fuel Co.', season3, field3),
        ]

        for i, item in enumerate(expense_data):
            cat, amount, exp_date, vendor, cs, field_obj = item[:6]
            equip = item[6] if len(item) > 6 else None
            Expense.objects.get_or_create(
                farm_profile=farm,
                category=cat,
                amount=amount,
                date=exp_date,
                defaults={
                    'vendor': vendor,
                    'crop_season': cs,
                    'field': field_obj,
                    'equipment': equip,
                    'payment_method': Expense.PaymentMethod.CHECK,
                },
            )
        self.stdout.write(f'  Created {len(expense_data)} expense records')

        # --- Create Inventory Items ---
        inv1, _ = InventoryItem.objects.get_or_create(
            farm_profile=farm,
            name='Diesel Fuel',
            defaults={
                'unit': 'gallons',
                'quantity_on_hand': Decimal('50.00'),
                'reorder_threshold': Decimal('100.00'),
                'cost_per_unit': Decimal('3.50'),
                'supplier': 'Local Fuel Co.',
            },
        )
        inv2, _ = InventoryItem.objects.get_or_create(
            farm_profile=farm,
            name='Fertilizer NPK 10-10-10',
            defaults={
                'unit': 'bags',
                'quantity_on_hand': Decimal('200.00'),
                'reorder_threshold': Decimal('50.00'),
                'cost_per_unit': Decimal('25.00'),
                'supplier': 'Nutrien Ag Solutions',
            },
        )
        inv3, _ = InventoryItem.objects.get_or_create(
            farm_profile=farm,
            name='Herbicide Roundup',
            defaults={
                'unit': 'gallons',
                'quantity_on_hand': Decimal('30.00'),
                'reorder_threshold': Decimal('20.00'),
                'cost_per_unit': Decimal('45.00'),
                'supplier': 'Crop Protection Inc.',
            },
        )
        self.stdout.write(f'  Created 3 inventory items')

        # --- Create Workers ---
        worker1, _ = Worker.objects.get_or_create(
            farm_profile=farm,
            name='Mike Johnson',
            defaults={
                'hourly_rate': Decimal('18.00'),
                'tax_id': 'SSN-111-22-3333',
                'phone': '555-0101',
            },
        )
        worker2, _ = Worker.objects.get_or_create(
            farm_profile=farm,
            name='Sarah Williams',
            defaults={
                'hourly_rate': Decimal('20.00'),
                'tax_id': 'SSN-444-55-6666',
                'phone': '555-0102',
            },
        )
        worker3, _ = Worker.objects.get_or_create(
            farm_profile=farm,
            name='Tom Brown',
            defaults={
                'hourly_rate': Decimal('15.00'),
                'tax_id': 'SSN-777-88-9999',
                'phone': '555-0103',
            },
        )
        self.stdout.write(f'  Created 3 workers')

        # --- Create Tasks ---
        task1, _ = Task.objects.get_or_create(
            name='Corn Planting',
            defaults={
                'crop_season': season1,
                'field': field1,
                'status': Task.TaskStatus.COMPLETED,
                'start_date': date(2025, 4, 15),
                'end_date': date(2025, 4, 18),
            },
        )
        task1.workers.add(worker1, worker2)

        task2, _ = Task.objects.get_or_create(
            name='Soybean Harvest',
            defaults={
                'crop_season': season2,
                'field': field2,
                'status': Task.TaskStatus.COMPLETED,
                'start_date': date(2025, 10, 20),
                'end_date': date(2025, 10, 22),
            },
        )
        task2.workers.add(worker1, worker3)

        # --- Create Attendance Records ---
        AttendanceRecord.objects.get_or_create(
            worker=worker1,
            task=task1,
            date=date(2025, 4, 15),
            defaults={'hours': Decimal('8.00'), 'overtime_hours': Decimal('0.00')},
        )
        AttendanceRecord.objects.get_or_create(
            worker=worker1,
            task=task1,
            date=date(2025, 4, 16),
            defaults={'hours': Decimal('8.00'), 'overtime_hours': Decimal('0.00')},
        )
        AttendanceRecord.objects.get_or_create(
            worker=worker2,
            task=task1,
            date=date(2025, 4, 15),
            defaults={'hours': Decimal('8.00'), 'overtime_hours': Decimal('0.00')},
        )
        AttendanceRecord.objects.get_or_create(
            worker=worker1,
            task=task2,
            date=date(2025, 10, 20),
            defaults={'hours': Decimal('10.00'), 'overtime_hours': Decimal('2.00')},
        )
        AttendanceRecord.objects.get_or_create(
            worker=worker3,
            task=task2,
            date=date(2025, 10, 20),
            defaults={'hours': Decimal('8.00'), 'overtime_hours': Decimal('0.00')},
        )
        self.stdout.write(f'  Created attendance records')

        # --- Create Budgets ---
        Budget.objects.get_or_create(
            crop_season=season1,
            category=categories['Seed'],
            defaults={'planned_amount': Decimal('6000.00')},
        )
        Budget.objects.get_or_create(
            crop_season=season1,
            category=categories['Fertilizer'],
            defaults={'planned_amount': Decimal('3500.00')},
        )
        Budget.objects.get_or_create(
            crop_season=season1,
            category=categories['Fuel'],
            defaults={'planned_amount': Decimal('2000.00')},
        )
        Budget.objects.get_or_create(
            crop_season=season1,
            category=categories['Chemicals'],
            defaults={'planned_amount': Decimal('2500.00')},
        )
        Budget.objects.get_or_create(
            crop_season=season2,
            category=categories['Seed'],
            defaults={'planned_amount': Decimal('4000.00')},
        )
        Budget.objects.get_or_create(
            crop_season=season2,
            category=categories['Rent'],
            defaults={'planned_amount': Decimal('8000.00')},
        )
        self.stdout.write(f'  Created 6 budget records')

        self.stdout.write(
            self.style.SUCCESS(
                '\nSeed data complete!\n'
                '  Login: farmowner / demo1234\n'
                '  Farm: Green Valley Farm (500 acres, Iowa)\n'
                '  3 fields, 3 crop seasons, 12 expenses, 3 inventory items,\n'
                '  3 equipment, 3 workers, 6 budgets'
            )
        )
