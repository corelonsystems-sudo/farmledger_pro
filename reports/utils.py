import io
import os
from datetime import date, datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Sum

from accounts.models import FarmProfile
from crops.models import CropSeason, HarvestRecord, Sale
from expenses.models import Category, Expense

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from django.http import HttpResponse


def get_farm_profile():
    """Return the first FarmProfile, or None if none exists."""
    return FarmProfile.objects.first()


def build_farm_header(doc_width=None):
    """Return a list of reportlab flowables forming a reusable farm header band.

    Includes farm name, location, phone, email, and optional logo.
    """
    styles = getSampleStyleSheet()
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_RIGHT

    fp = get_farm_profile()
    elements = []

    HEADER_BG = colors.HexColor('#2c3e50')
    HEADER_FG = colors.white
    SUB_FG = colors.HexColor('#b0c4de')

    style_farm_name = ParagraphStyle('FarmName', fontName='Helvetica-Bold', fontSize=16,
                                     textColor=HEADER_FG, leading=18)
    style_farm_info = ParagraphStyle('FarmInfo', fontName='Helvetica', fontSize=8,
                                     textColor=SUB_FG, leading=11)
    style_doc_label = ParagraphStyle('DocLabel', fontName='Helvetica-Oblique', fontSize=8,
                                     textColor=SUB_FG, leading=10, alignment=TA_RIGHT)

    if fp:
        info_lines = []
        if fp.location:
            info_lines.append(fp.location)
        if fp.phone:
            info_lines.append(f'Tel: {fp.phone}')
        if fp.email:
            info_lines.append(fp.email)
        if fp.tax_id:
            info_lines.append(f'Tax ID: {fp.tax_id}')
        info_text = '<br/>'.join(info_lines) if info_lines else ''
    else:
        info_text = 'Configure your farm profile in admin to see header details.'

    farm_name = fp.farm_name if fp else 'FarmLedger Pro'
    today_str = date.today().isoformat()

    # Build left cell (farm name + info) and right cell (logo or date)
    left_cell = []
    left_cell.append(Paragraph(farm_name, style_farm_name))
    if info_text:
        left_cell.append(Paragraph(info_text, style_farm_info))

    right_cell = []
    logo_flowable = None
    if fp and fp.logo:
        logo_path = os.path.join(settings.MEDIA_ROOT, fp.logo.name)
        if os.path.isfile(logo_path):
            try:
                logo_flowable = Image(logo_path, width=0.8*inch, height=0.8*inch)
            except Exception:
                logo_flowable = None
    if logo_flowable:
        right_cell.append(logo_flowable)
    else:
        right_cell.append(Paragraph(f'Date: {today_str}', style_doc_label))

    header_table = Table(
        [[left_cell, right_cell]],
        colWidths=[doc_width - 1.0*inch if doc_width else 5.5*inch, 1.0*inch],
    )
    header_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), HEADER_BG),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 12),
        ('RIGHTPADDING', (0, 0), (-1, -1), 12),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 0.15 * inch))
    return elements


# --- QuickBooks account mapping ---
QUICKBOOKS_ACCOUNT_MAP = {
    'part1_cartruck': 'Auto:Car and Truck',
    'part1_chemicals': 'Chemicals',
    'part1_conservation': 'Conservation Expenses',
    'part1_custom_hire': 'Custom Hire',
    'part1_depreciation': 'Depreciation',
    'part1_employee_benefits': 'Employee Benefits',
    'part1_feed': 'Feed',
    'part1_fertilizer': 'Fertilizer and Lime',
    'part1_freight_trucking': 'Freight and Trucking',
    'part1_gasoline_fuel': 'Gasoline, Fuel, and Oil',
    'part1_insurance': 'Insurance',
    'part1_interest': 'Interest Expense',
    'part1_labor_hired': 'Payroll Expenses',
    'part1_pension': 'Pension and Profit-Sharing',
    'part1_rent_lease_equipment': 'Rent/Lease:Equipment',
    'part1_rent_lease_land': 'Rent/Lease:Land',
    'part1_repairs_maintenance': 'Repairs and Maintenance',
    'part1_seeds_plants': 'Seeds and Plants',
    'part1_storage_warehousing': 'Storage and Warehousing',
    'part1_supplies': 'Supplies',
    'part1_taxes': 'Taxes',
    'part1_utilities': 'Utilities',
    'part1_veterinary': 'Veterinary and Medicine',
    'part1_other': 'Other Miscellaneous',
}


