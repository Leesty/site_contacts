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


def _get_client_ip(request: HttpRequest) -> str:
    """Получить IP клиента (учитывая X-Forwarded-For за прокси)."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")

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
    """Список SearchLink-ов менеджера с формой создания и вкладками по статусу."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")
    if request.user.partner_owner_id and not request.user.ref_searchlink_enabled:
        messages.warning(request, "SearchLink ещё не активирован для вашего аккаунта. Обратитесь к менеджеру.")
        return redirect("dashboard")

    tab = request.GET.get("tab", "all")
    q = (request.GET.get("q") or "").strip()
    links_qs = SearchLink.objects.filter(user=request.user)

    if tab == "rework":
        links_qs = links_qs.filter(report__status=SearchReport.Status.REWORK)
    elif tab == "rejected":
        links_qs = links_qs.filter(report__status=SearchReport.Status.REJECTED)
    elif tab == "pending":
        links_qs = links_qs.filter(report__status=SearchReport.Status.PENDING)
    elif tab == "approved":
        links_qs = links_qs.filter(report__status=SearchReport.Status.APPROVED)
    elif tab == "bot_waiting":
        links_qs = links_qs.filter(bot_started=False)
    elif tab == "bot_started":
        links_qs = links_qs.filter(bot_started=True)

    if q:
        from django.db.models import Q
        links_qs = links_qs.filter(Q(lead_name__icontains=q) | Q(code__icontains=q))
    links_qs = links_qs.order_by("-created_at")

    # Счётчики для бейджей
    user_links = SearchLink.objects.filter(user=request.user)
    rework_count = user_links.filter(report__status=SearchReport.Status.REWORK).count()
    rejected_count = user_links.filter(report__status=SearchReport.Status.REJECTED).count()
    pending_count = user_links.filter(report__status=SearchReport.Status.PENDING).count()
    bot_waiting_count = user_links.filter(bot_started=False).count()
    bot_started_count = user_links.filter(bot_started=True).count()

    paginator = Paginator(links_qs, 30)
    page_obj = paginator.get_page(request.GET.get("page", 1))

    # Подгрузить отчёты для отображения статусов
    for link in page_obj:
        try:
            link.report_obj = link.report
        except SearchReport.DoesNotExist:
            link.report_obj = None

    search_reward = SEARCH_REPORT_REWARD
    if request.user.partner_owner_id and request.user.ref_searchlink_enabled:
        search_reward = SEARCH_REPORT_REWARD - request.user.ref_searchlink_manager_cut

    return render(request, "search/my_links.html", {
        "page_obj": page_obj,
        "q": q,
        "tab": tab,
        "rework_count": rework_count,
        "rejected_count": rejected_count,
        "pending_count": pending_count,
        "bot_waiting_count": bot_waiting_count,
        "bot_started_count": bot_started_count,
        "search_reward": search_reward,
    })


# ─── Менеджер: создание ссылки ────────────────────────────────────────────────

