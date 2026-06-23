# Удаление фичи «Завод-лидов»: модели LidProject / LidProjectItem /
# AdminPhonePool + роль lid_customer. Фича отработала и больше не нужна.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0087_perf_indexes"),
    ]

    operations = [
        # Сначала зависимые (FK на LidProject), затем сам LidProject.
        migrations.DeleteModel(name="AdminPhonePool"),
        migrations.DeleteModel(name="LidProjectItem"),
        migrations.DeleteModel(name="LidProject"),
        # Убираем роль lid_customer из choices (cosmetic, на DDL не влияет).
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("user", "Пользователь"), ("support", "Поддержка"),
                    ("admin", "Админ по отчётам"), ("main_admin", "Главный админ"),
                    ("standalone_admin", "Самостоятельный админ"),
                    ("balance_admin", "Баланс‑админ"), ("worker", "Исполнитель"),
                    ("partner", "Партнёр"),
                ],
                db_index=True, default="user", max_length=20,
                help_text="Роль в системе (права доступа).",
            ),
        ),
    ]
