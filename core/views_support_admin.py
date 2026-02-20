import os
import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone as dt_utc
from io import BytesIO
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Case, Count, F, IntegerField, Max, Q, Sum, Value, When
from django.http import FileResponse, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from openpyxl import Workbook, load_workbook

from .forms import BaseCategoryUploadForm, BaseExcelUploadForm, LeadRejectForm, LeadReworkForm, LeadsExcelUploadForm
from .models import (
    BaseType,
    BasesImportJob,
    Contact,
    ContactRequest,
    Lead,
    LeadType,
    SupportMessage,
    SupportThread,
    User,
    UserBaseLimit,
    WithdrawalRequest,
)


def _require_support(request: HttpRequest) -> bool:
    user = request.user
    if not user.is_authenticated:
        return False
    # Доступ для ролей поддержки/админов и для staff/superuser
    if getattr(user, "is_support", False) or user.is_staff or user.is_superuser:
        return True
    return False


@login_required
def admin_users_pending(request: HttpRequest) -> HttpResponse:
    """Список новых пользователей со статусом pending с кнопками одобрения/бана."""

    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    if request.method == "POST":
        user_id = request.POST.get("user_id")
        action = request.POST.get("action")
        if user_id and action in {"approve", "ban"}:
            user = get_object_or_404(User, pk=user_id)
            if action == "approve":
                user.status = User.Status.APPROVED
                messages.success(request, f"Пользователь @{user.username} одобрен.")
            elif action == "ban":
                user.status = User.Status.BANNED
                messages.warning(request, f"Пользователь @{user.username} заблокирован.")
            user.save(update_fields=["status"])
        return redirect("admin_users_pending")

    pending_users = User.objects.filter(status=User.Status.PENDING).order_by("-date_joined")

    return render(
        request,
        "core/admin_users_pending.html",
        {
            "pending_users": pending_users,
        },
    )


