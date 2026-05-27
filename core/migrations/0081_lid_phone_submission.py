"""LidPhoneSubmission — обмен номерами клиент ↔ главный админ.

Плюс data-migration: создаёт юзера `zavodlidov` (role=lid_customer, password=1111).
"""

import django.db.models.deletion
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations, models


def create_zavodlidov_user(apps, schema_editor):
    User = apps.get_model("core", "User")
    if User.objects.filter(username="zavodlidov").exists():
        return
    User.objects.create(
        username="zavodlidov",
        password=make_password("1111"),
        role="lid_customer",
        status="approved",
        is_active=True,
    )


def delete_zavodlidov_user(apps, schema_editor):
    User = apps.get_model("core", "User")
    User.objects.filter(username="zavodlidov").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0080_curator_account"),
    ]

    operations = [
        migrations.CreateModel(
            name="LidPhoneSubmission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("phone", models.CharField(db_index=True, max_length=32)),
                ("business_date", models.DateField(
                    db_index=True,
                    help_text="Бизнес-дата (MSK, cutoff 11:00). Все submissions с одной и той же business_date обрабатываются в одном Excel-листе.",
                )),
                ("is_admin", models.BooleanField(
                    db_index=True, default=False,
                    help_text="False = номер от клиента, True = номер-ответ от админа.",
                )),
                ("customer", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="lid_phones_as_customer",
                    to=settings.AUTH_USER_MODEL,
                    help_text="Клиент, к чьему счёту относится номер (role=lid_customer).",
                )),
                ("submitter", models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name="lid_phones_submitted",
                    to=settings.AUTH_USER_MODEL,
                    help_text="Кто реально вставил номер.",
                )),
            ],
            options={
                "verbose_name": "Номер обмена (завод-лидов)",
                "verbose_name_plural": "Номера обмена (завод-лидов)",
                "ordering": ["-business_date", "-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="lidphonesubmission",
            index=models.Index(
                fields=["customer", "business_date", "is_admin"],
                name="core_lidpho_custome_a346b4_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="lidphonesubmission",
            constraint=models.UniqueConstraint(
                fields=["customer", "business_date", "is_admin", "phone"],
                name="uniq_lid_phone_per_day_side",
            ),
        ),
        migrations.RunPython(create_zavodlidov_user, delete_zavodlidov_user),
    ]