@login_required
@require_http_methods(["POST"])
def search_link_create(request: HttpRequest) -> HttpResponse:
    """Создать новый SearchLink."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")
    if request.user.partner_owner_id and not request.user.ref_searchlink_enabled:
        messages.warning(request, "SearchLink ещё не активирован для вашего аккаунта.")
        return redirect("dashboard")

    lead_name = (request.POST.get("lead_name") or "").strip()[:200]
    if not lead_name:
        messages.error(request, "Укажите имя/ник лида.")
        return redirect("search_links_my")

    link = SearchLink.objects.create(
        user=request.user, lead_name=lead_name, creator_ip=_get_client_ip(request),
    )
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

    # Сохраняем IP посетителя (только первый визит — не перезаписываем)
    # Проверка на накрутку происходит при одобрении, не при просмотре лендинга
    visitor_ip = _get_client_ip(request)
    if not link.visitor_ip:
        link.visitor_ip = visitor_ip
        link.save(update_fields=["visitor_ip"])

    return render(request, "search/landing.html", {
        "lead_name": link.lead_name,
        "deep_link": link.deep_link,
        "code": link.code,
    })


# ─── Публичный клик «Перейти в бота» ──────────────────────────────────────────

def search_link_go(request: HttpRequest, code: str) -> HttpResponse:
    """Клик на кнопку «Перейти в бота» на лендинге. Записывает IP и проверяет накрутку."""
    link = SearchLink.objects.filter(code=code).first()
    if not link:
        return render(request, "search/unavailable.html", status=404)

    clicker_ip = _get_client_ip(request)
    skip_ip = link.user_id == 285
    if not skip_ip and link.creator_ip and link.creator_ip == clicker_ip and not link.self_click:
        link.self_click = True
        link.save(update_fields=["self_click"])
        logger.warning("SearchLink self-click on go: link=%s user=%s ip=%s", link.code, link.user_id, clicker_ip)

    return redirect(link.deep_link)


# ─── Менеджер: отчёт ─────────────────────────────────────────────────────────

@login_required
def search_report_create(request: HttpRequest, code: str) -> HttpResponse:
    """Отправить отчёт по SearchLink."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")
    if request.user.partner_owner_id and not request.user.ref_searchlink_enabled:
        messages.warning(request, "SearchLink ещё не активирован для вашего аккаунта.")
        return redirect("dashboard")

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
        raw_contact = (request.POST.get("raw_contact") or "").strip()
        attachment = request.FILES.get("attachment")
        comment = (request.POST.get("comment") or "").strip()

        if not raw_contact:
            messages.error(request, "Укажите контакт или ссылку на клиента.")
            return render(request, "search/report_form.html", {"link": link})
        if not attachment:
            messages.error(request, "Приложите скриншот или видео.")
            return render(request, "search/report_form.html", {"link": link})

        report = SearchReport.objects.create(
            user=request.user,
            search_link=link,
            lead_date=lead_date or timezone.now().date(),
            raw_contact=raw_contact,
            attachment=attachment,
            comment=comment,
        )
        from .lead_utils import compress_lead_attachment
        compress_lead_attachment(report)
        messages.success(request, "Отчёт отправлен.")
        return redirect("search_links_my")

    return render(request, "search/report_form.html", {"link": link})


