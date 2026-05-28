"""HTTP-обёртка для общения Django ↔ windowgram (https://murzzvon.ru).

Используется в воронке холодных контактов:
  1) ensure_manager(user)   — при первой фиксации лида авто-регистрирует Django
     юзера как ManagerUser на windowgram (с is_approved=True через Bearer-auth
     auto-register endpoint).
  2) login_manager(user)    — получает свежий JWT (на лету, не кешируем).
  3) create_chat(jwt, ...)  — создаёт TG-чат через invite-pool.
  4) send_summary(...)      — Bearer-auth, шлёт сводку (Номер/Дата/Время)
     от notify_bot в чат.
  5) validate_chat(chat_id) — Bearer-auth, проверяет:
        • есть ли админ (artem_tele2 / shaneli77) в чате
        • зашёл ли клиент

Все вызовы — синхронные через requests, с короткими таймаутами и graceful-fail.
"""

from __future__ import annotations

import logging
import secrets

import requests

logger = logging.getLogger(__name__)


WINDOWGRAM_BASE_URL = "https://murzzvon.ru"
WINDOWGRAM_API_KEY = "p9EMWO1uPz75wFTEh2JS0Vo2oYtdAeyOs0veeH9FVu8"

DEFAULT_TIMEOUT = 30  # секунд (создание чата может идти 10-20с)


class WindowgramError(Exception):
    """Любая проблема при общении с windowgram. message можно показать менеджеру."""


def _bearer_headers() -> dict:
    return {"Authorization": f"Bearer {WINDOWGRAM_API_KEY}"}


def _bridge_login_for_user(user) -> str:
    """Уникальный manager-login на стороне windowgram для Django-юзера.

    Формат `site_<id>` — стабильный, ≥6 символов, не зависит от того, какой
    username юзер ввёл при регистрации (юзеры с username '5' падали с
    string_too_short в Pydantic-валидаторе windowgram).
    """
    return f"site_{user.id}"


