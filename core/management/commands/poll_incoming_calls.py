"""Cron-команда: опрашивает zvonok.com на предмет входящих звонков от клиентов.

Вызывается раз в час с бот-сервера 72.56.24.65:
    curl -s 'https://rupartnerka.ru/api/cron/poll-incoming-calls/?secret=XXX'

Локально:
    python manage.py poll_incoming_calls
"""
from django.core.management.base import BaseCommand

from core.robocall import poll_incoming_calls


class Command(BaseCommand):
    help = "Опрашивает zvonok.com по client_phone каждого pending_callback отчёта."

    def handle(self, *args, **options):
        s = poll_incoming_calls()
        self.stdout.write(
            f"Poll: checked={s['checked']} confirmed={s['confirmed']} "
            f"no_call={s['no_call']} errors={s['errors']} "
            f"skipped_throttle={s['skipped_throttle']}"
        )
        if s.get("skip_reason"):
            self.stdout.write(self.style.WARNING(f"Skipped entirely: {s['skip_reason']}"))