@login_required
def search_report_attachment(request: HttpRequest, code: str) -> HttpResponse:
    """Просмотр вложения отчёта SearchLink (для владельца ссылки)."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")
    link = get_object_or_404(SearchLink, code=code, user=request.user)
    try:
        report = link.report
    except SearchReport.DoesNotExist:
        return HttpResponse("Нет отчёта.", status=404)
    if not report.attachment:
        return HttpResponse("Нет вложения.", status=404)
    from django.shortcuts import redirect as redir
    return redir(report.attachment.url)


@login_required
def search_report_redo(request: HttpRequest, code: str) -> HttpResponse:
    """Доработка отчёта по SearchLink."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")
    if request.user.partner_owner_id and not request.user.ref_searchlink_enabled:
        messages.warning(request, "SearchLink ещё не активирован для вашего аккаунта.")
        return redirect("dashboard")

    link = get_object_or_404(SearchLink, code=code, user=request.user)
    try:
        report = link.report
    except SearchReport.DoesNotExist:
        return redirect("search_links_my")

    if report.status != SearchReport.Status.REWORK:
        messages.info(request, "Отчёт не на доработке.")
        return redirect("search_links_my")

    if request.method == "POST":
        raw_contact = (request.POST.get("raw_contact") or "").strip()
        attachment = request.FILES.get("attachment")
        comment = (request.POST.get("comment") or "").strip()

        if raw_contact:
            report.raw_contact = raw_contact
        if attachment:
            report.attachment = attachment
        if comment:
            report.comment = comment
        report.status = SearchReport.Status.PENDING
        report.rework_comment = ""
        report.save(update_fields=["raw_contact", "attachment", "comment", "status", "rework_comment", "updated_at"])
        if attachment:
            from .lead_utils import compress_lead_attachment
            compress_lead_attachment(report)
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
    # Только отчёты где бот реально стартовал (вебхук подтвердил)
    reports_qs = SearchReport.objects.filter(search_link__bot_started=True).select_related("user", "search_link", "reviewed_by")

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
        # Проверка накрутки: флаг self_click ставится при клике на кнопку «Перейти в бота»
        _sl = report.search_link
        if _sl.self_click:
            messages.error(
                request,
                f"Отчёт #{report_id} аннулирован: IP менеджера совпал с IP посетителя (накрутка). "
                f"IP: {_sl.creator_ip}",
            )
            report.status = SearchReport.Status.REJECTED
            report.rejection_reason = "Автоотклонение: менеджер сам нажал кнопку перехода в бота."
            report.reviewed_at = timezone.now()
            report.reviewed_by = request.user
            report.save(update_fields=["status", "rejection_reason", "reviewed_at", "reviewed_by"])
            return redirect("admin_search_reports_list")

        report.status = SearchReport.Status.APPROVED
        report.reviewed_at = timezone.now()
        report.reviewed_by = request.user
        report.save(update_fields=["status", "reviewed_at", "reviewed_by"])

        lead_owner = User.objects.select_for_update().get(pk=report.user_id)
        from .models import PartnerEarning, log_balance_change

        # Разделение награды для рефералов
        if lead_owner.partner_owner_id and lead_owner.ref_searchlink_enabled:
            manager_cut = max(1, min(99, lead_owner.ref_searchlink_manager_cut))
            ref_reward = SEARCH_REPORT_REWARD - manager_cut
        else:
            manager_cut = 0
            ref_reward = SEARCH_REPORT_REWARD

        _old = lead_owner.balance or 0
        lead_owner.balance = _old + ref_reward
        lead_owner.save(update_fields=["balance"])
        log_balance_change(lead_owner, "balance", _old, lead_owner.balance, f"search_approve#{report_id} +{ref_reward}", request.user)

        if manager_cut > 0:
            partner = User.objects.select_for_update().get(pk=lead_owner.partner_owner_id)
            _old_pb = partner.balance or 0
            partner.balance = _old_pb + manager_cut
            partner.save(update_fields=["balance"])
            log_balance_change(partner, "balance", _old_pb, partner.balance, f"search_partner_earning report#{report_id} +{manager_cut}", request.user)
            PartnerEarning.objects.create(partner=partner, search_report=report, amount=manager_cut)

    msg = f"Отчёт #{report_id} одобрен. +{ref_reward} руб. пользователю @{report.user.username}."
    if manager_cut > 0:
        msg += f" +{manager_cut} руб. менеджеру."
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": msg})
    messages.success(request, msg)
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
            from .models import PartnerEarning, log_balance_change
            pe = PartnerEarning.objects.filter(search_report=report).select_related("partner").first()
            if pe:
                partner = User.objects.select_for_update().get(pk=pe.partner_id)
                _old_pb = partner.balance or 0
                partner.balance = _old_pb - pe.amount
                partner.save(update_fields=["balance"])
                log_balance_change(partner, "balance", _old_pb, partner.balance, f"search_reject#{report_id} partner_rollback -{pe.amount}", request.user)
                _ref_reward = SEARCH_REPORT_REWARD - pe.amount
                pe.delete()
            else:
                _ref_reward = SEARCH_REPORT_REWARD
            lead_owner = User.objects.select_for_update().get(pk=report.user_id)
            _old = lead_owner.balance or 0
            lead_owner.balance = _old - _ref_reward
            lead_owner.save(update_fields=["balance"])
            log_balance_change(lead_owner, "balance", _old, lead_owner.balance, f"search_reject#{report_id} -{_ref_reward}", request.user)

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
            from .models import PartnerEarning, log_balance_change
            pe = PartnerEarning.objects.filter(search_report=report).select_related("partner").first()
            if pe:
                partner = User.objects.select_for_update().get(pk=pe.partner_id)
                _old_pb = partner.balance or 0
                partner.balance = _old_pb - pe.amount
                partner.save(update_fields=["balance"])
                log_balance_change(partner, "balance", _old_pb, partner.balance, f"search_rework#{report_id} partner_rollback -{pe.amount}", request.user)
                _ref_reward = SEARCH_REPORT_REWARD - pe.amount
                pe.delete()
            else:
                _ref_reward = SEARCH_REPORT_REWARD
            lead_owner = User.objects.select_for_update().get(pk=report.user_id)
            _old = lead_owner.balance or 0
            lead_owner.balance = _old - _ref_reward
            lead_owner.save(update_fields=["balance"])
            log_balance_change(lead_owner, "balance", _old, lead_owner.balance, f"search_rework#{report_id} -{_ref_reward}", request.user)

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


