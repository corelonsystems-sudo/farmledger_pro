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
        if value is None:
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
            offline_uuid = item.get('offline_uuid') or uuid.uuid4()
            obj, created = Expense.objects.get_or_create(
                offline_uuid=offline_uuid,
                defaults=item,
            )
            results.append({'id': obj.id, 'offline_uuid': str(obj.offline_uuid), 'created': created})
        return results