@login_required
def support_threads_list(request: HttpRequest) -> HttpResponse:
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    threads = (
        SupportThread.objects.select_related("user")
        .annotate(
            last_message_at=Max("messages__created_at"),
            messages_count=Count("messages"),
            is_unread=Case(
                When(Q(last_read_at__isnull=True), then=Value(1)),
                When(updated_at__gt=F("last_read_at"), then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("-is_unread", "-updated_at")
    )
    threads_agg = SupportThread.objects.aggregate(m=Max("updated_at"))
    admin_threads_updated_at = threads_agg["m"].isoformat() if threads_agg.get("m") else ""

    return render(
        request,
        "core/support_threads_list.html",
        {
            "threads": threads,
            "admin_threads_updated_at": admin_threads_updated_at,
        },
    )


@login_required
def support_thread_detail(request: HttpRequest, pk: int) -> HttpResponse:
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    thread = get_object_or_404(SupportThread.objects.select_related("user"), pk=pk)

    # Открытие диалога = «прочитано»
    thread.last_read_at = timezone.now()
    thread.save(update_fields=["last_read_at"])

    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()
        if text:
            SupportMessage.objects.create(
                thread=thread,
                sender=request.user,
                is_from_support=True,
                text=text,
            )
            thread.updated_at = timezone.now()
            thread.save(update_fields=["updated_at"])
            messages.success(request, "Ответ отправлен пользователю.")
            return redirect("support_thread_detail", pk=thread.pk)

    messages_qs = thread.messages.select_related("sender").order_by("created_at")
    threads_agg = SupportThread.objects.aggregate(m=Max("updated_at"))
    admin_threads_updated_at = threads_agg["m"].isoformat() if threads_agg.get("m") else ""

    return render(
        request,
        "core/support_thread_detail.html",
        {
            "thread": thread,
            "support_messages": messages_qs,
            "admin_threads_updated_at": admin_threads_updated_at,
        },
    )


@login_required
def support_message_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Удаление сообщения в диалоге (только для сотрудников поддержки)."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    msg = get_object_or_404(SupportMessage.objects.select_related("thread"), pk=pk)
    thread_pk = msg.thread_id
    msg.delete()
    messages.success(request, "Сообщение удалено.")
    return redirect("support_thread_detail", pk=thread_pk)


@login_required
def support_thread_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """Удаление чатов отключено — сохранение истории обращений."""
    return HttpResponseForbidden("Удаление чатов отключено.")


@login_required
def support_thread_by_user(request: HttpRequest, user_id: int) -> HttpResponse:
    """Открыть диалог поддержки с пользователем (редирект на страницу диалога)."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    target_user = get_object_or_404(User, pk=user_id)
    thread, _ = SupportThread.objects.get_or_create(user=target_user, is_closed=False)
    return redirect("support_thread_detail", pk=thread.pk)


def _day_bounds_lead_stats():
    """Границы «дня» для лидов (20:00 МСК), как в боте."""
    tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)
    from_day = now.date()
    if now.hour >= 20:
        from_day = now.date() + timedelta(days=1)

    def bounds(day: date):
        start = datetime.combine(day - timedelta(days=1), time(hour=20), tzinfo=tz)
        end = datetime.combine(day, time(hour=20), tzinfo=tz)
        return start.astimezone(dt_utc.utc), end.astimezone(dt_utc.utc)

    today_start, today_end = bounds(from_day)
    yesterday_start, yesterday_end = bounds(from_day - timedelta(days=1))
    return today_start, today_end, yesterday_start, yesterday_end


@login_required
def admin_user_lead_stats(request: HttpRequest, user_id: int) -> HttpResponse:
    """Статистика лидов по выбранному пользователю (для админа)."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    target_user = get_object_or_404(User, pk=user_id)
    today_start, today_end, yesterday_start, yesterday_end = _day_bounds_lead_stats()
    today_count = Lead.objects.filter(
        user=target_user,
        status=Lead.Status.APPROVED,
        created_at__gte=today_start,
        created_at__lt=today_end,
    ).count()
    yesterday_count = Lead.objects.filter(
        user=target_user,
        status=Lead.Status.APPROVED,
        created_at__gte=yesterday_start,
        created_at__lt=yesterday_end,
    ).count()
    total_count = Lead.objects.filter(user=target_user, status=Lead.Status.APPROVED).count()
    return render(
        request,
        "core/admin_user_lead_stats.html",
        {
            "target_user": target_user,
            "today_count": today_count,
            "yesterday_count": yesterday_count,
            "total_count": total_count,
        },
    )


@login_required
def admin_user_leads_list(request: HttpRequest, user_id: int) -> HttpResponse:
    """Список лидов (отчётов) пользователя по вкладкам: новые, в доработке, принятые, отклонённые."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    target_user = get_object_or_404(User, pk=user_id)
    tab = request.GET.get("tab", "all")
    if tab not in ("all", "new", "rework", "approved", "rejected"):
        tab = "all"
    base = Lead.objects.filter(user=target_user).select_related("lead_type", "base_type", "reviewed_by")
    if tab == "all":
        qs = base.order_by("-created_at")
    elif tab == "new":
        qs = base.filter(status=Lead.Status.PENDING).order_by("-created_at")
    elif tab == "rework":
        qs = base.filter(status=Lead.Status.REWORK).order_by("-reviewed_at")
    elif tab == "approved":
        qs = base.filter(status=Lead.Status.APPROVED).order_by("-reviewed_at")
    else:
        qs = base.filter(status=Lead.Status.REJECTED).order_by("-reviewed_at")
    paginator = Paginator(qs, 50)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    lead_approve_reward = getattr(settings, "LEAD_APPROVE_REWARD", 40)
    return render(
        request,
        "core/admin_user_leads_list.html",
        {
            "target_user": target_user,
            "page_obj": page_obj,
            "tab": tab,
            "lead_approve_reward": lead_approve_reward,
        },
    )


@login_required
def admin_leads_all_new(request: HttpRequest) -> HttpResponse:
    """Единая страница «Отчёты»: 4 вкладки — Новые отчёты, В доработке, Принятые, Отклонённые."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    tab = request.GET.get("tab", "new")
    if tab not in ("new", "rework", "approved", "rejected"):
        tab = "new"
    try:
        base = Lead.objects.select_related("user", "lead_type", "base_type", "reviewed_by")
        if tab == "new":
            qs = base.filter(status=Lead.Status.PENDING).order_by("-created_at")
        elif tab == "rework":
            qs = base.filter(status=Lead.Status.REWORK).order_by("-reviewed_at")
        elif tab == "approved":
            qs = base.filter(status=Lead.Status.APPROVED).order_by("-reviewed_at")
        else:  # rejected
            qs = base.filter(status=Lead.Status.REJECTED).order_by("-reviewed_at")
        paginator = Paginator(qs, 50)
        page_number = request.GET.get("page", 1)
        page_obj = paginator.get_page(page_number)
    except (OperationalError, ProgrammingError) as e:
        import logging
        logging.getLogger(__name__).exception("admin_leads_all_new: DB error (run migrate?): %s", e)
        return HttpResponse(
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Ошибка</title></head><body style='font-family:sans-serif;padding:2rem;'>"
            "<h1>Ошибка базы данных</h1>"
            "<p>Страница «Отчёты» не загружается. Часто это из‑за неприменённых миграций после обновления кода.</p>"
            "<p><strong>На сервере выполните:</strong> <code>python manage.py migrate</code></p>"
            "<p><a href='/staff/'>← В кабинет</a></p></body></html>",
            status=500,
            content_type="text/html; charset=utf-8",
        )
    lead_approve_reward = getattr(settings, "LEAD_APPROVE_REWARD", 40)
    return render(
        request,
        "core/admin_leads_all_new.html",
        {
            "page_obj": page_obj,
            "lead_approve_reward": lead_approve_reward,
            "tab": tab,
        },
    )


@login_required
def admin_media_storage_status(request: HttpRequest) -> HttpResponse:
    """Диагностика: куда сохраняются медиа (S3 из env или из админки), подключается ли S3. POST с action=test_write — реальная проверка записи."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    from django.core.files.storage import default_storage
    from django.core.files.base import ContentFile
    from core.storage import get_media_storage_diagnostic
    write_result = None
    if request.method == "POST" and request.POST.get("action") == "test_write":
        test_name = "_media_write_test.txt"
        try:
            default_storage.save(test_name, ContentFile(b"test-write-check"))
            exists = default_storage.exists(test_name)
            default_storage.delete(test_name)
            if exists:
                write_result = {"ok": True, "message": "Запись в хранилище прошла успешно. Файл создан и удалён. Если это S3 — проверьте бакет в панели (возможно, обновление счётчика с задержкой)."}
            else:
                write_result = {"ok": False, "message": "Файл записан, но сразу не найден (exists() вернул False)."}
        except Exception as e:
            import traceback
            write_result = {"ok": False, "message": str(e), "detail": traceback.format_exc()}
    diag = get_media_storage_diagnostic()
    return render(
        request,
        "core/admin_media_storage_status.html",
        {"diag": diag, "write_result": write_result},
    )


LEAD_APPROVE_REWARD = getattr(settings, "LEAD_APPROVE_REWARD", 40)


@login_required
@require_http_methods(["POST"])
def admin_lead_approve(request: HttpRequest, user_id: int, lead_id: int) -> HttpResponse:
    """Одобрить лид: начислить пользователю LEAD_APPROVE_REWARD руб., статус — одобрен."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    with transaction.atomic():
        lead = Lead.objects.select_for_update().filter(pk=lead_id, user_id=user_id).select_related("user").first()
        if not lead:
            return redirect("admin_user_leads_list", user_id=user_id)
        if lead.status == Lead.Status.APPROVED:
            messages.info(request, "Лид уже одобрен.")
            return redirect("admin_user_leads_list", user_id=user_id)
        if lead.status not in (Lead.Status.PENDING, Lead.Status.REWORK):
            messages.warning(request, "Можно одобрять только лиды на проверке или на доработке.")
            return redirect("admin_user_leads_list", user_id=user_id)
        lead.status = Lead.Status.APPROVED
        lead.rejection_reason = ""
        lead.rework_comment = ""
        lead.reviewed_at = timezone.now()
        lead.reviewed_by = request.user
        lead.save(update_fields=["status", "rejection_reason", "rework_comment", "reviewed_at", "reviewed_by"])
        lead.user.balance = (getattr(lead.user, "balance", 0) or 0) + LEAD_APPROVE_REWARD
        lead.user.save(update_fields=["balance"])
    msg = f"Лид #{lead_id} одобрен. Пользователю начислено {LEAD_APPROVE_REWARD} руб."
    messages.success(request, msg)
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "message": msg})
    next_url = request.GET.get("next") or request.POST.get("next")
    if next_url:
        from django.utils.http import url_has_allowed_host_and_scheme
        if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
            return redirect(next_url)
    return redirect("admin_user_leads_list", user_id=user_id)


@login_required
def admin_lead_reject(request: HttpRequest, user_id: int, lead_id: int) -> HttpResponse:
    """Отклонить лид — форма с причиной отклонения. Поддерживает и одобренные: списывает баланс."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    lead = get_object_or_404(Lead, pk=lead_id, user_id=user_id)
    if request.method == "POST":
        form = LeadRejectForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                lead_refresh = Lead.objects.select_for_update().select_related("user").get(pk=lead_id, user_id=user_id)
                was_approved = lead_refresh.status == Lead.Status.APPROVED
                lead_refresh.status = Lead.Status.REJECTED
                lead_refresh.rejection_reason = form.cleaned_data["rejection_reason"].strip()
                lead_refresh.rework_comment = ""
                lead_refresh.reviewed_at = timezone.now()
                lead_refresh.reviewed_by = request.user
                lead_refresh.save(update_fields=["status", "rejection_reason", "rework_comment", "reviewed_at", "reviewed_by"])
                if was_approved:
                    reward = getattr(settings, "LEAD_APPROVE_REWARD", 40)
                    lead_refresh.user.balance = max(0, (lead_refresh.user.balance or 0) - reward)
                    lead_refresh.user.save(update_fields=["balance"])
            messages.success(request, f"Лид #{lead_id} отклонён." + (" Баланс уменьшен." if was_approved else ""))
            from django.utils.http import url_has_allowed_host_and_scheme
            next_url = request.GET.get("next") or request.POST.get("next")
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect("admin_user_leads_list", user_id=user_id)
    else:
        form = LeadRejectForm()
    return render(
        request,
        "core/admin_lead_reject.html",
        {"lead": lead, "target_user": lead.user, "form": form, "next_url": request.GET.get("next")},
    )


@login_required
def admin_lead_rework(request: HttpRequest, user_id: int, lead_id: int) -> HttpResponse:
    """Отправить лид на доработку — форма с указанием, что доработать. Поддерживает одобренные: списывает баланс."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    lead = get_object_or_404(Lead, pk=lead_id, user_id=user_id)
    if request.method == "POST":
        form = LeadReworkForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                lead_refresh = Lead.objects.select_for_update().select_related("user").get(pk=lead_id, user_id=user_id)
                was_approved = lead_refresh.status == Lead.Status.APPROVED
                lead_refresh.status = Lead.Status.REWORK
                lead_refresh.rework_comment = form.cleaned_data["rework_comment"].strip()
                lead_refresh.rejection_reason = ""
                lead_refresh.reviewed_at = timezone.now()
                lead_refresh.reviewed_by = request.user
                lead_refresh.save(update_fields=["status", "rework_comment", "rejection_reason", "reviewed_at", "reviewed_by"])
                if was_approved:
                    reward = getattr(settings, "LEAD_APPROVE_REWARD", 40)
                    lead_refresh.user.balance = max(0, (lead_refresh.user.balance or 0) - reward)
                    lead_refresh.user.save(update_fields=["balance"])
            messages.success(request, f"Лид #{lead_id} отправлен на доработку." + (" Баланс уменьшен." if was_approved else ""))
            from django.utils.http import url_has_allowed_host_and_scheme
            next_url = request.GET.get("next") or request.POST.get("next")
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            return redirect("admin_user_leads_list", user_id=user_id)
    else:
        form = LeadReworkForm()
    return render(
        request,
        "core/admin_lead_rework.html",
        {"lead": lead, "target_user": lead.user, "form": form, "next_url": request.GET.get("next")},
    )


@login_required
def admin_lead_attachment(request: HttpRequest, user_id: int, lead_id: int) -> HttpResponse:
    """Отдаёт вложение лида (фото/видео). Доступ: staff или владелец лида. В проде /media/ не раздаётся — используем эту вьюху."""
    import logging
    lead = get_object_or_404(Lead, pk=lead_id, user_id=user_id)
    if not lead.attachment:
        return HttpResponseForbidden("У этого лида нет вложения.")
    if not _require_support(request) and request.user.id != lead.user_id:
        return HttpResponseForbidden("Нет доступа к этому файлу.")
    try:
        f = lead.attachment.open("rb")
    except OSError as e:
        logging.getLogger(__name__).warning(
            "Lead attachment missing on disk: lead_id=%s user_id=%s path=%s err=%s",
            lead_id, user_id, lead.attachment.name, e,
        )
        username = getattr(lead.user, "username", "id:%s" % lead.user_id)
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'><title>Файл недоступен</title></head><body style='font-family:sans-serif;padding:2rem;'>"
            "<h1>Файл не найден</h1>"
            "<p>Вложение этого отчёта в базе есть, но файл на сервере отсутствует. Обычно так бывает, если файл был загружен до настройки S3 и потерялся при обновлении/редеплое сервера.</p>"
            "<p><strong>Что сделать:</strong> попросите пользователя <strong>%s</strong> заново загрузить скриншот или видео через «Мои лиды» → доработка отчёта.</p>"
            "<p>После настройки S3 в админке (Настройки хранилища медиа) новые загрузки не будут теряться при редеплое.</p>"
            "<p><a href='javascript:history.back()'>← Назад</a></p></body></html>"
        ) % (username,)
        return HttpResponse(html, status=404, content_type="text/html; charset=utf-8")
    filename = lead.attachment.name.split("/")[-1] if lead.attachment.name else "attachment"
    response = FileResponse(f, as_attachment=False)
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
def admin_user_leads_export(request: HttpRequest, user_id: int, period: str) -> HttpResponse:
    """Выгрузка лидов пользователя за сегодня, вчера или все (Excel). В колонке «Скриншот» — ссылка на файл."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    if period not in ("today", "yesterday", "all"):
        return HttpResponseForbidden("Недопустимый период.")
    target_user = get_object_or_404(User, pk=user_id)
    leads_qs = Lead.objects.filter(user=target_user).select_related("lead_type", "base_type").order_by("-created_at")
    if period in ("today", "yesterday"):
        today_start, today_end, yesterday_start, yesterday_end = _day_bounds_lead_stats()
        if period == "today":
            start, end = today_start, today_end
        else:
            start, end = yesterday_start, yesterday_end
        leads_qs = leads_qs.filter(created_at__gte=start, created_at__lt=end)
    leads = list(leads_qs)
    wb = Workbook()
    ws = wb.active
    ws.title = "Лиды"
    ws.append(
        [
            "ID",
            "Пользователь",
            "Тип лида",
            "Тип базы",
            "Контакт (raw)",
            "Источник",
            "Комментарий",
            "Создан",
            "Скриншот (ссылка)",
        ]
    )
    for lead in leads:
        screenshot_url = ""
        if lead.attachment:
            screenshot_url = request.build_absolute_uri(
                reverse("admin_lead_attachment", args=[lead.user_id, lead.id])
            )
        ws.append(
            [
                lead.id,
                lead.user.username,
                lead.lead_type.name if lead.lead_type else "",
                lead.base_type.name if lead.base_type else "",
                lead.raw_contact,
                lead.source,
                lead.comment,
                lead.created_at.isoformat(),
                screenshot_url,
            ]
        )
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    filename = f"leads_{target_user.username}_{period}.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def admin_user_limits(request: HttpRequest, user_id: int) -> HttpResponse:
    """Редирект: окошко выдачи лимитов убрано, используйте «Выдача контактов по запросу»."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    return redirect("admin_all_users")


