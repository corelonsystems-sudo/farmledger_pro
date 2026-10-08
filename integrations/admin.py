from datetime import timedelta

from django.contrib import admin
from django.utils.html import format_html

from .models import BankAccount, BankTransaction


class BankTransactionInline(admin.TabularInline):
    model = BankTransaction
    extra = 1


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'bank_name', 'account_number_last4', 'is_active')
    list_filter = ('is_active', 'bank_name')
    search_fields = ('name', 'bank_name', 'account_number_last4')
    inlines = [BankTransactionInline]


@admin.register(BankTransaction)
class BankTransactionAdmin(admin.ModelAdmin):
    list_display = ('date', 'description', 'amount', 'bank_account', 'is_reconciled', 'is_flagged', 'matched_expense')
    list_filter = ('is_reconciled', 'is_flagged', 'bank_account', 'date')
    search_fields = ('description', 'transaction_id')
    date_hierarchy = 'date'
    actions = ['fuzzy_match_expenses']

    def fuzzy_match_expenses(self, request, queryset):
        """Bulk action that fuzzy matches bank transactions to expense records
        by amount and date within a 3-day window and flags matches for user approval.
        """
        from expenses.models import Expense
        from decimal import Decimal

        matched_count = 0
        for bt in queryset.filter(is_reconciled=False):
            date_min = bt.date - timedelta(days=3)
            date_max = bt.date + timedelta(days=3)
            potential_matches = Expense.objects.filter(
                amount=bt.amount,
                date__gte=date_min,
                date__lte=date_max,
            )
            if potential_matches.exists():
                best_match = potential_matches.first()
                bt.matched_expense = best_match
                bt.is_flagged = True
                bt.save()
                matched_count += 1

        self.message_user(
            request,
            f'{matched_count} bank transaction(s) matched to expenses and flagged for approval.',
        )

    fuzzy_match_expenses.short_description = 'Fuzzy match to expenses (3-day window)'
