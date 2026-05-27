"""Завод-лидов: обмен номерами между клиентом (role=lid_customer)
и главным админом.

Бизнес-дата: «день» начинается в 11:00 MSK. Все номера, добавленные
между 11:00 MSK дня X и 11:00 MSK дня X+1, имеют business_date = X.

При скачивании Excel формируются листы для каждого ЗАКРЫТОГО дня
(business_date < текущая business_date). Для каждого листа парование
номеров клиента и админа делается на лету, детерминистично (seed =
ISO-дата), так что повторное скачивание даёт тот же результат.
"""

from __future__ import annotations

import io
import random
import re
from datetime import date as date_cls, datetime, timedelta
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from openpyxl import Workbook

from .models import LidPhoneSubmission, User


MSK = ZoneInfo("Europe/Moscow")
BUSINESS_DAY_CUTOFF_HOUR = 11  # 11:00 MSK — новая бизнес-дата


def business_date_now() -> date_cls:
    """Возвращает текущую бизнес-дату (MSK, cutoff 11:00).

    Если сейчас 10:59 MSK — бизнес-дата = вчера.
    Если сейчас 11:00 MSK — бизнес-дата = сегодня.
    """
    now_msk = timezone.now().astimezone(MSK)
    if now_msk.hour < BUSINESS_DAY_CUTOFF_HOUR:
        return (now_msk - timedelta(days=1)).date()
    return now_msk.date()


def is_business_date_closed(d: date_cls) -> bool:
    """День закрыт = текущая бизнес-дата больше переданной."""
    return d < business_date_now()


def parse_phones(text: str) -> list[str]:
    """Парсит произвольный текст в список нормализованных телефонов.

    Стратегия: бьём текст по «разделителям» (новые строки, запятая,
    точка с запятой, табуляция, multiple spaces), затем каждый кусок
    нормализуем — оставляем цифры + ведущий `+`. Куски с длиной < 5
    цифр отбрасываются. Дубликаты в рамках вставки отсекаются.
    """
    if not text:
        return []
    # Разделители: новая строка, запятая, точка с запятой, табуляция,
    # 2+ подряд пробелов. Внутри одного chunk пробелы допустимы (`+7 999`
    # это один номер). Дедуп — по нормализованной строке.
    chunks = re.split(r"[\n\r,;\t]+|[ ]{2,}", text)
    seen: set[str] = set()
    out: list[str] = []
    for chunk in chunks:
        c = chunk.strip()
        if not c:
            continue
        # Оставляем цифры и опциональный ведущий +
        has_plus = c.startswith("+")
        digits = re.sub(r"\D", "", c)
        if len(digits) < 5:
            continue
        normalized = ("+" + digits) if has_plus else digits
        if normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def pair_phones(user_phones: list[str], admin_phones: list[str], seed: str) -> list[tuple[str, str]]:
    """Парует номера в детерминистично-случайном порядке.

    Количество строк = max(len(user), len(admin)). Каждая сторона
    представлена ВСЕМИ своими номерами хотя бы раз; разница добивается
    случайным повторением из своего пула. seed → одна и та же дата
    даёт один и тот же результат при повторном скачивании.
    """
    rng = random.Random(seed)
    n_user = len(user_phones)
    n_admin = len(admin_phones)
    n = max(n_user, n_admin)
    if n == 0:
        return []
    # Если одна сторона пустая — пары делать не из чего. Возвращаем
    # ту сторону что есть, с пустой второй ячейкой.
    if not user_phones:
        return [("", a) for a in admin_phones]
    if not admin_phones:
        return [(u, "") for u in user_phones]

    user_pool = user_phones[:]
    admin_pool = admin_phones[:]
    rng.shuffle(user_pool)
    rng.shuffle(admin_pool)

    if len(user_pool) < n:
        user_pool.extend(rng.choices(user_phones, k=n - len(user_pool)))
    if len(admin_pool) < n:
        admin_pool.extend(rng.choices(admin_phones, k=n - len(admin_pool)))

    return list(zip(user_pool, admin_pool))


# ─── Helpers для авторизации ──────────────────────────────────────────────

