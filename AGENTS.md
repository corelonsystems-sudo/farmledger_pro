# FarmLedger Pro

Django 4.2 farm-management system. The live code is at the repository root —
the nested `farmledger_pro/` directory is a stale duplicate; do not edit it.

## Commands

- Run dev server: `python manage.py runserver 0.0.0.0:8000`
- Tests: `python manage.py test`
- System check: `python manage.py check` (deploy: `python manage.py check --deploy`)
- Migrations: `python manage.py makemigrations` / `python manage.py migrate`

## Configuration

All deployment settings are environment-driven (see `.env.example`):
`DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`,
`DJANGO_SECURE_*`, `POSTGRES_*`, `FARMLEDGER_USE_SQLITE`, `CELERY_*`,
`EMAIL_BACKEND`, `CORS_*`, `AWS_*`.

`X_FRAME_OPTIONS` is intentionally `SAMEORIGIN` — the dashboard uses
same-origin iframe modals; do not change it to `DENY`.

## Architecture notes

- **Farm scoping**: `accounts/access.py` (`scoped_queryset`, `accessible_farms`,
  `FarmScopedMixin`). Farm membership is `accounts.FarmMembership`;
  `FarmProfile.user` is the owner and always has access. Any new queryset in a
  view must go through `scoped_queryset`.
- **Sale -> Income**: `crops/services.py::sync_sale_income` keeps exactly one
  auto-generated `Income` per `Sale` (created/updated via `post_save`,
  deleted via `Income.sale` CASCADE).
- **Inventory**: `InventoryTransaction.save()` is atomic; PURCHASE requires a
  `budget_item` and posts an `Expense`; inventory `Sale`s decrement
  `quantity_on_hand` and restore it on delete/update.
- **History protection**: harvest/sale/season/field links use `PROTECT`;
  `model_delete` renders a 409 modal listing blocking records.
- **Budget planning**: `budgets.queries.budget_plan_data` is the shared source
  for grouped budget pages and exports. `BudgetIncomePlan` stores planned
  capital/loan/revenue only; it must never post actual `Income`. Budget item
  categories can repeat; `line_code` is unique per budget. The full-page editor
  saves metadata and both inline formsets atomically. Unconfirmed amounts are
  stored as zero with `figure_type=UNCONFIRMED`; actuals come from linked expenses.
- **Reports**: `templates/reports/` with shared `report_styles.html`,
  `donut.html`, `export_menu.html`; exports (`?export=csv|xlsx|pdf`) are built
  in `farmledger/views.py::_export_report`. `/reports/?section=overview|sales|
  production|livestock|operations` is the single tabbed workspace; legacy report
  paths select the matching tab. Report views return `TemplateResponse` so the
  workspace can reuse isolated contexts through `reports/section_base.html`.
  Filters and export links must preserve `section`; HTML IDs must be unique
  across all five sections. Print and export target the selected report.