def build_pl_data(crop_season_id=None, start_date=None, end_date=None):
    """Build P&L data grouped by crop season or date range."""
    expenses_qs = Expense.objects.all()
    sales_qs = Sale.objects.all()

    if crop_season_id:
        expenses_qs = expenses_qs.filter(crop_season_id=crop_season_id)
        sales_qs = sales_qs.filter(harvest__crop_season_id=crop_season_id)

    if start_date:
        expenses_qs = expenses_qs.filter(date__gte=start_date)
        sales_qs = sales_qs.filter(sale_date__gte=start_date)
    if end_date:
        expenses_qs = expenses_qs.filter(date__lte=end_date)
        sales_qs = sales_qs.filter(sale_date__lte=end_date)

    income_items = []
    total_income = Decimal('0')

    for s in sales_qs.select_related('harvest__crop_season__crop'):
        revenue = s.total_amount
        income_items.append({
            'label': f'{s.harvest.crop_season.crop.name} sale ({s.sale_date})',
            'amount': revenue,
        })
        total_income += revenue

    expense_items = []
    total_expenses = Decimal('0')

    categories = Category.objects.all()
    for cat in categories:
        total = expenses_qs.filter(category=cat).aggregate(total=Sum('amount'))['total']
        if total:
            expense_items.append({
                'label': cat.name,
                'amount': total,
            })
            total_expenses += total

    net_profit = total_income - total_expenses

    return {
        'income_items': income_items,
        'total_income': total_income,
        'expense_items': expense_items,
        'total_expenses': total_expenses,
        'net_profit': net_profit,
        'crop_season_id': crop_season_id,
        'start_date': start_date,
        'end_date': end_date,
    }


def build_crop_balance_sheet(crop_season):
    """Build a per-crop balance sheet showing all income and expenses for a single crop season."""
    expenses_qs = Expense.objects.filter(crop_season=crop_season)
    harvests_qs = HarvestRecord.objects.filter(crop_season=crop_season)
    sales_qs = Sale.objects.filter(harvest__crop_season=crop_season)

    income_items = []
    total_income = Decimal('0')
    total_quantity = Decimal('0')

    for s in sales_qs:
        revenue = s.total_amount
        income_items.append({
            'label': f'Sale ({s.sale_date}) - {s.buyer or "N/A"}',
            'amount': revenue,
            'quantity': s.quantity,
        })
        total_income += revenue

    for h in harvests_qs:
        total_quantity += h.quantity

    expense_items = []
    total_expenses = Decimal('0')

    categories = Category.objects.all()
    for cat in categories:
        total = expenses_qs.filter(category=cat).aggregate(total=Sum('amount'))['total']
        if total:
            expense_items.append({
                'label': cat.name,
                'amount': total,
            })
            total_expenses += total

    net_profit = total_income - total_expenses
    cost_per_kg = (total_expenses / total_quantity) if total_quantity > 0 else Decimal('0')

    return {
        'crop_season': crop_season,
        'income_items': income_items,
        'total_income': total_income,
        'expense_items': expense_items,
        'total_expenses': total_expenses,
        'net_profit': net_profit,
        'total_quantity': total_quantity,
        'cost_per_kg': cost_per_kg,
    }


