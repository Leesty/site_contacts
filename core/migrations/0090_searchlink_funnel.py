from django.db import migrations, models


class Migration(migrations.Migration):
    """Поля воронки windowgram на SearchLink (2026-07): стадия + идемпотентные
    начисления (созвон/сделка). Только AddField — накопленный дрейф других
    моделей намеренно не включён (см. 0089)."""

    dependencies = [
        ('core', '0089_legacy_balance'),
    ]

    operations = [
        migrations.AddField(
            model_name='searchlink',
            name='funnel_stage',
            field=models.PositiveSmallIntegerField(
                default=0, db_index=True,
                help_text='Стадия воронки: 0 ждём бота, 1 бот, 2 чат, 3 созвон, 4 сделка. Обновляется синхронизатором из windowgram.',
            ),
        ),
        migrations.AddField(
            model_name='searchlink',
            name='chat_created',
            field=models.BooleanField(
                default=False,
                help_text='Под клиента создан групповой чат в windowgram (conversations.group_chat_id).',
            ),
        ),
        migrations.AddField(
            model_name='searchlink',
            name='wg_conversation_id',
            field=models.UUIDField(null=True, blank=True, help_text='Сматченный conversation в windowgram (для чтения статуса).'),
        ),
        migrations.AddField(
            model_name='searchlink',
            name='wg_status',
            field=models.CharField(max_length=32, blank=True, default='', help_text='Последний известный CRM-статус клиента в windowgram (кэш для отображения).'),
        ),
        migrations.AddField(
            model_name='searchlink',
            name='sozvon_credited_at',
            field=models.DateTimeField(null=True, blank=True, help_text='Когда начислен «созвон» (150 ₽). NULL = ещё не начислен.'),
        ),
        migrations.AddField(
            model_name='searchlink',
            name='deal_credited_at',
            field=models.DateTimeField(null=True, blank=True, help_text='Когда начислена «успешная сделка» (до 4000 ₽). NULL = ещё не начислен.'),
        ),
    ]
