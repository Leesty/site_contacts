import os
from pathlib import Path

from dotenv import load_dotenv


# Load environment variables from .env (project root or server path)
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(os.getenv("WEB_DOTENV_PATH", BASE_DIR.parent / ".env"))


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-in-prod")

DEBUG = os.getenv("DJANGO_DEBUG", "True") == "True"

ALLOWED_HOSTS: list[str] = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# Истоки, с которых разрешены POST-запросы (HTTPS за прокси). Иначе при регистрации/логине — 403 CSRF.
_origins = []
for _h in ALLOWED_HOSTS:
    if _h in ("localhost", "127.0.0.1"):
        _origins.append(f"http://{_h}")
    else:
        _origins.extend((f"https://{_h}", f"http://{_h}"))
CSRF_TRUSTED_ORIGINS = _origins


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Проектные приложения
    "core",
]

# Кастомная модель пользователя (см. core.models.User)
AUTH_USER_MODEL = "core.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "base_site.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "base_site.wsgi.application"


# Database: PostgreSQL (настраивается через переменные окружения)
_db_host = os.getenv("DB_HOST", "127.0.0.1")
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "basebot"),
        "USER": os.getenv("DB_USER", "basebot_user"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": _db_host,
        "PORT": os.getenv("DB_PORT", "5432"),
        # Повторное использование соединения (секунды). Сильно ускоряет при удалённой БД.
        "CONN_MAX_AGE": 300,
    }
}
# SSL для подключения к БД по публичному хосту (например Timeweb *.twc1.net)
if ".twc1.net" in _db_host or _db_host not in ("127.0.0.1", "localhost"):
    DATABASES["default"]["OPTIONS"] = {"sslmode": "require"}


AUTH_PASSWORD_VALIDATORS: list[dict] = []


LANGUAGE_CODE = "ru-ru"

TIME_ZONE = "Europe/Moscow"

USE_I18N = True
USE_TZ = True


STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Медиафайлы: при наличии бакета и ключей в окружении — только S3 (Timeweb). Иначе — из админки. Локально не сохраняем.
_s3_bucket = os.getenv("AWS_STORAGE_BUCKET_NAME", "").strip()
_s3_access = os.getenv("AWS_ACCESS_KEY_ID", "").strip()
_s3_secret = os.getenv("AWS_SECRET_ACCESS_KEY", "").strip()
_s3_region = os.getenv("AWS_S3_REGION_NAME", "ru-1").strip() or "ru-1"
_s3_endpoint = os.getenv("AWS_S3_ENDPOINT_URL", "").strip().rstrip("/")

USE_S3_MEDIA_ENV = (
    os.getenv("USE_S3_MEDIA", "").strip().lower() in ("1", "true", "yes")
    or bool(_s3_bucket and _s3_access and _s3_secret)
)

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

if USE_S3_MEDIA_ENV and _s3_bucket and _s3_access and _s3_secret:
    # S3 напрямую из переменных окружения (Timeweb Cloud и др.): STORAGES + явные OPTIONS.
    # Timeweb: подпись AWS Signature V4, path-style URL (https://s3.twcstorage.ru/bucket/key).
    # Все медиа — в бакете под префиксом media/ (media/leads/user_5/..., media/support/...).
    _s3_options = {
        "access_key": _s3_access,
        "secret_key": _s3_secret,
        "bucket_name": _s3_bucket,
        "region_name": _s3_region,
        "signature_version": "s3v4",
        "addressing_style": "path",  # Timeweb: URL вида /bucket/key, не virtual-hosted
        "object_parameters": {"CacheControl": "max-age=86400"},
        "default_acl": None,
        "location": "media",
    }
    if _s3_endpoint:
        _s3_options["endpoint_url"] = _s3_endpoint
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": _s3_options,
        },
    }
    # Для обратной совместимости (если что-то читает DEFAULT_FILE_STORAGE)
    DEFAULT_FILE_STORAGE = "storages.backends.s3.S3Storage"
    AWS_ACCESS_KEY_ID = _s3_access
    AWS_SECRET_ACCESS_KEY = _s3_secret
    AWS_STORAGE_BUCKET_NAME = _s3_bucket
    AWS_S3_REGION_NAME = _s3_region
    if _s3_endpoint:
        AWS_S3_ENDPOINT_URL = _s3_endpoint
        AWS_S3_SIGNATURE_VERSION = "s3v4"
else:
    # Конфиг из БД: админка → «Настройки хранилища медиа (S3)».
    DEFAULT_FILE_STORAGE = "core.storage.ConfigurableMediaStorage"


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Настройки аутентификации
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "index"

# Минимальный баланс для кнопки «Запрос на вывод» (руб.)
WITHDRAWAL_MIN_BALANCE = int(os.getenv("WITHDRAWAL_MIN_BALANCE", "500"))

# Лимит загрузки файлов: вложения лидов (скрин/видео) до 30 МБ
_DATA_UPLOAD_MAX = 33 * 1024 * 1024  # 33 МБ, чтобы 30 МБ файл проходил
DATA_UPLOAD_MAX_MEMORY_SIZE = _DATA_UPLOAD_MAX
FILE_UPLOAD_MAX_MEMORY_SIZE = _DATA_UPLOAD_MAX

# Рекомендации для продакшена (см. SECURITY.md):
# - DEBUG = False, задать SECRET_KEY и ALLOWED_HOSTS из окружения
# - Включить валидаторы паролей: AUTH_PASSWORD_VALIDATORS с PasswordValidator
# - При HTTPS: SESSION_COOKIE_SECURE = True, CSRF_COOKIE_SECURE = True
