"""
Проверка, куда сохраняются медиа (S3 или локально).
Запуск: python manage.py test_media_storage
"""
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from core.storage import get_media_config_from_db


class Command(BaseCommand):
    help = "Проверяет, используется ли S3 для медиа: сохраняет тестовый файл и читает обратно."

    def handle(self, *args, **options):
        from django.core.files.storage import default_storage
        test_name = "_test_media_storage_check.txt"
        test_content = ContentFile(b"test-s3-or-local")
        try:
            default_storage.save(test_name, test_content)
            self.stdout.write(self.style.SUCCESS("Файл сохранён через default_storage."))
            if default_storage.exists(test_name):
                with default_storage.open(test_name, "rb") as f:
                    data = f.read()
                if data == b"test-s3-or-local":
                    self.stdout.write(self.style.SUCCESS("Файл прочитан обратно — содержимое совпадает."))
                else:
                    self.stdout.write(self.style.WARNING("Файл прочитан, но содержимое не совпадает."))
            else:
                self.stdout.write(self.style.ERROR("Файл не найден после сохранения."))
            default_storage.delete(test_name)
        except Exception as e:
            self.stdout.write(self.style.ERROR("Ошибка: %s" % e))
            import traceback
            traceback.print_exc()
            return
        config = get_media_config_from_db()
        if config and config.enabled:
            self.stdout.write(
                self.style.SUCCESS(
                    "Включён S3 из настроек в БД (бакет %s). Выше сохранение/чтение прошли через выбранный бэкенд."
                    % config.bucket_name
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "S3 из БД не включён или не настроен — медиа сохраняются в локальную папку media/."
                )
            )
