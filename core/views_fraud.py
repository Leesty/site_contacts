"""Детектор накрутки: менеджеры, которые сами стартуют свои же ссылки.

Признаки (считаются по живым данным, ничего не кешируем):
  • «быстрые старты» — бот запущен раньше чем через 60 сек после создания
    ссылки. У честных 0-6%, у накрутчиков 75-100% (замер 17.08.2026).
  • переиспользование телеграма — один tg-аккаунт на нескольких ссылках.
  • общее ФИО в СМЗ у разных аккаунтов (мультиаккаунт).
Блокировка выплат — флаг `User.fraud_blocked`, синк такому не начисляет.
"""

from __future__ import annotations

import collections
import statistics

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models import Sum
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render

from .models import SearchLink, SmzBlacklist, User, WithdrawalRequest, normalize_fio

FAST_SECONDS = 60      # старт быстрее этого = подозрительно
MIN_LINKS = 5          # меньше — статистики не хватает
SUSPECT_PCT = 50       # доля быстрых стартов, с которой считаем накруткой


def _scores():
    """Считает метрики по каждому менеджеру. Возвращает список подозрительных."""
    gaps = collections.defaultdict(list)
    tg_by_user = collections.defaultdict(collections.Counter)
    fresh = collections.defaultdict(lambda: [0, 0])  # uid -> [свежих, всего с tg]
    fresh_from = int(getattr(settings, "FRAUD_FRESH_TG_ID", 8_000_000_000))
    for uid, created, started, tg in SearchLink.objects.filter(
            bot_started_at__isnull=False).values_list(
            "user_id", "created_at", "bot_started_at", "telegram_id"):
        gaps[uid].append((started - created).total_seconds())
        if tg:
            tg_by_user[uid][tg] += 1
            fresh[uid][1] += 1
            if tg > fresh_from:
                fresh[uid][0] += 1

    rows = []
    for uid, gg in gaps.items():
        if len(gg) < MIN_LINKS:
            continue
        fast = sum(1 for g in gg if g < FAST_SECONDS)
        pct = fast * 100.0 / len(gg)
        if pct < SUSPECT_PCT:
            continue
        tgs = tg_by_user.get(uid) or collections.Counter()
        fr, fr_tot = fresh.get(uid, [0, 0])
        rows.append({
            "uid": uid, "links": len(gg), "fast": fast, "pct": round(pct),
            "median": round(statistics.median(gg)),
            "tg_unique": len(tgs), "tg_max": max(tgs.values()) if tgs else 0,
            "fresh_pct": round(fr * 100.0 / fr_tot) if fr_tot else 0,
        })
    users = {u.id: u for u in User.objects.filter(id__in=[r["uid"] for r in rows])
             .select_related("partner_owner")}
    # мультиаккаунт по ФИО
    fio_count = collections.Counter(
        f.strip().lower() for f in User.objects.exclude(smz_fio="").values_list("smz_fio", flat=True)
        if f and f.strip())
    out = []
    for r in rows:
        u = users.get(r["uid"])
        if not u:
            continue
        fio = (u.smz_fio or "").strip()
        r.update({
            "user": u,
            "fio": fio or "—",
            "fio_accounts": fio_count.get(fio.lower(), 0) if fio else 0,
            "withdrawn": WithdrawalRequest.objects.filter(user=u, status="approved")
                         .aggregate(s=Sum("amount"))["s"] or 0,
        })
        out.append(r)
    out.sort(key=lambda r: (-r["pct"], -r["links"]))
    return out


@login_required
def admin_fraud(request: HttpRequest) -> HttpResponse:
    """Список подозреваемых в накрутке + блокировка выплат в один клик."""
    if getattr(request.user, "role", None) != "main_admin":
        return HttpResponseForbidden("Только для главного админа.")

    if request.method == "POST":
        uid = request.POST.get("user_id")
        action = request.POST.get("action")
        target = User.objects.filter(pk=uid).first()
        if target and action in ("block", "unblock"):
            target.fraud_blocked = (action == "block")
            target.fraud_note = ("накрутка: самостарты своих ссылок"
                                 if action == "block" else "")
            target.save(update_fields=["fraud_blocked", "fraud_note"])
            # Блокируем ЧЕЛОВЕКА, а не кабинет: новый аккаунт заводится за
            # минуту, самозанятость — нет. Заодно цепляем все его аккаунты.
            fio = (target.smz_fio or "").strip()
            if action == "block" and fio:
                SmzBlacklist.objects.get_or_create(
                    fio_norm=normalize_fio(fio),
                    defaults={"fio": fio, "created_by": request.user,
                              "reason": "накрутка: самостарты своих ссылок"},
                )
                same = User.objects.exclude(pk=target.pk).filter(smz_fio__iexact=fio)
                same.update(fraud_blocked=True, fraud_note="накрутка (то же ФИО)")
                messages.success(request, "@%s заблокирован. ФИО «%s» в чёрном списке, "
                                          "затронуто ещё аккаунтов: %s." % (target.username, fio, same.count()))
            elif action == "unblock" and fio:
                SmzBlacklist.objects.filter(fio_norm=normalize_fio(fio)).delete()
                messages.success(request, "@%s разблокирован, ФИО убрано из чёрного списка." % target.username)
            else:
                messages.success(request, "@%s: выплаты %s." % (
                    target.username, "заблокированы" if action == "block" else "разблокированы"))
        return redirect("admin_fraud")

    suspects = _scores()
    blocked = list(User.objects.filter(fraud_blocked=True).order_by("username"))
    # мультиаккаунты по ФИО — отдельный сигнал (сам по себе НЕ доказательство)
    fio_groups = []
    counter = collections.Counter(
        f.strip().lower() for f in User.objects.exclude(smz_fio="").values_list("smz_fio", flat=True)
        if f and f.strip())
    for fio, n in counter.most_common(15):
        if n < 2:
            continue
        us = list(User.objects.filter(smz_fio__iexact=fio).order_by("id"))
        fio_groups.append({
            "fio": fio.title(), "n": n, "users": us,
            "withdrawn": WithdrawalRequest.objects.filter(user__in=us, status="approved")
                         .aggregate(s=Sum("amount"))["s"] or 0,
        })
    return render(request, "core/admin_fraud.html", {
        "suspects": suspects, "blocked": blocked, "fio_groups": fio_groups,
        "blacklist": SmzBlacklist.objects.select_related("created_by").all()[:50],
        "fast_seconds": FAST_SECONDS, "suspect_pct": SUSPECT_PCT, "min_links": MIN_LINKS,
    })
