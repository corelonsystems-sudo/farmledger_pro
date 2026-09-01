from rest_framework import serializers

from .models import User, FarmProfile


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('id', 'username', 'email', 'role', 'phone', 'is_staff', 'is_active')
        read_only_fields = ('id',)


class FarmProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = FarmProfile
        fields = ('id', 'user', 'username', 'farm_name', 'location', 'acreage', 'tax_id', 'fiscal_year_start')
        read_only_fields = ('id', 'user')
