import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import F, Sum
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views import View

from budgets.models import Budget, BudgetItem
from crops.models import Crop, CropSeason, HarvestRecord, Sale
from expenses.models import Category, Expense
from equipment.models import Equipment
from fields.models import Field
from inventory.models import InventoryItem
from labor.models import Worker
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from .utils import (
    build_cash_flow_data,
    build_pl_data,
    build_schedule_f_data,
    build_crop_balance_sheet,
    build_harvest_report,
    generate_schedule_f_pdf,
    generate_pl_pdf,
    generate_balance_sheet_pdf,
    generate_cash_flow_pdf,
    generate_harvest_report_pdf,
    generate_quickbooks_iif,
)


class ReportBaseView(LoginRequiredMixin, View):
    template_name = 'reports/report_base.html'
    report_title = 'Report'

    def get_context_data(self, **kwargs):
        context = {'report_title': self.report_title}
        return context


class ProfitLossView(ReportBaseView):
    report_title = 'Profit & Loss Statement'

    def get(self, request, *args, **kwargs):
        export = request.GET.get('export')
        crop_season_id = request.GET.get('crop_season')
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')

        data = build_pl_data(crop_season_id, start_date, end_date)

        if export == 'pdf':
            return generate_pl_pdf(data)
        elif export == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="profit_loss.csv"'
            writer = csv.writer(response)
            writer.writerow(['Profit & Loss Statement'])
            writer.writerow(['Category', 'Amount'])
            for item in data['income_items']:
                writer.writerow([f"Income: {item['label']}", item['amount']])
            writer.writerow(['Total Income', data['total_income']])
            for item in data['expense_items']:
                writer.writerow([f"Expense: {item['label']}", item['amount']])
            writer.writerow(['Total Expenses', data['total_expenses']])
            writer.writerow(['Net Profit', data['net_profit']])
            return response

        context = self.get_context_data()
        context.update(data)
        return render(request, 'reports/profit_loss.html', context)


class CropBalanceSheetView(ReportBaseView):
    report_title = 'Per Crop Balance Sheet'

    def get(self, request, *args, **kwargs):
        crop_season_id = kwargs.get('crop_season_id') or request.GET.get('crop_season')
        if not crop_season_id:
            crop_seasons = CropSeason.objects.all()
            context = self.get_context_data()
            context['crop_seasons'] = crop_seasons
            return render(request, 'reports/select_crop.html', context)

        crop_season = get_object_or_404(CropSeason, id=crop_season_id)
        export = request.GET.get('export')
        data = build_crop_balance_sheet(crop_season)

        if export == 'pdf':
            return generate_balance_sheet_pdf(data, crop_season)
        elif export == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = f'attachment; filename="crop_balance_{crop_season.id}.csv"'
            writer = csv.writer(response)
            writer.writerow([f'Per Crop Balance Sheet: {crop_season}'])
            writer.writerow(['Item', 'Amount'])
            for item in data['income_items']:
                writer.writerow([f"Income: {item['label']}", item['amount']])
            writer.writerow(['Total Income', data['total_income']])
            for item in data['expense_items']:
                writer.writerow([f"Expense: {item['label']}", item['amount']])
            writer.writerow(['Total Expenses', data['total_expenses']])
            writer.writerow(['Net Profit', data['net_profit']])
            writer.writerow(['Total Quantity Produced', data['total_quantity']])
            writer.writerow(['Cost per kg', data['cost_per_kg']])
            return response

        context = self.get_context_data()
        context.update(data)
        return render(request, 'reports/crop_balance_sheet.html', context)


