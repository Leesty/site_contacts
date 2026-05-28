# Право на воронку холодных контактов + отчёты «Прозвон»
# (выдаётся главным админом + регистрирует подадмина в windowgram).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0085_call_reports"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="can_create_call_reports",
            field=models.BooleanField(
                default=False,
                db_index=True,
                help_text="Может ли менеджер вести списки контактов и сдавать отчёты «Прозвон».",
            ),
        ),
    ]