# ─── Админ: полная статистика SearchLink ──────────────────────────────────────

@login_required
def admin_search_stats(request: HttpRequest) -> HttpResponse:
    """Полная статистика SearchLink для главного админа: все ссылки, все пользователи."""
    if getattr(request.user, "role", None) != "main_admin":
        return HttpResponseForbidden("Только для главного админа.")

    from django.db.models import Count, Q

    q = (request.GET.get("q") or "").strip()
    user_filter = request.GET.get("user")
    bot_filter = request.GET.get("bot", "")
    report_filter = request.GET.get("report", "")
    tab = request.GET.get("tab", "users")

    # Per-user stats
    user_stats = (
        User.objects.filter(search_links__isnull=False)
        .annotate(
            links_total=Count("search_links", distinct=True),
            bot_started_count=Count("search_links", filter=Q(search_links__bot_started=True), distinct=True),
            visited_count=Count("search_links", filter=Q(search_links__visitor_ip__isnull=False), distinct=True),
            reports_count=Count("search_reports", distinct=True),
            approved_count=Count("search_reports", filter=Q(search_reports__status="approved"), distinct=True),
            rejected_count=Count("search_reports", filter=Q(search_reports__status="rejected"), distinct=True),
            pending_count=Count("search_reports", filter=Q(search_reports__status="pending"), distinct=True),
            self_click_count=Count("search_links", filter=Q(search_links__self_click=True), distinct=True),
        )
        .order_by("-links_total")
    )

    # Global totals
    total_links = SearchLink.objects.count()
    total_bot_started = SearchLink.objects.filter(bot_started=True).count()
    total_visited = SearchLink.objects.filter(visitor_ip__isnull=False).count()
    total_reports = SearchReport.objects.count()
    total_approved = SearchReport.objects.filter(status="approved").count()
    total_pending = SearchReport.objects.filter(status="pending").count()

    # Links tab — full list with filtering
    links_qs = SearchLink.objects.select_related("user").order_by("-created_at")
    if user_filter:
        links_qs = links_qs.filter(user_id=user_filter)
    if q:
        links_qs = links_qs.filter(
            Q(lead_name__icontains=q) | Q(code__icontains=q) |
            Q(user__username__icontains=q)
        )
    if bot_filter == "started":
        links_qs = links_qs.filter(bot_started=True)
    elif bot_filter == "not_started":
        links_qs = links_qs.filter(bot_started=False)
    elif bot_filter == "clicked":
        links_qs = links_qs.filter(bot_started=False, visitor_ip__isnull=False)
    elif bot_filter == "no_click":
        links_qs = links_qs.filter(visitor_ip__isnull=True)
    if report_filter == "has":
        links_qs = links_qs.filter(report__isnull=False)
    elif report_filter == "no":
        links_qs = links_qs.filter(report__isnull=True)
    elif report_filter == "approved":
        links_qs = links_qs.filter(report__status="approved")
    elif report_filter == "pending":
        links_qs = links_qs.filter(report__status="pending")

    paginator = Paginator(links_qs, 50)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    link_ids = [sl.id for sl in page_obj]
    reports_map = {r.search_link_id: r for r in SearchReport.objects.filter(search_link_id__in=link_ids)}
    for link in page_obj:
        link.report_obj = reports_map.get(link.id)

    return render(request, "search/admin_stats.html", {
        "tab": tab,
        "q": q,
        "user_filter": user_filter,
        "bot_filter": bot_filter,
        "report_filter": report_filter,
        "user_stats": user_stats,
        "page_obj": page_obj,
        "total_links": total_links,
        "total_bot_started": total_bot_started,
        "total_visited": total_visited,
        "total_reports": total_reports,
        "total_approved": total_approved,
        "total_pending": total_pending,
    })