class CashFlowView(ReportBaseView):
    report_title = 'Cash Flow Statement'

    def get(self, request, *args, **kwargs):
        export = request.GET.get('export')
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')

        data = build_cash_flow_data(start_date, end_date)

        if export == 'pdf':
            return generate_cash_flow_pdf(data)
        elif export == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="cash_flow.csv"'
            writer = csv.writer(response)
            writer.writerow(['Cash Flow Statement'])
            writer.writerow(['Date', 'Description', 'Type', 'Amount', 'Running Balance'])
            for item in data['transactions']:
                writer.writerow([item['date'], item['description'], item['type'], item['amount'], item['running_balance']])
            return response

        context = self.get_context_data()
        context.update(data)
        return render(request, 'reports/cash_flow.html', context)


class ScheduleFView(ReportBaseView):
    report_title = 'Schedule F Report'

    def get(self, request, *args, **kwargs):
        export = request.GET.get('export')
        year = request.GET.get('year')

        data = build_schedule_f_data(year)

        if export == 'pdf':
            return generate_schedule_f_pdf(data)
        elif export == 'csv':
            response = HttpResponse(content_type='text/csv')
            response['Content-Disposition'] = 'attachment; filename="schedule_f.csv"'
            writer = csv.writer(response)
            writer.writerow(['Schedule F Report'])
            writer.writerow(['Schedule F Line', 'Category', 'Amount'])
            for item in data['line_items']:
                writer.writerow([item['schedule_f_line'], item['category'], item['amount']])
            writer.writerow(['Total', data['total']])
            return response

        context = self.get_context_data()
        context.update(data)
        return render(request, 'reports/schedule_f.html', context)


class HarvestReportView(LoginRequiredMixin, View):
    """Download a PDF report for a single harvest."""

    def get(self, request, harvest_id, *args, **kwargs):
        harvest = get_object_or_404(HarvestRecord, pk=harvest_id)
        data = build_harvest_report(harvest)
        return generate_harvest_report_pdf(data)


class QuickBooksExportView(LoginRequiredMixin, View):
    """Returns an IIF formatted file mapping Django expense categories to QuickBooks accounts."""

    def get(self, request, *args, **kwargs):
        iif_content = generate_quickbooks_iif()
        response = HttpResponse(iif_content, content_type='text/plain')
        response['Content-Disposition'] = 'attachment; filename="farmledger_export.iif"'
        return response


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


