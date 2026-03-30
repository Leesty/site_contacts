"""Вьюхи для SearchLink-системы: генерация лендингов с привязкой к ботам."""
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from django.core.paginator import Paginator

from .models import SearchLink, SearchReport, User

logger = logging.getLogger(__name__)

SEARCH_REPORT_REWARD = getattr(settings, "SEARCH_REPORT_REWARD", 100)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _require_approved_user(request: HttpRequest) -> bool:
    user = request.user
    if not user.is_authenticated:
        return False
    return getattr(user, "role", None) == "user" and getattr(user, "status", None) == "approved"


def _require_support(request: HttpRequest) -> bool:
    user = request.user
    if not user.is_authenticated:
        return False
    if getattr(user, "is_support", False) or user.is_staff or user.is_superuser:
        return True
    return False


# ─── Менеджер: список ссылок ──────────────────────────────────────────────────

@login_required
def search_links_my(request: HttpRequest) -> HttpResponse:
    """Список SearchLink-ов менеджера с формой создания."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")

    q = (request.GET.get("q") or "").strip()
    links_qs = SearchLink.objects.filter(user=request.user)
    if q:
        from django.db.models import Q
        links_qs = links_qs.filter(Q(lead_name__icontains=q) | Q(code__icontains=q))
    links_qs = links_qs.order_by("-created_at")

    paginator = Paginator(links_qs, 30)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Подгрузить отчёты для отображения статусов
    for link in page_obj:
        try:
            link.report_obj = link.report
        except SearchReport.DoesNotExist:
            link.report_obj = None

    return render(request, "search/my_links.html", {
        "page_obj": page_obj,
        "q": q,
    })


# ─── Менеджер: создание ссылки ────────────────────────────────────────────────

@login_required
@require_http_methods(["POST"])
def search_link_create(request: HttpRequest) -> HttpResponse:
    """Создать новый SearchLink."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")

    lead_name = (request.POST.get("lead_name") or "").strip()[:200]
    if not lead_name:
        messages.error(request, "Укажите имя/ник лида.")
        return redirect("search_links_my")

    link = SearchLink.objects.create(user=request.user, lead_name=lead_name)
    messages.success(request, f"Ссылка создана для «{lead_name}».")
    return redirect("search_links_my")


# ─── Публичный лендинг ────────────────────────────────────────────────────────

def search_link_landing(request: HttpRequest, code: str) -> HttpResponse:
    """Лендинг SearchLink: /s/<code>/ — публичная страница без авторизации."""
    link = SearchLink.objects.filter(code=code).first()
    if not link:
        return render(request, "search/unavailable.html", status=404)

    if link.bot_started:
        return render(request, "search/unavailable.html")

    return render(request, "search/landing.html", {
        "lead_name": link.lead_name,
        "deep_link": link.deep_link,
        "code": link.code,
    })


# ─── Менеджер: отчёт ─────────────────────────────────────────────────────────

@login_required
def search_report_create(request: HttpRequest, code: str) -> HttpResponse:
    """Отправить отчёт по SearchLink."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")

    link = get_object_or_404(SearchLink, code=code, user=request.user)

    # Проверить что отчёт ещё не создан
    try:
        existing = link.report
        messages.info(request, "Отчёт к этой ссылке уже отправлен.")
        return redirect("search_links_my")
    except SearchReport.DoesNotExist:
        pass

    if request.method == "POST":
        lead_date = request.POST.get("lead_date")
        attachment = request.FILES.get("attachment")
        comment = (request.POST.get("comment") or "").strip()

        if not attachment:
            messages.error(request, "Приложите скриншот или видео.")
            return render(request, "search/report_form.html", {"link": link})

        report = SearchReport.objects.create(
            user=request.user,
            search_link=link,
            lead_date=lead_date or timezone.now().date(),
            attachment=attachment,
            comment=comment,
        )
        messages.success(request, "Отчёт отправлен.")
        return redirect("search_links_my")

    return render(request, "search/report_form.html", {"link": link})


@login_required
def search_report_redo(request: HttpRequest, code: str) -> HttpResponse:
    """Доработка отчёта по SearchLink."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")

    link = get_object_or_404(SearchLink, code=code, user=request.user)
    try:
        report = link.report
    except SearchReport.DoesNotExist:
        return redirect("search_links_my")

    if report.status != SearchReport.Status.REWORK:
        messages.info(request, "Отчёт не на доработке.")
        return redirect("search_links_my")

    if request.method == "POST":
        attachment = request.FILES.get("attachment")
        comment = (request.POST.get("comment") or "").strip()

        if attachment:
            report.attachment = attachment
        if comment:
            report.comment = comment
        report.status = SearchReport.Status.PENDING
        report.rework_comment = ""
        report.save(update_fields=["attachment", "comment", "status", "rework_comment", "updated_at"])
        messages.success(request, "Отчёт доработан и отправлен повторно.")
        return redirect("search_links_my")

    return render(request, "search/report_redo.html", {"link": link, "report": report})


# ─── Вебхук от бота ──────────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(["POST"])
def search_bot_start_webhook(request: HttpRequest) -> HttpResponse:
    """Вебхук от бота: POST /api/search-bot-start/ с code и telegram_id."""
    expected_secret = getattr(settings, "SEARCH_BOT_WEBHOOK_SECRET", "")
    auth = request.headers.get("Authorization", "")
    if not expected_secret or auth != f"Bearer {expected_secret}":
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=403)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    code = (data.get("code") or "").strip()
    telegram_id = data.get("telegram_id")

    if not code:
        return JsonResponse({"ok": False, "error": "missing_code"}, status=400)

    link = SearchLink.objects.filter(code=code).first()
    if not link:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)

    if link.bot_started:
        return JsonResponse({"ok": True, "already_started": True})

    link.bot_started = True
    link.bot_started_at = timezone.now()
    if telegram_id:
        link.telegram_id = telegram_id
    link.save(update_fields=["bot_started", "bot_started_at", "telegram_id", "updated_at"])

    return JsonResponse({"ok": True})


