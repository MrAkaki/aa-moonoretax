from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moontax', '0005_notificationsetting'),
    ]

    operations = [
        migrations.AddField(
            model_name='oretype',
            name='base_type_id',
            field=models.BigIntegerField(
                blank=True,
                null=True,
                help_text=(
                    'For a quality/compressed variant, the type_id of its base moon ore; '
                    'null for a base ore.'
                ),
            ),
        ),
    ]
