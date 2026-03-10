# CLAUDE.md — Партнёрка (site_contacts)

## Быстрый старт

```bash
# Запуск dev-сервера
python manage.py runserver

# Миграции
python manage.py migrate

# Сбор статики
python manage.py collectstatic --noinput
```

## Стек

- **Python 3.10+**, **Django 4.2+**, **PostgreSQL** (psycopg2-binary)
- **S3** (Timeweb Cloud) для медиа-файлов через django-storages + boto3
- **WhiteNoise** для статики в production, **Gunicorn** как WSGI-сервер
- **openpyxl** — импорт/экспорт Excel, **Pillow** — изображения, **imageio-ffmpeg** — сжатие видео

## Структура проекта

```
base_site/          — Django-проект (settings.py, urls.py, wsgi.py)
core/               — Основное приложение
  models.py         — 22 модели (User, Lead, Contact, WorkerSelfLead и др.)
  views.py          — Пользовательские вьюхи (дашборд, лиды, контакты, баланс)
  views_support_admin.py — Админские вьюхи (модерация, статистика, базы)
  views_worker.py   — Вьюхи воркеров (задания, отчёты, самостоятельные лиды)
  views_partner.py  — Вьюхи партнёров (рефералы, заработок)
  forms.py          — Все формы (регистрация, лиды, Excel, воркеры)
  urls.py           — ~80 маршрутов
  lead_utils.py     — Нормализация контактов, проверка дубликатов, сжатие
  storage.py        — ConfigurableMediaStorage (S3)
  templatetags/support_extras.py — Кастомные фильтры шаблонов
templates/          — Django-шаблоны
  base.html         — Мастер-шаблон (навбар зависит от роли)
  core/             — Основные страницы и админ-панель
  worker/           — Кабинет воркера
  partner/          — Кабинет партнёра
  auth/             — Логин, регистрация
static/css/main.css — Основные стили (тёмная тема, мобильная адаптация)
```

## Роли пользователей

| Роль | Описание |
|------|----------|
| `user` | Обычный пользователь — отправляет лиды, получает контакты, выводит баланс |
| `support` | Поддержка — модерация лидов, ответы на тикеты |
| `admin` | Администратор — полный доступ, управление пользователями, загрузка баз |
| `standalone_admin` | СС-админ — управление своей командой воркеров, назначение задач |
| `balance_admin` | Финансовый админ — обработка выплат, управление балансами |
| `worker` | Исполнитель — выполняет задания от standalone_admin, шлёт самостоятельные лиды |
| `partner` | Партнёр — реферальные ссылки, 10 руб. за каждый одобренный лид реферала |

## Ключевые модели

- **User** — кастомная модель (AbstractUser) с role, status, balance, telegram_id, partner_owner, standalone_admin_owner
- **Lead** — лид от пользователя (статус: pending/approved/rejected/rework), normalized_contact для дубликатов
- **Contact / BaseType** — контактные базы с лимитами выдачи в день
- **WorkerSelfLead** — самостоятельный лид воркера (отдельная таблица от Lead)
- **LeadAssignment / WorkerReport** — задания и отчёты воркеров
- **SupportThread / SupportMessage** — система поддержки
- **WithdrawalRequest / WorkerWithdrawalRequest** — заявки на вывод

## Проверка дубликатов лидов

Функция `_lead_exists_globally()` в `views.py` — нормализует контакт и проверяет кросс-платформенно (telegram/vk/ig/ok). Аналогичная `_self_lead_duplicate_exists()` в `views_worker.py` для самостоятельных лидов воркеров.

## Маршруты (основные группы)

- `/` — Лендинг
- `/dashboard/` — Дашборд (роль определяет контент)
- `/leads/report/` — Отправка лида
- `/staff/*` — Админ-панель (модерация, пользователи, базы, статистика)
- `/staff/standalone/*` — Панель СС-админа (воркеры, задания, отчёты)
- `/worker/*` — Кабинет воркера
- `/partner/*` — Кабинет партнёра
- `/support/*` — Поддержка
- `/admin/` — Django admin (is_staff=True)

## База данных

PostgreSQL. Конфигурация через `.env`:
- `DB_HOST`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_PORT`
- SSL автоматически для удалённых хостов

## Медиа-файлы

S3 (Timeweb Cloud). Конфигурация через `.env` (USE_S3_MEDIA, AWS_*) или через Django admin (MediaStorageConfig).

## Git

- Репозиторий: `https://github.com/Leesty/site_contacts.git`
- Основная ветка: `main`
- Рабочие ветки через worktree: `.claude/worktrees/`

## Стиль кода

- Язык интерфейса и комментариев: **русский**
- Код и переменные: **английский**
- Отступы: 4 пробела (Python), 2 пробела (HTML-шаблоны)
- Коммиты на английском (conventional commits: feat/fix/refactor)
- После изменений — **всегда мержить в main и пушить** (`git push origin main`)
