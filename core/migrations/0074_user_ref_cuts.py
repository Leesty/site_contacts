"""Общие ставки рефовода (role=user) с SearchLink и GroupReport.

Аналогично partner_searchlink_cut/partner_group_report_cut у партнёров —
теперь обычный рефовод задаёт ОДНУ общую ставку на всех своих рефералов
(а не per-link / per-referral как раньше)."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0073_manual_claim_search_link'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='ref_searchlink_cut',
            field=models.PositiveIntegerField(
                default=50,
                help_text=('Доля рефовода (₽) с одобренного SearchLink-отчёта реферала. '
                           'Реф получает SEARCH_REPORT_REWARD - ref_searchlink_cut. По умолчанию 50.'),
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='ref_group_report_cut',
            field=models.PositiveIntegerField(
                default=50,
                help_text=('Доля рефовода (₽) с одобренного отчёта по группам реферала. '
                           'Реф получает 80 - ref_group_report_cut. По умолчанию 50.'),
            ),
        ),
    ]