@login_required
def admin_user_balance(request: HttpRequest, user_id: int) -> HttpResponse:
    """Начисление или списание рублей пользователю (баланс)."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    target_user = get_object_or_404(User, pk=user_id)
    if request.method == "POST":
        try:
            amount = int(request.POST.get("amount") or 0)
            action = request.POST.get("action")
        except (TypeError, ValueError):
            amount = 0
            action = None
        if amount > 0 and action in ("add", "subtract"):
            current = target_user.balance or 0
            if action == "add":
                target_user.balance = current + amount
            else:
                target_user.balance = max(0, current - amount)
            target_user.save(update_fields=["balance"])
            msg = f"Начислено {amount} руб." if action == "add" else f"Списано {amount} руб."
            messages.success(request, f"Баланс @{target_user.username}: {msg}. Текущий баланс: {target_user.balance} руб.")
            return redirect("admin_all_users")
        messages.warning(request, "Укажите положительное число и действие (начислить / списать).")
    return render(
        request,
        "core/admin_user_balance.html",
        {"target_user": target_user},
    )


@login_required
def admin_user_search(request: HttpRequest) -> HttpResponse:
    """Поиск пользователей по нику (или части) — для выпадающего списка."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    q = (request.GET.get("q") or "").strip().lstrip("@")[:50]
    if not q:
        return JsonResponse({"users": []})
    users = (
        User.objects.filter(username__icontains=q)
        .values("id", "username")[:15]
        .order_by("username")
    )
    return JsonResponse({"users": list(users)})


