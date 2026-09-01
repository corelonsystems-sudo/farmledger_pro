from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('budgets', '0002_alter_budget_unique_together_budget_end_date_and_more'),
        ('crops', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='budget',
            name='crop_season',
        ),
        migrations.AddField(
            model_name='budgetitem',
            name='crop_season',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='budget_items',
                to='crops.cropseason',
            ),
        ),
    ]
