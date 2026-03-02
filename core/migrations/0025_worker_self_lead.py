from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import core.models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0024_alter_workerreport_attachment"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkerSelfLead",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("raw_contact", models.CharField(help_text="Контакт / ссылка (юзернейм, телефон и т.д.).", max_length=500)),
                ("lead_date", models.DateField(help_text="Дата лида.")),
                (
                    "attachment",
                    models.FileField(
                        blank=True,
                        help_text="Скриншот или видео подтверждения.",
                        null=True,
                        upload_to=core.models.worker_self_lead_upload_to,
                    ),
                ),
                ("comment", models.TextField(blank=True, help_text="Комментарий к лиду.")),
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
                ("reward", models.PositiveIntegerField(default=150, help_text="Вознаграждение за одобренный лид (руб.).")),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_worker_self_leads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "standalone_admin",
                    models.ForeignKey(
                        limit_choices_to={"role": "standalone_admin"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="received_worker_self_leads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "worker",
                    models.ForeignKey(
                        limit_choices_to={"role": "worker"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="self_leads",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Лид от исполнителя",
                "verbose_name_plural": "Лиды от исполнителей",
                "ordering": ["-created_at"],
            },
        ),
    ]