@login_required
def admin_all_users(request: HttpRequest) -> HttpResponse:
    """Список всех пользователей сайта с вкладками: все, активные за сегодня, активные за вчера."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    show = request.GET.get("show", "all")
    today_start, today_end, yesterday_start, yesterday_end = _day_bounds_lead_stats()
    total_users_count = User.objects.count()
    active_today_count = User.objects.filter(
        leads__created_at__gte=today_start, leads__created_at__lt=today_end
    ).distinct().count()
    active_yesterday_count = User.objects.filter(
        leads__created_at__gte=yesterday_start,
        leads__created_at__lt=yesterday_end,
    ).distinct().count()
    if show == "today":
        users_list = (
            User.objects.filter(
                leads__created_at__gte=today_start, leads__created_at__lt=today_end
            )
            .distinct()
            .order_by("-date_joined")
        )
    elif show == "yesterday":
        users_list = (
            User.objects.filter(
                leads__created_at__gte=yesterday_start,
                leads__created_at__lt=yesterday_end,
            )
            .distinct()
            .order_by("-date_joined")
        )
    else:
        users_list = User.objects.all().order_by("-date_joined")
    total_balance = User.objects.aggregate(s=Sum("balance"))["s"] or 0
    return render(
        request,
        "core/admin_all_users.html",
        {
            "users_list": users_list,
            "show": show,
            "total_users_count": total_users_count,
            "active_today_count": active_today_count,
            "active_yesterday_count": active_yesterday_count,
            "total_balance": total_balance,
        },
    )


@login_required
def admin_stats(request: HttpRequest) -> HttpResponse:
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    # Статистика по базам
    base_stats = []
    for base in BaseType.objects.all().order_by("order"):
        total = Contact.objects.filter(base_type=base).count()
        free = Contact.objects.filter(base_type=base, assigned_to__isnull=True, is_active=True).count()
        issued = total - free
        base_stats.append(
            {
                "base": base,
                "total": total,
                "free": free,
                "issued": issued,
            }
        )

    # Статистика по лидам: за неделю, месяц, всё время
    now = timezone.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    total_leads_week = Lead.objects.filter(
        status=Lead.Status.APPROVED, created_at__gte=week_ago
    ).count()
    total_leads_month = Lead.objects.filter(
        status=Lead.Status.APPROVED, created_at__gte=month_ago
    ).count()
    total_leads_all = Lead.objects.filter(status=Lead.Status.APPROVED).count()

    q_week = Lead.objects.filter(
        status=Lead.Status.APPROVED, created_at__gte=week_ago
    ).values("lead_type__name").annotate(c=Count("id"))
    q_month = Lead.objects.filter(
        status=Lead.Status.APPROVED, created_at__gte=month_ago
    ).values("lead_type__name").annotate(c=Count("id"))
    q_all = Lead.objects.filter(status=Lead.Status.APPROVED).values("lead_type__name").annotate(c=Count("id"))

    leads_week = {x["lead_type__name"] or "Без категории": x["c"] for x in q_week}
    leads_month = {x["lead_type__name"] or "Без категории": x["c"] for x in q_month}
    leads_all = {x["lead_type__name"] or "Без категории": x["c"] for x in q_all}

    # Для админской статистики показываем все типы лидов, включая «Самостоятельные лиды»,
    # чтобы не терять уже существующие данные.
    type_names = list(LeadType.objects.values_list("name", flat=True).order_by("name"))
    lead_type_stats = [
        {
            "name": n,
            "week": leads_week.get(n, 0),
            "month": leads_month.get(n, 0),
            "all": leads_all.get(n, 0),
        }
        for n in type_names
    ]
    # Категории, которые есть в лидах, но нет в LeadType (на всякий случай)
    for name in set(leads_week.keys()) | set(leads_month.keys()) | set(leads_all.keys()):
        if name not in type_names:
            lead_type_stats.append({
                "name": name,
                "week": leads_week.get(name, 0),
                "month": leads_month.get(name, 0),
                "all": leads_all.get(name, 0),
            })

    # Кто проверял лиды: сводка по админам (одобрено / отклонено / на доработку)
    reviewed_qs = (
        Lead.objects.filter(reviewed_by__isnull=False)
        .values("reviewed_by__username", "reviewed_by__id")
        .annotate(
            approved=Count("id", filter=Q(status=Lead.Status.APPROVED)),
            rejected=Count("id", filter=Q(status=Lead.Status.REJECTED)),
            rework=Count("id", filter=Q(status=Lead.Status.REWORK)),
        )
    )
    reviewed_by_stats = [
        {
            "username": x["reviewed_by__username"] or "—",
            "user_id": x["reviewed_by__id"],
            "approved": x["approved"],
            "rejected": x["rejected"],
            "rework": x["rework"],
            "total": x["approved"] + x["rejected"] + x["rework"],
        }
        for x in reviewed_qs.order_by("-approved", "-rejected")
    ]

    return render(
        request,
        "core/admin_stats.html",
        {
            "base_stats": base_stats,
            "lead_type_stats": lead_type_stats,
            "total_leads_week": total_leads_week,
            "total_leads_month": total_leads_month,
            "total_leads_all": total_leads_all,
            "reviewed_by_stats": reviewed_by_stats,
        },
    )


def _allocate_contacts_to_user(target_user, base_type, count: int) -> int:
    """Выдаёт пользователю до count контактов из базы. Возвращает количество выданных."""
    from django.db import transaction

    with transaction.atomic():
        free_qs = (
            Contact.objects.select_for_update()
            .filter(base_type=base_type, assigned_to__isnull=True, is_active=True)
            .order_by("id")[:count]
        )
        contacts_to_give = list(free_qs)
        if not contacts_to_give:
            return 0
        now = timezone.now()
        for c in contacts_to_give:
            c.assigned_to = target_user
            c.assigned_at = now
            c.save(update_fields=["assigned_to", "assigned_at", "updated_at"])
        return len(contacts_to_give)


@login_required
def admin_contact_requests(request: HttpRequest) -> HttpResponse:
    """Список заявок на контакты. «Выдать контакты» — сбрасывает лимит (добавляет доп. лимит), пользователь сам нажимает «Получить контакты»."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    if request.method == "POST" and request.POST.get("action") == "refresh":
        return redirect("admin_contact_requests")
    if request.method == "POST":
        req_id = request.POST.get("request_id")
        if req_id:
            req = get_object_or_404(ContactRequest, pk=req_id, status="pending")
            target_user = req.user
            bases_to_give = [req.base_type] if req.base_type else list(BaseType.objects.all().order_by("order"))
            for base in bases_to_give:
                obj, _ = UserBaseLimit.objects.get_or_create(
                    user=target_user, base_type=base, defaults={"extra_daily_limit": 0}
                )
                obj.extra_daily_limit += base.default_daily_limit
                obj.save(update_fields=["extra_daily_limit"])
            req.status = "resolved"
            req.resolved_at = timezone.now()
            req.resolved_by = request.user
            req.save(update_fields=["status", "resolved_at", "resolved_by", "updated_at"])
            messages.success(
                request,
                f"@{target_user.username}: добавлен доп. лимит. Пользователь может нажать «Получить контакты» на странице контактов.",
            )
            return redirect("admin_contact_requests")
    pending = ContactRequest.objects.filter(status="pending").select_related("user", "base_type").order_by("-created_at")
    return render(
        request,
        "core/admin_contact_requests.html",
        {"pending_requests": pending},
    )


