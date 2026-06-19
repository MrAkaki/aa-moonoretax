import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moontax', '0004_moonpopsummary'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='NotificationSetting',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('moon_pop', models.BooleanField(default=True, help_text='Notify when a new moon extraction is scheduled.')),
                ('moon_dead', models.BooleanField(default=True, help_text='Notify when a moon pop is finalized / the ore field despawns.')),
                ('invoice_emitted', models.BooleanField(default=True, help_text='Notify when a new tax invoice is emitted to you.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='moontax_notification_setting', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
