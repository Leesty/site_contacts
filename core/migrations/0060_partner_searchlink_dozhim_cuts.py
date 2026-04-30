"""Партнёрам — настраиваемые доли с SearchLink (150 ₽) и дожим (40 ₽).

- partner_searchlink_cut (default 30) — сколько партнёр забирает с SearchLink-отчёта
  реферала (реф получает 150 - 30 = 120).
- partner_dozhim_cut (default 10) — сколько партнёр забирает с дожим-лида реферала
  (реф получает 40 - 10 = 30).

Поля общие для модели User, но реально используются только для role=partner.
Дефолты применяются ко всем строкам — для не-партнёров значения игнорируются.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0059_partnerlink_searchlink_cut"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="partner_searchlink_cut",
            field=models.PositiveIntegerField(
                default=30,
                help_text="Доля партнёра (руб.) с одобренного SearchLink-отчёта реферала. Default 30 (реф получает SEARCH_REPORT_REWARD - 30 = 120). Применяется ко всем рефералам сразу.",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="partner_dozhim_cut",
            field=models.PositiveIntegerField(
                default=10,
                help_text="Доля партнёра (руб.) с одобренного дожим-лида реферала. Default 10 (реф получает DOZHIM_APPROVE_REWARD - 10 = 30). Применяется ко всем рефералам сразу.",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="partner_rate",
            field=models.PositiveIntegerField(
                default=10,
                help_text="(Legacy) Ставка партнёра (руб.) за каждый одобренный лид реферала. Используется только для старой Lead-системы.",
            ),
        ),
    ]
