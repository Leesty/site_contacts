# Gunicorn configuration for Timeweb Cloud
# Увеличенный таймаут для загрузки больших файлов (видео)

# Таймаут воркера в секундах (по умолчанию 30)
timeout = 120

# Количество воркеров (можно оставить по умолчанию или задать)
# workers = 2

# Таймаут на graceful shutdown
graceful_timeout = 30

# Логирование
accesslog = "-"
errorlog = "-"
loglevel = "info"
