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


def total_actions(admin) -> int:
    """Сколько модерационных действий совершил админ (Lead + SR + GroupReport)."""
    return (count_lead_actions(admin)
            + count_searchreport_actions(admin)
            + count_groupreport_actions(admin))


def total_earned(admin) -> int:
    """Сколько админ заработал на модерации, ₽ (int)."""
    lead = count_lead_actions(admin)
    sr = count_searchreport_actions(admin)
    gr = count_groupreport_actions(admin)
    return int(
        lead * LEAD_REVIEW_RATE
        + sr * SEARCH_REVIEW_RATE
        + gr * GROUP_REPORT_REVIEW_RATE
    )