def _is_lid_customer(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) == "lid_customer"


def _is_main_admin(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) == "main_admin"


# ─── Клиентский кабинет ──────────────────────────────────────────────────

@login_required
def customer_dashboard(request: HttpRequest) -> HttpResponse:
    """Кабинет клиента: вставить номера + история + скачать Excel."""
    if not _is_lid_customer(request.user):
        return HttpResponseForbidden("Только для заказчиков лидов.")

    customer = request.user
    today = business_date_now()

    if request.method == "POST":
        raw = request.POST.get("phones") or ""
        phones = parse_phones(raw)
        added = 0
        dup = 0
        for ph in phones:
            try:
                LidPhoneSubmission.objects.create(
                    customer=customer,
                    submitter=customer,
                    phone=ph,
                    business_date=today,
                    is_admin=False,
                )
                added += 1
            except IntegrityError:
                dup += 1
        if added:
            messages.success(request, f"Добавлено номеров: {added}.{f' Дубликатов пропущено: {dup}.' if dup else ''}")
        elif dup:
            messages.info(request, f"Все {dup} номеров уже были добавлены сегодня.")
        else:
            messages.warning(request, "Не удалось распознать ни одного номера.")
        return redirect("zavod_lidov_customer")

    # Сводка: сколько добавлено за сегодня + история по дням
    today_count = LidPhoneSubmission.objects.filter(
        customer=customer, business_date=today, is_admin=False,
    ).count()
    today_admin_count = LidPhoneSubmission.objects.filter(
        customer=customer, business_date=today, is_admin=True,
    ).count()

    # История за прошлые дни: список (date, my_count, admin_count, closed)
    history_rows = []
    daily = (
        LidPhoneSubmission.objects.filter(customer=customer)
        .values("business_date", "is_admin")
        .order_by("-business_date")
    )
    by_day: dict = {}
    for row in daily:
        d = row["business_date"]
        side_count = LidPhoneSubmission.objects.filter(
            customer=customer, business_date=d, is_admin=row["is_admin"],
        ).count()
        by_day.setdefault(d, {"my": 0, "admin": 0})
        if row["is_admin"]:
            by_day[d]["admin"] = side_count
        else:
            by_day[d]["my"] = side_count
    for d in sorted(by_day.keys(), reverse=True):
        history_rows.append({
            "date": d,
            "my": by_day[d]["my"],
            "admin": by_day[d]["admin"],
            "closed": is_business_date_closed(d),
        })

    has_anything_to_download = any(r["closed"] and (r["my"] or r["admin"]) for r in history_rows)

    return render(request, "zavod_lidov/dashboard.html", {
        "today": today,
        "today_count": today_count,
        "today_admin_count": today_admin_count,
        "history_rows": history_rows,
        "has_download": has_anything_to_download,
    })


@login_required
def customer_download_excel(request: HttpRequest) -> HttpResponse:
    """Скачать Excel: один файл, листы — по каждой закрытой бизнес-дате
    где у клиента были собственные номера."""
    if not _is_lid_customer(request.user):
        return HttpResponseForbidden("Только для заказчиков лидов.")

    customer = request.user
    today = business_date_now()

    closed_dates = (
        LidPhoneSubmission.objects.filter(customer=customer, is_admin=False)
        .exclude(business_date__gte=today)
        .values_list("business_date", flat=True)
        .distinct()
        .order_by("business_date")
    )

    wb = Workbook()
    wb.remove(wb.active)  # дефолтный лист
    any_added = False
    for d in closed_dates:
        user_phones = list(
            LidPhoneSubmission.objects.filter(
                customer=customer, business_date=d, is_admin=False,
            ).order_by("created_at").values_list("phone", flat=True)
        )
        admin_phones = list(
            LidPhoneSubmission.objects.filter(
                customer=customer, business_date=d, is_admin=True,
            ).order_by("created_at").values_list("phone", flat=True)
        )
        pairs = pair_phones(user_phones, admin_phones, seed=d.isoformat())

        ws = wb.create_sheet(title=d.strftime("%Y-%m-%d"))
        ws.append(["Номер клиента", "Номер админа"])
        for u_phone, a_phone in pairs:
            ws.append([u_phone, a_phone])
        ws.column_dimensions["A"].width = 22
        ws.column_dimensions["B"].width = 22
        any_added = True

    if not any_added:
        # Пустой файл с одним пустым листом, чтобы кнопка не падала
        ws = wb.create_sheet(title="Пусто")
        ws.append(["Ещё нет закрытых дней с номерами."])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    fname = f"lidov_{customer.username}_{today.strftime('%Y-%m-%d')}.xlsx"
    response["Content-Disposition"] = f'attachment; filename="{fname}"'
    return response