def _manager_login_for_user(user) -> str:
    """Внутренний хелпер: логинимся под manager-аккаунтом пользователя, возвращаем JWT.

    Сценарий: при первом вызове `ensure_manager` сохраняет в User.windowgram_manager_*
    логин (site_<id>) и сгенерированный пароль. Здесь — просто берём их.
    """
    login = _bridge_login_for_user(user)
    password = user.windowgram_manager_password
    if not password:
        raise WindowgramError(
            "У вашего аккаунта не настроена связка с CRM. "
            "Попробуйте ещё раз — мы создадим её автоматически."
        )
    try:
        r = requests.post(
            f"{WINDOWGRAM_BASE_URL}/api/manager/login",
            json={"login": login, "password": password},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise WindowgramError(f"Бот-сервер недоступен: {exc}")
    if r.status_code != 200:
        raise WindowgramError(f"Логин в CRM не удался ({r.status_code}): {r.text[:200]}")
    data = r.json() or {}
    token = data.get("token")
    if not token:
        raise WindowgramError("CRM не вернул JWT-токен.")
    return token


def ensure_manager(user) -> None:
    """Идемпотентно гарантирует, что у Django-юзера есть ManagerUser на windowgram.

    Если у user уже заполнены windowgram_manager_id+password → пропускаем.
    Иначе генерим пароль, вызываем POST /api/managers/auto-register
    (новый Bearer-auth endpoint, см. windowgram), сохраняем UUID+password.
    """
    if user.windowgram_manager_id and user.windowgram_manager_password:
        return

    password = user.windowgram_manager_password or secrets.token_urlsafe(24)
    login = _bridge_login_for_user(user)

    try:
        r = requests.post(
            f"{WINDOWGRAM_BASE_URL}/api/managers/auto-register",
            headers=_bearer_headers(),
            json={
                "login": login,
                "password": password,
                "display_name": user.get_full_name() or user.username,
            },
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise WindowgramError(f"Бот-сервер недоступен: {exc}")
    if r.status_code != 200:
        raise WindowgramError(f"Авто-регистрация в CRM не удалась ({r.status_code}): {r.text[:200]}")

    data = r.json() or {}
    mgr_id = data.get("manager_id") or data.get("id")
    if not mgr_id:
        raise WindowgramError("CRM не вернул manager_id.")
    user.windowgram_manager_id = str(mgr_id)
    user.windowgram_manager_password = password
    user.save(update_fields=["windowgram_manager_id", "windowgram_manager_password"])


def create_chat(user, title: str) -> dict:
    """Создаёт TG-чат через invite-pool. Возвращает {chat_id, invite_link, title, ...}.

    Внутри: ensure_manager → login → create-chat. На каждое создание новый login.
    """
    ensure_manager(user)
    jwt = _manager_login_for_user(user)
    try:
        r = requests.post(
            f"{WINDOWGRAM_BASE_URL}/api/manager/create-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={"title": title[:128], "purpose": "cold_contact"},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise WindowgramError(f"Бот-сервер не ответил: {exc}")

    if r.status_code == 429:
        # rate-limit: сообщаем менеджеру нормально
        try:
            detail = r.json().get("detail") or r.text
        except Exception:
            detail = r.text
        raise WindowgramError(f"Превышен лимит создания чатов: {detail}")
    if r.status_code != 200:
        raise WindowgramError(f"CRM отказал в создании чата ({r.status_code}): {r.text[:300]}")

    data = r.json() or {}
    if not data.get("chat_id"):
        raise WindowgramError("CRM не вернул chat_id.")
    return data


def send_summary(chat_id: int, phone: str, date_str: str, time_str: str) -> None:
    """Отправляет в чат сводку через notify_bot. Только для чатов
    с purpose='cold_contact' (валидируется на стороне windowgram).
    """
    try:
        r = requests.post(
            f"{WINDOWGRAM_BASE_URL}/api/manager/chats/{chat_id}/send-summary",
            headers=_bearer_headers(),
            json={"phone": phone, "date": date_str, "time": time_str},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("send_summary failed for chat %s: %s", chat_id, exc)
        return  # не валим основной поток
    if r.status_code != 200:
        logger.warning(
            "send_summary chat=%s returned %s: %s", chat_id, r.status_code, r.text[:200],
        )


def validate_chat(chat_id: int) -> tuple[bool, str]:
    """Проверка состояния чата на windowgram-стороне.

    is_complete=True требует выполнения ВСЕХ 4 условий:
      • admin_in_chat — артем/володя в чате (artem_invited=True)
      • client_joined — клиент зашёл (client_platform_user_id/username заполнен)
      • offer_done — был выслан оффер (бот зарегистрировал /оффер)
      • sozvon_done — был назначен созвон (бот зарегистрировал /созвон)

    Возвращает (is_complete, note) — note человекочитаемая причина
    если хотя бы одно условие не выполнено.
    """
    try:
        r = requests.get(
            f"{WINDOWGRAM_BASE_URL}/api/manager/chats/{chat_id}/validation",
            headers=_bearer_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        return False, f"Бот-сервер недоступен: {exc}"
    if r.status_code == 404:
        return False, "Чат не найден в CRM."
    if r.status_code != 200:
        return False, f"CRM вернул {r.status_code}: {r.text[:200]}"

    data = r.json() or {}
    admin_in_chat = bool(data.get("admin_in_chat"))
    client_joined = bool(data.get("client_joined"))
    offer_done = bool(data.get("offer_done"))
    sozvon_done = bool(data.get("sozvon_done"))

    if admin_in_chat and client_joined and offer_done and sozvon_done:
        return True, "Все 4 этапа пройдены: админ и клиент в чате, оффер и созвон зарегистрированы."

    missing = []
    if not admin_in_chat:
        missing.append("админ ещё не в чате")
    if not client_joined:
        missing.append("клиент ещё не зашёл")
    if not offer_done:
        missing.append("/оффер не выполнен")
    if not sozvon_done:
        missing.append("/созвон не выполнен")
    return False, "; ".join(missing) + "."


# ─── Хелпер: формирование заголовка чата ─────────────────────────────────

def format_chat_title(name: str, phone: str) -> str:
    """Никита и партнеры (+79036700374) / Партнеры (+79036700374)."""
    name = (name or "").strip()
    phone = (phone or "").strip()
    base = f"{name} и партнеры" if name else "Партнеры"
    if phone:
        return f"{base} ({phone})"
    return base
