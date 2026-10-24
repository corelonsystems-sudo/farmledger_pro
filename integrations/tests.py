import json
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from datetime import date

from accounts.models import Currency, FarmProfile, User
from crops.models import Invoice, InvoiceItem, InvoicePayment
from income.models import Income

from .models import MtnMoMoTransaction
from .mtn_momo import MoMoError, normalize_msisdn

MTN_SETTINGS = {
    'MTN_MOMO_SUBSCRIPTION_KEY': 'sub-key',
    'MTN_MOMO_API_USER': 'api-user',
    'MTN_MOMO_API_KEY': 'api-key',
    'MTN_MOMO_TARGET_ENV': 'sandbox',
}


class NormalizeMsisdnTests(TestCase):
    def test_variants(self):
        self.assertEqual(normalize_msisdn('0772123456'), '256772123456')
        self.assertEqual(normalize_msisdn('+256 772 123 456'), '256772123456')
        self.assertEqual(normalize_msisdn('256772123456'), '256772123456')
        self.assertEqual(normalize_msisdn('0752 999 888'), '256752999888')

    def test_invalid(self):
        for bad in ('', '123', '0800123456', '1234567890'):
            with self.assertRaises(MoMoError):
                normalize_msisdn(bad)


class MtnPayInvoiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(
            user=self.user, farm_name='Farm A',
            default_currency=Currency.objects.create(
                code='UGX', name='Ugandan Shilling', symbol='UGX'))
        self.invoice = Invoice.objects.create(
            farm_profile=self.farm, buyer='Kampala Market',
            date=date(2026, 1, 10), status=Invoice.Status.ISSUED)
        InvoiceItem.objects.create(
            invoice=self.invoice, description='Coffee', quantity=Decimal('100'),
            unit='kg', unit_price=Decimal('1000'))
        self.client.force_login(self.user)
        self.url = reverse('mtn_pay_invoice', args=[self.invoice.pk])

    def _post(self, payload=None, **kw):
        return self.client.post(
            self.url, json.dumps(payload or {'phone': '0772123456'}),
            content_type='application/json', **kw)

    @override_settings(MTN_MOMO_SUBSCRIPTION_KEY='', MTN_MOMO_API_USER='',
                       MTN_MOMO_API_KEY='')
    def test_unconfigured_returns_503(self):
        response = self._post()
        self.assertEqual(response.status_code, 503)
        self.assertIn('not configured', response.json()['error'])

    @override_settings(**MTN_SETTINGS)
    def test_invalid_phone(self):
        response = self._post({'phone': '123'})
        self.assertEqual(response.status_code, 400)

    def test_proforma_rejected(self):
        self.invoice.kind = Invoice.Kind.PROFORMA
        self.invoice.save()
        response = self._post()
        self.assertEqual(response.status_code, 400)
        self.assertIn('proforma', response.json()['error'].lower())

    def test_amount_over_balance_rejected(self):
        response = self._post({'phone': '0772123456', 'amount': '999999'})
        self.assertEqual(response.status_code, 400)

    @override_settings(**MTN_SETTINGS)
    @patch('integrations.views.MtnMoMoClient.request_to_pay')
    def test_request_created_pending(self, mock_rtp):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['ok'])
        txn = MtnMoMoTransaction.objects.get(reference_id=data['reference_id'])
        self.assertEqual(txn.status, 'PENDING')
        self.assertEqual(txn.amount, Decimal('100000'))
        self.assertEqual(txn.payer_msisdn, '256772123456')
        self.assertEqual(txn.invoice_id, self.invoice.pk)
        mock_rtp.assert_called_once()

    @override_settings(**MTN_SETTINGS)
    @patch('integrations.views.MtnMoMoClient.request_to_pay')
    def test_mtn_failure_marks_failed(self, mock_rtp):
        mock_rtp.side_effect = MoMoError('MTN rejected')
        response = self._post()
        self.assertEqual(response.status_code, 502)
        txn = MtnMoMoTransaction.objects.get()
        self.assertEqual(txn.status, 'FAILED')


@override_settings(**MTN_SETTINGS)
class MtnStatusSettlementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pw')
        self.farm = FarmProfile.objects.create(
            user=self.user, farm_name='Farm A',
            default_currency=Currency.objects.create(
                code='UGX', name='Ugandan Shilling', symbol='UGX'))
        self.invoice = Invoice.objects.create(
            farm_profile=self.farm, buyer='Buyer',
            date=date(2026, 1, 10), status=Invoice.Status.ISSUED)
        InvoiceItem.objects.create(
            invoice=self.invoice, description='Maize', quantity=Decimal('50'),
            unit='kg', unit_price=Decimal('1000'))
        self.txn = MtnMoMoTransaction.objects.create(
            farm_profile=self.farm, invoice=self.invoice,
            external_id=self.invoice.number, amount=Decimal('50000'),
            currency='UGX', payer_msisdn='256772123456')
        self.client.force_login(self.user)
        self.url = reverse('mtn_payment_status', args=[self.txn.reference_id])

    @patch('integrations.views.MtnMoMoClient.get_payment_status')
    def test_successful_books_payment_and_income(self, mock_status):
        mock_status.return_value = {
            'status': 'SUCCESSFUL',
            'financialTransactionId': 'FIN-123',
            'amount': '50000', 'currency': 'UGX',
        }
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'SUCCESSFUL')
        payment = self.txn.invoice_payment
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, Decimal('50000'))
        self.assertEqual(payment.method, InvoicePayment.Method.MOBILE)
        self.assertEqual(payment.reference, 'FIN-123')
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, Invoice.Status.PAID)
        self.assertEqual(Income.objects.filter(invoice_payment=payment).count(), 1)

    @patch('integrations.views.MtnMoMoClient.get_payment_status')
    def test_double_poll_does_not_duplicate_payment(self, mock_status):
        mock_status.return_value = {'status': 'SUCCESSFUL',
                                    'financialTransactionId': 'FIN-9'}
        self.client.get(self.url)
        self.client.get(self.url)
        self.assertEqual(InvoicePayment.objects.count(), 1)
        self.assertEqual(Income.objects.count(), 1)

    @patch('integrations.views.MtnMoMoClient.get_payment_status')
    def test_pending_keeps_pending(self, mock_status):
        mock_status.return_value = {'status': 'PENDING'}
        self.client.get(self.url)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'PENDING')
        self.assertIsNone(self.txn.invoice_payment_id)

    @patch('integrations.views.MtnMoMoClient.get_payment_status')
    def test_failed_status(self, mock_status):
        mock_status.return_value = {
            'status': 'FAILED',
            'reason': {'code': 'PAYER_NOT_FOUND', 'message': 'bad number'},
        }
        self.client.get(self.url)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'FAILED')
        self.assertEqual(InvoicePayment.objects.count(), 0)

    def test_farm_scoped(self):
        other = User.objects.create_user(username='other', password='pw')
        FarmProfile.objects.create(user=other, farm_name='Farm B')
        self.client.force_login(other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_callback_settles(self):
        response = self.client.post(
            reverse('mtn_momo_callback'),
            json.dumps({
                'referenceId': str(self.txn.reference_id),
                'status': 'SUCCESSFUL',
                'financialTransactionId': 'CB-1',
            }),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.txn.refresh_from_db()
        self.assertEqual(self.txn.status, 'SUCCESSFUL')
        self.assertIsNotNone(self.txn.invoice_payment_id)
