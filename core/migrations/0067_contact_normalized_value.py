"""Глобальная защита от двойной выдачи: добавляем нормализованное значение
контакта (phone:79..., telegram:user, vk:id1) и индекс по нему.

Сама нормализация существующих 572к записей делается отдельным скриптом —
миграция только добавляет колонку, чтобы накатилось быстро."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0066_group_report_review_log'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='normalized_value',
            field=models.CharField(
                blank=True, db_index=True, default='', max_length=255,
                help_text=('Нормализованный value (phone:79..., telegram:user, vk:id1) — '
                           'для глобальной защиты от двойной выдачи одного и того же '
                           'номера/ника через разные базы.'),
            ),
        ),
    ]
