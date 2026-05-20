"""Начисления админу за модерацию.

- Обычные Lead-отчёты (через LeadReviewLog): 2.5 ₽ за каждое action
  (approve / reject / rework). Покрывает «Поиск» и «Дожим» — оба идут
  через одну Lead-таблицу.
- SearchLink-отчёты (через SearchReportReviewLog): 10 ₽ за action.
- GroupReport (через GroupReportReviewLog): 10 ₽ за action.

Используется в:
- core.views.user_dashboard (баланс админа)
- core.views_support_admin.admin_earnings_stats
- core.context_processors.admin_balance_context
"""
from decimal import Decimal

LEAD_REVIEW_RATE = Decimal("2.5")
SEARCH_REVIEW_RATE = Decimal("10")
GROUP_REPORT_REVIEW_RATE = Decimal("10")

_REVIEWED_STATUSES = ("approved", "rejected", "rework")


def count_lead_actions(admin) -> int:
    from .models import LeadReviewLog
    return LeadReviewLog.objects.filter(admin=admin).count()


def count_searchreport_actions(admin) -> int:
    """Сколько действий над SR-отчётами совершил админ.

    Считается по SearchReportReviewLog (одна запись на каждое action). Если
    статус позже сменили другим админом — первый сохраняет свой кредит.
    """
    from .models import SearchReportReviewLog
    return SearchReportReviewLog.objects.filter(admin=admin).count()


def count_groupreport_actions(admin) -> int:
    """Сколько действий над GroupReport-отчётами совершил админ."""
    from .models import GroupReportReviewLog
    return GroupReportReviewLog.objects.filter(admin=admin).count()


def _action_counts(admin) -> tuple[int, int, int]:
    """(lead_actions, sr_actions, gr_actions) одним блоком — 3 COUNT."""
    return (
        count_lead_actions(admin),
        count_searchreport_actions(admin),
        count_groupreport_actions(admin),
    )


def total_actions(admin) -> int:
    """Сколько модерационных действий совершил админ (Lead + SR + GroupReport)."""
    lead, sr, gr = _action_counts(admin)
    return lead + sr + gr


def total_earned(admin) -> int:
    """Сколько админ заработал на модерации, ₽ (int)."""
    lead, sr, gr = _action_counts(admin)
    return int(
        lead * LEAD_REVIEW_RATE
        + sr * SEARCH_REVIEW_RATE
        + gr * GROUP_REPORT_REVIEW_RATE
    )


def actions_earned_for_admins(admin_ids):
    """Bulk-версия: один запрос на каждую таблицу-источник, GROUP BY admin.

    Возвращает `{admin_id: {"actions": int, "earned": int}}` только
    для админов с реальной активностью. У вызывающего кода должен
    быть default-fallback (0, 0) для админов, которых нет в результате.

    3 запроса всего, вместо 6×N в наивном цикле.
    """
    from .models import LeadReviewLog, SearchReportReviewLog, GroupReportReviewLog
    from django.db.models import Count

    if not admin_ids:
        return {}
    ids = list(admin_ids)

    lead_map = dict(
        LeadReviewLog.objects.filter(admin_id__in=ids)
        .values("admin_id").annotate(c=Count("id"))
        .values_list("admin_id", "c")
    )
    sr_map = dict(
        SearchReportReviewLog.objects.filter(admin_id__in=ids)
        .values("admin_id").annotate(c=Count("id"))
        .values_list("admin_id", "c")
    )
    gr_map = dict(
        GroupReportReviewLog.objects.filter(admin_id__in=ids)
        .values("admin_id").annotate(c=Count("id"))
        .values_list("admin_id", "c")
    )

    out: dict[int, dict] = {}
    for uid in (set(lead_map) | set(sr_map) | set(gr_map)):
        lead = lead_map.get(uid, 0)
        sr = sr_map.get(uid, 0)
        gr = gr_map.get(uid, 0)
        out[uid] = {
            "actions": lead + sr + gr,
            "earned": int(lead * LEAD_REVIEW_RATE
                          + sr * SEARCH_REVIEW_RATE
                          + gr * GROUP_REPORT_REVIEW_RATE),
        }
    return out
