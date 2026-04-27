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
        if getattr(request.user, "role", None) in ("support", "admin", "main_admin", "standalone_admin", "worker"):
            return {"rework_leads_count": 0}
        from .models import Lead
        count = Lead.objects.filter(user=request.user, status=Lead.Status.REWORK).count()
        return {"rework_leads_count": count}
    except Exception as e:
        logger.exception("rework_leads context processor: %s", e)
        return {"rework_leads_count": 0}


def admin_balance_context(request):
    """Баланс админа: 2.5₽ за Lead-action + 10₽ за SearchLink-action — минус выводы."""
    try:
        user = getattr(request, "user", None)
        if not user or not user.is_authenticated:
            return {}
        if getattr(user, "role", None) not in ("admin", "main_admin"):
            return {}
        from .models import WithdrawalRequest
        from .admin_earnings import total_earned
        from django.db.models import Sum
        earned = total_earned(user)
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


def site_url(request):
    """Основной домен сайта (SITE_URL) для реферальных ссылок."""
    from django.conf import settings
    return {"site_url": getattr(settings, "SITE_URL", f"{request.scheme}://{request.get_host()}")}
