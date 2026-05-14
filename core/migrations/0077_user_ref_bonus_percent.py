"""Обычные рефоводы (role=user) переключаются на модель «реферал получает
полную ставку, рефовод — % сверху». Дефолт 30%."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0076_group_report_cut_default_20'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='ref_bonus_percent',
            field=models.PositiveIntegerField(
                default=30,
                help_text=('Процент бонуса рефовода с каждого одобренного отчёта реферала '
                           '(Lead/SearchLink/Группы). Реферал получает ПОЛНУЮ ставку, рефовод '
                           'дополнительно — pool × процент. По умолчанию 30. Применяется ко '
                           'всем рефералам этого рефовода. Только role=user; у partner — '
                           'своя фикс-логика.'),
            ),
        ),
    ]
