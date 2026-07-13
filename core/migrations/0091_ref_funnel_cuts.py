from django.db import migrations, models


class Migration(migrations.Migration):
    """Доли рефовода за события реферала в новой воронке (созвон/сделка).
    Только AddField — накопленный дрейф других моделей намеренно не включён
    (см. 0089/0090)."""

    dependencies = [
        ('core', '0090_searchlink_funnel'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='ref_sozvon_cut',
            field=models.PositiveIntegerField(
                default=50,
                help_text='Доля рефовода (₽) с созвона реферала (из 150). Реф получает 150 - ref_sozvon_cut. По умолчанию 50 (реф 100, рефовод 50).',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='ref_deal_cut',
            field=models.PositiveIntegerField(
                default=1000,
                help_text='Доля рефовода (₽) со сделки реферала (из 4000). Реф получает 4000 - ref_deal_cut. По умолчанию 1000 (реф 3000, рефовод 1000).',
            ),
        ),
    ]
