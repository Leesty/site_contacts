import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"

    def ready(self):
        from django.conf import settings
        if getattr(settings, "USE_S3_MEDIA_ENV", False) and getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""):
            logger.info(
                "Media storage: S3 (bucket=%s, endpoint=%s). Вложения лидов сохраняются в облако.",
                getattr(settings, "AWS_STORAGE_BUCKET_NAME", ""),
                getattr(settings, "AWS_S3_ENDPOINT_URL", "") or "default",
            )
        else:
            logger.info("Media storage: из админки (ConfigurableMediaStorage). Задайте AWS_* в окружении для S3.")

