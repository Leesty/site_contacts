from django.db import migrations, models


class Migration(migrations.Migration):
    """Фи varvara за создание чата (10 ₽) — поле идемпотентности.
    Только AddField (накопленный дрейф других моделей намеренно не включён)."""

    dependencies = [
        ('core', '0091_ref_funnel_cuts'),
    ]

    operations = [
        migrations.AddField(
            model_name='searchlink',
            name='chat_credited_at',
            field=models.DateTimeField(
                null=True, blank=True,
                help_text='Когда начислено фи varvara за создание чата (10 ₽, только у менеджеров без рефовода). NULL = ещё не начислено.',
            ),
        ),
    ]
