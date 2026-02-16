"""Хранилище медиа: только S3 из настроек в БД. Локальное сохранение не используется — при отсутствии/ошибке S3 операции падают с ошибкой."""
import logging
import time

from django.conf import settings
from django.core.files.storage import FileSystemStorage

logger = logging.getLogger(__name__)

# Кэш конфига из БД (ключ, чтобы сбросить при сохранении в админке)
_MEDIA_CONFIG_CACHE = {"config": None, "cache_until": 0}
CACHE_SECONDS = 300


def get_media_config_from_db():
    """Возвращает единственную запись MediaStorageConfig из БД (с кэшем). Не бросает исключений."""
    now = time.time()
    if _MEDIA_CONFIG_CACHE["config"] is not None and now < _MEDIA_CONFIG_CACHE["cache_until"]:
        return _MEDIA_CONFIG_CACHE["config"]
    try:
        from .models import MediaStorageConfig
        config = MediaStorageConfig.objects.filter(enabled=True).first()
        if not config or not config.bucket_name or not config.access_key_id or not config.secret_access_key:
            config = None
        _MEDIA_CONFIG_CACHE["config"] = config
        _MEDIA_CONFIG_CACHE["cache_until"] = now + CACHE_SECONDS
        return config
    except Exception as e:
        logger.warning(
            "MediaStorageConfig from DB failed. Выполните миграции? %s",
            e,
            exc_info=True,
        )
        _MEDIA_CONFIG_CACHE["config"] = None
        _MEDIA_CONFIG_CACHE["cache_until"] = now + 60
        return None


def clear_media_config_cache():
    """Сбросить кэш (вызвать после сохранения настроек в админке)."""
    _MEDIA_CONFIG_CACHE["config"] = None
    _MEDIA_CONFIG_CACHE["cache_until"] = 0


def get_media_storage_diagnostic():
    """
    Диагностика хранилища медиа: откуда берётся S3 (env или БД) и подключается ли он.
    Возвращает dict: source ("env" | "db" | "none"), bucket, endpoint, error (если есть).
    """
    from django.conf import settings as django_settings
    use_env = getattr(django_settings, "USE_S3_MEDIA_ENV", False)
    if use_env:
        bucket = getattr(django_settings, "AWS_STORAGE_BUCKET_NAME", "") or ""
        endpoint = getattr(django_settings, "AWS_S3_ENDPOINT_URL", "") or ""
        return {"source": "env", "bucket": bucket, "endpoint": endpoint, "error": None}
    config = get_media_config_from_db()
    if not config or not config.bucket_name or not config.access_key_id or not config.secret_access_key:
        return {
            "source": "db",
            "bucket": "",
            "endpoint": "",
            "error": "В админке не включён или не заполнен S3 (Core → Настройки хранилища медиа). Локальное сохранение отключено — загрузки будут падать с ошибкой.",
        }
    endpoint = (getattr(config, "endpoint_url", None) or "").strip().rstrip("/")
    try:
        from storages.backends.s3 import S3Storage
        opts = _build_s3_opts(config)
        S3Storage(**opts)
        return {"source": "db", "bucket": config.bucket_name, "endpoint": endpoint or "(по умолчанию)", "error": None}
    except Exception as e:
        return {"source": "db", "bucket": config.bucket_name, "endpoint": endpoint, "error": str(e)}


def _build_s3_opts(config):
    """Параметры для S3Storage: endpoint без слэша, s3v4, path-style (как в Timeweb: /bucket/key)."""
    endpoint = (getattr(config, "endpoint_url", None) or "").strip().rstrip("/")
    opts = {
        "access_key": config.access_key_id,
        "secret_key": config.secret_access_key,
        "bucket_name": config.bucket_name,
        "region_name": (config.region_name or "").strip() or "ru-1",
        "signature_version": "s3v4",
        "addressing_style": "path",
    }
    if endpoint:
        opts["endpoint_url"] = endpoint
    return opts


class ConfigurableMediaStorage(FileSystemStorage):
    """Storage: только S3 из БД. Если S3 включён в админке — используем только его, без fallback на локальный диск. Если S3 не настроен или ошибка — операции с файлами падают с ошибкой."""

    def __init__(self, **kwargs):
        super().__init__(location=kwargs.get("location", settings.MEDIA_ROOT), **kwargs)
        self._s3_backend = None
        self._use_s3 = None
        self._resolve_error = None  # сообщение ошибки при неудачной инициализации S3

    def _resolve_backend(self):
        if self._use_s3 is not None:
            if self._use_s3:
                return self._s3_backend
            raise RuntimeError(
                "Медиа только в S3. В админке включите и заполните «Настройки хранилища медиа (S3)» (бакет, ключи, endpoint). "
                "Локальное сохранение отключено."
            )
        config = get_media_config_from_db()
        if not config or not config.bucket_name or not config.access_key_id or not config.secret_access_key:
            self._use_s3 = False
            raise RuntimeError(
                "Медиа только в S3. В админке Django: Core → «Настройки хранилища медиа (S3)» → включите и заполните бакет, Access Key, Secret Key, Endpoint URL (например https://s3.twcstorage.ru)."
            )
        try:
            from storages.backends.s3 import S3Storage
            opts = _build_s3_opts(config)
            self._s3_backend = S3Storage(**opts)
            self._use_s3 = True
            logger.info(
                "Media storage: S3 включён, bucket=%s endpoint=%s",
                config.bucket_name,
                opts.get("endpoint_url", "default"),
            )
            return self._s3_backend
        except Exception as e:
            self._use_s3 = False
            self._resolve_error = str(e)
            logger.exception(
                "Media storage: не удалось подключиться к S3 (локальное сохранение отключено). bucket=%s endpoint=%s",
                config.bucket_name,
                getattr(config, "endpoint_url", ""),
            )
            raise RuntimeError(
                "Не удалось подключиться к S3. Проверьте в админке ключи и Endpoint URL (для Timeweb: https://s3.twcstorage.ru или https://s3.timeweb.cloud). Ошибка: %s"
                % e
            ) from e

    def _open(self, name, mode="rb"):
        backend = self._resolve_backend()
        return backend._open(name, mode)

    def _save(self, name, content):
        backend = self._resolve_backend()
        logger.info("Media storage: сохранение в S3: %s", name)
        return backend._save(name, content)

    def delete(self, name):
        return self._resolve_backend().delete(name)

    def exists(self, name):
        return self._resolve_backend().exists(name)

    def url(self, name):
        return self._resolve_backend().url(name)

    def path(self, name):
        backend = self._resolve_backend()
        if hasattr(backend, "path"):
            return backend.path(name)
        return None  # S3 — нет локального пути
