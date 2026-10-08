import uuid

from rest_framework import serializers

from budgets.models import BudgetItem
from .models import Category, Expense


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'


class ExpenseSerializer(serializers.ModelSerializer):
    budget_item = serializers.PrimaryKeyRelatedField(
        queryset=BudgetItem.objects.all(),
    )

    class Meta:
        model = Expense
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at', 'category')

    def validate_budget_item(self, value):
        # Processing-stage expenses are system-generated without a budget
        # item; every manually managed expense still requires one.
        if value is None and not getattr(
                self.instance, 'processing_stage_id', None):
            raise serializers.ValidationError('Every expense must be linked to a budget item.')
        return value

    def create(self, validated_data):
        budget_item = validated_data.get('budget_item')
        if budget_item:
            validated_data['category'] = budget_item.category
        return super().create(validated_data)

    def update(self, instance, validated_data):
        budget_item = validated_data.get('budget_item')
        if budget_item:
            validated_data['category'] = budget_item.category
        return super().update(instance, validated_data)


class BulkExpenseSyncSerializer(serializers.Serializer):
    expenses = ExpenseSerializer(many=True)

    def create(self, validated_data):
        expenses_data = validated_data.get('expenses', [])
        results = []
        for item in expenses_data:
            offline_uuid = item.pop('offline_uuid', None) or uuid.uuid4()
            item['category'] = item['budget_item'].category
            obj = Expense.objects.filter(offline_uuid=offline_uuid).first()
            created = False
            if obj is None:
                obj = Expense.objects.create(offline_uuid=offline_uuid, **item)
                created = True
            elif obj.farm_profile_id != item['farm_profile'].pk:
                # UUID belongs to another farm's record: mint a fresh one
                # instead of returning data owned by a different farm.
                obj = Expense.objects.create(**item)
                created = True
            results.append({'id': obj.id, 'offline_uuid': str(obj.offline_uuid), 'created': created})
        return results
