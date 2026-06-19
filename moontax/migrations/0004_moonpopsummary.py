import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moontax', '0003_configuration_table_page_size'),
    ]

    operations = [
        migrations.CreateModel(
            name='MoonPopSummary',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('ore_mined_units', models.BigIntegerField(default=0, help_text='Total ore units mined in the pop window (linked + unlinked miners).')),
                ('expected_total_taxes', models.BigIntegerField(default=0, help_text='Total ore units owed across all invoices emitted for this pop.')),
                ('invoices_emitted', models.PositiveIntegerField(default=0)),
                ('finalized_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('extraction', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='summary', to='moontax.extraction')),
                ('moon', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='moontax.moon')),
                ('structure', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='moontax.structure')),
            ],
            options={
                'ordering': ['-finalized_at'],
            },
        ),
    ]
