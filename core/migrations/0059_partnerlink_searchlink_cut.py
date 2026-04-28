"""PartnerLink: добавлено поле ref_searchlink_cut (доля рефовода с SearchLink-отчёта).

Старое ref_reward осталось для backward-compat — оно про legacy Lead-систему,
которая больше не используется менеджерами, но миграцию данных не делаем
чтобы не ломать исторические записи.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0058_searchlink_default_for_refs"),
    ]

    operations = [
        migrations.AddField(
            model_name="partnerlink",
            name="ref_searchlink_cut",
            field=models.PositiveIntegerField(
                default=50,
                help_text="Доля рефовода (руб.) с одобренного SearchLink-отчёта реферала. Реф получает SEARCH_REPORT_REWARD - ref_searchlink_cut. По умолчанию 50 (реф 100, рефовод 50 при награде 150).",
            ),
        ),
        migrations.AlterField(
            model_name="partnerlink",
            name="ref_reward",
            field=models.PositiveIntegerField(
                default=20,
                help_text="(Legacy) Ставка рефу за одобренный лид старой Lead-системы (руб.). Партнёр получает 40 - ref_reward.",
            ),
        ),
    ]
