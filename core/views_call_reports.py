"""Админская проверка отчётов «Прозвон» (CallReport).

UX-флоу скопирован с SearchLink:
- AJAX-кнопки: approve / reject (modal) / rework (modal) с fadeRow
- Toast-уведомления
- Видео-превью если screencast — видео
- Поиск ?q= по контакту / @менеджеру / источнику
- Пагинация

Видят: main_admin / admin / support.
По дефолту таб «Новые» — только is_complete=True + status=pending.
Approve → +80₽ менеджеру, +10₧ админу (через log_balance_change).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import (
    FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import CallReport, User, log_balance_change


MANAGER_REWARD = 80
ADMIN_REWARD = 10


def _can_review(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "role", None) in {"main_admin", "support", "admin"}


def _is_ajax(request: HttpRequest) -> bool:
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _resp(request: HttpRequest, success: bool, message: str, *, status: int | None = None):
    """AJAX → JsonResponse, иначе — flash + redirect c сохранением таба."""
    if _is_ajax(request):
        return JsonResponse(
            {"success": success, "message": message},
            status=status or (200 if success else 400),
        )
    if success:
        messages.success(request, message)
    else:
        messages.error(request, message)
    tab = (request.POST.get("return_tab") or request.GET.get("tab") or "").strip()
    url = reverse("admin_call_reports_list")
    if tab:
        url += f"?tab={tab}"
    return redirect(url)


@login_required
def admin_call_reports_list(request: HttpRequest) -> HttpResponse:
    if not _can_review(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")

    tab = (request.GET.get("tab") or "new").lower()
    q = (request.GET.get("q") or "").strip()
    base = CallReport.objects.select_related(
        "cold_contact", "cold_contact__owner", "reviewed_by",
    )

    if tab == "all":
        qs = base
    elif tab == "approved":
        qs = base.filter(status=CallReport.Status.APPROVED)
    elif tab == "rejected":
        qs = base.filter(status=CallReport.Status.REJECTED)
    elif tab == "incomplete":
        qs = base.filter(status=CallReport.Status.PENDING, is_complete=False)
    else:
        qs = base.filter(status=CallReport.Status.PENDING, is_complete=True)
        tab = "new"

    if q:
        qs = qs.filter(
            Q(cold_contact__contact__icontains=q)
            | Q(cold_contact__owner__username__icontains=q)
            | Q(cold_contact__name__icontains=q)
            | Q(cold_contact__tg_username__icontains=q)
            | Q(source__icontains=q)
        )

    qs = qs.order_by("-created_at")

    counts = {
        "new": base.filter(status=CallReport.Status.PENDING, is_complete=True).count(),
        "incomplete": base.filter(status=CallReport.Status.PENDING, is_complete=False).count(),
        "approved": base.filter(status=CallReport.Status.APPROVED).count(),
        "rejected": base.filter(status=CallReport.Status.REJECTED).count(),
    }

    page_obj = Paginator(qs, 50).get_page(request.GET.get("page", 1))

    return render(request, "core/admin_call_reports.html", {
        "page_obj": page_obj,
        "tab": tab,
        "q": q,
        "counts": counts,
        "manager_reward": MANAGER_REWARD,
        "admin_reward": ADMIN_REWARD,
    })


@login_required
def admin_call_report_approve(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _can_review(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return _resp(request, False, "Только POST.", status=405)

    with transaction.atomic():
        report = CallReport.objects.select_for_update().filter(pk=report_id).first()
        if not report:
            return _resp(request, False, "Отчёт не найден.", status=404)
        if report.status == CallReport.Status.APPROVED:
            # Идемпотентно — фронт всё равно удалит строку
            return _resp(request, True, f"Отчёт #{report_id} уже одобрен.")
        if not report.is_complete:
            return _resp(request, False, "Нельзя одобрять отчёт без авто-валидации (4 этапа).", status=400)

        manager = User.objects.select_for_update().get(pk=report.cold_contact.owner_id)
        admin = User.objects.select_for_update().get(pk=request.user.pk)

        _old_m = manager.balance or 0
        manager.balance = _old_m + MANAGER_REWARD
        manager.save(update_fields=["balance"])
        log_balance_change(
            manager, "balance", _old_m, manager.balance,
            f"call_report_approve#{report.id} +{MANAGER_REWARD}", request.user,
        )

        _old_a = admin.balance or 0
        admin.balance = _old_a + ADMIN_REWARD
        admin.save(update_fields=["balance"])
        log_balance_change(
            admin, "balance", _old_a, admin.balance,
            f"call_report_admin_fee#{report.id} +{ADMIN_REWARD}", request.user,
        )

        report.status = CallReport.Status.APPROVED
        report.paid_reward = MANAGER_REWARD
        report.reviewed_at = timezone.now()
        report.reviewed_by = request.user
        report.rejection_reason = ""
        report.rework_comment = ""
        report.save(update_fields=[
            "status", "paid_reward", "reviewed_at", "reviewed_by",
            "rejection_reason", "rework_comment", "updated_at",
        ])

    return _resp(
        request, True,
        f"Отчёт #{report.id} одобрен. Менеджер +{MANAGER_REWARD}₽, вам +{ADMIN_REWARD}₽.",
    )


@login_required
def admin_call_report_reject(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _can_review(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return _resp(request, False, "Только POST.", status=405)

    reason = (request.POST.get("reason") or "").strip()[:1000]
    if not reason:
        return _resp(request, False, "Укажите причину отклонения.", status=400)

    with transaction.atomic():
        report = CallReport.objects.select_for_update().select_related("cold_contact").filter(pk=report_id).first()
        if not report:
            return _resp(request, False, "Отчёт не найден.", status=404)
        if report.status == CallReport.Status.REJECTED:
            return _resp(request, True, f"Отчёт #{report_id} уже отклонён.")
        # Откат начислений — условие проверяется ПОД локом (защита от double-submit).
        if report.status == CallReport.Status.APPROVED and report.paid_reward:
            manager = User.objects.select_for_update().get(pk=report.cold_contact.owner_id)
            _old_m = manager.balance or 0
            manager.balance = _old_m - report.paid_reward
            manager.save(update_fields=["balance"])
            log_balance_change(
                manager, "balance", _old_m, manager.balance,
                f"call_report_reject_rollback#{report.id} -{report.paid_reward}", request.user,
            )
            report.paid_reward = 0

        report.status = CallReport.Status.REJECTED
        report.rejection_reason = reason
        report.reviewed_at = timezone.now()
        report.reviewed_by = request.user
        report.save(update_fields=[
            "status", "rejection_reason", "reviewed_at", "reviewed_by",
            "paid_reward", "updated_at",
        ])

    return _resp(request, True, f"Отчёт #{report.id} отклонён.")


@login_required
def admin_call_report_rework(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _can_review(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return _resp(request, False, "Только POST.", status=405)

    comment = (request.POST.get("comment") or "").strip()[:1000]

    with transaction.atomic():
        report = CallReport.objects.select_for_update().select_related("cold_contact").filter(pk=report_id).first()
        if not report:
            return _resp(request, False, "Отчёт не найден.", status=404)
        if report.status == CallReport.Status.REWORK:
            return _resp(request, True, f"Отчёт #{report_id} уже на доработке.")
        # Откат — под локом.
        if report.status == CallReport.Status.APPROVED and report.paid_reward:
            manager = User.objects.select_for_update().get(pk=report.cold_contact.owner_id)
            _old_m = manager.balance or 0
            manager.balance = _old_m - report.paid_reward
            manager.save(update_fields=["balance"])
            log_balance_change(
                manager, "balance", _old_m, manager.balance,
                f"call_report_rework_rollback#{report.id} -{report.paid_reward}", request.user,
            )
            report.paid_reward = 0

        report.status = CallReport.Status.REWORK
        report.rework_comment = comment
        report.reviewed_at = timezone.now()
        report.reviewed_by = request.user
        report.save(update_fields=[
            "status", "rework_comment", "reviewed_at", "reviewed_by",
            "paid_reward", "updated_at",
        ])

    return _resp(request, True, f"Отчёт #{report.id} отправлен на доработку.")


@login_required
def admin_call_report_screencast(request: HttpRequest, report_id: int) -> HttpResponse:
    """Отдать скринкаст отчёта (только админам / самому менеджеру)."""
    report = get_object_or_404(CallReport, pk=report_id)
    if not (_can_review(request.user) or report.cold_contact.owner_id == request.user.id):
        return HttpResponseForbidden("Недоступно.")
    if not report.screencast:
        raise Http404
    try:
        return FileResponse(report.screencast.open("rb"))
    except FileNotFoundError:
        raise Http404


def pending_call_reports_count() -> int:
    """Для бейджа на дашборде главного админа."""
    return CallReport.objects.filter(
        status=CallReport.Status.PENDING, is_complete=True,
    ).count()
