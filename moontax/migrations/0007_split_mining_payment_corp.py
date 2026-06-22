from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moontax', '0006_oretype_base_type_id'),
    ]

    operations = [
        # ── Configuration: rename existing fields ────────────────────────────
        migrations.RenameField(
            model_name='configuration',
            old_name='target_corporation_id',
            new_name='mining_corporation_id',
        ),
        migrations.RenameField(
            model_name='configuration',
            old_name='target_corporation_name',
            new_name='mining_corporation_name',
        ),
        # ── Configuration: add payment corp fields ───────────────────────────
        migrations.AddField(
            model_name='configuration',
            name='payment_corporation_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='configuration',
            name='payment_corporation_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        # ── TokenConfig: add role field (non-unique default first) ───────────
        # Step 1: add with a default so the existing single row (if any)
        # gets labelled as the mining token automatically.
        migrations.AddField(
            model_name='tokenconfig',
            name='role',
            field=models.CharField(
                choices=[('mining', 'Mining corp'), ('payment', 'Payment corp')],
                default='mining',
                max_length=16,
            ),
            preserve_default=False,
        ),
        # Step 2: no data migration needed — the default already labels the
        # existing row as "mining".
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
        # Step 3: enforce uniqueness now that all rows have a role value.
        migrations.AlterField(
            model_name='tokenconfig',
            name='role',
            field=models.CharField(
                choices=[('mining', 'Mining corp'), ('payment', 'Payment corp')],
                max_length=16,
                unique=True,
            ),
        ),
    ]
