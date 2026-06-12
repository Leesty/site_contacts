# Производительность: индексы для горячих путей (списки отчётов, 20s-поллер,
# подсчёт заработка админов). Только аддитивные операции — безопасно на проде.
#
# NB: db_index=True на status/role/action-полях даёт single-column индексы
# через AlterField. Композитные — через AddIndex. Прочие "висящие" изменения
# моделей (zvonok_campaign_id и т.п.) НЕ трогаем — они вне зоны этого аудита.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0086_can_create_call_reports"),
    ]

    operations = [
        # ── Single-column indexes (db_index=True) на горячих status/role-полях ──
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("user", "Пользователь"), ("support", "Поддержка"),
                    ("admin", "Админ по отчётам"), ("main_admin", "Главный админ"),
                    ("standalone_admin", "Самостоятельный админ"),
                    ("balance_admin", "Баланс‑админ"), ("worker", "Исполнитель"),
                    ("partner", "Партнёр"), ("lid_customer", "Заказчик лидов (zavodlidov)"),
                ],
                db_index=True, default="user", max_length=20,
                help_text="Роль в системе (права доступа).",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="status",
            field=models.CharField(
                choices=[("pending", "На модерации"), ("approved", "Одобрен"), ("banned", "Забанен")],
                db_index=True, default="pending", max_length=20,
                help_text="Статус модерации пользователя.",
            ),
        ),
        migrations.AlterField(
            model_name="contactrequest",
            name="status",
            field=models.CharField(
                choices=[("pending", "Ожидает"), ("resolved", "Обработано")],
                db_index=True, default="pending", max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="withdrawalrequest",
            name="status",
            field=models.CharField(
                choices=[("pending", "На рассмотрении"), ("approved", "Выполнено"), ("rejected", "Отклонено")],
                db_index=True, default="pending", max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="workerwithdrawalrequest",
            name="status",
            field=models.CharField(
                choices=[("pending", "На рассмотрении"), ("approved", "Выплачено"), ("rejected", "Отклонено")],
                db_index=True, default="pending", max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="leadreviewlog",
            name="action",
            field=models.CharField(
                choices=[("approved", "Одобрено"), ("rejected", "Отклонено"), ("rework", "На доработку")],
                db_index=True, max_length=20,
            ),
        ),
        # ── Композитные индексы для списков и сортировок ──
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(fields=["status", "-created_at"], name="core_lead_status_cf36a8_idx"),
        ),
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(fields=["user", "status"], name="core_lead_user_id_737708_idx"),
        ),
        migrations.AddIndex(
            model_name="lead",
            index=models.Index(fields=["reviewed_at"], name="core_lead_reviewe_8b8521_idx"),
        ),
        migrations.AddIndex(
            model_name="searchreport",
            index=models.Index(fields=["status", "-created_at"], name="core_search_status_4f6122_idx"),
        ),
    ]