def build_cash_flow_data(start_date=None, end_date=None):
    """Build a cash flow statement with a running balance column."""
    expenses_qs = Expense.objects.all()
    sales_qs = Sale.objects.all()

    if start_date:
        expenses_qs = expenses_qs.filter(date__gte=start_date)
        sales_qs = sales_qs.filter(sale_date__gte=start_date)
    if end_date:
        expenses_qs = expenses_qs.filter(date__lte=end_date)
        sales_qs = sales_qs.filter(sale_date__lte=end_date)

    transactions = []

    for s in sales_qs.select_related('harvest__crop_season__crop'):
        transactions.append({
            'date': s.sale_date,
            'description': f'Income: {s.harvest.crop_season.crop.name} sale',
            'type': 'Income',
            'amount': s.total_amount,
        })

    for e in expenses_qs.select_related('category'):
        transactions.append({
            'date': e.date,
            'description': f'Expense: {e.category.name} - {e.vendor}',
            'type': 'Expense',
            'amount': -e.amount,
        })

    transactions.sort(key=lambda t: t['date'])

    running_balance = Decimal('0')
    for t in transactions:
        running_balance += t['amount']
        t['running_balance'] = running_balance

    return {
        'transactions': transactions,
        'start_date': start_date,
        'end_date': end_date,
        'final_balance': running_balance,
    }


def build_schedule_f_data(year=None):
    """Build a Schedule F report grouping expenses by their schedule F line."""
    expenses_qs = Expense.objects.all()

    if year:
        expenses_qs = expenses_qs.filter(date__year=year)

    line_items = []
    total = Decimal('0')

    schedule_f_choices = dict(Category.SCHEDULE_F_LINES)

    categories = Category.objects.all()
    for cat in categories:
        line_total = expenses_qs.filter(category=cat).aggregate(total=Sum('amount'))['total']
        if line_total:
            line_label = schedule_f_choices.get(cat.schedule_f_line, cat.schedule_f_line)
            line_items.append({
                'schedule_f_line': line_label,
                'schedule_f_line_code': cat.schedule_f_line,
                'category': cat.name,
                'amount': line_total,
            })
            total += line_total

    line_items.sort(key=lambda x: x['schedule_f_line_code'])

    return {
        'line_items': line_items,
        'total': total,
        'year': year,
    }


