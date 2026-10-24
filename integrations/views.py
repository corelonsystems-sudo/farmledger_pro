import csv
import hashlib
import hmac
import json
import logging

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .models import BankAccount, BankTransaction, MtnMoMoTransaction
from .mtn_momo import MoMoError, MtnMoMoClient, normalize_msisdn

logger = logging.getLogger(__name__)

PLAID_WEBHOOK_SECRET = ''  # Set via environment variable in production


# ---------------------------------------------------------------------------
# MTN Mobile Money — invoice collections (request-to-pay)
# ---------------------------------------------------------------------------

def _invoice_for_user(user, pk):
    from accounts.access import scoped_queryset
    from crops.models import Invoice
    return get_object_or_404(
        scoped_queryset(Invoice, user, write=True).select_related('farm_profile'), pk=pk)


def _settle_mtn_transaction(txn, body):
    """Update a MoMo transaction from an MTN status body; book the invoice
    payment exactly once when the collection succeeds."""
    from crops.models import InvoicePayment
    txn.raw_response = body or {}
    status = (body or {}).get('status', '')
    reason = body.get('reason')
    txn.status_reason = json.dumps(reason) if isinstance(reason, dict) else (reason or '')
    txn.financial_transaction_id = body.get('financialTransactionId', '') or ''
    if status == MtnMoMoTransaction.Status.SUCCESSFUL and not txn.invoice_payment_id:
        txn.status = MtnMoMoTransaction.Status.SUCCESSFUL
        if txn.invoice_id and not txn.invoice.is_proforma and txn.invoice.balance >= txn.amount:
            payment = InvoicePayment.objects.create(
                invoice=txn.invoice,
                amount=txn.amount,
                date=timezone.localdate(),
                method=InvoicePayment.Method.MOBILE,
                reference=txn.financial_transaction_id or str(txn.reference_id),
                notes=f'MTN MoMo collection from {txn.payer_msisdn}',
            )
            txn.invoice_payment = payment
            txn.invoice.refresh_status()
    elif status == MtnMoMoTransaction.Status.FAILED:
        txn.status = MtnMoMoTransaction.Status.FAILED
    txn.save()
    return txn


@login_required
def mtn_pay_invoice_view(request, pk):
    """POST {phone, amount?} -> send an MTN request-to-pay for an invoice."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    invoice = _invoice_for_user(request.user, pk)
    if invoice.is_proforma:
        return JsonResponse(
            {'error': 'A proforma cannot take payments - convert it to an invoice first.'},
            status=400)
    if invoice.balance <= 0:
        return JsonResponse({'error': 'This invoice is already fully paid.'}, status=400)
    try:
        payload = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        payload = request.POST
    client = MtnMoMoClient()
    try:
        msisdn = normalize_msisdn(
            payload.get('phone', ''), sandbox=(client.target_env == 'sandbox'))
    except MoMoError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    try:
        amount = Decimal(str(payload.get('amount') or invoice.balance))
    except Exception:
        return JsonResponse({'error': 'Invalid amount.'}, status=400)
    if amount <= 0 or amount > invoice.balance:
        return JsonResponse(
            {'error': f'Amount must be between 0 and the balance ({invoice.balance}).'},
            status=400)
    if not client.is_configured:
        return JsonResponse(
            {'error': 'MTN MoMo is not configured. Set MTN_MOMO_* credentials in the environment.'},
            status=503)
    # The MoMo sandbox only accepts EUR; production (mtnuganda) uses the farm's
    # currency code (UGX).
    currency = 'EUR' if client.target_env == 'sandbox' else (
        getattr(getattr(invoice.farm_profile, 'default_currency', None), 'code', None)
        or 'UGX')
    txn = MtnMoMoTransaction.objects.create(
        farm_profile=invoice.farm_profile,
        invoice=invoice,
        external_id=invoice.number,
        amount=amount,
        currency=currency,
        payer_msisdn=msisdn,
        payer_message=f'Payment for invoice {invoice.number}',
        payee_note=f'Invoice {invoice.number}',
    )
    try:
        client.request_to_pay(
            str(txn.reference_id),
            amount=txn.amount,
            currency=txn.currency,
            msisdn=msisdn,
            payer_message=txn.payer_message,
            payee_note=txn.payee_note,
        )
    except MoMoError as exc:
        txn.status = MtnMoMoTransaction.Status.FAILED
        txn.status_reason = str(exc)
        txn.save()
        return JsonResponse({'error': str(exc), 'reference_id': str(txn.reference_id)}, status=502)
    return JsonResponse({
        'ok': True,
        'reference_id': str(txn.reference_id),
        'status': txn.status,
        'message': 'Payment request sent. The customer should approve it on their phone.',
    })


@login_required
def mtn_payment_status_view(request, reference_id):
    """GET -> poll MTN for a transaction's latest status (and settle it)."""
    from accounts.access import scoped_queryset
    txn = get_object_or_404(
        scoped_queryset(MtnMoMoTransaction, request.user).select_related(
            'invoice', 'invoice__farm_profile'),
        reference_id=reference_id)
    client = MtnMoMoClient()
    if not client.is_configured:
        return JsonResponse({'status': txn.status, 'configured': False})
    if txn.status == MtnMoMoTransaction.Status.PENDING:
        try:
            body = client.get_payment_status(str(txn.reference_id))
        except MoMoError as exc:
            return JsonResponse({'status': txn.status, 'error': str(exc)}, status=502)
        txn = _settle_mtn_transaction(txn, body)
    return JsonResponse({
        'status': txn.status,
        'financial_transaction_id': txn.financial_transaction_id,
        'reason': txn.status_reason,
        'payment_recorded': bool(txn.invoice_payment_id),
    })


