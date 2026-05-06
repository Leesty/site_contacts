"""Замена связки с подадмином: одно платформо-числовое поле → два
независимых username-поля (TG + VK), потому что один менеджер может
работать сразу через обе платформы.

Drop старых полей безопасен — данных в них на момент миграции нет."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0068_user_bot_admin_link'),
    ]

    operations = [
        migrations.RemoveField(model_name='user', name='bot_admin_platform'),
        migrations.RemoveField(model_name='user', name='bot_admin_user_id'),
        migrations.RemoveField(model_name='user', name='bot_admin_username'),
        migrations.AddField(
            model_name='user', name='bot_admin_tg_username',
            field=models.CharField(
                blank=True, default='', max_length=100,
                help_text='@username в Telegram (без @). Для регистрации менеджера как подадмина бота.',
            ),
        ),
        migrations.AddField(
            model_name='user', name='bot_admin_vk_screen_name',
            field=models.CharField(
                blank=True, default='', max_length=100,
                help_text='screen_name VK (последняя часть ссылки vk.com/...).',
            ),
        ),
    ]