def generate_schedule_f_pdf(data):
    """Export Schedule F as a filled PDF using reportlab."""
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="schedule_f.pdf"'

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    elements.extend(build_farm_header(doc.width))

    title = 'Schedule F - Farm Expenses'
    if data.get('year'):
        title += f' ({data["year"]})'
    elements.append(Paragraph(title, styles['Title']))
    elements.append(Spacer(1, 0.3 * inch))

    table_data = [['Schedule F Line', 'Category', 'Amount']]
    for item in data['line_items']:
        table_data.append([
            item['schedule_f_line'],
            item['category'],
            f'${item["amount"]}',
        ])
    table_data.append(['', 'TOTAL', f'${data["total"]}'])

    table = Table(table_data, colWidths=[2.5 * inch, 2 * inch, 1.5 * inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (2, 0), (2, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(table)

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    response.write(pdf)
    return response


def generate_pl_pdf(data):
    """Export P&L as a PDF using reportlab."""
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="profit_loss.pdf"'

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    elements.extend(build_farm_header(doc.width))

    elements.append(Paragraph('Profit & Loss Statement', styles['Title']))
    elements.append(Spacer(1, 0.3 * inch))

    table_data = [['Item', 'Amount']]
    table_data.append(['INCOME', ''])
    for item in data['income_items']:
        table_data.append([f'  {item["label"]}', f'${item["amount"]}'])
    table_data.append(['Total Income', f'${data["total_income"]}'])
    table_data.append(['', ''])
    table_data.append(['EXPENSES', ''])
    for item in data['expense_items']:
        table_data.append([f'  {item["label"]}', f'${item["amount"]}'])
    table_data.append(['Total Expenses', f'${data["total_expenses"]}'])
    table_data.append(['', ''])
    table_data.append(['NET PROFIT', f'${data["net_profit"]}'])

    table = Table(table_data, colWidths=[4 * inch, 2 * inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
        ('FONTNAME', (0, len(table_data) - 4), (-1, len(table_data) - 4), 'Helvetica-Bold'),
        ('FONTNAME', (0, len(table_data) - 1), (-1, len(table_data) - 1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(table)

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    response.write(pdf)
    return response


def generate_balance_sheet_pdf(data, crop_season):
    """Export per-crop balance sheet as a PDF."""
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="crop_balance_{crop_season.id}.pdf"'

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    elements.extend(build_farm_header(doc.width))

    elements.append(Paragraph(f'Per Crop Balance Sheet: {crop_season}', styles['Title']))
    elements.append(Spacer(1, 0.3 * inch))

    table_data = [['Item', 'Amount']]
    table_data.append(['INCOME', ''])
    for item in data['income_items']:
        table_data.append([f'  {item["label"]}', f'${item["amount"]}'])
    table_data.append(['Total Income', f'${data["total_income"]}'])
    table_data.append(['', ''])
    table_data.append(['EXPENSES', ''])
    for item in data['expense_items']:
        table_data.append([f'  {item["label"]}', f'${item["amount"]}'])
    table_data.append(['Total Expenses', f'${data["total_expenses"]}'])
    table_data.append(['', ''])
    table_data.append(['NET PROFIT', f'${data["net_profit"]}'])
    table_data.append(['Total Quantity Produced', f'{data["total_quantity"]} kg'])
    table_data.append(['Cost per kg', f'${data["cost_per_kg"]}/kg'])

    table = Table(table_data, colWidths=[4 * inch, 2 * inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(table)

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    response.write(pdf)
    return response


def generate_cash_flow_pdf(data):
    """Export cash flow statement as a PDF."""
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="cash_flow.pdf"'

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    elements.extend(build_farm_header(doc.width))

    elements.append(Paragraph('Cash Flow Statement', styles['Title']))
    elements.append(Spacer(1, 0.3 * inch))

    table_data = [['Date', 'Description', 'Type', 'Amount', 'Running Balance']]
    for t in data['transactions']:
        table_data.append([
            str(t['date']),
            t['description'],
            t['type'],
            f'${t["amount"]}',
            f'${t["running_balance"]}',
        ])
    table_data.append(['', '', '', 'Final Balance', f'${data["final_balance"]}'])

    table = Table(table_data, colWidths=[1 * inch, 2.2 * inch, 0.8 * inch, 1 * inch, 1.2 * inch])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (3, 0), (4, -1), 'RIGHT'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    elements.append(table)

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    response.write(pdf)
    return response


def build_harvest_report(harvest):
    """Build a detailed report for a single harvest record."""
    from crops.models import ProcessingStage
    stages = list(ProcessingStage.objects.filter(harvest=harvest).order_by('sequence'))
    sales = list(Sale.objects.filter(harvest=harvest).order_by('sale_date'))

    raw_sales = [s for s in sales if s.processing_stage_id is None]
    processed_sales = [s for s in sales if s.processing_stage_id is not None]

    raw_sold = sum((s.quantity for s in raw_sales), Decimal('0'))
    raw_revenue = sum((s.total_amount for s in raw_sales), Decimal('0'))
    processed_sold = sum((s.quantity for s in processed_sales), Decimal('0'))
    processed_revenue = sum((s.total_amount for s in processed_sales), Decimal('0'))

    first_input = stages[0].input_quantity if stages else Decimal('0')
    raw_available = harvest.quantity - first_input
    raw_remaining = raw_available - raw_sold
    raw_unsold = raw_remaining

    total_depreciated = sum(
        ((s.input_quantity or Decimal('0')) - (s.output_quantity or Decimal('0')))
        for s in stages
    )

    # Build processing trend: raw → stage1 → stage2 → … showing flow
    trend = []
    stage_lines = []
    trend.append({
        'label': 'Raw Harvest',
        'input': harvest.quantity,
        'output': harvest.quantity,
        'unit': harvest.unit,
        'depreciated': Decimal('0'),
    })
    for idx, stage in enumerate(stages):
        next_input = stages[idx + 1].input_quantity if idx + 1 < len(stages) else Decimal('0')
        balance = (stage.output_quantity or Decimal('0')) - next_input
        depreciated = (stage.input_quantity or Decimal('0')) - (stage.output_quantity or Decimal('0'))
        stage_sales = [s for s in sales if s.processing_stage_id == stage.pk]
        stage_sold = sum((s.quantity for s in stage_sales), Decimal('0'))
        stage_revenue = sum((s.total_amount for s in stage_sales), Decimal('0'))
        stage_unsold = (stage.output_quantity or Decimal('0')) - stage_sold
        stage_lines.append({
            'stage': stage,
            'balance': balance,
            'depreciated': depreciated,
            'sold': stage_sold,
            'unsold': stage_unsold,
            'revenue': stage_revenue,
            'sale_count': len(stage_sales),
            'sales': stage_sales,
        })
        trend.append({
            'label': f'Stage {stage.sequence}: {stage.name}',
            'input': stage.input_quantity or Decimal('0'),
            'output': stage.output_quantity or Decimal('0'),
            'unit': stage.output_unit or harvest.unit,
            'depreciated': depreciated,
        })

    total_revenue = raw_revenue + processed_revenue
    total_sold = raw_sold + processed_sold
    total_unsold = raw_unsold + sum((sl['unsold'] for sl in stage_lines), Decimal('0'))

    return {
        'harvest': harvest,
        'stages': stage_lines,
        'trend': trend,
        'raw_sales': raw_sales,
        'processed_sales': processed_sales,
        'raw_sold': raw_sold,
        'raw_revenue': raw_revenue,
        'raw_available': raw_available,
        'raw_remaining': raw_remaining,
        'raw_unsold': raw_unsold,
        'processed_sold': processed_sold,
        'processed_revenue': processed_revenue,
        'total_depreciated': total_depreciated,
        'total_revenue': total_revenue,
        'total_sold': total_sold,
        'total_unsold': total_unsold,
        'first_input': first_input,
    }


def generate_harvest_report_pdf(data):
    """Generate a harvest report PDF matching the requested layout."""
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle

    harvest = data['harvest']
    crop = harvest.crop_season.crop
    unit = harvest.unit
    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="harvest_report_{harvest.id}.pdf"'

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            leftMargin=0.55*inch, rightMargin=0.55*inch,
                            topMargin=0.5*inch, bottomMargin=0.5*inch)
    styles = getSampleStyleSheet()

    # Shared style constants
    from reportlab.lib.styles import ParagraphStyle
    blue_header = colors.HexColor('#d4e2f2')
    blue_alt = colors.HexColor('#e9eff7')
    blue_total = colors.HexColor('#d9e4f0')
    blue_raw = colors.HexColor('#eef3f9')
    line_color = colors.HexColor('#b8c6d9')

    style_title = ParagraphStyle('HTitle', parent=styles['Title'],
                                 fontSize=13, fontName='Helvetica-Bold',
                                 alignment=TA_CENTER, spaceAfter=8)
    style_section = ParagraphStyle('HSection', parent=styles['Normal'],
                                   fontSize=10, fontName='Helvetica-Bold',
                                   alignment=TA_LEFT, spaceBefore=14, spaceAfter=4)
    style_cell = ParagraphStyle('HCell', parent=styles['Normal'], fontSize=8)
    style_header = ParagraphStyle('HHeader', parent=styles['Normal'],
                                  fontSize=8, fontName='Helvetica-Bold',
                                  alignment=TA_LEFT)
    style_right = ParagraphStyle('HRight', parent=styles['Normal'], fontSize=8,
                                 alignment=TA_RIGHT)
    style_label = ParagraphStyle('HLabel', parent=styles['Normal'], fontSize=8,
                                 fontName='Helvetica-Bold', alignment=TA_LEFT)
    style_value = ParagraphStyle('HValue', parent=styles['Normal'], fontSize=8, alignment=TA_LEFT)
    style_center = ParagraphStyle('HCenter', parent=styles['Normal'], fontSize=8,
                                  alignment=TA_CENTER)
    style_footer = ParagraphStyle('HFooter', parent=styles['Normal'], fontSize=7,
                                  alignment=TA_CENTER, textColor=colors.HexColor('#444444'))

    def base_table_style():
        return TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 2),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            # Horizontal lines only, no column borders
            ('LINEABOVE', (0, 0), (-1, 0), 0.5, line_color),
            ('LINEBELOW', (0, 0), (-1, -1), 0.5, line_color),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, blue_alt]),
        ])

    elements = []

    # Title: "Coffee — Main Harvest | 24 August 2026"
    harvest_date_str = harvest.harvest_date.strftime('%d %B %Y')
    title_text = f'{crop.name} &mdash; {harvest.get_harvest_type_display()} | {harvest_date_str}'
    elements.append(Paragraph(title_text, style_title))

    # Top 2x2 harvest info table
    info_data = [
        ['Harvest Type', harvest.get_harvest_type_display(), 'Quantity Harvested', f'{harvest.quantity} {unit}'],
        ['Quality Grade', harvest.quality_grade or 'N/A', 'Moisture Content', f'{harvest.moisture_content}%'],
    ]
    info_table = Table(info_data, colWidths=[1.2*inch, 2.2*inch, 1.4*inch, 2.1*inch])
    info_table.setStyle(base_table_style())
    info_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), blue_header),
        ('BACKGROUND', (2, 0), (2, -1), blue_header),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTNAME', (2, 0), (2, -1), 'Helvetica-Bold'),
        ('ALIGN', (1, 0), (1, -1), 'LEFT'),
        ('ALIGN', (3, 0), (3, -1), 'LEFT'),
    ]))
    elements.append(info_table)
    elements.append(Spacer(1, 0.1 * inch))

    # 1. HARVEST & SALES SUMMARY (two side-by-side tables)
    elements.append(Paragraph('1. HARVEST & SALES SUMMARY', style_section))

    left_summary = [
        ['Metric', 'Quantity / Value'],
        ['Total Harvested', f'{harvest.quantity} {unit}'],
        ['Sent to Processing', f'{data["first_input"]} {unit}'],
        ['Raw Available', f'{data["raw_available"]} {unit}'],
        ['Raw Sold', f'{data["raw_sold"]} {unit}'],
        ['Raw Unsold', f'{data["raw_unsold"]} {unit}'],
    ]
    right_summary = [
        ['Metric', 'Quantity / Value'],
        ['Total Revenue', f'${data["total_revenue"]}'],
        ['Raw Revenue', f'${data["raw_revenue"]}'],
        ['Processed Revenue', f'${data["processed_revenue"]}'],
        ['Total Depreciated', f'{data["total_depreciated"]} {unit}'],
        ['Total Unsold', f'{data["total_unsold"]}'],
    ]

    def make_summary_table(rows):
        t = Table(rows, colWidths=[2.0*inch, 1.4*inch])
        t.setStyle(base_table_style())
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), blue_header),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'RIGHT'),
        ]))
        return t

    summary_wrap = Table(
        [[make_summary_table(left_summary), make_summary_table(right_summary)]],
        colWidths=[3.45*inch, 3.45*inch],
    )
    summary_wrap.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    elements.append(summary_wrap)

    # 2. SALES AND STOCK TABLE
    elements.append(Paragraph('2. SALES AND STOCK TABLE', style_section))

    stock_data = [
        ['Stage', 'Input Qty', 'Output Qty', 'Depreciated', 'Sold', 'Unsold', 'Revenue', 'Sales', 'Unit']
    ]

    # Raw / unprocessed row
    stock_data.append([
        'Raw / Unprocessed',
        f'{harvest.quantity}',
        f'{data["raw_available"]}',
        '0',
        f'{data["raw_sold"]}',
        f'{data["raw_unsold"]}',
        f'${data["raw_revenue"]}',
        str(len(data['raw_sales'])),
        unit,
    ])

    # Processing stage rows
    for sl in data['stages']:
        stage = sl['stage']
        out_unit = stage.output_unit or unit
        stock_data.append([
            stage.name,
            f'{stage.input_quantity or "-"}',
            f'{stage.output_quantity or "-"}',
            f'{(stage.input_quantity or Decimal("0")) - (stage.output_quantity or Decimal("0"))}',
            f'{sl["sold"]}',
            f'{sl["unsold"]}',
            f'${sl["revenue"]}',
            str(sl['sale_count']),
            out_unit,
        ])

    # TOTAL row
    total_sales = len(data['raw_sales']) + len(data['processed_sales'])
    stock_data.append([
        'TOTAL',
        f'{harvest.quantity}',
        '',
        f'{data["total_depreciated"]}',
        f'{data["total_sold"]}',
        f'{data["total_unsold"]}',
        f'${data["total_revenue"]}',
        str(total_sales),
        unit,
    ])

    stock_table = Table(stock_data, colWidths=[1.7*inch, 0.75*inch, 0.75*inch, 0.75*inch, 0.6*inch, 0.7*inch, 0.8*inch, 0.5*inch, 0.5*inch])
    stock_table.setStyle(base_table_style())
    stock_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), blue_header),
        ('BACKGROUND', (0, -1), (-1, -1), blue_total),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('ALIGN', (1, 1), (-2, -1), 'RIGHT'),
        ('ALIGN', (8, 1), (8, -1), 'LEFT'),
        ('ALIGN', (0, 1), (0, -2), 'LEFT'),
    ]))
    elements.append(stock_table)

    # Footer
    elements.append(Spacer(1, 0.3 * inch))
    generated = datetime.today().strftime('%d %B %Y')
    elements.append(Paragraph(f'Report generated: {generated} &bull; FarmLedger Pro', style_footer))

    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    response.write(pdf)
    return response