@login_required
def admin_withdrawal_requests(request: HttpRequest) -> HttpResponse:
    """Список заявок на вывод. Одобрить — подтвердить вывод (баланс уже обнулён). Отклонить — вернуть сумму на баланс."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    if request.method == "POST":
        req_id = request.POST.get("request_id")
        action = request.POST.get("action")
        if req_id and action in ("approve", "reject"):
            with transaction.atomic():
                wreq = (
                    WithdrawalRequest.objects.select_for_update()
                    .select_related("user")
                    .filter(pk=req_id, status="pending")
                    .first()
                )
                if not wreq:
                    messages.warning(request, "Заявка уже обработана или не найдена.")
                    return redirect("admin_withdrawal_requests")
                now = timezone.now()
                if action == "approve":
                    wreq.status = "approved"
                    messages.success(
                        request,
                        f"Вывод @{wreq.user.username} на {wreq.amount} руб. одобрен. Баланс пользователя уже был обнулён при подаче заявки.",
                    )
                else:
                    wreq.user.balance = (wreq.user.balance or 0) + wreq.amount
                    wreq.user.save(update_fields=["balance"])
                    wreq.status = "rejected"
                    messages.info(request, f"Заявка на вывод от @{wreq.user.username} отклонена. Баланс восстановлен.")
                wreq.processed_at = now
                wreq.processed_by = request.user
                wreq.save(update_fields=["status", "processed_at", "processed_by"])
            return redirect("admin_withdrawal_requests")
    pending = WithdrawalRequest.objects.filter(status="pending").select_related("user").order_by("created_at")
    history = (
        WithdrawalRequest.objects.exclude(status="pending")
        .select_related("user", "processed_by")
        .order_by("-created_at")[:200]
    )
    total_approved_withdrawals = (
        WithdrawalRequest.objects.filter(status="approved").aggregate(s=Sum("amount"))["s"] or 0
    )
    return render(
        request,
        "core/admin_withdrawal_requests.html",
        {
            "pending_requests": pending,
            "history_requests": history,
            "total_approved_withdrawals": total_approved_withdrawals,
        },
    )


# Значения первой строки/заголовка — не считаем контактом (как в боте)
EXCEL_HEADER_VALUES = frozenset(("value", "значение", "контакт", "данные"))


def _excel_contact_value(cell_value) -> str | None:
    """Возвращает значение контакта из ячейки или None (пусто/заголовок)."""
    if cell_value is None:
        return None
    value = str(cell_value).strip()
    if not value:
        return None
    if value.lower() in EXCEL_HEADER_VALUES:
        return None
    return value


def _excel_row_is_assigned(row: tuple) -> bool:
    """Строка «отработана»: в колонках ID/Username/Date (справа от Value) есть данные."""
    if not row or len(row) < 2:
        return False
    for i in (1, 2, 3):
        if i < len(row) and row[i] is not None and str(row[i]).strip():
            return True
    return False


BULK_CREATE_BATCH_SIZE = 1000
# Лимит строк на одну загрузку, чтобы не превышать таймаут воркера (gunicorn)
MAX_UPLOAD_ROWS = 40_000

EXCEL_SHEET_MAP = {
    # Короткие названия
    "Тг": "telegram",
    "ТГ": "telegram",
    "Вотсап": "whatsapp",
    "Макс": "max",
    "Вайбер": "viber",
    "Инст": "instagram",
    "ВК": "vk",
    "ВКонтакте": "vk",
    "Вконтакте": "vk",
    "вконтакте": "vk",
    "Ок": "ok",
    "Почта": "email",
    # Полные/латинские
    "Telegram": "telegram",
    "telegram": "telegram",
    "WhatsApp": "whatsapp",
    "Whatsapp": "whatsapp",
    "whatsapp": "whatsapp",
    "Max": "max",
    "max": "max",
    "Viber": "viber",
    "viber": "viber",
    "Нельзяграм": "instagram",
    "Нельзяграм (там где Reels)": "instagram",
    "Instagram": "instagram",
    "instagram": "instagram",
    "VK": "vk",
    "Одноклассники": "ok",
    "одноклассники": "ok",
    "OK": "ok",
    "Ok": "ok",
    "Email": "email",
    "email": "email",
    "Почты": "email",
}


def _run_bases_import_background(file_path: str, job_id: int) -> None:
    """Выполняет импорт всех листов в фоне. Обновляет BasesImportJob по завершении. Удаляет файл."""
    try:
        wb = load_workbook(file_path, read_only=True)
        created, skipped, details = _process_excel_all_sheets(wb, max_rows=None)
        wb.close()
        msg = f"Добавлено контактов: {created}, пропущено (дубликаты): {skipped}.\n\n" + "\n".join(details)
        BasesImportJob.objects.filter(pk=job_id).update(status=BasesImportJob.Status.SUCCESS, message=msg)
    except Exception as e:
        BasesImportJob.objects.filter(pk=job_id).update(
            status=BasesImportJob.Status.ERROR,
            message=f"Ошибка: {e}",
        )
    finally:
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except OSError:
            pass


@login_required
def upload_bases_excel(request: HttpRequest) -> HttpResponse:
    """Редирект на единую страницу загрузки/выгрузки баз."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")
    return redirect("bases_excel")


