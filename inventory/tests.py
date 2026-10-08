from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from accounts.models import FarmProfile, User
from budgets.models import Budget, BudgetItem
from expenses.models import Category, Expense
from .models import InventoryItem, InventoryTransaction


class InventoryPostingTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username='keeper', password='pw')
        self.farm = FarmProfile.objects.create(user=user, farm_name='Farm A')
        other_farm = FarmProfile.objects.create(
            user=User.objects.create_user(username='other', password='pw'),
            farm_name='Farm B')
        self.item = InventoryItem.objects.create(
            farm_profile=self.farm, name='Fertilizer', unit='kg',
            quantity_on_hand=Decimal('10'), reorder_threshold=Decimal('5'))
        self.category = Category.objects.create(name='Fertilizer')
        budget = Budget.objects.create(
            farm_profile=self.farm, name='FY26', status='ACTIVE')
        self.budget_item = BudgetItem.objects.create(
            budget=budget, category=self.category,
            planned_amount=Decimal('1000'))
        other_budget = Budget.objects.create(
            farm_profile=other_farm, name='Other', status='ACTIVE')
        self.other_budget_item = BudgetItem.objects.create(
            budget=other_budget, category=self.category,
            planned_amount=Decimal('1000'))

    def test_purchase_without_budget_item_is_rejected_and_stock_unchanged(self):
        with self.assertRaises(ValidationError):
            InventoryTransaction.objects.create(
                item=self.item, transaction_type='PURCHASE',
                quantity=Decimal('5'), unit_cost=Decimal('2'),
                date=date(2025, 1, 10))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, Decimal('10'))
        self.assertEqual(Expense.objects.count(), 0)
        self.assertEqual(InventoryTransaction.objects.count(), 0)

    def test_purchase_posts_expense_and_stock_atomically(self):
        txn = InventoryTransaction.objects.create(
            item=self.item, transaction_type='PURCHASE',
            quantity=Decimal('5'), unit_cost=Decimal('2'),
            budget_item=self.budget_item, date=date(2025, 1, 10))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, Decimal('15'))
        expense = Expense.objects.get()
        self.assertEqual(expense.budget_item, self.budget_item)
        self.assertEqual(expense.category, self.category)
        self.assertEqual(expense.amount, Decimal('10'))
        self.assertEqual(expense.farm_profile, self.farm)
        txn.refresh_from_db()
        self.assertEqual(txn.expense, expense)

    def test_purchase_rejects_budget_item_from_other_farm(self):
        with self.assertRaises(ValidationError):
            InventoryTransaction.objects.create(
                item=self.item, transaction_type='PURCHASE',
                quantity=Decimal('5'), unit_cost=Decimal('2'),
                budget_item=self.other_budget_item, date=date(2025, 1, 10))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, Decimal('10'))
        self.assertEqual(Expense.objects.count(), 0)

    def test_usage_decrements_stock_and_rejects_overshoot(self):
        InventoryTransaction.objects.create(
            item=self.item, transaction_type='USAGE',
            quantity=Decimal('4'), date=date(2025, 1, 10))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, Decimal('6'))
        with self.assertRaises(ValidationError):
            InventoryTransaction.objects.create(
                item=self.item, transaction_type='USAGE',
                quantity=Decimal('50'), date=date(2025, 1, 11))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity_on_hand, Decimal('6'))

    def test_transaction_update_does_not_repost(self):
        txn = InventoryTransaction.objects.create(
            item=self.item, transaction_type='PURCHASE',
            quantity=Decimal('5'), unit_cost=Decimal('2'),
            budget_item=self.budget_item, date=date(2025, 1, 10))
        txn.notes = 'corrected'
        txn.save()
        self.item.refresh_from_db()
        # Still +5, not +10
        self.assertEqual(self.item.quantity_on_hand, Decimal('15'))
        self.assertEqual(Expense.objects.count(), 1)
