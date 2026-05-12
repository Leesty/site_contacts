"""Дефолтная ставка рефовода с GroupReport: 50 → 20.

Реферал теперь получает 60 ₽ из пула 80, рефовод/партнёр — 20 ₽.
Также массово выставляем 20 ₽ всем существующим рефоводам (`role=user`)
и партнёрам (`role=partner`). Каждый может потом изменить через UI.
"""

from django.db import migrations, models


def apply_new_default(apps, schema_editor):
    User = apps.get_model("core", "User")
    User.objects.update(
        partner_group_report_cut=20,
        ref_group_report_cut=20,
    )


def revert_old_default(apps, schema_editor):
    User = apps.get_model("core", "User")
    User.objects.update(
        partner_group_report_cut=50,
        ref_group_report_cut=50,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0075_searchreport_manual_unverified'),
    ]

    operations = [
        migrations.AlterField(
            model_name='user',
            name='partner_group_report_cut',
            field=models.PositiveIntegerField(
                default=20,
                help_text=('Доля партнёра (₽) с одобренного отчёта по группам реферала. '
                           'Default 20 — реферал получает 80-20=60. Применяется ко всем '
                           'рефералам этого партнёра.'),
            ),
        ),
        migrations.AlterField(
            model_name='user',
            name='ref_group_report_cut',
            field=models.PositiveIntegerField(
                default=20,
                help_text=('Доля рефовода (₽) с одобренного отчёта по группам реферала. '
                           'Реф получает 80 - ref_group_report_cut. По умолчанию 20 '
                           '(реф 60, рефовод 20).'),
            ),
        ),
        migrations.RunPython(apply_new_default, revert_old_default),
    ]
