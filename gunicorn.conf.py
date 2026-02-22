# Gunicorn configuration for Timeweb Cloud

# Таймаут воркера в секундах (для загрузки больших файлов)
timeout = 120

# Количество воркеров (2-4 для небольших проектов)
workers = 2

# Тип воркеров (sync для стабильности)
worker_class = "sync"

# Таймаут на graceful shutdown
graceful_timeout = 30

# Перезапуск воркеров после N запросов (предотвращает утечки памяти)
max_requests = 500
max_requests_jitter = 50

# Preload приложения для экономии памяти
preload_app = True

# Keep-alive соединения
keepalive = 5

# Логирование
accesslog = "-"
errorlog = "-"
loglevel = "info"
