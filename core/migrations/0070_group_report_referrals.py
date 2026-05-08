"""Реф-система для GroupReport.

- User.partner_group_report_cut — общая ставка партнёра (role=partner) с
  одобренного GR реферала (default 50 → реф получает 30).
- PartnerLink.ref_group_report_cut — per-link ставка обычного рефовода
  (role=user) с одобренного GR реферала (default 50).
- PartnerEarning.group_report — FK на одобренный GR (для аудита,
  по аналогии с lead/search_report).
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0069_user_bot_admin_dual_platform'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='partner_group_report_cut',
            field=models.PositiveIntegerField(
                default=50,
                help_text=('Доля партнёра (₽) с одобренного отчёта по группам реферала. '
                           'Default 50 — реферал получает 80-50=30. Применяется ко всем '
                           'рефералам этого партнёра.'),
            ),
        ),
        migrations.AddField(
            model_name='partnerlink',
            name='ref_group_report_cut',
            field=models.PositiveIntegerField(
                default=50,
                help_text=('Доля рефовода (₽) с одобренного отчёта по группам реферала. '
                           'Реф получает GROUP_REPORT_APPROVE_REWARD - ref_group_report_cut. '
                           'По умолчанию 50 (реф 30, рефовод 50).'),
            ),
        ),
        migrations.AddField(
            model_name='partnerearning',
            name='group_report',
            field=models.OneToOneField(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='partner_earning',
                to='core.groupreport',
            ),
        ),
    ]
