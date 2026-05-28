# Воронка «холодный контакт → лид → чат через windowgram → отчёт Прозвон».

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.models  # для call_report_upload_to


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0084_cold_contacts"),
    ]

    operations = [
        # ─── User: связка с windowgram ManagerUser ─────────────────────────
        migrations.AddField(
            model_name="user",
            name="windowgram_manager_id",
            field=models.CharField(
                blank=True, default="", max_length=64,
                help_text="UUID ManagerUser в windowgram (для создания чатов через invite-pool).",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="windowgram_manager_password",
            field=models.CharField(
                blank=True, default="", max_length=128,
                help_text="Авто-сгенерированный пароль для ManagerUser в windowgram.",
            ),
        ),
        # ─── ColdContact: TG-аккаунт клиента + данные чата ────────────────
        migrations.AddField(
            model_name="coldcontact",
            name="tg_username",
            field=models.CharField(
                blank=True, default="", max_length=100,
                help_text="TG @username клиента (без @), либо ссылка t.me/...",
            ),
        ),
        migrations.AddField(
            model_name="coldcontact",
            name="chat_id",
            field=models.BigIntegerField(
                blank=True, null=True, db_index=True,
                help_text="TG chat_id созданного чата (отрицательный для basic group).",
            ),
        ),
        migrations.AddField(
            model_name="coldcontact",
            name="chat_invite_link",
            field=models.CharField(
                blank=True, default="", max_length=255,
                help_text="Invite-ссылка на чат для клиента.",
            ),
        ),
        migrations.AddField(
            model_name="coldcontact",
            name="chat_created_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        # ─── CallReport (Прозвон) ──────────────────────────────────────────
        migrations.CreateModel(
            name="CallReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("screencast", models.FileField(
                    upload_to=core.models.call_report_upload_to,
                    help_text="Скринкаст переписки / звонка.",
                )),
                ("source", models.CharField(
                    blank=True, max_length=255,
                    help_text="Откуда менеджер взял этот контакт (свободный текст).",
                )),
                ("status", models.CharField(
                    choices=[
                        ("pending", "На проверке"),
                        ("approved", "Одобрен"),
                        ("rejected", "Отклонён"),
                        ("rework", "На доработке"),
                    ],
                    db_index=True, default="pending", max_length=20,
                )),
                ("is_complete", models.BooleanField(
                    db_index=True, default=False,
                    help_text="Авто-валидация: админ (artem_tele2 / shaneli77) в чате И клиент зашёл.",
                )),
                ("validation_note", models.TextField(blank=True, help_text="Что не хватило для is_complete.")),
                ("rejection_reason", models.TextField(blank=True)),
                ("rework_comment", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("paid_reward", models.IntegerField(
                    default=0, help_text="Сколько начислено менеджеру при approve (для аудита).",
                )),
                ("cold_contact", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="call_report",
                    to="core.coldcontact",
                )),
                ("reviewed_by", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="reviewed_call_reports",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Отчёт «Прозвон»",
                "verbose_name_plural": "Отчёты «Прозвон»",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="callreport",
            index=models.Index(fields=["status", "is_complete"], name="core_callre_status_d6cf2a_idx"),
        ),
    ]
