from decimal import Decimal

from django.contrib.auth.models import AbstractUser
from django.db import models


class Currency(models.Model):
    code = models.CharField(max_length=3, unique=True, help_text='e.g. USD, EUR, UGX')
    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=10, default='$')
    rate = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        default=Decimal('1.0'),
        help_text='Amount of this currency equal to 1 base (USD) unit'
    )
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = 'currencies'

    def __str__(self):
        return f'{self.name} ({self.code})'


class User(AbstractUser):
    class Role(models.TextChoices):
        OWNER = 'OWNER', 'Owner'
        MANAGER = 'MANAGER', 'Manager'
        WORKER = 'WORKER', 'Worker'
        ACCOUNTANT = 'ACCOUNTANT', 'Accountant'

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.WORKER,
    )
    phone = models.CharField(max_length=20, blank=True, default='')

    class Meta:
        permissions = [
            ('can_manage_users', 'Can manage users'),
            ('can_view_reports', 'Can view reports'),
            ('can_manage_budgets', 'Can manage budgets'),
            ('can_manage_expenses', 'Can manage expenses'),
            ('can_run_payroll', 'Can run payroll'),
        ]


class FarmProfile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='farm_profile',
    )
    farm_name = models.CharField(max_length=200)
    location = models.CharField(max_length=300, blank=True, default='')
    acreage = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_id = models.CharField(max_length=50, blank=True, default='')
    fiscal_year_start = models.DateField(null=True, blank=True)
    phone = models.CharField(max_length=30, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    logo = models.ImageField(upload_to='farm_logos/', null=True, blank=True)
    default_currency = models.ForeignKey(
        Currency,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='farm_profiles',
        help_text='Currency used to display monetary values'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.farm_name
