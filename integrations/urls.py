from django.urls import path

from .views import (
    BankCSVImportView,
    MtnMoMoCallbackView,
    PlaidWebhookView,
    mtn_pay_invoice_view,
    mtn_payment_status_view,
)

urlpatterns = [
    path('bank/import-csv/', BankCSVImportView.as_view(), name='bank_csv_import'),
    path('plaid/webhook/', PlaidWebhookView.as_view(), name='plaid_webhook'),
    path('mtn/invoices/<int:pk>/pay/', mtn_pay_invoice_view, name='mtn_pay_invoice'),
    path('mtn/status/<uuid:reference_id>/', mtn_payment_status_view, name='mtn_payment_status'),
    path('mtn/callback/', MtnMoMoCallbackView.as_view(), name='mtn_momo_callback'),
]
