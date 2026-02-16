"""
Проверка, куда сохраняются медиа (S3 или локально).
Запуск: python manage.py test_media_storage
На сервере запускайте после настройки S3 в админке — так вы увидите, попадают ли файлы в бакет.
"""
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from core.storage import get_media_config_from_db


class Command(BaseCommand):
    help = "Проверяет, используется ли S3 для медиа: сохраняет тестовый файл и читает обратно."

    def handle(self, *args, **options):
        from django.core.files.storage import default_storage
        config = get_media_config_from_db()
        if config and config.enabled and config.bucket_name:
            self.stdout.write(
                "В админке включён S3: бакет=%s endpoint=%s"
                % (config.bucket_name, getattr(config, "endpoint_url", "") or "(по умолчанию)")
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "В админке S3 не включён или не настроен. Локальное сохранение отключено — загрузки работают только через S3."
                )
            )
        test_name = "_test_media_storage_check.txt"
        test_content = ContentFile(b"test-s3-or-local")
        try:
            default_storage.save(test_name, test_content)
            self.stdout.write(self.style.SUCCESS("Файл сохранён в S3."))
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
            self.stdout.write(self.style.SUCCESS("Итог: S3 работает, вложения будут сохраняться в облако."))
        except RuntimeError as e:
            self.stdout.write(self.style.ERROR("Ошибка: %s" % e))
            if not (config and config.enabled):
                self.stdout.write(
                    self.style.WARNING("Включите и заполните «Настройки хранилища медиа (S3)» в админке (Core → Настройки хранилища медиа).")
                )
            return
        except Exception as e:
            self.stdout.write(self.style.ERROR("Ошибка при сохранении/чтении (S3 недоступен или неверные ключи/endpoint): %s" % e))
            import traceback
            traceback.print_exc()
            return
