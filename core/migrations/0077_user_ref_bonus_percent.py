"""(reverted) ref_bonus_percent был добавлен и тут же откачен в коде —
но в БД продакшна столбец уже создан, миграция отмечена применённой.
Файл сохранён как stub для целостности dependency graph; реальный
DROP столбца — в миграции 0078_subref_milestone (RemoveField)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0076_group_report_cut_default_20'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='ref_bonus_percent',
            field=models.PositiveIntegerField(default=30),
        ),
    ]
