"""Синхронизатор воронки SearchLink с CRM windowgram (2026-07).

Читает статус клиента из windowgram.conversations (кросс-DB), обновляет стадию
SearchLink и НАЧИСЛЯЕТ менеджеру/рефоводу/Варваре на переходах воронки.

Стадии (SearchLink.funnel_stage):
    1 бот запущен   — SearchLink.bot_started (Django, вебхук)
    2 создан чат    — conversations.group_chat_id IS NOT NULL
    3 созвон        — conversations.status ∈ {waiting_payment, waiting_no_date, answer_date}
    4 сделка        — conversations.status = 'paid'

Начисления (идемпотентно, по одному разу на клиента; форвард-онли — baseline
проставляется отдельным скриптом):
    Созвон  (всего 150): с рефоводом → менеджер 100 / рефовод 50;
                         без рефовода → менеджер 150, Варвара +10 (сверху).
    Сделка  (всего 4000, за вычетом уже выданного созвона):
                         с рефоводом → менеджер до 3000 / рефовод до 1000;
                         без рефовода → менеджер до 4000, Варвара +100 (сверху).
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

SOZVON_STATUSES = ("waiting_payment", "waiting_no_date", "answer_date")
PAID_STATUS = "paid"


def _stage_rank(status: str | None, has_chat: bool) -> int:
    """Стадия воронки клиента по данным windowgram (2..4), либо 0 если только бот."""
    if status == PAID_STATUS:
        return 4
    if status in SOZVON_STATUSES:
        return 3
    if has_chat:
        return 2
    return 0  # бота уже засчитали в Django (bot_started), чата ещё нет


def _fetch_wg_state(links: list) -> dict:
    """Батч-чтение состояния клиентов из windowgram.

    Возвращает {link_id: {"conv_id", "status", "has_chat", "rank"}} — лучший
    (максимальной стадии) conversation на каждый link. Приоритет матча:
    telegram_id → telegram_username → vk_user_id → vk_screen_name.

    VK-клиент может быть привязан к ссылке двумя способами: по числовому
    vk_user_id (надёжно, из вебхука бота) ИЛИ только по vk_screen_name (когда
    менеджер завёл клиента вручную по тегу, а бот не прислал числовой id).
    В windowgram VK-юзер лежит в telegram_users с platform='vk', где
    username = screen_name, vk_id = числовой id. Поэтому screen_name матчим
    строго по platform='vk', чтобы не пересечься с telegram-username.

    «Создан чат» (стадия 2): в TG это group_chat_id, в VK — беседа, т.е.
    vk_peer_id >= 2_000_000_000 (CHAT_PEER_OFFSET). У VK group_chat_id почти
    всегда пустой, поэтому без учёта беседы VK-клиенты с чатом висли бы на «бот».
    """
    tg_ids = sorted({l.telegram_id for l in links if l.telegram_id})
    unames = sorted({l.telegram_username.lower() for l in links if l.telegram_username})
    vk_ids = sorted({l.vk_user_id for l in links if l.vk_user_id})
    vk_screens = sorted({l.vk_screen_name.lower() for l in links
                         if l.vk_screen_name and not l.vk_user_id})
    if not (tg_ids or unames or vk_ids or vk_screens):
        return {}

    rows = []
    with connections["windowgram"].cursor() as wg:
        wg.execute(
            """
            SELECT t.telegram_id, lower(t.username) AS uname, t.vk_id, t.platform,
                   c.id::text AS conv_id, c.status,
                   (c.group_chat_id IS NOT NULL OR c.vk_peer_id >= 2000000000) AS has_chat
            FROM conversations c
            JOIN telegram_users t ON t.id = c.telegram_user_id
            WHERE (t.telegram_id = ANY(%s))
               OR (t.username IS NOT NULL AND t.username <> '' AND lower(t.username) = ANY(%s))
               OR (t.vk_id = ANY(%s))
               OR (t.platform = 'vk' AND t.username IS NOT NULL AND t.username <> ''
                   AND lower(t.username) = ANY(%s))
            """,
            [tg_ids or [0], unames or [""], vk_ids or [0], vk_screens or [""]],
        )
        rows = wg.fetchall()

    # Индексируем conversation'ы по каждому идентификатору, оставляя МАКС стадию.
    by_tg: dict[int, dict] = {}
    by_uname: dict[str, dict] = {}
    by_vk: dict[int, dict] = {}
    by_vk_screen: dict[str, dict] = {}

    def _put(idx: dict, key, conv):
        prev = idx.get(key)
        if prev is None or conv["rank"] > prev["rank"]:
            idx[key] = conv

    for tg_id, uname, vk_id, platform, conv_id, status, has_chat in rows:
        conv = {"conv_id": conv_id, "status": status or "",
                "has_chat": bool(has_chat), "rank": _stage_rank(status, bool(has_chat))}
        if tg_id:
            _put(by_tg, tg_id, conv)
        if uname:
            _put(by_uname, uname, conv)
        if vk_id:
            _put(by_vk, vk_id, conv)
        if platform == "vk" and uname:
            _put(by_vk_screen, uname, conv)

    out: dict = {}
    for l in links:
        conv = None
        if l.telegram_id and l.telegram_id in by_tg:
            conv = by_tg[l.telegram_id]
        elif l.telegram_username and l.telegram_username.lower() in by_uname:
            conv = by_uname[l.telegram_username.lower()]
        elif l.vk_user_id and l.vk_user_id in by_vk:
            conv = by_vk[l.vk_user_id]
        elif l.vk_screen_name and l.vk_screen_name.lower() in by_vk_screen:
            conv = by_vk_screen[l.vk_screen_name.lower()]
        if conv:
            out[l.id] = conv
    return out


def _credit(user, amount: int, reason: str, actor):
    """Начислить amount на User.balance с логом (внутри уже открытой транзакции)."""
    from .models import User, log_balance_change
    u = User.objects.select_for_update().get(pk=user.pk)
    old = u.balance or 0
    u.balance = old + amount
    u.save(update_fields=["balance"])
    log_balance_change(u, "balance", old, u.balance, reason, actor)
    # авто-аккредитация: минус → плюс
    if not u.is_accredited and old < 0 and u.balance >= 0:
        u.is_accredited = True
        u.save(update_fields=["is_accredited"])


def baseline_searchlink_funnel(dry_run: bool = True) -> dict:
    """Форвард-онли baseline (разовый). Обновляет стадию всех bot_started ссылок
    по текущему состоянию windowgram и ПОМЕЧАЕТ уже достигнутые созвон/сделку как
    начисленные (проставляет *_credited_at) БЕЗ выплаты — чтобы синхронизатор
    платил только за БУДУЩИЕ переходы.

    dry_run=True (по умолчанию) — только считает, ничего не пишет.
    """
    from .models import SearchLink

    links = list(SearchLink.objects.filter(bot_started=True)
                 .only("id", "funnel_stage", "chat_created", "wg_conversation_id", "wg_status",
                       "sozvon_credited_at", "deal_credited_at",
                       "telegram_id", "telegram_username", "vk_user_id"))
    summary = {"checked": len(links), "matched": 0,
               "would_mark_sozvon": 0, "would_mark_deal": 0, "stage2": 0, "stage3": 0, "stage4": 0}
    if not links:
        return summary
    wg_state = _fetch_wg_state(links)
    now = timezone.now()
    to_update = []

    for link in links:
        conv = wg_state.get(link.id)
        if not conv:
            continue
        summary["matched"] += 1
        new_stage = max(1, conv["rank"])
        if new_stage == 2:
            summary["stage2"] += 1
        elif new_stage == 3:
            summary["stage3"] += 1
        elif new_stage == 4:
            summary["stage4"] += 1
        touched = False
        if new_stage > link.funnel_stage:
            link.funnel_stage = new_stage; touched = True
        if conv["has_chat"] and not link.chat_created:
            link.chat_created = True; touched = True
        if conv["status"] and conv["status"] != link.wg_status:
            link.wg_status = conv["status"][:32]; touched = True
        if conv["conv_id"] and str(link.wg_conversation_id or "") != conv["conv_id"]:
            link.wg_conversation_id = conv["conv_id"]; touched = True
        # помечаем достигнутые стадии как «уже обработанные» (без выплаты)
        if new_stage >= 3 and link.sozvon_credited_at is None:
            link.sozvon_credited_at = now; touched = True
            summary["would_mark_sozvon"] += 1
        if new_stage >= 4 and link.deal_credited_at is None:
            link.deal_credited_at = now; touched = True
            summary["would_mark_deal"] += 1
        if touched:
            to_update.append(link)

    if to_update and not dry_run:
        for i in range(0, len(to_update), 500):
            SearchLink.objects.bulk_update(
                to_update[i:i + 500],
                ["funnel_stage", "chat_created", "wg_status", "wg_conversation_id",
                 "sozvon_credited_at", "deal_credited_at"],
            )
    summary["updated"] = len(to_update)
    return summary


def sync_searchlink_funnel(link_ids: list | None = None, dry_run: bool = False) -> dict:
    """Основной проход синхронизатора. Возвращает сводку.

    Обрабатывает bot_started ссылки, у которых сделка ещё не начислена
    (deal_credited_at IS NULL) — только они могут продвинуться по воронке.

    dry_run=True — считает, что БЫЛО БЫ начислено, но ничего не пишет (для
    проверки форвард-онли после baseline: сразу после baseline должно быть 0).
    """
    from .models import SearchLink, User

    SOZVON_TOTAL = getattr(settings, "SEARCH_SOZVON_REWARD", 150)
    SOZVON_REF = getattr(settings, "SEARCH_SOZVON_REFERRER", 50)
    DEAL_TOTAL = getattr(settings, "SEARCH_DEAL_REWARD", 4000)
    DEAL_REF = getattr(settings, "SEARCH_DEAL_REFERRER", 1000)
    VARVARA_SOZVON = getattr(settings, "SEARCH_VARVARA_SOZVON_FEE", 10)
    VARVARA_DEAL = getattr(settings, "SEARCH_VARVARA_DEAL_FEE", 100)
    varvara = User.objects.filter(pk=getattr(settings, "VARVARA_USER_ID", 123)).first()

    qs = SearchLink.objects.filter(bot_started=True, deal_credited_at__isnull=True)
    if link_ids is not None:
        qs = qs.filter(id__in=link_ids)
    links = list(qs.select_related("user", "user__partner_owner")
                 .only("id", "funnel_stage", "chat_created", "wg_conversation_id", "wg_status",
                       "sozvon_credited_at", "deal_credited_at",
                       "telegram_id", "telegram_username", "vk_user_id",
                       "user__id", "user__partner_owner"))
    summary = {"checked": len(links), "stage_updated": 0,
               "sozvon_credited": 0, "deal_credited": 0,
               "sozvon_rub": 0, "deal_rub": 0, "errors": 0}
    if not links:
        return summary

    wg_state = _fetch_wg_state(links)
    CACHE_FIELDS = ["funnel_stage", "chat_created", "wg_status", "wg_conversation_id"]

    # ── Проход 1 (Python, без записи): вычисляем кэш-поля + кто требует начисления ──
    field_only = []            # ссылки только с обновлением кэша (без денег)
    credit_links = []          # [(link, new_stage)] — требуют начисления, транзакционно
    for link in links:
        conv = wg_state.get(link.id)
        if not conv:
            continue
        new_stage = max(1, conv["rank"])  # bot_started → минимум 1
        touched = False
        if new_stage > link.funnel_stage:
            link.funnel_stage = new_stage; touched = True
        if conv["has_chat"] and not link.chat_created:
            link.chat_created = True; touched = True
        if conv["status"] != link.wg_status:
            link.wg_status = conv["status"][:32]; touched = True
        if conv["conv_id"] and str(link.wg_conversation_id or "") != conv["conv_id"]:
            link.wg_conversation_id = conv["conv_id"]; touched = True
        needs = (new_stage == 3 and link.sozvon_credited_at is None) or \
                (new_stage == 4 and link.deal_credited_at is None)
        if touched:
            summary["stage_updated"] += 1
        if needs:
            credit_links.append((link, new_stage))
        elif touched:
            field_only.append(link)

    # bulk-обновление кэш-полей (один-несколько запросов вместо тысяч)
    if field_only and not dry_run:
        for i in range(0, len(field_only), 500):
            SearchLink.objects.bulk_update(field_only[i:i + 500], CACHE_FIELDS)

    def _apply_credit(l, new_stage):
        """Начисление за созвон/сделку для l. Возвращает список изменённых полей.

        Пишет в БД только если not dry_run; счётчики summary — всегда.
        """
        manager = l.user
        referrer = manager.partner_owner if manager.partner_owner_id else None
        has_ref = referrer is not None

        def dc(user, amount, reason):
            if user and amount and not dry_run:
                _credit(user, amount, reason, varvara)

        upd = []
        if new_stage == 3 and l.sozvon_credited_at is None:
            if has_ref:
                dc(manager, SOZVON_TOTAL - SOZVON_REF, f"sozvon#{l.pk} +{SOZVON_TOTAL - SOZVON_REF}")
                dc(referrer, SOZVON_REF, f"sozvon_ref#{l.pk} +{SOZVON_REF}")
            else:
                dc(manager, SOZVON_TOTAL, f"sozvon#{l.pk} +{SOZVON_TOTAL}")
                dc(varvara, VARVARA_SOZVON, f"sozvon_varvara#{l.pk} +{VARVARA_SOZVON}")
            l.sozvon_credited_at = timezone.now(); upd.append("sozvon_credited_at")
            summary["sozvon_credited"] += 1; summary["sozvon_rub"] += SOZVON_TOTAL
        if new_stage == 4 and l.deal_credited_at is None:
            sozvon_given = l.sozvon_credited_at is not None
            if has_ref:
                mgr_sozvon = (SOZVON_TOTAL - SOZVON_REF) if sozvon_given else 0
                ref_sozvon = SOZVON_REF if sozvon_given else 0
                dc(manager, (DEAL_TOTAL - DEAL_REF) - mgr_sozvon, f"deal#{l.pk} +{(DEAL_TOTAL - DEAL_REF) - mgr_sozvon}")
                dc(referrer, DEAL_REF - ref_sozvon, f"deal_ref#{l.pk} +{DEAL_REF - ref_sozvon}")
            else:
                mgr_sozvon = SOZVON_TOTAL if sozvon_given else 0
                dc(manager, DEAL_TOTAL - mgr_sozvon, f"deal#{l.pk} +{DEAL_TOTAL - mgr_sozvon}")
                dc(varvara, VARVARA_DEAL, f"deal_varvara#{l.pk} +{VARVARA_DEAL}")
            l.deal_credited_at = timezone.now(); upd.append("deal_credited_at")
            summary["deal_credited"] += 1; summary["deal_rub"] += DEAL_TOTAL
        return upd

    # ── Проход 2: начисления (транзакционно, только для нужных ссылок) ──
    for link, new_stage in credit_links:
        try:
            if dry_run:
                _apply_credit(link, new_stage)  # только счётчики
                continue
            with transaction.atomic():
                l = SearchLink.objects.select_for_update().get(pk=link.id)
                if l.deal_credited_at is not None:
                    continue  # финализирован (гонка)
                # переносим вычисленные кэш-поля на свежую строку
                l.funnel_stage = max(l.funnel_stage, new_stage)
                l.chat_created = l.chat_created or link.chat_created
                l.wg_status = link.wg_status
                l.wg_conversation_id = link.wg_conversation_id
                upd = _apply_credit(l, new_stage)
                l.save(update_fields=list(set(upd + CACHE_FIELDS)) + ["updated_at"])
        except Exception as e:  # noqa: BLE001
            summary["errors"] += 1
            logger.exception("sync_searchlink_funnel link#%s: %s", link.id, e)

    return summary
