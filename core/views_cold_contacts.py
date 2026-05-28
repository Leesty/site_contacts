"""Списки холодных контактов: воронка 3 попыток дозвона.

Доступ — `role IN (user, worker)`. Админам/саппортам/партнёрам не показываем.

Логика статусов:
- При добавлении: `final_status = in_progress`, попыток нет.
- Менеджер ставит статус каждой из 3 попыток.
- Если в любой попытке статус = `lead` → требуем заполнить (имя/дата/время),
  ColdContact.final_status = `lead`.
- Если все 3 попытки = `ndz` → final_status = `no_answer`.
- Если любая попытка = `refused` → final_status = `refused`.
- Иначе → `in_progress`.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import CallAttempt, CallReport, ColdContact
from .services.windowgram_api import (
    WindowgramError, create_chat, format_chat_title, send_summary, validate_chat,
)


MSK = ZoneInfo("Europe/Moscow")

ALLOWED_ROLES = ("user", "worker")


def _is_minion(user) -> bool:
    """Менеджер/воркер с явно выданным правом can_create_call_reports.

    Только тем кому главный админ выдал право (через UI permissions),
    видна вкладка «Списки контактов» и доступны связанные endpoint'ы.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "role", None) not in ALLOWED_ROLES:
        return False
    return bool(getattr(user, "can_create_call_reports", False))


def _recompute_final_status(contact: ColdContact) -> None:
    """Пересчитать final_status на основе попыток. Сохраняет contact."""
    attempts = list(contact.attempts.all())
    statuses = [a.status for a in attempts]

    # Приоритет: lead > refused > no_answer > in_progress
    if CallAttempt.Status.LEAD in statuses:
        contact.final_status = ColdContact.FinalStatus.LEAD
    elif CallAttempt.Status.REFUSED in statuses:
        contact.final_status = ColdContact.FinalStatus.REFUSED
    elif len(attempts) >= 3 and all(s == CallAttempt.Status.NDZ for s in statuses):
        contact.final_status = ColdContact.FinalStatus.NO_ANSWER
    else:
        contact.final_status = ColdContact.FinalStatus.IN_PROGRESS

    contact.save(update_fields=["final_status", "updated_at"])


# ─── Список + фильтры ─────────────────────────────────────────────────────

