from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moontax', '0002_oretype'),
    ]

    operations = [
        migrations.AddField(
            model_name='configuration',
            name='table_page_size',
            field=models.PositiveIntegerField(default=25, help_text='Default number of rows per page in dashboard/staff/admin tables.'),
        ),
    ]
