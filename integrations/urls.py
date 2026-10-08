from django.urls import path

from .views import BankCSVImportView, PlaidWebhookView

urlpatterns = [
    path('bank/import-csv/', BankCSVImportView.as_view(), name='bank_csv_import'),
    path('plaid/webhook/', PlaidWebhookView.as_view(), name='plaid_webhook'),
]
