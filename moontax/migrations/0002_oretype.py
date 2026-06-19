from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('moontax', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='OreType',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type_id', models.BigIntegerField(unique=True)),
                ('name', models.CharField(blank=True, max_length=100)),
                ('group_id', models.BigIntegerField(blank=True, null=True)),
            ],
            options={
                'ordering': ['group_id', 'name'],
            },
        ),
    ]
