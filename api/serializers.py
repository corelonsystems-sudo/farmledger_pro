from rest_framework import serializers

from crops.models import CropSeason, HarvestRecord
from equipment.models import Equipment, EquipmentUsage, MaintenanceLog
from fields.models import Field, LandParcel
from inventory.models import InventoryAlert, InventoryItem, InventoryTransaction
from labor.models import AttendanceRecord, PayrollRun, Task, Worker


class FieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = Field
        fields = '__all__'


class LandParcelSerializer(serializers.ModelSerializer):
    class Meta:
        model = LandParcel
        fields = '__all__'


class CropSeasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = CropSeason
        fields = '__all__'


class HarvestRecordSerializer(serializers.ModelSerializer):
    net_profit = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = HarvestRecord
        fields = '__all__'


class InventoryItemSerializer(serializers.ModelSerializer):
    is_low_stock = serializers.BooleanField(read_only=True)

    class Meta:
        model = InventoryItem
        fields = '__all__'


class InventoryTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryTransaction
        fields = '__all__'


class InventoryAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryAlert
        fields = '__all__'


class EquipmentSerializer(serializers.ModelSerializer):
    cost_per_hour = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    total_maintenance_cost = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    depreciation = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Equipment
        fields = '__all__'


class MaintenanceLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = MaintenanceLog
        fields = '__all__'


class EquipmentUsageSerializer(serializers.ModelSerializer):
    class Meta:
        model = EquipmentUsage
        fields = '__all__'


class WorkerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Worker
        fields = '__all__'


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = '__all__'


class AttendanceRecordSerializer(serializers.ModelSerializer):
    total_pay = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = AttendanceRecord
        fields = '__all__'


class PayrollRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollRun
        fields = '__all__'
