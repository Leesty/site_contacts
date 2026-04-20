# Generated manually on 2026-04-21

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0051_searchreport_paid_reward'),
    ]

    operations = [
        migrations.AddField(
            model_name='searchlink',
            name='platform',
            field=models.CharField(
                max_length=16,
                choices=[('telegram', 'Telegram'), ('vk', 'VK')],
                default='telegram',
                db_index=True,
                help_text='Платформа бота: Telegram или VK.',
            ),
        ),
        migrations.AddField(
            model_name='searchlink',
            name='vk_user_id',
            field=models.BigIntegerField(
                null=True,
                blank=True,
                help_text='VK user id лида (из вебхука при первом сообщении community-боту).',
            ),
        ),
        migrations.AddField(
            model_name='searchlink',
            name='vk_screen_name',
            field=models.CharField(
                max_length=64,
                blank=True,
                default='',
                help_text='VK screen_name лида (nickname в URL vk.com/<screen_name>).',
            ),
        ),
        migrations.AddField(
            model_name='searchlink',
            name='vk_first_name',
            field=models.CharField(
                max_length=128,
                blank=True,
                default='',
                help_text='VK first_name лида, из вебхука.',
            ),
        ),
    ]
