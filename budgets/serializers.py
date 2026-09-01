from rest_framework import serializers

from .models import Budget, BudgetItem


class BudgetItemSerializer(serializers.ModelSerializer):
    spent_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = BudgetItem
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class BudgetSerializer(serializers.ModelSerializer):
    total_planned = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_spent = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Budget
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')
