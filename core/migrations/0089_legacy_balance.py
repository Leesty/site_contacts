from django.db import migrations, models


class Migration(migrations.Migration):
    """Добавляет User.legacy_balance — замороженный положительный баланс старой
    системы (2026-07). Только AddField: накопленный дрейф других моделей
    (RemoveField/AlterField, который подтянул makemigrations) намеренно НЕ
    включён — это отдельная непровязанная история, не относящаяся к этой задаче.
    """

    dependencies = [
        ('core', '0088_remove_zavod_lidov'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='legacy_balance',
            field=models.IntegerField(
                default=0,
                help_text='Легаси-баланс (руб.): замороженный положительный баланс '
                          'старой системы на 2026-07. Вывод пока недоступен — выплатим позже.',
            ),
        ),
    ]