def _process_excel_all_sheets(wb, max_rows: int | None = MAX_UPLOAD_ROWS) -> tuple[int, int, list]:
    """Обработка книги Excel: все листы по EXCEL_SHEET_MAP. Свободные контакты (без ID/User/Date).
    max_rows: лимит строк (защита от таймаута при синхронной загрузке); None — без лимита (фоновый импорт)."""
    total_created = 0
    total_skipped = 0
    details = []
    total_rows_processed = 0
    limit_reached = False
    for sheet in wb.worksheets:
        if limit_reached:
            details.append(f"Лист «{sheet.title}» — пропущен (достигнут лимит {max_rows} строк)")
            continue
        base_slug = EXCEL_SHEET_MAP.get(sheet.title)
        if not base_slug:
            details.append(f"Лист «{sheet.title}» — неизвестный тип, пропущен")
            continue
        try:
            base_type = BaseType.objects.get(slug=base_slug)
        except BaseType.DoesNotExist:
            details.append(f"Лист «{sheet.title}» — база не найдена")
            continue
        free_values = []
        for row in sheet.iter_rows(min_row=2, max_col=4, values_only=True):
            value = _excel_contact_value(row[0] if row else None)
            if not value:
                continue
            if _excel_row_is_assigned(row):
                continue
            free_values.append(value)
        if max_rows is not None:
            remaining = max_rows - total_rows_processed
            if remaining <= 0:
                limit_reached = True
                details.append(f"Лист «{sheet.title}» — пропущен (достигнут лимит {max_rows} строк)")
                continue
            to_process = free_values[:remaining]
            skipped_by_limit = len(free_values) - len(to_process)
        else:
            to_process = free_values
            skipped_by_limit = 0
        count_before = Contact.objects.filter(base_type=base_type).count()
        for i in range(0, len(to_process), BULK_CREATE_BATCH_SIZE):
            chunk = to_process[i : i + BULK_CREATE_BATCH_SIZE]
            Contact.objects.bulk_create(
                [Contact(base_type=base_type, value=v) for v in chunk],
                ignore_conflicts=True,
            )
        count_after = Contact.objects.filter(base_type=base_type).count()
        sheet_created = count_after - count_before
        sheet_skipped = len(to_process) - sheet_created
        total_created += sheet_created
        total_skipped += sheet_skipped
        total_rows_processed += len(to_process)
        if max_rows is not None and skipped_by_limit > 0:
            limit_reached = True
            details.append(
                f"«{base_type.name}» — обработано {len(to_process)} из {len(free_values)} (лимит {max_rows}), "
                f"добавлено {sheet_created}, дубликатов {sheet_skipped}. Остальные листы не загружены."
            )
        else:
            details.append(
                f"«{base_type.name}» — свободных строк {len(free_values)}, добавлено {sheet_created}, дубликатов {sheet_skipped}"
            )
        if skipped_by_limit > 0:
            break
    return total_created, total_skipped, details


