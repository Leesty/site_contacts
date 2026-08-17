"""Аналитика НОВОЙ системы (воронка SearchLink → windowgram) для главного админа.

Единственный источник правды по деньгам — BalanceLog: только то, что РЕАЛЬНО
начислено. Стадии воронки берём из SearchLink.funnel_stage.

Ключевые нюансы (грабли, на которые уже наступали):
  • Форвард-онли: до запуска 13.07.2026 события воронки помечены обработанными
    БЕЗ выплаты (baseline). Поэтому «в воронке» (стадия) и «оплачено» (деньги) —
    РАЗНЫЕ числа, показываем оба и не смешиваем.
  • Ретро-дубль 13.07: 3 сделки начислены дважды (крон + руками), затем
    реверснуты `reversal_double_retro_deal`. Поэтому события считаем по
    УНИКАЛЬНЫМ ссылкам (COUNT DISTINCT link_id), а деньги — суммой с реверсами.
  • Фи за чат (`chat_varvara`) отменено 22.07 и реверснуто (`chat_revert`) —
    в сумме даёт 0, отдельной строкой не показываем.
  • Реверс дубля адресован и менеджерам, и Варваре — раскидываем по получателю.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import connection
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone

from .models import SearchLink, User

# Запуск новой системы: до этой даты события отбейзлайнены без выплат.
FUNNEL_LAUNCH = "2026-07-13"

PERIODS = {"7": "7 дней", "30": "30 дней", "90": "90 дней", "all": "всё время"}

# Классификация записей BalanceLog по «кошелькам» новой системы.
# reversal_double_retro_deal адресован и менеджеру, и Варваре — делим по user_id.
_KIND_SQL = """
    CASE
        WHEN reason ~ '^sozvon#'              THEN 'mgr_sozvon'
        WHEN reason ~ '^deal#'                THEN 'mgr_deal'
        WHEN reason ~ '^fix_sozvon_advance'   THEN 'mgr_fix'
        WHEN reason ~ '^sozvon_ref#'          THEN 'ref_sozvon'
        WHEN reason ~ '^deal_ref#'            THEN 'ref_deal'
        WHEN reason ~ '^ref_percent_comp'     THEN 'ref_fix'
        WHEN reason ~ 'milestone'             THEN 'milestone'
        WHEN reason ~ 'varvara' OR reason ~ '^chat_revert' THEN 'fee'
        WHEN reason ~ '^reversal_double_retro_deal'
             THEN CASE WHEN user_id = %(varvara)s THEN 'fee' ELSE 'mgr_deal' END
        ELSE NULL
    END