@method_decorator(csrf_exempt, name='dispatch')
class MtnMoMoCallbackView(View):
    """MTN server-to-server callback for request-to-pay status updates."""

    def post(self, request, *args, **kwargs):
        try:
            body = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        reference_id = body.get('referenceId') or body.get('externalId')
        if not reference_id:
            return JsonResponse({'error': 'referenceId required'}, status=400)
        txn = MtnMoMoTransaction.objects.filter(reference_id=reference_id).first()
        if not txn:
            return JsonResponse({'error': 'Unknown reference'}, status=404)
        _settle_mtn_transaction(txn, body)
        return JsonResponse({'status': 'accepted'})





@method_decorator(csrf_exempt, name='dispatch')
class BankCSVImportView(View):
    """Import endpoint that accepts a standard bank CSV export, parses it
    with the Python csv module, and creates BankTransaction records.
    """

    def post(self, request, *args, **kwargs):
        bank_account_id = request.POST.get('bank_account_id')
        if not bank_account_id:
            return JsonResponse({'error': 'bank_account_id is required'}, status=400)

        try:
            bank_account = BankAccount.objects.get(id=bank_account_id)
        except BankAccount.DoesNotExist:
            return JsonResponse({'error': 'Bank account not found'}, status=404)

        csv_file = request.FILES.get('csv_file')
        if not csv_file:
            return JsonResponse({'error': 'csv_file is required'}, status=400)

        decoded_file = csv_file.read().decode('utf-8').splitlines()
        reader = csv.DictReader(decoded_file)

        created_count = 0
        skipped_count = 0

        for row in reader:
            date_str = row.get('Date', row.get('date', ''))
            description = row.get('Description', row.get('description', row.get('Name', '')))
            amount_str = row.get('Amount', row.get('amount', '0'))
            transaction_id = row.get('Transaction ID', row.get('transaction_id', ''))

            try:
                from datetime import datetime
                parsed_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                amount = abs(float(amount_str))
            except (ValueError, TypeError):
                skipped_count += 1
                continue

            tx_id = transaction_id or f'{parsed_date}-{description}-{amount}'

            _, created = BankTransaction.objects.get_or_create(
                bank_account=bank_account,
                transaction_id=tx_id,
                defaults={
                    'date': parsed_date,
                    'description': description,
                    'amount': amount,
                },
            )
            if created:
                created_count += 1
            else:
                skipped_count += 1

        return JsonResponse({
            'created': created_count,
            'skipped': skipped_count,
        })


@method_decorator(csrf_exempt, name='dispatch')
class PlaidWebhookView(View):
    """Stub Plaid webhook view that validates the webhook signature and
    queues a Celery task to fetch and import new transactions.
    """

    def post(self, request, *args, **kwargs):
        import os
        secret = os.environ.get('PLAID_WEBHOOK_SECRET', PLAID_WEBHOOK_SECRET)

        body = request.body
        signature = request.headers.get('Plaid-Verification', '')

        if secret:
            computed = hmac.new(
                secret.encode(),
                body,
                hashlib.sha256,
            ).hexdigest()
            if not hmac.compare_digest(computed, signature):
                return JsonResponse({'error': 'Invalid webhook signature'}, status=403)

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        webhook_type = payload.get('webhook_type', '')
        item_id = payload.get('item_id', '')

        if webhook_type == 'TRANSACTIONS_DEFAULT_UPDATE':
            from .tasks import fetch_and_import_transactions
            fetch_and_import_transactions.delay(item_id)

        return JsonResponse({'status': 'accepted'})
