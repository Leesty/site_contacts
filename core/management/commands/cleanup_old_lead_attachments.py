"""
Удаление файлов вложений лидов (фото и видео в отчётах) старше 30 дней для экономии места.

Запуск:
  python manage.py cleanup_old_lead_attachments
  python manage.py cleanup_old_lead_attachments --days 30 --dry-run  # только показать

Автозапуск (cron, раз в сутки в 03:00):
  0 3 * * * cd /path/to/web && python manage.py cleanup_old_lead_attachments
"""
from datetime import timedelta

from django.db.models import Q
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Lead


class Command(BaseCommand):
    help = "Удаляет вложения (фото и видео) лидов старше 30 дней."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Удалять вложения лидов старше N дней (по умолчанию 30).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что было бы удалено, без удаления.",
        )

    def handle(self, *args, **options):
        days = options["days"]
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(days=days)

        qs = Lead.objects.filter(created_at__lt=cutoff).exclude(
            Q(attachment="") | Q(attachment__isnull=True)
        )
        count = qs.count()

        if count == 0:
            self.stdout.write(
                self.style.SUCCESS(f"Лидов с вложениями старше {days} дн. не найдено.")
            )
            return

        if dry_run:
            self.stdout.write(
                f"Dry-run: было бы очищено вложений у {count} лидов (созданы до {cutoff:%Y-%m-%d %H:%M})."
            )
            return

        cleared = 0
        for lead in qs.iterator():
            try:
                lead.attachment.delete(save=True)
                cleared += 1
            except Exception as e:
                self.stderr.write(
                    self.style.WARNING(f"Лид {lead.pk}: не удалось удалить вложение: {e}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Удалены вложения у {cleared} из {count} лидов (старше {days} дн.)."
            )
        )
