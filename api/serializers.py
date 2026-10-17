from rest_framework import serializers

from budgets.models import Budget, BudgetIncomePlan, BudgetItem
from crops.models import Crop, CropSeason, Customer, HarvestRecord, ProcessingStage, Sale
from equipment.models import Equipment, EquipmentUsage, MaintenanceLog
from fields.models import Field, LandParcel
from income.models import Income
from inventory.models import InventoryAlert, InventoryItem, InventoryTransaction
from labor.models import AttendanceRecord, PayrollLine, PayrollRun, Task, Worker


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


class CropSerializer(serializers.ModelSerializer):
    class Meta:
        model = Crop
        fields = '__all__'


class ProcessingStageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProcessingStage
        fields = '__all__'


class SaleSerializer(serializers.ModelSerializer):
    total_amount = serializers.DecimalField(
        max_digits=16, decimal_places=2, read_only=True)
    receivable = serializers.DecimalField(
        max_digits=16, decimal_places=2, read_only=True)

    class Meta:
        model = Sale
        fields = '__all__'


class CustomerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Customer
        fields = '__all__'


class IncomeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Income
        fields = '__all__'


class BudgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Budget
        fields = '__all__'


class BudgetItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetItem
        fields = '__all__'


class BudgetIncomePlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = BudgetIncomePlan
        fields = '__all__'


class PayrollLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollLine
        fields = '__all__'
