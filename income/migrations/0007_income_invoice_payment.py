"""Book invoice payments into the income ledger.

Adds Income.invoice_payment (CASCADE) so each invoice payment keeps one
auto-generated income row, and backfills income for existing payments on
non-sale invoices (sale-linked invoices are already covered by Income.sale).
"""
from django.db import migrations, models
import django.db.models.deletion


METHOD_MAP = {
    'CASH': 'CASH',
    'BANK': 'BANK_TRANSFER',
    'MOBILE': 'CARD',
    'CHEQUE': 'CHECK',
    'OTHER': 'OTHER',
}


def backfill_payment_incomes(apps, schema_editor):
    InvoicePayment = apps.get_model('crops', 'InvoicePayment')
    Income = apps.get_model('income', 'Income')
    IncomeSource = apps.get_model('income', 'IncomeSource')
    Customer = apps.get_model('crops', 'Customer')

    source, _ = IncomeSource.objects.get_or_create(
        name='Invoice Payments',
        defaults={
            'code': 'INV',
            'schedule_f_line': 'part1_sales',
            'description': 'Payments received on invoices',
        },
    )
    payments = (InvoicePayment.objects
                .filter(invoice__sale__isnull=True)
                .select_related('invoice'))
    for payment in payments:
        invoice = payment.invoice
        customer_name = invoice.buyer
        if not customer_name and invoice.customer_id:
            customer = Customer.objects.filter(pk=invoice.customer_id).first()
            customer_name = customer.name if customer else ''
        Income.objects.create(
            invoice_payment_id=payment.pk,
            farm_profile_id=invoice.farm_profile_id,
            source=source,
            amount=payment.amount,
            date=payment.date,
            customer=customer_name,
            customer_ref_id=invoice.customer_id,
            payment_method=METHOD_MAP.get(payment.method, 'OTHER'),
            notes=f'Auto-generated from invoice {invoice.number} payment.',
        )


class Migration(migrations.Migration):

    dependencies = [
        ('crops', '0022_customer_tin'),
        ('income', '0006_incomesource_auto_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='income',
            name='invoice_payment',
            field=models.ForeignKey(
                blank=True,
                help_text='Auto-generated income linked to this invoice payment; removed when the payment is deleted.',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='income_records',
                to='crops.invoicepayment',
            ),
        ),
        migrations.RunPython(backfill_payment_incomes, migrations.RunPython.noop),
    ]