def _process_excel_single_sheet(wb, base_type: BaseType) -> tuple[int, int]:
    """Обработка книги Excel: первый лист, первый столбец. Только свободные контакты (без ID/User/Date)."""
    ws = wb.active
    free_values = []
    for row in ws.iter_rows(min_row=2, max_col=4, values_only=True):
        value = _excel_contact_value(row[0] if row else None)
        if not value:
            continue
        if _excel_row_is_assigned(row):
            continue
        free_values.append(value)
    if len(free_values) > MAX_UPLOAD_ROWS:
        raise ValueError(
            f"В файле слишком много строк: {len(free_values)}. "
            f"Максимум за одну загрузку: {MAX_UPLOAD_ROWS}. Разбейте файл на части или загрузите «все листы»."
        )
    count_before = Contact.objects.filter(base_type=base_type).count()
    for i in range(0, len(free_values), BULK_CREATE_BATCH_SIZE):
        chunk = free_values[i : i + BULK_CREATE_BATCH_SIZE]
        Contact.objects.bulk_create(
            [Contact(base_type=base_type, value=v) for v in chunk],
            ignore_conflicts=True,
        )
    count_after = Contact.objects.filter(base_type=base_type).count()
    return count_after - count_before, len(free_values) - (count_after - count_before)


@login_required
def bases_excel(request: HttpRequest) -> HttpResponse:
    """Одна страница: загрузка по категории, загрузка всех листов (шаблон бота), выгрузка."""
    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    base_types = list(BaseType.objects.all().order_by("order"))
    form_all = BaseExcelUploadForm()
    form_category = BaseCategoryUploadForm()

    if request.method == "POST":
        if "upload_all" in request.POST:
            form_all = BaseExcelUploadForm(request.POST, request.FILES)
            if form_all.is_valid():
                file = form_all.cleaned_data["file"]
                if not (file and file.name and file.name.lower().endswith(".xlsx")):
                    messages.error(request, "Нужен файл в формате .xlsx")
                else:
                    # Фоновый импорт без лимита строк: сохраняем файл и запускаем поток
                    import_dir = settings.MEDIA_ROOT / "imports"
                    import_dir.mkdir(parents=True, exist_ok=True)
                    safe_name = f"{uuid.uuid4().hex}.xlsx"
                    file_path = import_dir / safe_name
                    with open(file_path, "wb") as f:
                        for chunk in file.chunks():
                            f.write(chunk)
                    job = BasesImportJob.objects.create(
                        status=BasesImportJob.Status.RUNNING,
                        started_by=request.user,
                    )
                    thread = threading.Thread(
                        target=_run_bases_import_background,
                        args=(str(file_path), job.pk),
                        daemon=True,
                    )
                    thread.start()
                    messages.success(
                        request,
                        "Импорт запущен в фоне. Файл будет обработан полностью (без лимита строк). "
                        "Обновите страницу через несколько минут, чтобы увидеть результат.",
                    )
                    return redirect("bases_excel")
        elif "upload_category" in request.POST:
            form_category = BaseCategoryUploadForm(request.POST, request.FILES)
            if form_category.is_valid():
                base_type = form_category.cleaned_data["base_type"]
                file = form_category.cleaned_data["file"]
                if not (file and file.name and file.name.lower().endswith(".xlsx")):
                    messages.error(request, "Нужен файл в формате .xlsx")
                else:
                    try:
                        wb = load_workbook(file, read_only=True)
                        created, skipped = _process_excel_single_sheet(wb, base_type)
                        wb.close()
                        messages.success(
                            request,
                            f"База «{base_type.name}»: добавлено {created} контактов, пропущено (дубликаты) {skipped}.",
                        )
                        return redirect("bases_excel")
                    except Exception as e:
                        messages.error(request, f"Ошибка при обработке файла: {e}")

    sheet_names_hint = "Тг, Вотсап, Макс, Вайбер, Инст, ВК, Ок, Почта (или Telegram, WhatsApp и т.д.)"
    latest_import_job = BasesImportJob.objects.order_by("-created_at").first()
    return render(
        request,
        "core/bases_excel.html",
        {
            "form_all": form_all,
            "form_category": form_category,
            "base_types": base_types,
            "sheet_names_hint": sheet_names_hint,
            "latest_import_job": latest_import_job,
        },
    )


