from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0053_receipt_status_waived'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='zvonok_public_key',
            field=models.CharField(
                max_length=255, blank=True, default='',
                help_text='Public key zvonok.com (сгенерить в Настройках профиля на zvonok.com).',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='zvonok_campaign_id',
            field=models.CharField(
                max_length=64, blank=True, default='',
                help_text='ID кампании zvonok.com, в которой настроен робот.',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='zvonok_last_tested_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='Когда последний раз тестировался звонок через API.',
            ),
        ),
    ]
