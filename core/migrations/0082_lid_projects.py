"""Завод-лидов: переходим с per-day phones на per-project items.

Дропаем LidPhoneSubmission (фича только что задеплоена, тестовые данные
очищены), создаём LidProject + LidProjectItem.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0081_lid_phone_submission"),
    ]

    operations = [
        migrations.DeleteModel(name="LidPhoneSubmission"),

        migrations.CreateModel(
            name="LidProject",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(
                    max_length=200,
                    help_text="Название проекта (станет именем листа в Excel).",
                )),
                ("business_date", models.DateField(
                    db_index=True,
                    help_text="Бизнес-дата (MSK, cutoff 11:00). По этой дате считаем «проект закрыт» = business_date < сегодняшняя.",
                )),
                ("customer", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="lid_projects_as_customer",
                    to=settings.AUTH_USER_MODEL,
                    help_text="Клиент-заказчик (role=lid_customer).",
                )),
            ],
            options={
                "verbose_name": "Проект (завод-лидов)",
                "verbose_name_plural": "Проекты (завод-лидов)",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="lidproject",
            index=models.Index(
                fields=["customer", "business_date"],
                name="core_lidpro_custome_a9414c_idx",
            ),
        ),

        migrations.CreateModel(
            name="LidProjectItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("value", models.CharField(db_index=True, max_length=500)),
                ("is_admin", models.BooleanField(
                    db_index=True, default=False,
                    help_text="False = значение от клиента, True = ответ от админа.",
                )),
                ("project", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="items",
                    to="core.lidproject",
                )),
                ("submitter", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="lid_project_items_submitted",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "verbose_name": "Значение проекта (завод-лидов)",
                "verbose_name_plural": "Значения проекта (завод-лидов)",
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="lidprojectitem",
            index=models.Index(
                fields=["project", "is_admin"],
                name="core_lidpro_project_02d9f2_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="lidprojectitem",
            constraint=models.UniqueConstraint(
                fields=["project", "is_admin", "value"],
                name="uniq_lid_item_per_project_side",
            ),
        ),
    ]
