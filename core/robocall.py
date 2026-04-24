"""Интеграция с zvonok.com: запуск роботизированных звонков в рамках phone_callback SearchReport'ов."""
from __future__ import annotations

import json
import logging
import re
import secrets
import urllib.parse
import urllib.request
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

ZVONOK_API_URL = "https://zvonok.com/manager/cabapi_external/api/v1/phones/call/"
# Если scheduled_at улетел в прошлое больше чем на это значение — пропускаем звонок
SKIP_GRACE_SECONDS = 5 * 60  # 5 минут


def normalize_phone(phone: str) -> str | None:
    """Приводит номер к международному формату +7XXXXXXXXXX (возвращает None если невалидный)."""
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


def _call_zvonok(public_key: str, campaign_id: str, phone: str) -> tuple[int, str]:
    """Дёргает zvonok API /phones/call/. Возвращает (status_code, body_str)."""
    data = urllib.parse.urlencode({
        "public_key": public_key,
        "phone": phone,
        "campaign_id": campaign_id,
    }).encode("utf-8")
    req = urllib.request.Request(
        ZVONOK_API_URL,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.request.HTTPError as http_e:
        try:
            body = http_e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(http_e)
        return http_e.code, body
    except Exception as e:
        return 0, str(e)


def _extract_call_id(body: str) -> str:
    """Пытается выдернуть call_id из JSON-ответа zvonok (даже если ошибка)."""
    try:
        parsed = json.loads(body)
    except Exception:
        return ""
    if isinstance(parsed, dict):
        # При успехе есть call_id, при ошибке его нет
        cid = parsed.get("call_id") or (parsed.get("data") or {}).get("call_id") if isinstance(parsed.get("data"), dict) else None
        return str(cid) if cid else ""
    return ""


def schedule_phone_callback_attempts(search_report) -> None:
    """Создаёт 3 RobocallAttempt'а для phone-отчёта и СРАЗУ дёргает stage=1.

    Stages 2 и 3 создаются с scheduled_at = callback_at - 1h/-10min.
    Если их scheduled_at уже в прошлом — помечаются skipped.
    """
    from .models import SiteSettings, RobocallAttempt

    if search_report.report_type != search_report.ReportType.PHONE_CALLBACK:
        return
    if not search_report.callback_at or not search_report.client_phone:
        logger.warning("phone-report #%s missing callback_at or client_phone", search_report.pk)
        return

    st = SiteSettings.get_settings()
    now = timezone.now()

    # Создаём 3 попытки
    stage_configs = [
        (RobocallAttempt.Stage.IMMEDIATE, now, st.zvonok_campaign_id_now),
        (RobocallAttempt.Stage.HOUR_BEFORE, search_report.callback_at - timedelta(hours=1), st.zvonok_campaign_id_1h),
        (RobocallAttempt.Stage.TEN_MIN_BEFORE, search_report.callback_at - timedelta(minutes=10), st.zvonok_campaign_id_10min),
    ]

    for stage, sched, camp_id in stage_configs:
        att, created = RobocallAttempt.objects.get_or_create(
            search_report=search_report,
            stage=stage,
            defaults={
                "scheduled_at": sched,
                "zvonok_campaign_id": camp_id,
            },
        )
        # Если stage > 1 и scheduled_at в прошлом за пределами grace — skip
        if stage != RobocallAttempt.Stage.IMMEDIATE and sched < now - timedelta(seconds=SKIP_GRACE_SECONDS):
            att.skipped = True
            att.skip_reason = f"scheduled_at ({sched.isoformat()}) в прошлом на момент подачи отчёта"
            att.save(update_fields=["skipped", "skip_reason", "updated_at"])

    # Stage 1 — дёргаем СРАЗУ (синхронно)
    imm = search_report.robocall_attempts.filter(stage=RobocallAttempt.Stage.IMMEDIATE).first()
    if imm:
        fire_robocall_attempt(imm)


def fire_robocall_attempt(attempt) -> bool:
    """Делает реальный POST в zvonok API для указанного attempt'а.

    Возвращает True если попытка отправлена (fired_at проставлен),
    False если пропущена/ошибка.
    """
    from .models import SiteSettings

    if attempt.fired_at or attempt.skipped:
        return False

    st = SiteSettings.get_settings()
    if not st.zvonok_public_key:
        attempt.skipped = True
        attempt.skip_reason = "zvonok_public_key не задан в настройках"
        attempt.save(update_fields=["skipped", "skip_reason", "updated_at"])
        return False

    campaign_id = attempt.zvonok_campaign_id or ""
    if not campaign_id:
        # Fallback на настройки (если добавили кампанию после создания attempt)
        stage_campaign_map = {
            1: st.zvonok_campaign_id_now,
            2: st.zvonok_campaign_id_1h,
            3: st.zvonok_campaign_id_10min,
        }
        campaign_id = stage_campaign_map.get(attempt.stage, "")

    if not campaign_id:
        attempt.skipped = True
        attempt.skip_reason = f"campaign_id не задан для stage={attempt.stage}"
        attempt.save(update_fields=["skipped", "skip_reason", "updated_at"])
        return False

    phone = normalize_phone(attempt.search_report.client_phone)
    if not phone:
        attempt.skipped = True
        attempt.skip_reason = f"номер «{attempt.search_report.client_phone}» невалидный"
        attempt.save(update_fields=["skipped", "skip_reason", "updated_at"])
        return False

    status_code, body = _call_zvonok(st.zvonok_public_key, campaign_id, phone)
    attempt.fired_at = timezone.now()
    attempt.zvonok_campaign_id = campaign_id
    attempt.zvonok_response = body[:2000]
    attempt.zvonok_call_id = _extract_call_id(body)
    attempt.save(update_fields=[
        "fired_at", "zvonok_campaign_id", "zvonok_response", "zvonok_call_id", "updated_at",
    ])
    logger.info(
        "Robocall fired report=%s stage=%s phone=%s http=%s call_id=%s",
        attempt.search_report_id, attempt.stage, phone, status_code, attempt.zvonok_call_id,
    )
    return status_code == 200


def dispatch_pending_attempts() -> dict:
    """Запускает все scheduled_at<=now attempt'ы (используется кроном).

    Возвращает сводку: {checked, fired, skipped, errors}.
    """
    from .models import RobocallAttempt

    now = timezone.now()
    qs = RobocallAttempt.objects.filter(
        fired_at__isnull=True,
        skipped=False,
        scheduled_at__lte=now,
    ).select_related("search_report")

    fired = skipped_count = errors = 0
    total = qs.count()
    for att in qs:
        # Stage != 1: если scheduled_at в прошлом более чем на grace — skip
        if att.stage != RobocallAttempt.Stage.IMMEDIATE and att.scheduled_at < now - timedelta(seconds=SKIP_GRACE_SECONDS):
            att.skipped = True
            att.skip_reason = f"scheduled_at отстал более чем на {SKIP_GRACE_SECONDS}s (был {att.scheduled_at.isoformat()})"
            att.save(update_fields=["skipped", "skip_reason", "updated_at"])
            skipped_count += 1
            continue
        try:
            if fire_robocall_attempt(att):
                fired += 1
            else:
                skipped_count += 1
        except Exception as e:
            errors += 1
            logger.exception("dispatch fail for attempt %s: %s", att.pk, e)

    return {"checked": total, "fired": fired, "skipped": skipped_count, "errors": errors}


def get_or_create_webhook_secret() -> str:
    """Получает текущий webhook-секрет; если не задан — генерирует."""
    from .models import SiteSettings

    st = SiteSettings.get_settings()
    if not st.zvonok_webhook_secret:
        st.zvonok_webhook_secret = secrets.token_urlsafe(24)
        st.save(update_fields=["zvonok_webhook_secret"])
    return st.zvonok_webhook_secret
