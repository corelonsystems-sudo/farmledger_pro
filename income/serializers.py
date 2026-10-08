import uuid

from rest_framework import serializers

from budgets.models import BudgetItem
from .models import Income, IncomeSource


class IncomeSourceSerializer(serializers.ModelSerializer):
    class Meta:
        model = IncomeSource
        fields = '__all__'


class IncomeSerializer(serializers.ModelSerializer):
    source = serializers.PrimaryKeyRelatedField(
        queryset=IncomeSource.objects.all(),
        required=False,
        allow_null=True,
    )
    budget_item = serializers.PrimaryKeyRelatedField(
        queryset=BudgetItem.objects.all(),
        required=False,
        allow_null=True,
    )

    class Meta:
        model = Income
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class BulkIncomeSyncSerializer(serializers.Serializer):
    incomes = IncomeSerializer(many=True)

    def create(self, validated_data):
        incomes_data = validated_data.get('incomes', [])
        results = []
        for item in incomes_data:
            offline_uuid = item.pop('offline_uuid', None) or uuid.uuid4()
            obj = Income.objects.filter(offline_uuid=offline_uuid).first()
            created = False
            if obj is None:
                obj = Income.objects.create(offline_uuid=offline_uuid, **item)
                created = True
            elif obj.farm_profile_id != item['farm_profile'].pk:
                # UUID belongs to another farm's record: mint a fresh one
                # instead of returning data owned by a different farm.
                obj = Income.objects.create(**item)
                created = True
            results.append({'id': obj.id, 'offline_uuid': str(obj.offline_uuid), 'created': created})
        return results
