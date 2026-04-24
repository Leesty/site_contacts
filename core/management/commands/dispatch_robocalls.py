"""Management-команда для крона: запускает отложенные звонки zvonok.

Крон на боте-сервере 72.56.24.65 раз в минуту делает:
    curl -s 'https://rupartnerka.ru/api/cron/dispatch-robocalls/?secret=XXX'

Эта команда для локального запуска / тестов:
    python manage.py dispatch_robocalls
"""
from django.core.management.base import BaseCommand

from core.robocall import dispatch_pending_attempts


class Command(BaseCommand):
    help = "Запускает отложенные robocall-attempts (Stage 2 и 3), когда их scheduled_at наступил."

    def handle(self, *args, **options):
        summary = dispatch_pending_attempts()
        self.stdout.write(
            f"Robocall dispatch: checked={summary['checked']} fired={summary['fired']} "
            f"skipped={summary['skipped']} errors={summary['errors']}"
        )
