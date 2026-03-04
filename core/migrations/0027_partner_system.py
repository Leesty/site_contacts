import core.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0026_alter_workerselfleadreward_default"),
    ]

    operations = [
        # 1. Add PARTNER to role choices (no DB change needed — CharField stores raw values)
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("user", "Пользователь"),
                    ("support", "Поддержка"),
                    ("admin", "Администратор"),
                    ("standalone_admin", "Самостоятельный админ"),
                    ("balance_admin", "Баланс\u2011админ"),
                    ("worker", "Исполнитель"),
                    ("partner", "Партнёр"),
                ],
                default="user",
                help_text="Роль в системе (права доступа).",
                max_length=20,
            ),
        ),
        # 2. Add partner_owner FK on User
        migrations.AddField(
            model_name="user",
            name="partner_owner",
            field=models.ForeignKey(
                blank=True,
                help_text="Партнёр, привлёкший этого пользователя.",
                limit_choices_to={"role": "partner"},
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="partner_users",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # 3. Create PartnerLink
        migrations.CreateModel(
            name="PartnerLink",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(default=core.models.partner_link_code, max_length=32, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("note", models.CharField(blank=True, help_text="Заметка для идентификации ссылки.", max_length=100)),
                (
                    "partner",
                    models.ForeignKey(
                        limit_choices_to={"role": "partner"},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="partner_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Реферальная ссылка партнёра",
                "verbose_name_plural": "Реферальные ссылки партнёров",
                "ordering": ["-created_at"],
            },
        ),
        # 4. Create PartnerEarning
        migrations.CreateModel(
            name="PartnerEarning",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("amount", models.PositiveIntegerField(default=10)),
                (
                    "lead",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="partner_earning",
                        to="core.lead",
                    ),
                ),
                (
                    "partner",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="partner_earnings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Начисление партнёру",
                "verbose_name_plural": "Начисления партнёрам",
                "ordering": ["-created_at"],
            },
        ),
    ]
