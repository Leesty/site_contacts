"""Выплаты по НОВОЙ системе — отдельно от старых балансов.

Считаем только то, что заработано по воронке на когорте «старт бота с
SEARCH_SOZVON_START_CUTOFF», и вычищаем накрутку:
  • аккаунты с флагом `fraud_blocked` не попадают в список вовсе;
  • реферские деньги, пришедшие С НАКРУЧЕННЫХ ссылок, не засчитываются
    рефоводу (иначе честный на вид рефовод получает долю с фермы).
Источник правды — BalanceLog: только реально начисленное.
"""

from __future__ import annotations

import collections
import re
from datetime import datetime, timezone as dtz

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import render

from .models import BalanceLog, SearchLink, User

KINDS = ("sozvon", "deal", "sozvon_ref", "deal_ref", "sozvon_varvara", "deal_varvara")


def _cutoff():
    raw = getattr(settings, "SEARCH_SOZVON_START_CUTOFF", None)
    if not raw:
        return None
    if isinstance(raw, str):
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=dtz.utc)
    return raw


@login_required
def admin_new_payouts(request: HttpRequest) -> HttpResponse:
    """Кому и сколько платить по новой системе, за вычетом накрутки."""
    if getattr(request.user, "role", None) != "main_admin":
        return HttpResponseForbidden("Только для главного админа.")

    cutoff = _cutoff()
    links_qs = SearchLink.objects.select_related("user")
    if cutoff:
        links_qs = links_qs.filter(bot_started_at__gte=cutoff)
    links = {l.id: l for l in links_qs.only("id", "user_id", "user__username",
                                            "user__fraud_blocked", "bot_started_at")}
    fraud_link_ids = {lid for lid, l in links.items() if l.user.fraud_blocked}

    per_user = collections.defaultdict(lambda: collections.Counter())
    tainted = collections.Counter()          # реф-деньги с накрученных ссылок
    events = collections.defaultdict(lambda: collections.defaultdict(set))
    for uid, delta, reason in BalanceLog.objects.filter(field="balance").filter(
            reason__regex=r"^(sozvon|deal|sozvon_ref|deal_ref|sozvon_varvara|deal_varvara)#",
    ).values_list("user_id", "delta", "reason"):
        m = re.match(r"([a-z_]+)#(\d+)", reason or "")
        if not m:
            continue
        kind, lid = m.group(1), int(m.group(2))
        if lid not in links:
            continue
        if lid in fraud_link_ids:
            # деньги с накрученной ссылки: и менеджеру, и его рефоводу
            tainted[uid] += delta or 0
            continue
        per_user[uid][kind] += delta or 0
        if kind in ("sozvon", "deal"):
            events[uid][kind].add(lid)

    users = {u.id: u for u in User.objects.filter(
        id__in=set(per_user) | set(tainted)).select_related("partner_owner")}

    rows, total = [], 0
    for uid, c in per_user.items():
        u = users.get(uid)
        if not u or u.fraud_blocked:
            continue
        mgr = c["sozvon"] + c["deal"]
        ref = c["sozvon_ref"] + c["deal_ref"]
        fee = c["sozvon_varvara"] + c["deal_varvara"]
        summ = mgr + ref + fee
        if summ <= 0:
            continue
        total += summ
        rows.append({
            "user": u,
            "n_sozvon": len(events[uid]["sozvon"]),
            "n_deal": len(events[uid]["deal"]),
            "sozvon": c["sozvon"], "deal": c["deal"],
            "ref": ref, "fee": fee, "total": summ,
            "tainted": tainted.get(uid, 0),
        })
    rows.sort(key=lambda r: -r["total"])

    fraud_rows = [{"user": users[uid], "amount": amt}
                  for uid, amt in tainted.items() if uid in users and amt > 0]
    fraud_rows.sort(key=lambda r: -r["amount"])

    return render(request, "core/admin_new_payouts.html", {
        "rows": rows, "total": total,
        "fraud_rows": fraud_rows,
        "fraud_total": sum(r["amount"] for r in fraud_rows),
        "cutoff": cutoff,
        "links_total": len(links),
        "fraud_links": len(fraud_link_ids),
    })
