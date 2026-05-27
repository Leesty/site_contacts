"""Завод-лидов: лимиты на проектах + общий пул номеров от админа.

- LidProject: total_limit, daily_limit, is_closed, last_fill_business_date
- Новая модель AdminPhonePool — пул, разбираемый авто-раздачей
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0082_lid_projects"),
    ]

    operations = [
        migrations.AddField(
            model_name="lidproject",
            name="total_limit",
            field=models.PositiveIntegerField(
                null=True, blank=True,
                help_text="Общий лимит админских номеров. Достигнут → закрыт. NULL = без лимита.",
            ),
        ),
        migrations.AddField(
            model_name="lidproject",
            name="daily_limit",
            field=models.PositiveIntegerField(
                null=True, blank=True,
                help_text="Сколько номеров заливать в проект каждый день. NULL = ничего автоматически.",
            ),
        ),
        migrations.AddField(
            model_name="lidproject",
            name="is_closed",
            field=models.BooleanField(
                default=False, db_index=True,
                help_text="Достигнут total_limit. Больше не заливаем, доступно только скачивание.",
            ),
        ),
        migrations.AddField(
            model_name="lidproject",
            name="last_fill_business_date",
            field=models.DateField(
                null=True, blank=True,
                help_text="Дата последней ежедневной раздачи. Чтобы не дважды в один бизнес-день.",
            ),
        ),
        migrations.AddIndex(
            model_name="lidproject",
            index=models.Index(
                fields=["customer", "is_closed"],
                name="core_lidpro_custome_ccb636_idx",
            ),
        ),
        migrations.CreateModel(
            name="AdminPhonePool",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("value", models.CharField(db_index=True, max_length=500)),
                ("is_used", models.BooleanField(db_index=True, default=False)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                ("customer", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="lid_pool_entries",
                    to=settings.AUTH_USER_MODEL,
                )),
                ("submitter", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="lid_pool_submitted",
                    to=settings.AUTH_USER_MODEL,
                    help_text="Кто залил в пул (главный админ).",
                )),
                ("used_in_project", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="from_pool_items",
                    to="core.lidproject",
                )),
            ],
            options={
                "verbose_name": "Пул админа (завод-лидов)",
                "verbose_name_plural": "Пул админа (завод-лидов)",
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="adminphonepool",
            index=models.Index(
                fields=["customer", "is_used"],
                name="core_adminp_custome_146274_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="adminphonepool",
            constraint=models.UniqueConstraint(
                fields=["customer", "value"],
                name="uniq_pool_per_customer_value",
            ),
        ),
    ]