def _make_bases_excel_response(wb, filename: str) -> HttpResponse:
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def download_bases_excel(request: HttpRequest) -> HttpResponse:
    """Выгрузка всех баз контактов в один Excel (по листам)."""

    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    wb = Workbook()
    first_sheet = True

    for base in BaseType.objects.all().order_by("order"):
        contacts = Contact.objects.filter(base_type=base).order_by("id")

        if first_sheet:
            ws = wb.active
            ws.title = base.name[:31]
            first_sheet = False
        else:
            ws = wb.create_sheet(title=base.name[:31])

        ws.append(["Value", "User", "Assigned at"])
        for c in contacts:
            ws.append(
                [
                    c.value,
                    c.assigned_to.username if c.assigned_to else "",
                    c.assigned_at.isoformat() if c.assigned_at else "",
                ]
            )

    return _make_bases_excel_response(wb, "bases.xlsx")


@login_required
def download_bases_excel_category(request: HttpRequest, base_type_id: int) -> HttpResponse:
    """Выгрузка одной базы контактов по категории."""

    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    base_type = get_object_or_404(BaseType, pk=base_type_id)
    contacts = Contact.objects.filter(base_type=base_type).order_by("id")

    wb = Workbook()
    ws = wb.active
    ws.title = base_type.name[:31]
    ws.append(["Value", "User", "Assigned at"])
    for c in contacts:
        ws.append(
            [
                c.value,
                c.assigned_to.username if c.assigned_to else "",
                c.assigned_at.isoformat() if c.assigned_at else "",
            ]
        )

    safe_name = base_type.slug or "base"
    return _make_bases_excel_response(wb, f"bases_{safe_name}.xlsx")


@login_required
def download_leads_excel(request: HttpRequest) -> HttpResponse:
    """Выгрузка всех лидов в один Excel."""

    if not _require_support(request):
        return HttpResponseForbidden("Недостаточно прав.")

    wb = Workbook()
    ws = wb.active
    ws.title = "Лиды"

    ws.append(
        [
            "ID",
            "Пользователь",
            "Тип лида",
            "Тип базы",
            "Контакт (raw)",
            "Источник",
            "Комментарий",
            "Создан",
        ]
    )

    leads = (
        Lead.objects.select_related("user", "lead_type", "base_type")
        .all()
        .order_by("-created_at")
    )

    for lead in leads:
        ws.append(
            [
                lead.id,
                lead.user.username,
                lead.lead_type.name if lead.lead_type else "",
                lead.base_type.name if lead.base_type else "",
                lead.raw_contact,
                lead.source,
                lead.comment,
                lead.created_at.isoformat(),
            ]
        )

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="leads.xlsx"'
    return response

