"""Админская проверка отчётов «Прозвон» (CallReport).

- Видят: main_admin / support.
- По умолчанию список — только `is_complete=True` + `status=pending`.
  Отчёты без валидации висят у менеджера, до админа не доходят.
- Approve → +80₽ менеджеру, +10₽ админу, логируется через log_balance_change.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import CallReport, User, log_balance_change


MANAGER_REWARD = 80
ADMIN_REWARD = 10


def _can_review(user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return getattr(user, "role", None) in {"main_admin", "support", "admin"}


def _back_to_list(request: HttpRequest):
    """Редирект на список с сохранением исходной вкладки.

    Берём return_tab из POST (hidden input в формах) или ?tab=... из GET.
    Без него после approve/reject/rework админ оказывался на дефолтном
    табе «Новые», теряя контекст где он работал.
    """
    from django.urls import reverse
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
    base = CallReport.objects.select_related("cold_contact", "cold_contact__owner", "reviewed_by")

    if tab == "all":
        qs = base
    elif tab == "approved":
        qs = base.filter(status=CallReport.Status.APPROVED)
    elif tab == "rejected":
        qs = base.filter(status=CallReport.Status.REJECTED)
    elif tab == "incomplete":
        # Только для главного админа — невалидированные на pending
        if request.user.role != "main_admin":
            return HttpResponseForbidden("Только главному админу.")
        qs = base.filter(status=CallReport.Status.PENDING, is_complete=False)
    else:
        # default: «новые» — только is_complete=True + pending
        qs = base.filter(status=CallReport.Status.PENDING, is_complete=True)
        tab = "new"

    qs = qs.order_by("-created_at")

    # Счётчики для табов
    counts = {
        "new": base.filter(status=CallReport.Status.PENDING, is_complete=True).count(),
        "incomplete": base.filter(status=CallReport.Status.PENDING, is_complete=False).count(),
        "approved": base.filter(status=CallReport.Status.APPROVED).count(),
        "rejected": base.filter(status=CallReport.Status.REJECTED).count(),
    }

    return render(request, "core/admin_call_reports.html", {
        "reports": qs[:200],
        "tab": tab,
        "counts": counts,
        "manager_reward": MANAGER_REWARD,
        "admin_reward": ADMIN_REWARD,
    })


@login_required
def admin_call_report_approve(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _can_review(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return _back_to_list(request)

    with transaction.atomic():
        report = CallReport.objects.select_for_update().filter(pk=report_id).first()
        if not report:
            return _back_to_list(request)
        if report.status == CallReport.Status.APPROVED:
            messages.info(request, "Отчёт уже одобрен.")
            return _back_to_list(request)
        if not report.is_complete:
            messages.warning(request, "Нельзя одобрять отчёт без авто-валидации.")
            return _back_to_list(request)

        manager = User.objects.select_for_update().get(pk=report.cold_contact.owner_id)
        admin = User.objects.select_for_update().get(pk=request.user.pk)

        # +80₽ менеджеру
        _old_m = manager.balance or 0
        manager.balance = _old_m + MANAGER_REWARD
        manager.save(update_fields=["balance"])
        log_balance_change(
            manager, "balance", _old_m, manager.balance,
            f"call_report_approve#{report.id} +{MANAGER_REWARD}", request.user,
        )

        # +10₽ админу (если это не тот же человек — но и тогда тоже норм)
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

    messages.success(
        request,
        f"Отчёт #{report.id} одобрен. Менеджер +{MANAGER_REWARD}₽, вам +{ADMIN_REWARD}₽.",
    )
    return _back_to_list(request)


@login_required
def admin_call_report_reject(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _can_review(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return _back_to_list(request)

    report = get_object_or_404(CallReport, pk=report_id)
    reason = (request.POST.get("reason") or "").strip()[:1000]
    if not reason:
        messages.error(request, "Укажите причину отклонения.")
        return _back_to_list(request)

    report.status = CallReport.Status.REJECTED
    report.rejection_reason = reason
    report.reviewed_at = timezone.now()
    report.reviewed_by = request.user
    report.save(update_fields=[
        "status", "rejection_reason", "reviewed_at", "reviewed_by", "updated_at",
    ])
    messages.success(request, f"Отчёт #{report.id} отклонён.")
    return _back_to_list(request)


@login_required
def admin_call_report_rework(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _can_review(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return _back_to_list(request)

    report = get_object_or_404(CallReport, pk=report_id)
    comment = (request.POST.get("comment") or "").strip()[:1000]
    if not comment:
        messages.error(request, "Укажите что доработать.")
        return _back_to_list(request)

    report.status = CallReport.Status.REWORK
    report.rework_comment = comment
    report.reviewed_at = timezone.now()
    report.reviewed_by = request.user
    report.save(update_fields=[
        "status", "rework_comment", "reviewed_at", "reviewed_by", "updated_at",
    ])
    messages.success(request, f"Отчёт #{report.id} отправлен на доработку.")
    return _back_to_list(request)


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