# ─── Кабинет главного админа ─────────────────────────────────────────────

@login_required
def admin_overview(request: HttpRequest) -> HttpResponse:
    """Главный админ: видит список всех submissions, может вставить свои
    номера в ответ для конкретного клиента."""
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только для главного админа.")

    if request.method == "POST":
        customer_id = (request.POST.get("customer_id") or "").strip()
        raw = request.POST.get("phones") or ""
        if not customer_id.isdigit():
            messages.error(request, "Не указан клиент.")
            return redirect("admin_zavod_lidov_overview")
        customer = User.objects.filter(pk=int(customer_id), role="lid_customer").first()
        if not customer:
            messages.error(request, "Клиент не найден или не lid_customer.")
            return redirect("admin_zavod_lidov_overview")
        phones = parse_phones(raw)
        if not phones:
            messages.warning(request, "Не удалось распознать ни одного номера.")
            return redirect("admin_zavod_lidov_overview")
        today = business_date_now()
        added = dup = 0
        for ph in phones:
            try:
                LidPhoneSubmission.objects.create(
                    customer=customer,
                    submitter=request.user,
                    phone=ph,
                    business_date=today,
                    is_admin=True,
                )
                added += 1
            except IntegrityError:
                dup += 1
        if added:
            messages.success(request, f"Добавлено для @{customer.username}: {added}. {f'Дубли: {dup}.' if dup else ''}")
        return redirect("admin_zavod_lidov_overview")

    # Сводка по клиентам и дням
    customers = User.objects.filter(role="lid_customer").order_by("username")
    today = business_date_now()
    summary = []
    for c in customers:
        days_qs = (
            LidPhoneSubmission.objects.filter(customer=c)
            .values_list("business_date", flat=True).distinct()
            .order_by("-business_date")[:30]
        )
        days = []
        for d in days_qs:
            my = LidPhoneSubmission.objects.filter(customer=c, business_date=d, is_admin=False).count()
            ad = LidPhoneSubmission.objects.filter(customer=c, business_date=d, is_admin=True).count()
            days.append({
                "date": d,
                "user_count": my,
                "admin_count": ad,
                "closed": is_business_date_closed(d),
                "needs_attention": (d == today) and my > 0 and ad == 0,
            })
        # Текущая (today) — отдельно, может ещё не быть в days_qs
        today_my = LidPhoneSubmission.objects.filter(customer=c, business_date=today, is_admin=False).count()
        today_ad = LidPhoneSubmission.objects.filter(customer=c, business_date=today, is_admin=True).count()
        summary.append({
            "customer": c,
            "today_my": today_my,
            "today_admin": today_ad,
            "needs_admin_now": today_my > 0 and today_ad == 0,
            "days": days,
        })

    return render(request, "core/admin_zavod_lidov.html", {
        "today": today,
        "summary": summary,
    })


def pending_admin_attention_count() -> int:
    """Сколько lid_customer'ов имеют сегодня клиентские номера БЕЗ ответа админа.

    Используется как бейдж на дашборде главного админа.
    """
    today = business_date_now()
    customers_today = set(
        LidPhoneSubmission.objects.filter(
            business_date=today, is_admin=False,
        ).values_list("customer_id", flat=True).distinct()
    )
    if not customers_today:
        return 0
    customers_with_reply = set(
        LidPhoneSubmission.objects.filter(
            business_date=today, is_admin=True,
            customer_id__in=customers_today,
        ).values_list("customer_id", flat=True).distinct()
    )
    return len(customers_today - customers_with_reply)
