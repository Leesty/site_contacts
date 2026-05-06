"""Связка User с подадмином на windowgram.bot_command_admins.

Заполняется при grant право на отчёты по группам — главный админ указывает
TG/VK ID, и Django синхронно регистрирует менеджера как sub-admin на боте."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0067_contact_normalized_value'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='bot_admin_platform',
            field=models.CharField(
                blank=True, default='', max_length=16,
                choices=[('telegram', 'Telegram'), ('vk', 'VK')],
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='bot_admin_user_id',
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='bot_admin_username',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
    ]
