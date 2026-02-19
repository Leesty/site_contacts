"""Контекст-процессоры для шаблонов."""

from .models import Lead


def rework_leads(request):
    """Количество лидов на доработке у текущего пользователя (для колокольчика)."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"rework_leads_count": 0}
    if getattr(request.user, "is_staff", False) or getattr(request.user, "is_superuser", False):
        return {"rework_leads_count": 0}
    if getattr(request.user, "role", None) in ("support", "admin"):
        return {"rework_leads_count": 0}
    count = Lead.objects.filter(user=request.user, status=Lead.Status.REWORK).count()
    return {"rework_leads_count": count}
