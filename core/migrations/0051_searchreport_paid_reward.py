# Generated manually on 2026-04-21

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0050_searchlink_telegram_username'),
    ]

    operations = [
        migrations.AddField(
            model_name='searchreport',
            name='paid_reward',
            field=models.IntegerField(default=0, help_text='Сколько реально начислено менеджеру на момент одобрения (без партнёрского cut). Для старых/не одобренных записей — 0.'),
        ),
    ]
