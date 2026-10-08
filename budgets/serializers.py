from rest_framework import serializers

from django.core.exceptions import ValidationError as ModelValidationError

from .models import Budget, BudgetItem


class ValidatedBudgetSerializer(serializers.ModelSerializer):
    def validate(self, attrs):
        instance = self.Meta.model()
        if self.instance:
            for field in self.Meta.model._meta.concrete_fields:
                setattr(instance, field.attname, getattr(self.instance, field.attname))
        for key, value in attrs.items():
            setattr(instance, key, value)
        try:
            instance.clean()
        except ModelValidationError as exc:
            raise serializers.ValidationError(exc.message_dict)
        return attrs


class BudgetItemSerializer(ValidatedBudgetSerializer):
    spent_amount = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = BudgetItem
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')


class BudgetSerializer(ValidatedBudgetSerializer):
    def validate(self, attrs):
        attrs = super().validate(attrs)
        if attrs.get('status') == Budget.Status.ACTIVE and self.instance and (
                self.instance.items.filter(figure_type='UNCONFIRMED').exists()
                or self.instance.income_plans.filter(figure_type='UNCONFIRMED').exists()):
            raise serializers.ValidationError({'status': 'Confirm all line amounts before activating this budget.'})
        return attrs

    total_income_planned = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    funding_balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_planned = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    total_spent = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    remaining = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Budget
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at')
