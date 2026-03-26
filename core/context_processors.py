"""Контекст-процессоры для шаблонов."""

import logging

logger = logging.getLogger(__name__)


def rework_leads(request):
    """Количество лидов на доработке у текущего пользователя (для колокольчика)."""
    try:
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return {"rework_leads_count": 0}
        if getattr(request.user, "status", None) != "approved":
            return {"rework_leads_count": 0}
        if getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False):
            return {"rework_leads_count": 0}
        if getattr(request.user, "role", None) in ("support", "admin", "standalone_admin", "worker"):
            return {"rework_leads_count": 0}
        from .models import Lead
        count = Lead.objects.filter(user=request.user, status=Lead.Status.REWORK).count()
        return {"rework_leads_count": count}
    except Exception as e:
        logger.exception("rework_leads context processor: %s", e)
        return {"rework_leads_count": 0}


def admin_balance_context(request):
    """Вычисленный баланс для role=admin (из LeadReviewLog × 2.5)."""
    try:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return {}
        if getattr(user, "role", None) != "admin":
            return {}
        from .models import LeadReviewLog, WithdrawalRequest
        from django.db.models import Sum
        from decimal import Decimal
        actions = LeadReviewLog.objects.filter(admin=user).count()
        earned = int(actions * Decimal("2.5"))
        withdrawn = WithdrawalRequest.objects.filter(user=user, status__in=("pending", "approved")).aggregate(s=Sum("amount")).get("s") or 0
        return {"admin_balance": max(0, earned - withdrawn)}
    except Exception as e:
        logger.exception("admin_balance_context: %s", e)
        return {}


def department_context(request):
    """Текущий отдел (поиск/дожим) для переключателя в навбаре."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"department": "search"}
    if getattr(request.user, "role", None) != "user":
        return {"department": "search"}
    return {"department": request.session.get("department", "search")}
