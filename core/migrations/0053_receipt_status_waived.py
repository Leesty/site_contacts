# Generated manually on 2026-04-23

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0052_searchlink_vk_platform'),
    ]

    operations = [
        migrations.AlterField(
            model_name='withdrawalrequest',
            name='receipt_status',
            field=models.CharField(
                choices=[
                    ('none', 'Нет чека'),
                    ('pending', 'На проверке'),
                    ('approved', 'Одобрен'),
                    ('rejected', 'Отклонён'),
                    ('waived', 'Без чека (архив)'),
                ],
                default='none',
                help_text='Статус проверки чека.',
                max_length=20,
            ),
        ),
    ]
