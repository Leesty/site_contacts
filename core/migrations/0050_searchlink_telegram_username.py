# Generated manually on 2026-04-20

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0049_searchlink_display_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='searchlink',
            name='telegram_username',
            field=models.CharField(blank=True, default='', help_text='Telegram username лида (без @), из вебхука при /start.', max_length=64),
        ),
        migrations.AddField(
            model_name='searchlink',
            name='telegram_first_name',
            field=models.CharField(blank=True, default='', help_text='Telegram first_name лида, из вебхука при /start.', max_length=128),
        ),
    ]
