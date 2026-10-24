import uuid

from django.db import models


class MtnMoMoTransaction(models.Model):
    """An MTN Mobile Money collection request (request-to-pay)."""

    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SUCCESSFUL = 'SUCCESSFUL', 'Successful'
        FAILED = 'FAILED', 'Failed'

    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='mtn_transactions',
    )
    invoice = models.ForeignKey(
        'crops.Invoice',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='mtn_transactions',
    )
    invoice_payment = models.ForeignKey(
        'crops.InvoicePayment',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='mtn_transactions',
    )
    reference_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    external_id = models.CharField(max_length=100, blank=True, default='')
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3, default='UGX')
    payer_msisdn = models.CharField(max_length=20)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    financial_transaction_id = models.CharField(max_length=100, blank=True, default='')
    payer_message = models.CharField(max_length=200, blank=True, default='')
    payee_note = models.CharField(max_length=200, blank=True, default='')
    status_reason = models.TextField(blank=True, default='')
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'MTN {self.amount} {self.currency} from {self.payer_msisdn} - {self.status}'


class BankAccount(models.Model):
    farm_profile = models.ForeignKey(
        'accounts.FarmProfile',
        on_delete=models.CASCADE,
        related_name='bank_accounts',
    )
    name = models.CharField(max_length=200)
    bank_name = models.CharField(max_length=200)
    account_number_last4 = models.CharField(max_length=4, blank=True, default='')
    routing_number = models.CharField(max_length=20, blank=True, default='')
    account_type = models.CharField(max_length=20, default='checking')
    plaid_item_id = models.CharField(max_length=100, blank=True, default='')
    plaid_access_token = models.CharField(max_length=200, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.name} - {self.bank_name} (...{self.account_number_last4})'


class BankTransaction(models.Model):
    bank_account = models.ForeignKey(
        BankAccount,
        on_delete=models.CASCADE,
        related_name='transactions',
    )
    date = models.DateField()
    description = models.CharField(max_length=500)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    transaction_id = models.CharField(max_length=100, blank=True, default='', db_index=True)
    matched_expense = models.ForeignKey(
        'expenses.Expense',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='matched_bank_transactions',
    )
    matched_income = models.ForeignKey(
        'income.Income',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='matched_bank_transactions',
    )
    is_reconciled = models.BooleanField(default=False)
    is_flagged = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        farm = self.bank_account.farm_profile_id if self.bank_account_id else None
        if farm and self.matched_expense_id and self.matched_expense.farm_profile_id != farm:
            raise ValidationError({'matched_expense': 'Expense belongs to another farm.'})
        if farm and self.matched_income_id and self.matched_income.farm_profile_id != farm:
            raise ValidationError({'matched_income': 'Income belongs to another farm.'})

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.date} - {self.description[:50]} - ${self.amount}'
