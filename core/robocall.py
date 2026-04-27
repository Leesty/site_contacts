"""Pull-модель zvonok.com: опрашиваем входящую кампанию по номерам клиентов.

Новый флоу (с 2026-04-27):
- Менеджер подаёт SearchReport с типом phone_callback и номером клиента.
- Клиент звонит на один из наших 5 номеров (привязаны к входящей кампании zvonok).
- Раз в час cron дёргает /api/cron/poll-incoming-calls/ (на бот-сервере).
- Поллер для каждого pending_callback отчёта проверяет zvonok API:
    GET /phones/calls_by_phone/?campaign_id=<incoming>&phone=<client_phone>
  и если найден звонок с нажатой «1» — подтверждает отчёт.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import urllib.parse
import urllib.request
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)

CALLS_BY_PHONE_URL = "https://zvonok.com/manager/cabapi_external/api/v1/phones/calls_by_phone/"
# Опрос пропускается, если отчёт опрашивали меньше чем POLL_THROTTLE назад
POLL_THROTTLE = timedelta(minutes=55)
# Максимум отчётов за один cron-проход (защита от долгих run'ов)
POLL_BATCH_SIZE = 200


def normalize_phone(phone: str) -> str | None:
    """Приводит номер к международному формату +7XXXXXXXXXX (или None если битый)."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return None
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) == 10 and digits[0] == "9":
        digits = "7" + digits
    if len(digits) < 10 or len(digits) > 15:
        return None
    return "+" + digits


def _fetch_calls_by_phone(public_key: str, campaign_id: str, phone: str) -> tuple[int, list | dict | None, str]:
    """GET /phones/calls_by_phone/. Возвращает (status_code, parsed_json_or_None, raw_body)."""
    qs = urllib.parse.urlencode({
        "public_key": public_key,
        "campaign_id": campaign_id,
        "phone": phone,
    })
    url = f"{CALLS_BY_PHONE_URL}?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        code = e.code
    except Exception as e:
        return 0, None, str(e)

    try:
        parsed = json.loads(body)
    except Exception:
        parsed = None
    return code, parsed, body


def _extract_button_pressed(calls: list) -> tuple[bool, str]:
    """Из списка звонков ищет звонок с нажатой «1». Возвращает (pressed, call_id)."""
    for c in calls:
        if not isinstance(c, dict):
            continue
        bn = c.get("button_num")
        uc = c.get("user_choice")
        for v in (bn, uc):
            if v is None:
                continue
            try:
                if int(str(v).strip()) == 1:
                    return True, str(c.get("call_id") or "")
            except (TypeError, ValueError):
                continue
    return False, ""


def poll_incoming_calls() -> dict:
    """Опрашивает zvonok для всех pending_callback отчётов и подтверждает их.

    Возвращает сводку: {checked, confirmed, no_call, errors, skipped_throttle}.
    """
    from .models import SearchReport, SiteSettings

    st = SiteSettings.get_settings()
    if not st.zvonok_public_key:
        return {"checked": 0, "confirmed": 0, "no_call": 0, "errors": 0, "skipped_throttle": 0, "skip_reason": "no_public_key"}
    campaign_id = (st.zvonok_incoming_campaign_id or "").strip()
    if not campaign_id:
        return {"checked": 0, "confirmed": 0, "no_call": 0, "errors": 0, "skipped_throttle": 0, "skip_reason": "no_incoming_campaign_id"}

    now = timezone.now()
    cutoff = now - POLL_THROTTLE

    qs = (
        SearchReport.objects
        .filter(
            report_type=SearchReport.ReportType.PHONE_CALLBACK,
            status=SearchReport.Status.PENDING_CALLBACK,
            callback_confirmed_at__isnull=True,
        )
        .exclude(client_phone="")
        .order_by("zvonok_last_polled_at", "id")
    )

    checked = confirmed = no_call = errors = skipped_throttle = 0
    for report in qs[:POLL_BATCH_SIZE]:
        if report.zvonok_last_polled_at and report.zvonok_last_polled_at > cutoff:
            skipped_throttle += 1
            continue

        phone = normalize_phone(report.client_phone) or report.client_phone
        try:
            code, parsed, raw = _fetch_calls_by_phone(st.zvonok_public_key, campaign_id, phone)
        except Exception as e:
            logger.exception("poll_incoming_calls: report=%s phone=%s exception: %s", report.pk, phone, e)
            errors += 1
            continue

        report.zvonok_last_polled_at = now
        update_fields = ["zvonok_last_polled_at", "updated_at"]

        # «Phone doesn't exist» — клиент ещё не звонил. Это норма, не ошибка.
        if isinstance(parsed, dict) and parsed.get("status") == "error":
            no_call += 1
            report.save(update_fields=update_fields)
            checked += 1
            continue

        if not isinstance(parsed, list):
            logger.info("poll_incoming_calls: unexpected response report=%s code=%s body=%s", report.pk, code, raw[:200])
            errors += 1
            report.save(update_fields=update_fields)
            continue

        pressed, call_id = _extract_button_pressed(parsed)
        if pressed:
            report.callback_confirmed_at = now
            report.zvonok_call_id = call_id[:64]
            if report.status == SearchReport.Status.PENDING_CALLBACK:
                report.status = SearchReport.Status.PENDING
            update_fields.extend(["callback_confirmed_at", "zvonok_call_id", "status"])
            confirmed += 1
            logger.info("poll_incoming_calls: report=%s phone=%s CONFIRMED via call_id=%s", report.pk, phone, call_id)
        else:
            no_call += 1

        report.save(update_fields=update_fields)
        checked += 1

    return {
        "checked": checked,
        "confirmed": confirmed,
        "no_call": no_call,
        "errors": errors,
        "skipped_throttle": skipped_throttle,
    }


def get_or_create_webhook_secret() -> str:
    """Получает текущий секрет cron-эндпоинта; если не задан — генерирует."""
    from .models import SiteSettings

    st = SiteSettings.get_settings()
    if not st.zvonok_webhook_secret:
        st.zvonok_webhook_secret = secrets.token_urlsafe(24)
        st.save(update_fields=["zvonok_webhook_secret"])
    return st.zvonok_webhook_secret
