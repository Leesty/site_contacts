"""Кросс-DB авто-матчинг unstarted SearchLink'ов с conversation'ами в боте.

Когда webhook от бота не дошёл до сайта (Telegram съел deeplink-параметр на каком-то
клиенте), conversation в боте всё равно создаётся. Эта команда добивает такие
ссылки: для каждого unstarted SearchLink ищет в windowgram.conversations нового
клиента у того же бота, созданного после ссылки.

Локально:
    python manage.py match_searchlinks

В проде вызывается автоматически из cron-эндпоинта /api/cron/poll-incoming-calls/.
"""
from django.core.management.base import BaseCommand

from core.views_search import auto_match_searchlinks_with_bot_convs


class Command(BaseCommand):
    help = "Авто-матчит unstarted SearchLink'и с conversation'ами в windowgram."

    def handle(self, *args, **options):
        s = auto_match_searchlinks_with_bot_convs()
        self.stdout.write(
            f"checked={s.get('checked', 0)} matched={s.get('matched', 0)} "
            f"skipped_no_convs={s.get('skipped_no_convs', 0)} errors={s.get('errors', 0)}"
        )