@login_required
def contacts_list(request: HttpRequest) -> HttpResponse:
    if not _is_minion(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")

    status_filter = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()

    qs = ColdContact.objects.filter(owner=request.user).prefetch_related("attempts")

    if status_filter in {s.value for s in ColdContact.FinalStatus}:
        qs = qs.filter(final_status=status_filter)
    elif status_filter == "callback_today":
        now_msk = timezone.now().astimezone(MSK)
        start_msk = now_msk.replace(hour=0, minute=0, second=0, microsecond=0)
        end_msk = start_msk.replace(hour=23, minute=59, second=59)
        contact_ids = (
            CallAttempt.objects.filter(
                status=CallAttempt.Status.CALLBACK,
                callback_at__gte=start_msk,
                callback_at__lte=end_msk,
                contact__owner=request.user,
            ).values_list("contact_id", flat=True).distinct()
        )
        qs = qs.filter(id__in=contact_ids)

    if q:
        qs = qs.filter(Q(contact__icontains=q) | Q(source__icontains=q) | Q(name__icontains=q))

    qs = qs.order_by("-created_at")

    # Готовим строку: для каждого contact — массив attempts по [1,2,3]
    contacts = []
    for c in qs:
        by_no: dict[int, CallAttempt] = {a.attempt_no: a for a in c.attempts.all()}
        attempts_rows = [by_no.get(i) for i in (1, 2, 3)]
        contacts.append({"obj": c, "attempts": attempts_rows})

    return render(request, "core/cold_contacts_list.html", {
        "contacts": contacts,
        "status_filter": status_filter,
        "q": q,
        "attempt_statuses": CallAttempt.Status.choices,
        "final_statuses": ColdContact.FinalStatus.choices,
    })


# ─── Добавить контакт ─────────────────────────────────────────────────────

@login_required
def contacts_add(request: HttpRequest) -> HttpResponse:
    if not _is_minion(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return redirect("cold_contacts_list")

    source = (request.POST.get("source") or "").strip()[:255]
    contact = (request.POST.get("contact") or "").strip()[:255]
    if not contact:
        messages.error(request, "Укажите контакт.")
        return redirect("cold_contacts_list")

    ColdContact.objects.create(
        owner=request.user,
        source=source,
        contact=contact,
    )
    return redirect("cold_contacts_list")


# ─── Обновить попытку N ───────────────────────────────────────────────────

@login_required
def contact_attempt_update(request: HttpRequest, contact_id: int, n: int) -> HttpResponse:
    if not _is_minion(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return redirect("cold_contacts_list")
    if n not in (1, 2, 3):
        return HttpResponseForbidden("Некорректный номер попытки.")

    contact = get_object_or_404(ColdContact, pk=contact_id, owner=request.user)
    status = (request.POST.get("status") or "").strip()
    valid_statuses = {s.value for s in CallAttempt.Status}
    if status not in valid_statuses:
        # Пустая строка → удалить попытку (если есть)
        CallAttempt.objects.filter(contact=contact, attempt_no=n).delete()
        _recompute_final_status(contact)
        return redirect("cold_contacts_list")

    callback_at = None
    if status == CallAttempt.Status.CALLBACK:
        raw_dt = (request.POST.get("callback_at") or "").strip()
        if raw_dt:
            try:
                # input type=datetime-local → "YYYY-MM-DDTHH:MM"
                dt_naive = datetime.fromisoformat(raw_dt)
                callback_at = dt_naive.replace(tzinfo=MSK)
            except ValueError:
                callback_at = None

    with transaction.atomic():
        attempt, _ = CallAttempt.objects.update_or_create(
            contact=contact, attempt_no=n,
            defaults={"status": status, "callback_at": callback_at},
        )
        _recompute_final_status(contact)

    return redirect("cold_contacts_list")


# ─── Зафиксировать лид (имя / дата / время) ──────────────────────────────

@login_required
def contact_mark_lead(request: HttpRequest, contact_id: int) -> HttpResponse:
    if not _is_minion(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return redirect("cold_contacts_list")

    contact = get_object_or_404(ColdContact, pk=contact_id, owner=request.user)

    name = (request.POST.get("name") or "").strip()[:255]
    tg_username = (request.POST.get("tg_username") or "").strip()[:100]
    # Чистим обвязки t.me/, @ — оставляем только сам username
    for p in ("https://t.me/", "http://t.me/", "t.me/", "telegram.me/"):
        if tg_username.lower().startswith(p):
            tg_username = tg_username[len(p):]
            break
    tg_username = tg_username.lstrip("@").strip().rstrip("/")
    raw_date = (request.POST.get("call_date") or "").strip()
    raw_time = (request.POST.get("call_time") or "").strip()
    attempt_no = request.POST.get("attempt_no") or "1"
    try:
        attempt_no = int(attempt_no)
        if attempt_no not in (1, 2, 3):
            attempt_no = 1
    except (TypeError, ValueError):
        attempt_no = 1

    call_date = None
    call_time = None
    if raw_date:
        try:
            call_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            pass
    if raw_time:
        try:
            call_time = datetime.strptime(raw_time, "%H:%M").time()
        except ValueError:
            pass

    with transaction.atomic():
        contact.name = name
        contact.tg_username = tg_username
        contact.lead_call_date = call_date
        contact.lead_call_time = call_time
        contact.final_status = ColdContact.FinalStatus.LEAD
        contact.save(update_fields=[
            "name", "tg_username", "lead_call_date", "lead_call_time",
            "final_status", "updated_at",
        ])
        CallAttempt.objects.update_or_create(
            contact=contact, attempt_no=attempt_no,
            defaults={"status": CallAttempt.Status.LEAD, "callback_at": None},
        )

    # Создаём чат через windowgram. Если падает — лид всё равно зафиксирован
    # (менеджер увидит ошибку и сможет повторить через «создать чат»).
    if not contact.chat_id:
        title = format_chat_title(name, contact.contact)
        try:
            chat_data = create_chat(request.user, title)
            contact.chat_id = chat_data["chat_id"]
            contact.chat_invite_link = chat_data.get("invite_link") or ""
            contact.chat_created_at = timezone.now()
            contact.save(update_fields=["chat_id", "chat_invite_link", "chat_created_at", "updated_at"])
            # Шлём в чат сводку (Номер / Дата / Время)
            date_str = call_date.strftime("%d.%m") if call_date else ""
            time_str = call_time.strftime("%H:%M") if call_time else ""
            send_summary(
                contact.chat_id, contact.contact, date_str, time_str,
            )
            messages.success(request, f"Лид зафиксирован, чат «{title}» создан.")
        except WindowgramError as exc:
            messages.warning(
                request,
                f"Лид зафиксирован, но создать чат не удалось: {exc}. "
                "Попробуйте кнопку «Создать чат» позже.",
            )

    return redirect("cold_contacts_list")


# ─── Повторное создание чата (если первая попытка упала) ─────────────────

@login_required
def contact_create_chat(request: HttpRequest, contact_id: int) -> HttpResponse:
    if not _is_minion(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return redirect("cold_contacts_list")

    contact = get_object_or_404(ColdContact, pk=contact_id, owner=request.user)
    if contact.chat_id:
        messages.info(request, "Чат уже создан.")
        return redirect("cold_contacts_list")
    if contact.final_status != ColdContact.FinalStatus.LEAD:
        messages.error(request, "Чат создаётся только для контактов со статусом «лид».")
        return redirect("cold_contacts_list")

    title = format_chat_title(contact.name, contact.contact)
    try:
        chat_data = create_chat(request.user, title)
        contact.chat_id = chat_data["chat_id"]
        contact.chat_invite_link = chat_data.get("invite_link") or ""
        contact.chat_created_at = timezone.now()
        contact.save(update_fields=["chat_id", "chat_invite_link", "chat_created_at", "updated_at"])
        date_str = contact.lead_call_date.strftime("%d.%m") if contact.lead_call_date else ""
        time_str = contact.lead_call_time.strftime("%H:%M") if contact.lead_call_time else ""
        send_summary(contact.chat_id, contact.contact, date_str, time_str)
        messages.success(request, f"Чат «{title}» создан.")
    except WindowgramError as exc:
        messages.error(request, f"Не удалось создать чат: {exc}")
    return redirect("cold_contacts_list")


# ─── Отчёт «Прозвон» — менеджер сдаёт ──────────────────────────────────

@login_required
def contact_call_report_create(request: HttpRequest, contact_id: int) -> HttpResponse:
    if not _is_minion(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")

    contact = get_object_or_404(ColdContact, pk=contact_id, owner=request.user)
    if not contact.chat_id:
        messages.error(request, "Сначала зафиксируйте лид и создайте чат.")
        return redirect("cold_contacts_list")
    if hasattr(contact, "call_report"):
        messages.info(request, "Отчёт по этому контакту уже отправлен.")
        return redirect("cold_contacts_list")

    if request.method == "POST":
        screencast = request.FILES.get("screencast")
        source = (request.POST.get("source") or "").strip()[:255]
        if not screencast:
            messages.error(request, "Прикрепите скринкаст.")
            return redirect("cold_contact_call_report", contact_id=contact.id)

        # Авто-валидация: админ в чате + клиент зашёл
        is_complete, note = validate_chat(contact.chat_id)

        with transaction.atomic():
            report = CallReport.objects.create(
                cold_contact=contact,
                screencast=screencast,
                source=source,
                status=CallReport.Status.PENDING,
                is_complete=is_complete,
                validation_note=note,
            )
        if is_complete:
            messages.success(
                request,
                f"Отчёт #{report.id} принят. Валидация пройдена — отчёт ушёл на проверку.",
            )
        else:
            messages.warning(
                request,
                f"Отчёт #{report.id} сохранён, но валидация не прошла: {note} "
                "Отчёт пока не виден админам.",
            )
        return redirect("cold_contacts_list")

    return render(request, "core/call_report_form.html", {
        "contact": contact,
    })


# ─── Удалить контакт ──────────────────────────────────────────────────────

@login_required
def contact_delete(request: HttpRequest, contact_id: int) -> HttpResponse:
    if not _is_minion(request.user):
        return HttpResponseForbidden("Недоступно для вашей роли.")
    if request.method != "POST":
        return redirect("cold_contacts_list")

    contact = get_object_or_404(ColdContact, pk=contact_id, owner=request.user)
    contact.delete()
    return redirect("cold_contacts_list")
