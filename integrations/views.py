import csv
import hashlib
import hmac
import json
import logging

from django.http import HttpResponse, JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from .models import BankAccount, BankTransaction

logger = logging.getLogger(__name__)

PLAID_WEBHOOK_SECRET = ''  # Set via environment variable in production


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
