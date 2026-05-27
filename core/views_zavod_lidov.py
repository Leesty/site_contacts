"""Завод-лидов: обмен по проектам (номера/сайты) клиент ↔ главный админ.

Бизнес-логика:
- Клиент создаёт ПРОЕКТ: textarea с номерами/сайтами + название проекта.
- Главный админ видит список всех проектов всех клиентов, заходит в
  карточку проекта и добавляет свои значения в ответ.
- Бизнес-дата проекта = MSK-дата создания (cutoff 11:00).
- В 11:00 МСК проект «закрывается» (business_date < today).
- Клиент скачивает Excel: один файл, лист = название проекта,
  колонки [значение клиента, значение админа]. Парование случайно
  и детерминистично по seed=project_id.

Значения: либо телефон (нормализуется в +цифры), либо сайт/URL (как
есть, lowercase), либо любая другая строка (как есть). Дубликаты
внутри (project, side) отсекаются БД-ой через UniqueConstraint.
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
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from openpyxl import Workbook

from .models import LidProject, LidProjectItem, User


MSK = ZoneInfo("Europe/Moscow")
BUSINESS_DAY_CUTOFF_HOUR = 11  # 11:00 MSK — новая бизнес-дата


def business_date_now() -> date_cls:
    """Текущая бизнес-дата (MSK, cutoff 11:00).

    До 11:00 МСК — это всё ещё «вчерашний» бизнес-день.
    """
    now_msk = timezone.now().astimezone(MSK)
    if now_msk.hour < BUSINESS_DAY_CUTOFF_HOUR:
        return (now_msk - timedelta(days=1)).date()
    return now_msk.date()


def is_project_closed(project: "LidProject") -> bool:
    return project.business_date < business_date_now()


# ─── Парсер значений: телефон / URL / любая строка ──────────────────────

_PHONE_LIKE_RE = re.compile(r"^[\d\+\-\s\(\)]+$")


def _normalize_value(raw: str) -> str | None:
    """Нормализует одно значение. Возвращает None если пусто после trim.

    Принимаем любую непустую строку:
    - Если похоже на телефон (>=5 цифр + только цифры/+/-/()) →
      нормализуем в +цифры/цифры.
    - Если содержит точку или слэш → URL/сайт, lowercase.
    - Иначе → как есть (любая строка, в т.ч. короткая «1», «А-12»).
    """
    v = raw.strip()
    if not v:
        return None
    # Телефон: только цифры/+/-/()/пробелы И минимум 5 цифр
    if _PHONE_LIKE_RE.match(v):
        digits = re.sub(r"\D", "", v)
        if len(digits) >= 5:
            has_plus = v.lstrip().startswith("+")
            return ("+" + digits) if has_plus else digits
        # Короткий «1», «42» — не телефон, оставляем как есть
        return v
    # URL/сайт
    if "/" in v or "." in v:
        return v.lower()
    return v


def parse_values(text: str) -> list[str]:
    """Разбивает вставленный текст на список значений (без дубликатов)."""
    if not text:
        return []
    chunks = re.split(r"[\n\r,;\t]+|[ ]{2,}", text)
    seen: set[str] = set()
    out: list[str] = []
    for chunk in chunks:
        v = _normalize_value(chunk)
        if v is None or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def pair_values(user_vals: list[str], admin_vals: list[str], seed: str) -> list[tuple[str, str]]:
    """Паруем значения. rows = max(len(user), len(admin)).

    Каждая сторона представлена ВСЕМИ значениями хотя бы раз;
    недостающие добиваются случайным повтором (seed детерминирует
    результат — повторное скачивание даст ту же таблицу).
    Если одна сторона пуста — другая остаётся, пара с "".
    """
    rng = random.Random(seed)
    n = max(len(user_vals), len(admin_vals))
    if n == 0:
        return []
    if not user_vals:
        return [("", a) for a in admin_vals]
    if not admin_vals:
        return [(u, "") for u in user_vals]

    user_pool = user_vals[:]
    admin_pool = admin_vals[:]
    rng.shuffle(user_pool)
    rng.shuffle(admin_pool)
    if len(user_pool) < n:
        user_pool.extend(rng.choices(user_vals, k=n - len(user_pool)))
    if len(admin_pool) < n:
        admin_pool.extend(rng.choices(admin_vals, k=n - len(admin_pool)))
    return list(zip(user_pool, admin_pool))


def _sanitize_sheet_name(name: str, existing: set[str]) -> str:
    """Excel: max 31 char, без \\ / ? * [ ] : и не пустое. Уникальность."""
    cleaned = re.sub(r"[\\/?*\[\]:]", "_", name).strip() or "проект"
    cleaned = cleaned[:31]
    base = cleaned
    i = 2
    while cleaned in existing:
        suffix = f" ({i})"
        cleaned = (base[: 31 - len(suffix)] + suffix)
        i += 1
    existing.add(cleaned)
    return cleaned


# ─── Auth helpers ─────────────────────────────────────────────────────────

def _is_lid_customer(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) == "lid_customer"


def _is_main_admin(user) -> bool:
    return getattr(user, "is_authenticated", False) and getattr(user, "role", None) == "main_admin"


# ─── Клиентский кабинет ──────────────────────────────────────────────────

@login_required
def customer_dashboard(request: HttpRequest) -> HttpResponse:
    """Клиент: создание проектов + кнопка скачать."""
    if not _is_lid_customer(request.user):
        return HttpResponseForbidden("Только для заказчиков лидов.")

    customer = request.user
    today = business_date_now()

    if request.method == "POST":
        project_name = (request.POST.get("project_name") or "").strip()[:200]
        raw_values = request.POST.get("values") or ""
        values = parse_values(raw_values)
        if not project_name:
            messages.error(request, "Укажите название проекта.")
            return redirect("zavod_lidov_customer")
        if not values:
            messages.warning(request, "Не удалось распознать ни одного значения.")
            return redirect("zavod_lidov_customer")
        project = LidProject.objects.create(
            customer=customer, name=project_name, business_date=today,
        )
        added = 0
        for v in values:
            try:
                LidProjectItem.objects.create(
                    project=project, submitter=customer, value=v, is_admin=False,
                )
                added += 1
            except IntegrityError:
                pass
        messages.success(request, f"Проект «{project_name}» создан. Добавлено значений: {added}.")
        return redirect("zavod_lidov_customer")

    # Список ВСЕХ проектов клиента, новые сверху, с метаданными
    projects_qs = (
        LidProject.objects.filter(customer=customer)
        .annotate(
            n_total=Count("items"),
        )
        .order_by("-created_at")
    )
    projects = []
    for p in projects_qs:
        projects.append({
            "obj": p,
            "n_total": p.n_total,
            "closed": p.business_date < today,
        })

    return render(request, "zavod_lidov/dashboard.html", {
        "today": today,
        "projects": projects,
    })


@login_required
def customer_download_excel(request: HttpRequest, project_id: int) -> HttpResponse:
    """Excel конкретного проекта: один лист с этим проектом."""
    if not _is_lid_customer(request.user):
        return HttpResponseForbidden("Только для заказчиков лидов.")

    project = get_object_or_404(
        LidProject, pk=project_id, customer=request.user,
    )
    today = business_date_now()
    # Проект должен быть закрыт (прошёл cutoff). Открытые качать нельзя —
    # данные ещё не готовы.
    if project.business_date >= today:
        return HttpResponseForbidden("Проект ещё в работе.")

    user_vals = list(
        project.items.filter(is_admin=False)
        .order_by("created_at").values_list("value", flat=True)
    )
    admin_vals = list(
        project.items.filter(is_admin=True)
        .order_by("created_at").values_list("value", flat=True)
    )
    pairs = pair_values(user_vals, admin_vals, seed=f"proj-{project.pk}")

    wb = Workbook()
    wb.remove(wb.active)
    sheet_name = _sanitize_sheet_name(project.name, set())
    ws = wb.create_sheet(title=sheet_name)
    for u_val, a_val in pairs:
        ws.append([u_val, a_val])
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 32

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    # safe-имя файла из названия проекта (только latin/digit/underscore)
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", project.name).strip("_") or f"project-{project.pk}"
    safe = safe[:50]
    fname = f"{safe}_{project.business_date.strftime('%Y-%m-%d')}.xlsx"
    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{fname}"'
    return response


# ─── Главный админ: список проектов + детальная карточка ─────────────────

@login_required
def admin_overview(request: HttpRequest) -> HttpResponse:
    """Список ВСЕХ проектов (по всем клиентам), новые сверху."""
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только для главного админа.")

    today = business_date_now()
    qs = (
        LidProject.objects.select_related("customer")
        .annotate(
            n_user=Count("items", filter=Q(items__is_admin=False)),
            n_admin=Count("items", filter=Q(items__is_admin=True)),
        )
        .order_by("-created_at")[:200]
    )
    projects = []
    for p in qs:
        projects.append({
            "obj": p,
            "n_user": p.n_user,
            "n_admin": p.n_admin,
            "closed": p.business_date < today,
            "needs_attention": (p.business_date == today) and p.n_user > 0 and p.n_admin == 0,
        })
    return render(request, "core/admin_zavod_lidov.html", {
        "today": today,
        "projects": projects,
    })


@login_required
def admin_project_detail(request: HttpRequest, project_id: int) -> HttpResponse:
    """Детальная карточка проекта: список значений клиента + textarea
    для добавления админских значений."""
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только для главного админа.")

    project = get_object_or_404(LidProject.objects.select_related("customer"), pk=project_id)

    if request.method == "POST":
        raw = request.POST.get("values") or ""
        values = parse_values(raw)
        if not values:
            messages.warning(request, "Не удалось распознать ни одного значения.")
            return redirect("admin_zavod_lidov_project", project_id=project.pk)
        added = dup = 0
        for v in values:
            try:
                LidProjectItem.objects.create(
                    project=project, submitter=request.user, value=v, is_admin=True,
                )
                added += 1
            except IntegrityError:
                dup += 1
        msg = f"Добавлено: {added}."
        if dup:
            msg += f" Дубликатов пропущено: {dup}."
        messages.success(request, msg)
        return redirect("admin_zavod_lidov_project", project_id=project.pk)

    user_items = list(project.items.filter(is_admin=False).order_by("created_at"))
    admin_items = list(project.items.filter(is_admin=True).order_by("created_at"))

    return render(request, "core/admin_zavod_lidov_project.html", {
        "project": project,
        "user_items": user_items,
        "admin_items": admin_items,
        "closed": is_project_closed(project),
    })


def pending_admin_attention_count() -> int:
    """Сколько проектов СЕГОДНЯ ждут ответа админа (есть values клиента,
    нет ни одного админского). Для бейджа на дашборде главного админа."""
    today = business_date_now()
    return (
        LidProject.objects.filter(business_date=today)
        .annotate(
            n_user=Count("items", filter=Q(items__is_admin=False)),
            n_admin=Count("items", filter=Q(items__is_admin=True)),
        )
        .filter(n_user__gt=0, n_admin=0)
        .count()
    )
