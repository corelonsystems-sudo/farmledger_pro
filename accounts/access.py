from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import FarmProfile, User

FARM_LOOKUPS = {
    'accounts.farmprofile': 'pk',
    'accounts.farmmembership': 'farm_profile_id',
    'fields.field': 'farm_profile_id',
    'fields.landparcel': 'field__farm_profile_id',
    'crops.cropseason': 'field__farm_profile_id',
    'crops.harvestrecord': 'crop_season__field__farm_profile_id',
    'crops.processingstage': 'harvest__crop_season__field__farm_profile_id',
    'crops.sale': 'harvest__crop_season__field__farm_profile_id',
    'crops.customer': 'farm_profile_id',
    'expenses.expense': 'farm_profile_id',
    'income.income': 'farm_profile_id',
    'inventory.inventoryitem': 'farm_profile_id',
    'inventory.inventorytransaction': 'item__farm_profile_id',
    'inventory.inventoryalert': 'item__farm_profile_id',
    'equipment.equipment': 'farm_profile_id',
    'equipment.maintenancelog': 'equipment__farm_profile_id',
    'equipment.equipmentusage': 'equipment__farm_profile_id',
    'labor.worker': 'farm_profile_id',
    'labor.task': None,
    'labor.attendancerecord': 'worker__farm_profile_id',
    'labor.payrollrun': 'farm_profile_id',
    'budgets.budget': 'farm_profile_id',
    'budgets.budgetitem': 'budget__farm_profile_id',
    'budgets.budgetincomeplan': 'budget__farm_profile_id',
    'integrations.bankaccount': 'farm_profile_id',
    'integrations.banktransaction': 'bank_account__farm_profile_id',
    'livestock.animalgroup': 'farm_profile_id',
    'livestock.animalrecord': 'farm_profile_id',
    'livestock.animalhealthrecord': 'animal__farm_profile_id',
    'livestock.breedingrecord': 'farm_profile_id',
    'livestock.animalfeedlog': 'group__farm_profile_id',
    'livestock.milkproductionrecord': 'group__farm_profile_id',
    'livestock.poultrybatch': 'farm_profile_id',
    'livestock.eggproductionrecord': 'batch__farm_profile_id',
    'livestock.poultryfeedlog': 'batch__farm_profile_id',
    'livestock.poultryhealthrecord': 'batch__farm_profile_id',
}
GLOBAL_CATALOGS = {
    'accounts.currency', 'crops.crop', 'expenses.category', 'expenses.scheduleline',
    'expenses.budgetsubcode', 'income.incomesource', 'livestock.animalspecies',
}
FINANCIAL_MODELS = {
    'expenses.expense', 'income.income', 'budgets.budget',
    'budgets.budgetitem', 'budgets.budgetincomeplan', 'inventory.inventorytransaction',
    'integrations.bankaccount', 'integrations.banktransaction',
}


def accessible_farms(user, write_model=None):
    farms = FarmProfile.objects.all()
    if not user.is_authenticated:
        return farms.none()
    if user.is_superuser:
        return farms
    membership = Q(memberships__user=user)
    if write_model is not None:
        roles = [User.Role.OWNER, User.Role.MANAGER]
        if write_model in FINANCIAL_MODELS:
            roles.append(User.Role.ACCOUNTANT)
        if write_model == 'accounts.farmmembership':
            roles = [User.Role.OWNER]
        membership &= Q(memberships__role__in=roles)
    return farms.filter(Q(user=user) | membership).distinct()


def scoped_queryset(model, user, queryset=None, write=False):
    qs = queryset if queryset is not None else model.objects.all()
    if not user.is_authenticated:
        return qs.none()
    if user.is_superuser:
        return qs
    label = model._meta.label_lower
    if label == 'accounts.user':
        return qs.filter(pk=user.pk) if not write else qs.none()
    if label in GLOBAL_CATALOGS:
        return qs if not write else qs.none()
    farms = accessible_farms(user, write_model=label if write else None)
    if label == 'labor.task':
        ids = farms.values('pk')
        return qs.filter(Q(field__farm_profile_id__in=ids)
                         | Q(crop_season__field__farm_profile_id__in=ids)
                         | Q(workers__farm_profile_id__in=ids)).distinct()
    if label == 'crops.sale':
        ids = farms.values('pk')
        return qs.filter(Q(harvest__crop_season__field__farm_profile_id__in=ids)
                         | Q(inventory_item__farm_profile_id__in=ids))
    lookup = FARM_LOOKUPS.get(label)
    return qs.filter(**{f'{lookup}__in': farms.values('pk')}) if lookup else qs.none()


class FarmScopedMixin:
    """DRF mixin: scope querysets to the user's farms and validate that
    related objects submitted on writes belong to an accessible farm."""

    def get_queryset(self):
        return scoped_queryset(
            self.queryset.model, self.request.user, self.queryset,
            write=self.request.method not in ('GET', 'HEAD', 'OPTIONS'),
        )

    def _validate_related_farms(self, data):
        model = self.queryset.model
        label = model._meta.label_lower
        creating_own_farm = (model is FarmProfile and not FarmProfile.objects.filter(
            user=self.request.user).exists())
        if not self.request.user.is_superuser and (
                label in GLOBAL_CATALOGS or not (creating_own_farm or accessible_farms(
                    self.request.user, write_model=label).exists())):
            raise PermissionDenied('You cannot change records for this farm.')
        for name, value in data.items():
            field = model._meta.get_field(name)
            if not field.is_relation or value is None:
                continue
            related = field.related_model
            values = value if field.many_to_many else [value]
            for obj in values:
                if not scoped_queryset(related, self.request.user).filter(pk=obj.pk).exists():
                    raise ValidationError({name: 'Related record is not accessible.'})
                if related is FarmProfile and not accessible_farms(
                        self.request.user, write_model=label).filter(pk=obj.pk).exists():
                    raise PermissionDenied('You cannot change records for this farm.')

    def perform_create(self, serializer):
        self._validate_related_farms(serializer.validated_data)
        if self.queryset.model is FarmProfile:
            serializer.save(user=self.request.user)
        else:
            serializer.save()

    def perform_update(self, serializer):
        self._validate_related_farms(serializer.validated_data)
        serializer.save()