def generate_quickbooks_iif():
    """Generate an IIF formatted file mapping Django expense categories to QuickBooks accounts."""
    lines = []

    lines.append('!ACCNT\tNAME\tACCNTTYPE')
    seen_accounts = set()
    for code, account_name in QUICKBOOKS_ACCOUNT_MAP.items():
        if account_name not in seen_accounts:
            lines.append(f'ACCNT\t{account_name}\tEXPENSE')
            seen_accounts.add(account_name)

    lines.append('')
    lines.append('!TRNS\tDATE\tACCNT\tAMOUNT\tDOCNUM\tMEMO')
    lines.append('!SPL\tDATE\tACCNT\tAMOUNT\tDOCNUM\tMEMO')
    lines.append('!ENDTRNS')

    expenses = Expense.objects.select_related('category', 'farm_profile').all()
    for i, exp in enumerate(expenses, 1):
        account = QUICKBOOKS_ACCOUNT_MAP.get(
            exp.category.schedule_f_line, 'Other Miscellaneous'
        )
        doc_num = f'FL-{exp.id:06d}'
        memo = f'{exp.category.name} - {exp.vendor} - {exp.notes[:40] if exp.notes else ""}'
        lines.append(f'TRNS\t{exp.date}\tAccounts Payable\t{exp.amount}\t{doc_num}\t{memo}')
        lines.append(f'SPL\t{exp.date}\t{account}\t-{exp.amount}\t{doc_num}\t{memo}')

    lines.append('ENDTRNS')

    return '\r\n'.join(lines)
