from django.db import models


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
    is_reconciled = models.BooleanField(default=False)
    is_flagged = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.date} - {self.description[:50]} - ${self.amount}'
