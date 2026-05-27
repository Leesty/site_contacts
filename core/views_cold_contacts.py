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

from .models import CallAttempt, ColdContact


MSK = ZoneInfo("Europe/Moscow")

ALLOWED_ROLES = ("user", "worker")


def _is_minion(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) in ALLOWED_ROLES


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
        contact.lead_call_date = call_date
        contact.lead_call_time = call_time
        contact.final_status = ColdContact.FinalStatus.LEAD
        contact.save(update_fields=["name", "lead_call_date", "lead_call_time", "final_status", "updated_at"])
        CallAttempt.objects.update_or_create(
            contact=contact, attempt_no=attempt_no,
            defaults={"status": CallAttempt.Status.LEAD, "callback_at": None},
        )

    return redirect("cold_contacts_list")


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
