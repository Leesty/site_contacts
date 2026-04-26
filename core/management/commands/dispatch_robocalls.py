"""Management-команда для крона: запускает отложенные звонки zvonok.

Крон на боте-сервере 72.56.24.65 раз в минуту делает:
    curl -s 'https://rupartnerka.ru/api/cron/dispatch-robocalls/?secret=XXX'

Эта команда для локального запуска / тестов:
    python manage.py dispatch_robocalls
"""
from django.core.management.base import BaseCommand

from core.robocall import dispatch_pending_attempts, poll_call_results


class Command(BaseCommand):
    help = "Запускает отложенные robocall-attempts (Stage 2 и 3) + поллит результаты zvonok."

    def handle(self, *args, **options):
        d = dispatch_pending_attempts()
        p = poll_call_results()
        self.stdout.write(
            f"Dispatch: checked={d['checked']} fired={d['fired']} "
            f"skipped={d['skipped']} errors={d['errors']}"
        )
        self.stdout.write(
            f"Poll: polled={p['polled']} pressed={p['pressed']} "
            f"skipped={p['skipped']} errors={p['errors']}"
        )
