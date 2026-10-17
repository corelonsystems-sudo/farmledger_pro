from django.apps import AppConfig


class AuditConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'audit'

    def ready(self):
        from django.apps import apps

        from .mixin import register_audit_signals

        # Audit every farm-scoped model plus financial catalogs.
        labels = [
            'accounts.FarmProfile', 'accounts.FarmMembership',
            'fields.Field', 'fields.LandParcel',
            'crops.CropSeason', 'crops.HarvestRecord', 'crops.ProcessingStage',
            'crops.Sale', 'crops.Customer',
            'expenses.Expense',
            'income.Income',
            'inventory.InventoryItem', 'inventory.InventoryTransaction',
            'inventory.InventoryAlert',
            'equipment.Equipment', 'equipment.MaintenanceLog', 'equipment.EquipmentUsage',
            'labor.Worker', 'labor.Task', 'labor.AttendanceRecord',
            'labor.PayrollRun', 'labor.PayrollLine',
            'budgets.Budget', 'budgets.BudgetItem', 'budgets.BudgetIncomePlan',
            'integrations.BankAccount', 'integrations.BankTransaction',
            'livestock.AnimalGroup', 'livestock.AnimalRecord',
            'livestock.AnimalHealthRecord', 'livestock.BreedingRecord',
            'livestock.AnimalFeedLog', 'livestock.MilkProductionRecord',
            'livestock.PoultryBatch', 'livestock.EggProductionRecord',
            'livestock.PoultryFeedLog', 'livestock.PoultryHealthRecord',
        ]
        for label in labels:
            register_audit_signals(apps.get_model(label))
