"""Migration: SS Worker Sub-System — referral links, lead assignments, worker reports, withdrawals."""
# Generated manually 2026-03-02

import django.db.models.deletion
import django.utils.timezone

from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0022_add_indexes_lead_status_contact_is_active"),
    ]

    operations = [
        # 1. Alter User.role to include WORKER
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("user", "Пользователь"),
                    ("support", "Поддержка"),
                    ("admin", "Администратор"),
                    ("standalone_admin", "Самостоятельный админ"),
                    ("worker", "Исполнитель"),
                ],
                default="user",
                help_text="Роль в системе (права доступа).",
                max_length=20,
            ),
        ),
        # 2. Add User.standalone_admin_owner FK
        migrations.AddField(
            model_name="user",
            name="standalone_admin_owner",
            field=models.ForeignKey(
                blank=True,
                help_text="Самостоятельный админ, к которому привязан исполнитель (воркер).",
                limit_choices_to={"role": "standalone_admin"},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="workers",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # 3. ReferralLink
        migrations.CreateModel(
            name="ReferralLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(help_text="Уникальный код ссылки (случайный).", max_length=32, unique=True)),
                ("is_active", models.BooleanField(default=True, help_text="Активна ли ссылка для регистрации.")),
                ("note", models.CharField(blank=True, help_text="Заметка для идентификации ссылки.", max_length=100)),
                (
                    "standalone_admin",
                    models.ForeignKey(
                        limit_choices_to={"role": "standalone_admin"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="referral_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Реферальная ссылка",
                "verbose_name_plural": "Реферальные ссылки",
                "ordering": ["-created_at"],
            },
        ),
        # 4. LeadAssignment
        migrations.CreateModel(
            name="LeadAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("task_description", models.TextField(blank=True, help_text="Описание задачи для исполнителя.")),
                (
                    "lead",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assignments",
                        to="core.lead",
                    ),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        limit_choices_to={"role": "worker"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lead_assignments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "assigned_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assigned_leads_by",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Назначение лида",
                "verbose_name_plural": "Назначения лидов",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AlterUniqueTogether(
            name="leadassignment",
            unique_together={("lead", "worker")},
        ),
        # 5. WorkerReport
        migrations.CreateModel(
            name="WorkerReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("raw_contact", models.CharField(help_text="Контакт / результат работы.", max_length=255)),
                ("comment", models.TextField(blank=True)),
                ("attachment", models.FileField(blank=True, help_text="Скриншот/видео подтверждения.", null=True, upload_to="worker_reports/")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "На проверке"),
                            ("approved", "Одобрен"),
                            ("rejected", "Отклонён"),
                            ("rework", "На доработке"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("rework_comment", models.TextField(blank=True, help_text="Что исправить (при статусе «На доработке»).")),
                ("rejection_reason", models.TextField(blank=True)),
                ("reward", models.PositiveIntegerField(default=150, help_text="Вознаграждение за одобренный отчёт (руб.).")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "assignment",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="report",
                        to="core.leadassignment",
                    ),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="worker_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "standalone_admin",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="received_worker_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_worker_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Отчёт исполнителя",
                "verbose_name_plural": "Отчёты исполнителей",
                "ordering": ["-created_at"],
            },
        ),
        # 6. WorkerWithdrawalRequest
        migrations.CreateModel(
            name="WorkerWithdrawalRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.PositiveIntegerField(help_text="Сумма к выводу (руб.)")),
                ("payout_details", models.TextField(help_text="Реквизиты для вывода.")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "На рассмотрении"),
                            ("approved", "Выплачено"),
                            ("rejected", "Отклонено"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "worker",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="worker_withdrawal_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "standalone_admin",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="worker_withdrawals_to_process",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "processed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="processed_worker_withdrawals",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Заявка воркера на вывод",
                "verbose_name_plural": "Заявки воркеров на вывод",
                "ordering": ["-created_at"],
            },
        ),
    ]