# ─── Админ: модерация отчётов ────────────────────────────────────────────────

@login_required
def admin_search_reports_list(request: HttpRequest) -> HttpResponse:
    """Список отчётов SearchLink для модерации (только где bot_started=True)."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    tab = request.GET.get("tab", "pending")
    reports_qs = SearchReport.objects.filter(search_link__bot_started=True).select_related("user", "search_link")

    if tab == "approved":
        reports_qs = reports_qs.filter(status=SearchReport.Status.APPROVED)
    elif tab == "rejected":
        reports_qs = reports_qs.filter(status=SearchReport.Status.REJECTED)
    elif tab == "rework":
        reports_qs = reports_qs.filter(status=SearchReport.Status.REWORK)
    else:
        reports_qs = reports_qs.filter(status=SearchReport.Status.PENDING)

    paginator = Paginator(reports_qs.order_by("-created_at"), 30)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    pending_count = SearchReport.objects.filter(
        search_link__bot_started=True, status=SearchReport.Status.PENDING
    ).count()

    return render(request, "core/admin_search_reports.html", {
        "page_obj": page_obj,
        "tab": tab,
        "pending_count": pending_count,
    })


@login_required
@require_http_methods(["POST"])
def admin_search_report_approve(request: HttpRequest, report_id: int) -> HttpResponse:
    """Одобрить отчёт SearchLink: +100₽ к balance пользователя."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    with transaction.atomic():
        report = SearchReport.objects.select_for_update().select_related("user", "search_link").filter(pk=report_id).first()
        if not report:
            messages.error(request, "Отчёт не найден.")
            return redirect("admin_search_reports_list")
        if report.status == SearchReport.Status.APPROVED:
            messages.info(request, "Отчёт уже одобрен.")
            return redirect("admin_search_reports_list")
        if not report.search_link.bot_started:
            messages.error(request, "Бот не стартован — нельзя одобрить.")
            return redirect("admin_search_reports_list")

        report.status = SearchReport.Status.APPROVED
        report.reviewed_at = timezone.now()
        report.reviewed_by = request.user
        report.save(update_fields=["status", "reviewed_at", "reviewed_by"])

        lead_owner = User.objects.select_for_update().get(pk=report.user_id)
        lead_owner.balance = (lead_owner.balance or 0) + SEARCH_REPORT_REWARD
        lead_owner.save(update_fields=["balance"])

    messages.success(request, f"Отчёт #{report_id} одобрен. +{SEARCH_REPORT_REWARD} руб. пользователю @{report.user.username}.")
    return redirect("admin_search_reports_list")


@login_required
@require_http_methods(["POST"])
def admin_search_report_reject(request: HttpRequest, report_id: int) -> HttpResponse:
    """Отклонить отчёт SearchLink."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    reason = (request.POST.get("reason") or "").strip()
    with transaction.atomic():
        report = SearchReport.objects.select_for_update().select_related("user").filter(pk=report_id).first()
        if not report:
            return redirect("admin_search_reports_list")
        was_approved = report.status == SearchReport.Status.APPROVED
        report.status = SearchReport.Status.REJECTED
        report.rejection_reason = reason
        report.reviewed_at = timezone.now()
        report.reviewed_by = request.user
        report.save(update_fields=["status", "rejection_reason", "reviewed_at", "reviewed_by"])
        if was_approved:
            lead_owner = User.objects.select_for_update().get(pk=report.user_id)
            lead_owner.balance = max(0, (lead_owner.balance or 0) - SEARCH_REPORT_REWARD)
            lead_owner.save(update_fields=["balance"])

    messages.success(request, f"Отчёт #{report_id} отклонён.")
    return redirect("admin_search_reports_list")


@login_required
@require_http_methods(["POST"])
def admin_search_report_rework(request: HttpRequest, report_id: int) -> HttpResponse:
    """Отправить отчёт SearchLink на доработку."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    comment = (request.POST.get("comment") or "").strip()
    with transaction.atomic():
        report = SearchReport.objects.select_for_update().select_related("user").filter(pk=report_id).first()
        if not report:
            return redirect("admin_search_reports_list")
        was_approved = report.status == SearchReport.Status.APPROVED
        report.status = SearchReport.Status.REWORK
        report.rework_comment = comment
        report.reviewed_at = timezone.now()
        report.reviewed_by = request.user
        report.save(update_fields=["status", "rework_comment", "reviewed_at", "reviewed_by"])
        if was_approved:
            lead_owner = User.objects.select_for_update().get(pk=report.user_id)
            lead_owner.balance = max(0, (lead_owner.balance or 0) - SEARCH_REPORT_REWARD)
            lead_owner.save(update_fields=["balance"])

    messages.success(request, f"Отчёт #{report_id} отправлен на доработку.")
    return redirect("admin_search_reports_list")


@login_required
def admin_search_report_attachment(request: HttpRequest, report_id: int) -> HttpResponse:
    """Просмотр вложения отчёта SearchLink."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    report = get_object_or_404(SearchReport, pk=report_id)
    if not report.attachment:
        return HttpResponse("Нет вложения.", status=404)
    from django.shortcuts import redirect as redir
    return redir(report.attachment.url)
