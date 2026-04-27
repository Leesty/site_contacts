"""Начисления админу за модерацию.

- Обычные Lead-отчёты (через LeadReviewLog): 2.5 ₽ за каждое action
  (approve / reject / rework). Покрывает «Поиск» и «Дожим» — оба идут
  через одну Lead-таблицу.
- SearchLink-отчёты (SearchReport.reviewed_by): 10 ₽ за action. Считаются
  по факту наличия `reviewed_by` со статусом из {approved, rejected, rework}.

Используется в:
- core.views.user_dashboard (баланс админа)
- core.views_support_admin.admin_earnings_stats
- core.context_processors.admin_balance_context
"""
from decimal import Decimal

LEAD_REVIEW_RATE = Decimal("2.5")
SEARCH_REVIEW_RATE = Decimal("10")

_REVIEWED_STATUSES = ("approved", "rejected", "rework")


def count_lead_actions(admin) -> int:
    from .models import LeadReviewLog
    return LeadReviewLog.objects.filter(admin=admin).count()


def count_searchreport_actions(admin) -> int:
    from .models import SearchReport
    return SearchReport.objects.filter(
        reviewed_by=admin,
        status__in=_REVIEWED_STATUSES,
    ).count()


def total_actions(admin) -> int:
    """Сколько модерационных действий совершил админ (Lead + SearchReport)."""
    return count_lead_actions(admin) + count_searchreport_actions(admin)


def total_earned(admin) -> int:
    """Сколько админ заработал на модерации, ₽ (int)."""
    lead = count_lead_actions(admin)
    sr = count_searchreport_actions(admin)
    return int(lead * LEAD_REVIEW_RATE + sr * SEARCH_REVIEW_RATE)