"""


def _period_start(period: str):
    """Начало периода или None для «всё время»."""
    if period == "all":
        return None
    try:
        days = int(period)
    except (TypeError, ValueError):
        days = 30
    return timezone.now() - timedelta(days=days)


def _money_rows(since):
    """Деньги новой системы: [(user_id, kind, sum_delta, uniq_links), ...].

    Один проход по BalanceLog с классификацией; события — по уникальным
    ссылкам (защита от ретро-дублей).
    """
    params = {"varvara": int(getattr(settings, "VARVARA_USER_ID", 123))}
    where = "field = 'balance'"
    if since is not None:
        where += " AND created_at >= %(since)s"
        params["since"] = since
    sql = f"""
        WITH cls AS (
            SELECT user_id, delta, {_KIND_SQL} AS kind,
                   substring(reason from '#([0-9]+)') AS link_id
            FROM core_balancelog
            WHERE {where}
        )
        SELECT user_id, kind, COALESCE(SUM(delta), 0),
               COUNT(DISTINCT link_id) FILTER (WHERE delta > 0)
        FROM cls
        WHERE kind IS NOT NULL
        GROUP BY 1, 2
    """
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _daily_rows(days: int = 30):
    """Динамика по дням: оплаченные созвоны/сделки и суммарные выплаты."""
    params = {
        "varvara": int(getattr(settings, "VARVARA_USER_ID", 123)),
        "since": timezone.now() - timedelta(days=days),
    }
    sql = f"""
        WITH cls AS (
            SELECT created_at, delta, {_KIND_SQL} AS kind,
                   substring(reason from '#([0-9]+)') AS link_id
            FROM core_balancelog
            WHERE field = 'balance' AND created_at >= %(since)s
        )
        SELECT date_trunc('day', created_at)::date AS d,
               COUNT(DISTINCT link_id) FILTER (WHERE kind = 'mgr_sozvon') AS sozvons,
               COUNT(DISTINCT link_id) FILTER (WHERE kind = 'mgr_deal' AND delta > 0) AS deals,
               COALESCE(SUM(delta), 0) AS paid
        FROM cls
        WHERE kind IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """
    with connection.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _bot_starts_by_day(days: int = 30) -> dict:
    """Старты бота по дням (события воронки, не деньги)."""
    since = timezone.now() - timedelta(days=days)
    with connection.cursor() as cur:
        cur.execute(
            """SELECT date_trunc('day', bot_started_at)::date, COUNT(*)
               FROM core_searchlink
               WHERE bot_started_at IS NOT NULL AND bot_started_at >= %s
               GROUP BY 1""",
            [since],
        )
        return {r[0]: r[1] for r in cur.fetchall()}


def _booking_stats():
    """Кто из клиентов записался на встречу в боте (`calendar_events`).

    Возвращает (by_user, total_booked): сколько клиентов КАЖДОГО менеджера
    записались на встречу. Считаем по сматченным SearchLink — только те, что
    реально привязаны к диалогу CRM.
    """
    from django.db import connections
    from .models import SearchLink

    with connections["windowgram"].cursor() as cur:
        cur.execute(
            "SELECT DISTINCT conversation_id::text FROM calendar_events "
            "WHERE conversation_id IS NOT NULL"
        )
        booked = {r[0] for r in cur.fetchall()}
    by_user = {}
    rows = SearchLink.objects.filter(
        bot_started=True, wg_conversation_id__isnull=False,
    ).values_list("user_id", "wg_conversation_id")
    total = 0
    for uid, conv in rows:
        if str(conv) in booked:
            by_user[uid] = by_user.get(uid, 0) + 1
            total += 1
    return by_user, total


def _bookings_by_day(days: int = 30) -> dict:
    """Назначенные в боте встречи по дням (только наши сматченные клиенты)."""
    from django.db import connections

    from .models import SearchLink

    since = timezone.now() - timedelta(days=days)
    with connections["windowgram"].cursor() as cur:
        cur.execute(
            """SELECT conversation_id::text, date_trunc('day', created_at)::date
               FROM calendar_events
               WHERE conversation_id IS NOT NULL AND created_at >= %s""",
            [since],
        )
        by_conv = {r[0]: r[1] for r in cur.fetchall()}
    if not by_conv:
        return {}
    ours = set(
        str(c) for c in SearchLink.objects.filter(
            bot_started=True, wg_conversation_id__isnull=False,
        ).values_list("wg_conversation_id", flat=True)
    )
    out: dict = {}
    for conv, day in by_conv.items():
        if conv in ours:
            out[day] = out.get(day, 0) + 1
    return out


@login_required
def admin_funnel_stats(request: HttpRequest) -> HttpResponse:
    """Полная аналитика новой воронки: деньги, конверсии, менеджеры, рефоводы."""
    # Сводные деньги по всем менеджерам — уровень главного админа
    # (как у admin_search_stats / admin_earnings_stats).
    if getattr(request.user, "role", None) != "main_admin":
        return HttpResponseForbidden("Только для главного админа.")

    period = request.GET.get("period", "30")
    if period not in PERIODS:
        period = "30"
    since = _period_start(period)
    sort = request.GET.get("sort", "earned")

    # ── Деньги (за период) ────────────────────────────────────────────────
    rows = _money_rows(since)
    by_user: dict[int, dict] = {}
    totals = {k: 0 for k in ("mgr_sozvon", "mgr_deal", "mgr_fix", "ref_sozvon",
                             "ref_deal", "ref_fix", "milestone", "fee")}
    events = {"sozvon": 0, "deal": 0}
    for user_id, kind, total, uniq in rows:
        totals[kind] = totals.get(kind, 0) + int(total or 0)
        u = by_user.setdefault(user_id, {k: 0 for k in totals})
        u[kind] += int(total or 0)
        u.setdefault("n_sozvon", 0)
        u.setdefault("n_deal", 0)
        if kind == "mgr_sozvon":
            u["n_sozvon"] += int(uniq or 0)
            events["sozvon"] += int(uniq or 0)
        elif kind == "mgr_deal":
            u["n_deal"] += int(uniq or 0)
            events["deal"] += int(uniq or 0)

    mgr_total = totals["mgr_sozvon"] + totals["mgr_deal"] + totals["mgr_fix"]
    ref_total = totals["ref_sozvon"] + totals["ref_deal"] + totals["ref_fix"]
    paid_total = mgr_total + ref_total + totals["milestone"] + totals["fee"]

    # Средний чек сделки (что реально ушло людям с одной сделки)
    avg_deal = round((totals["mgr_deal"] + totals["ref_deal"]) / events["deal"]) if events["deal"] else 0

    # ── Воронка: состояние за всё время (snapshot, без периода) ───────────
    f = SearchLink.objects.aggregate(
        links=Count("id"),
        bots=Count("id", filter=Q(bot_started=True)),
        chats=Count("id", filter=Q(funnel_stage__gte=2)),
        sozvons=Count("id", filter=Q(funnel_stage__gte=3)),
        deals=Count("id", filter=Q(funnel_stage__gte=4)),
    )

    def _pct(a, b):
        return round(a * 100 / b, 1) if b else 0.0

    funnel = [
        {"name": "Ссылок создано", "icon": "🔗", "n": f["links"], "conv": None, "of": None},
        {"name": "Бот запущен", "icon": "🤖", "n": f["bots"], "conv": _pct(f["bots"], f["links"]), "of": "от ссылок"},
        {"name": "Чат создан", "icon": "💬", "n": f["chats"], "conv": _pct(f["chats"], f["bots"]), "of": "от ботов"},
        {"name": "Созвон", "icon": "📞", "n": f["sozvons"], "conv": _pct(f["sozvons"], f["chats"]), "of": "от чатов"},
        {"name": "Сделка", "icon": "✅", "n": f["deals"], "conv": _pct(f["deals"], f["sozvons"]), "of": "от созвонов"},
    ]

    # ── Платформы ─────────────────────────────────────────────────────────
    platforms = list(
        SearchLink.objects.values("platform").annotate(
            links=Count("id"),
            bots=Count("id", filter=Q(bot_started=True)),
            deals=Count("id", filter=Q(funnel_stage__gte=4)),
        ).order_by("-links")
    )
    for p in platforms:
        p["conv"] = _pct(p["bots"], p["links"])

    # ── Запись на встречу в боте (150 ₽ платятся за неё) ──────────────────
    booked_by_user, booked_total = _booking_stats()

    # ── По менеджерам ─────────────────────────────────────────────────────
    fl = {
        r["user_id"]: r
        for r in SearchLink.objects.values("user_id").annotate(
            links=Count("id"),
            bots=Count("id", filter=Q(bot_started=True)),
            chats=Count("id", filter=Q(funnel_stage__gte=2)),
            sozvons=Count("id", filter=Q(funnel_stage__gte=3)),
            deals=Count("id", filter=Q(funnel_stage__gte=4)),
        )
    }
    uids = set(fl) | set(by_user)
    users = {
        u.id: u
        for u in User.objects.filter(id__in=uids).select_related("partner_owner").only(
            "id", "username", "role", "is_accredited", "partner_owner__username"
        )
    }
    varvara_id = int(getattr(settings, "VARVARA_USER_ID", 123))

    managers = []
    for uid in uids:
        u = users.get(uid)
        if not u or uid == varvara_id:
            continue
        m = by_user.get(uid, {})
        stat = fl.get(uid, {})
        earned = m.get("mgr_sozvon", 0) + m.get("mgr_deal", 0) + m.get("mgr_fix", 0)
        as_ref = m.get("ref_sozvon", 0) + m.get("ref_deal", 0) + m.get("ref_fix", 0) + m.get("milestone", 0)
        if not earned and not as_ref and not stat.get("links"):
            continue
        managers.append({
            "id": uid,
            "username": u.username,
            "role": u.role,
            "accredited": u.is_accredited,
            "referrer": u.partner_owner.username if u.partner_owner_id else None,
            "links": stat.get("links", 0),
            "bots": stat.get("bots", 0),
            "chats": stat.get("chats", 0),
            "sozvons_funnel": stat.get("sozvons", 0),
            "deals_funnel": stat.get("deals", 0),
            "n_sozvon": m.get("n_sozvon", 0),
            "n_deal": m.get("n_deal", 0),
            "earn_sozvon": m.get("mgr_sozvon", 0) + m.get("mgr_fix", 0),
            "earn_deal": m.get("mgr_deal", 0),
            "earned": earned,
            "as_ref": as_ref,
            "conv": _pct(stat.get("deals", 0), stat.get("bots", 0)),
            "booked": booked_by_user.get(uid, 0),
            "not_booked": max(0, stat.get("bots", 0) - booked_by_user.get(uid, 0)),
            "booked_pct": _pct(booked_by_user.get(uid, 0), stat.get("bots", 0)),
        })
    sort_key = {
        "earned": lambda r: -r["earned"],
        "links": lambda r: -r["links"],
        "bots": lambda r: -r["bots"],
        "deals": lambda r: -r["n_deal"],
        "conv": lambda r: -r["conv"],
    }.get(sort, lambda r: -r["earned"])
    managers.sort(key=sort_key)

    # ── Рефоводы ──────────────────────────────────────────────────────────
    ref_counts = {
        r["partner_owner_id"]: r["n"]
        for r in User.objects.filter(partner_owner__isnull=False)
        .values("partner_owner_id").annotate(n=Count("id"))
    }
    referrers = []
    for uid, m in by_user.items():
        got = m.get("ref_sozvon", 0) + m.get("ref_deal", 0) + m.get("ref_fix", 0) + m.get("milestone", 0)
        if got <= 0 or uid == varvara_id:
            continue
        u = users.get(uid)
        if not u:
            continue
        referrers.append({
            "username": u.username,
            "accredited": u.is_accredited,
            "refs": ref_counts.get(uid, 0),
            "from_sozvon": m.get("ref_sozvon", 0) + m.get("ref_fix", 0),
            "from_deal": m.get("ref_deal", 0),
            "milestone": m.get("milestone", 0),
            "total": got,
        })
    referrers.sort(key=lambda r: -r["total"])

    # ── Динамика по дням ──────────────────────────────────────────────────
    starts = _bot_starts_by_day(30)
    bookings = _bookings_by_day(30)
    daily = []
    max_paid = 1
    for d, sozvons, deals, paid in _daily_rows(30):
        paid = int(paid or 0)
        max_paid = max(max_paid, paid)
        daily.append({"date": d, "starts": starts.get(d, 0),
                      "booked": bookings.get(d, 0), "sozvons": sozvons,
                      "deals": deals, "paid": paid})
    seen_days = {r["date"] for r in daily}
    for d in set(starts) | set(bookings):
        if d not in seen_days:
            daily.append({"date": d, "starts": starts.get(d, 0),
                          "booked": bookings.get(d, 0), "sozvons": 0,
                          "deals": 0, "paid": 0})
    daily.sort(key=lambda r: r["date"])
    for row in daily:
        row["bar"] = round(row["paid"] * 100 / max_paid) if max_paid else 0
    daily.reverse()  # свежие сверху

    # ── Фи Варвары ────────────────────────────────────────────────────────
    varvara = by_user.get(varvara_id, {})

    return render(request, "core/admin_funnel_stats.html", {
        "period": period, "periods": PERIODS, "sort": sort,
        "paid_total": paid_total,
        "mgr_total": mgr_total, "ref_total": ref_total,
        "milestone_total": totals["milestone"], "fee_total": totals["fee"],
        "t": totals, "events": events, "avg_deal": avg_deal,
        "funnel": funnel, "platforms": platforms,
        "booked_total": booked_total,
        "booked_pct_total": _pct(booked_total, f["bots"]),
        "not_booked_total": max(0, f["bots"] - booked_total),
        "managers": managers, "referrers": referrers, "daily": daily,
        "varvara_fee": varvara.get("fee", 0),
        "launch": FUNNEL_LAUNCH,
        "rates": {
            "sozvon": getattr(settings, "SEARCH_SOZVON_REWARD", 150),
            "sozvon_ref": getattr(settings, "SEARCH_SOZVON_REFERRER", 50),
            "deal": getattr(settings, "SEARCH_DEAL_REWARD", 4000),
            "deal_ref": getattr(settings, "SEARCH_DEAL_REFERRER", 1000),
            "fee_sozvon": getattr(settings, "SEARCH_VARVARA_SOZVON_FEE", 10),
            "fee_deal": getattr(settings, "SEARCH_VARVARA_DEAL_FEE", 100),
        },
    })