class ReportDashboardView(LoginRequiredMixin, View):
    """Unified report page with tabbed sections and chart data."""

    def get(self, request, *args, **kwargs):
        tab = request.GET.get('tab', 'overview')
        crop_season_id = request.GET.get('crop_season')
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        year = request.GET.get('year')

        # --- Overview data ---
        total_expenses = Expense.objects.aggregate(t=Sum('amount'))['t'] or Decimal('0')
        total_revenue = Sale.objects.aggregate(
            t=Sum(F('quantity') * F('unit_price'))
        )['t'] or Decimal('0')
        net_profit = total_revenue - total_expenses

        # Expense by category (for chart)
        expense_by_cat = []
        for cat in Category.objects.all():
            total = Expense.objects.filter(category=cat).aggregate(t=Sum('amount'))['t']
            if total:
                expense_by_cat.append({'label': cat.name, 'amount': float(total)})

        # Revenue by crop (for chart)
        revenue_by_crop = []
        for crop in Crop.objects.all():
            total = Sale.objects.filter(
                harvest__crop_season__crop=crop
            ).aggregate(
                t=Sum(F('quantity') * F('unit_price'))
            )['t']
            if total:
                revenue_by_crop.append({'label': crop.name, 'amount': float(total)})

        # Monthly expense trend (for chart)
        monthly_expenses = []
        for month in range(1, 13):
            total = Expense.objects.filter(date__month=month).aggregate(t=Sum('amount'))['t']
            monthly_expenses.append(float(total or 0))

        monthly_revenue = []
        for month in range(1, 13):
            total = Sale.objects.filter(sale_date__month=month).aggregate(
                t=Sum(F('quantity') * F('unit_price'))
            )['t']
            monthly_revenue.append(float(total or 0))

        # Budget vs Actual (for chart + table)
        budget_vs_actual = []
        for item in BudgetItem.objects.select_related('budget', 'category'):
            spent = item.spent_amount
            budget_vs_actual.append({
                'label': f'{item.budget.name} - {item.category.name}',
                'planned': float(item.planned_amount),
                'spent': float(spent or 0),
            })

        # Crop season profitability (for chart)
        crop_profit = []
        for season in CropSeason.objects.select_related('crop', 'field'):
            revenue = season.total_revenue
            expenses = season.total_expenses
            crop_profit.append({
                'label': f'{season.crop.name} ({season.planting_date.year})',
                'revenue': float(revenue),
                'expenses': float(expenses),
                'profit': float(revenue - expenses),
            })

        # Equipment costs (for chart)
        equipment_costs = []
        for eq in Equipment.objects.all():
            maint = eq.total_maintenance_cost
            equipment_costs.append({
                'label': eq.name,
                'purchase': float(eq.purchase_cost),
                'maintenance': float(maint),
            })

        # Inventory value (for chart)
        inventory_value = []
        for item in InventoryItem.objects.all():
            val = float(item.quantity_on_hand * item.cost_per_unit)
            if val > 0:
                inventory_value.append({'label': item.name, 'value': val})

        # Worker costs
        worker_costs = []
        for w in Worker.objects.filter(is_active=True):
            worker_costs.append({
                'label': w.name,
                'rate': float(w.hourly_rate),
            })

        # --- P&L data ---
        pl_data = build_pl_data(crop_season_id, start_date, end_date)

        # --- Cash flow data ---
        cash_flow_data = build_cash_flow_data(start_date, end_date)

        # --- Schedule F data ---
        schedule_f_data = build_schedule_f_data(year)

        # --- Crop balance sheet (pick first season if none selected) ---
        crop_seasons = CropSeason.objects.select_related('crop', 'field').all()
        balance_sheet = None
        selected_season = None
        if crop_season_id:
            selected_season = get_object_or_404(CropSeason, id=crop_season_id)
            balance_sheet = build_crop_balance_sheet(selected_season)
        elif crop_seasons.exists():
            selected_season = crop_seasons.first()
            balance_sheet = build_crop_balance_sheet(selected_season)

        context = {
            'report_title': 'Reports Dashboard',
            'tab': tab,
            'crop_seasons': crop_seasons,
            'selected_season_id': int(crop_season_id) if crop_season_id else (selected_season.id if selected_season else None),
            'start_date': start_date or '',
            'end_date': end_date or '',
            'year': year or '',

            # Overview
            'total_expenses': total_expenses,
            'total_revenue': total_revenue,
            'net_profit': net_profit,
            'expense_count': Expense.objects.count(),
            'harvest_count': HarvestRecord.objects.count(),
            'budget_count': Budget.objects.count(),
            'crop_count': Crop.objects.count(),
            'field_count': Field.objects.count(),
            'worker_count': Worker.objects.filter(is_active=True).count(),

            # Chart data (JSON)
            'expense_by_cat_json': json.dumps(expense_by_cat),
            'revenue_by_crop_json': json.dumps(revenue_by_crop),
            'monthly_expenses_json': json.dumps(monthly_expenses),
            'monthly_revenue_json': json.dumps(monthly_revenue),
            'budget_vs_actual_json': json.dumps(budget_vs_actual),
            'crop_profit_json': json.dumps(crop_profit),
            'equipment_costs_json': json.dumps(equipment_costs),
            'inventory_value_json': json.dumps(inventory_value),
            'worker_costs_json': json.dumps(worker_costs),

            # P&L
            'pl_data': pl_data,

            # Cash flow
            'cash_flow_data': cash_flow_data,

            # Schedule F
            'schedule_f_data': schedule_f_data,

            # Crop balance sheet
            'balance_sheet': balance_sheet,
        }
        return render(request, 'reports/report_dashboard.html', context)
