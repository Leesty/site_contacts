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

from .models import AdminPhonePool, LidProject, LidProjectItem, User


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


# ─── Авто-раздача из пула в проекты (lazy, при заходе) ───────────────────

from django.db import transaction as _transaction


def try_daily_fill_for_customer(customer) -> int:
    """Раздаёт номера из пула клиента по его открытым проектам.

    Идемпотентно по бизнес-дате: если `project.last_fill_business_date`
    уже = сегодняшняя бизнес-дата, проект пропускается.

    Для каждого открытого проекта (FIFO по created_at):
      need = min(project.daily_limit,
                 (total_limit или ∞) - текущее_число_admin_items,
                 свободных_номеров_в_пуле)
      выбираем `need` случайных свободных записей пула,
      помечаем is_used=True + used_in_project,
      создаём LidProjectItem(is_admin=True, value=…) пачкой.
    Если admin_items >= total_limit → is_closed=True.

    Возвращает суммарное число залитых номеров (для отладки).
    """
    today_bd = business_date_now()
    total_added = 0

    projects = list(
        LidProject.objects.filter(
            customer=customer, is_closed=False,
        ).filter(
            # Только те, у которых daily_limit > 0 и last_fill != сегодня
            daily_limit__isnull=False, daily_limit__gt=0,
        ).order_by("created_at")
    )
    if not projects:
        return 0

    # Все свободные номера пула этого клиента
    pool_qs = AdminPhonePool.objects.filter(
        customer=customer, is_used=False,
    )

    for project in projects:
        # Пропускаем если уже раздавали в этот бизнес-день
        if project.last_fill_business_date == today_bd:
            continue

        cur_admin_count = LidProjectItem.objects.filter(
            project=project, is_admin=True,
        ).count()

        need = project.daily_limit or 0
        if project.total_limit is not None:
            remaining_to_total = max(0, project.total_limit - cur_admin_count)
            need = min(need, remaining_to_total)

        if need <= 0:
            # Проект уже добрал total — закрываем
            with _transaction.atomic():
                project.is_closed = True
                project.last_fill_business_date = today_bd
                project.save(update_fields=["is_closed", "last_fill_business_date", "updated_at"])
            continue

        # Берём `need` случайных свободных записей из пула
        # (PostgreSQL ORDER BY random() — fine для пулов до сотен тысяч)
        free_ids = list(
            pool_qs.order_by("?").values_list("id", flat=True)[:need]
        )
        if not free_ids:
            # Пул пуст — просто помечаем дату чтобы не пытаться сегодня снова
            with _transaction.atomic():
                project.last_fill_business_date = today_bd
                project.save(update_fields=["last_fill_business_date", "updated_at"])
            continue

        with _transaction.atomic():
            free_entries = list(
                AdminPhonePool.objects.select_for_update()
                .filter(id__in=free_ids, is_used=False)
            )
            now = timezone.now()
            new_items: list[LidProjectItem] = []
            taken_ids: list[int] = []
            for entry in free_entries:
                # Возможен дубль-проверка через UniqueConstraint —
                # пробуем добавить, если дубль в проекте, пропускаем
                try:
                    item = LidProjectItem(
                        project=project, value=entry.value, is_admin=True,
                    )
                    item.save()
                    new_items.append(item)
                    entry.is_used = True
                    entry.used_in_project = project
                    entry.used_at = now
                    taken_ids.append(entry.id)
                except Exception:
                    # Уникальный конфликт (значение уже было в проекте) — пропускаем
                    continue

            if taken_ids:
                AdminPhonePool.objects.filter(id__in=taken_ids).update(
                    is_used=True, used_in_project=project, used_at=now,
                )

            # Обновляем дату раздачи + проверяем закрытие
            new_total = cur_admin_count + len(new_items)
            project.last_fill_business_date = today_bd
            if project.total_limit is not None and new_total >= project.total_limit:
                project.is_closed = True
            project.save(update_fields=["is_closed", "last_fill_business_date", "updated_at"])
            total_added += len(new_items)

    return total_added


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

        def _parse_int(s):
            try:
                n = int((s or "").strip())
                return n if n > 0 else None
            except (TypeError, ValueError):
                return None

        total_limit = _parse_int(request.POST.get("total_limit"))
        daily_limit = _parse_int(request.POST.get("daily_limit"))
        # Кап дневного лимита — не больше 50/день на проект.
        if daily_limit is not None and daily_limit > 50:
            daily_limit = 50
        values = parse_values(raw_values)

        if not project_name:
            messages.error(request, "Укажите название проекта.")
            return redirect("zavod_lidov_customer")
        project = LidProject.objects.create(
            customer=customer, name=project_name, business_date=today,
            total_limit=total_limit, daily_limit=daily_limit,
        )
        # Собственные значения клиента (опционально — могут оставить пустым)
        for v in values:
            try:
                LidProjectItem.objects.create(
                    project=project, submitter=customer, value=v, is_admin=False,
                )
            except IntegrityError:
                pass
        messages.success(request, f"Проект «{project_name}» создан.")
        return redirect("zavod_lidov_customer")

    # Lazy-fill: попробовать раздать из пула если ещё не сегодня
    try:
        try_daily_fill_for_customer(customer)
    except Exception:
        pass  # не валим dashboard если что-то с пулом

    # Список ВСЕХ проектов клиента, новые сверху, с метаданными
    today_msk_midnight = (
        timezone.now().astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    projects_qs = (
        LidProject.objects.filter(customer=customer)
        .annotate(
            n_total=Count("items"),
            n_admin_today=Count(
                "items",
                filter=Q(items__is_admin=True, items__created_at__gte=today_msk_midnight),
            ),
        )
        .order_by("-created_at")
    )
    projects = []
    for p in projects_qs:
        progress_pct = 0
        if p.total_limit:
            n_admin = LidProjectItem.objects.filter(project=p, is_admin=True).count()
            progress_pct = min(100, int(100 * n_admin / p.total_limit)) if p.total_limit else 0
        projects.append({
            "obj": p,
            "n_total": p.n_total,
            "n_admin_today": p.n_admin_today,
            "progress_pct": progress_pct,
        })

    return render(request, "zavod_lidov/dashboard.html", {
        "today": today,
        "projects": projects,
    })


@login_required
def customer_finish_project(request: HttpRequest, project_id: int) -> HttpResponse:
    if not _is_lid_customer(request.user):
        return HttpResponseForbidden("Только для заказчиков лидов.")
    if request.method != "POST":
        return redirect("zavod_lidov_customer")
    project = get_object_or_404(
        LidProject, pk=project_id, customer=request.user,
    )
    if not project.is_closed:
        project.is_closed = True
        project.save(update_fields=["is_closed", "updated_at"])
    return redirect("zavod_lidov_customer")


@login_required
def customer_download_excel(request: HttpRequest, project_id: int) -> HttpResponse:
    """Excel конкретного проекта: один лист с этим проектом."""
    if not _is_lid_customer(request.user):
        return HttpResponseForbidden("Только для заказчиков лидов.")

    project = get_object_or_404(
        LidProject, pk=project_id, customer=request.user,
    )
    # Скачать можно только когда проект закрыт (достиг total_limit).
    if not project.is_closed:
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
    """Главная страница: пул каждого клиента + список всех проектов."""
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только для главного админа.")

    # При заходе админа тоже триггерим lazy-fill для всех клиентов
    customers = User.objects.filter(role="lid_customer").order_by("username")
    for c in customers:
        try:
            try_daily_fill_for_customer(c)
        except Exception:
            pass

    today = business_date_now()

    # Пул-статус по клиентам
    pool_summary = []
    for c in customers:
        pool_summary.append({
            "customer": c,
            "n_free": AdminPhonePool.objects.filter(customer=c, is_used=False).count(),
            "n_used": AdminPhonePool.objects.filter(customer=c, is_used=True).count(),
            "n_open_projects": LidProject.objects.filter(customer=c, is_closed=False).count(),
        })

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
        progress_pct = 0
        if p.total_limit:
            progress_pct = min(100, int(100 * p.n_admin / p.total_limit))
        projects.append({
            "obj": p,
            "n_user": p.n_user,
            "n_admin": p.n_admin,
            "progress_pct": progress_pct,
        })
    return render(request, "core/admin_zavod_lidov.html", {
        "today": today,
        "projects": projects,
        "pool_summary": pool_summary,
    })


@login_required
def admin_pool(request: HttpRequest, customer_id: int) -> HttpResponse:
    """Страница пула: главный админ заливает большие пачки номеров."""
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только для главного админа.")
    customer = get_object_or_404(User, pk=customer_id, role="lid_customer")

    if request.method == "POST":
        raw = request.POST.get("values") or ""
        values = parse_values(raw)
        if not values:
            messages.warning(request, "Не удалось распознать ни одного значения.")
            return redirect("admin_zavod_lidov_pool", customer_id=customer.id)
        added = dup = 0
        for v in values:
            try:
                AdminPhonePool.objects.create(
                    customer=customer, submitter=request.user, value=v,
                )
                added += 1
            except IntegrityError:
                dup += 1
        msg = f"Добавлено в пул @{customer.username}: {added}."
        if dup:
            msg += f" Дубликатов пропущено: {dup}."
        messages.success(request, msg)
        return redirect("admin_zavod_lidov_pool", customer_id=customer.id)

    n_free = AdminPhonePool.objects.filter(customer=customer, is_used=False).count()
    n_used = AdminPhonePool.objects.filter(customer=customer, is_used=True).count()
    open_projects = (
        LidProject.objects.filter(customer=customer, is_closed=False)
        .annotate(n_admin=Count("items", filter=Q(items__is_admin=True)))
        .order_by("created_at")
    )

    return render(request, "core/admin_zavod_lidov_pool.html", {
        "customer": customer,
        "n_free": n_free,
        "n_used": n_used,
        "open_projects": open_projects,
    })


@login_required
def admin_project_detail(request: HttpRequest, project_id: int) -> HttpResponse:
    """Read-only карточка проекта: ленты значений клиента и админа."""
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только для главного админа.")

    project = get_object_or_404(LidProject.objects.select_related("customer"), pk=project_id)
    user_items = list(project.items.filter(is_admin=False).order_by("created_at"))
    admin_items = list(project.items.filter(is_admin=True).order_by("created_at"))
    return render(request, "core/admin_zavod_lidov_project.html", {
        "project": project,
        "user_items": user_items,
        "admin_items": admin_items,
        "closed": project.is_closed,
    })


def pending_admin_attention_count() -> int:
    """Сколько клиентов сейчас имеют ПУСТОЙ пул при наличии открытых проектов
    с daily_limit. Для бейджа на дашборде главного админа."""
    customers_with_open_projects = (
        LidProject.objects.filter(is_closed=False, daily_limit__gt=0)
        .values_list("customer_id", flat=True).distinct()
    )
    count = 0
    for cid in customers_with_open_projects:
        if not AdminPhonePool.objects.filter(customer_id=cid, is_used=False).exists():
            count += 1
    return count
