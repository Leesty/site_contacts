"""Включить SearchLink для всех ранее обделённых рефералов + сменить дефолты модели.

- Поле `ref_searchlink_enabled`: default False → True.
- Поле `ref_searchlink_manager_cut`: default 30 → 50.
- Data migration: для всех рефов (role=user, partner_owner_id IS NOT NULL),
  у которых SearchLink был отключён, ставим enabled=true и cut=50.
  Тех, у кого SearchLink уже был включён, не трогаем — там partner мог
  сознательно выставить свою долю.
"""
from django.db import migrations, models


def enable_for_orphan_refs(apps, schema_editor):
    User = apps.get_model("core", "User")
    User.objects.filter(
        role="user",
        partner_owner_id__isnull=False,
        ref_searchlink_enabled=False,
    ).update(
        ref_searchlink_enabled=True,
        ref_searchlink_manager_cut=50,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0057_zvonok_incoming_polling"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="ref_searchlink_enabled",
            field=models.BooleanField(
                default=True,
                help_text="Разрешён ли SearchLink для реферала. По умолчанию включено для всех новых рефералов.",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="ref_searchlink_manager_cut",
            field=models.PositiveIntegerField(
                default=50,
                help_text="Доля менеджера-рефовладельца (руб.) с одобренного SearchLink-отчёта реферала. По умолчанию 50 (реферал получает SEARCH_REPORT_REWARD - 50).",
            ),
        ),
        migrations.RunPython(enable_for_orphan_refs, migrations.RunPython.noop),
    ]
