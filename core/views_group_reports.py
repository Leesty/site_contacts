"""Views для GroupReport — отчёты менеджеров о работе с группами (бета).

Модуль закрывает 4 контура:

1. **Менеджер** (роль `user`, флаг `can_create_group_reports=True`):
   - Список своих отчётов
   - Форма создания (4 поля: скринкаст / id клиента / id менеджера / дата)
   - Просмотр своего скринкаста
   - Урезанный календарь «Свободные слоты» (только если status=approved)

2. **Главный админ** — управление правами (выдать / отозвать).

3. **Модерация** (admin / main_admin):
   - Список + фильтр по статусу
   - approve (+80₽ менеджеру, +10₽ админу), reject, rework
   - Просмотр скринкаста любого отчёта

4. **Авто-валидация** против `windowgram.admin_task_progress`. Если запись
   с `(platform, admin_platform_user_id, client_platform_user_id)` есть и
   все 4 этапа (`artem_invited + link_done + offer_done + sozvon_done`)
   выполнены — отчёт `is_complete=True`. Иначе виден только главному админу.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import connections, transaction
from django.db.models import Q
from django.http import (
    FileResponse, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from .forms import (
    GroupReportCreateForm,
    GroupReportRejectForm,
    GroupReportReworkForm,
)
from .models import GroupReport, GroupReportReviewLog, User, log_balance_change


GROUP_REPORT_APPROVE_REWARD = 80  # ₽ за approve менеджеру

# Sync с бот-сервером (windowgram) при grant/revoke права на отчёты по группам.
# Ключ — тот же, что для webhook'а в обратную сторону (search-bot-start).
WINDOWGRAM_BASE_URL = "https://murzzvon.ru"
WINDOWGRAM_API_KEY = "p9EMWO1uPz75wFTEh2JS0Vo2oYtdAeyOs0veeH9FVu8"


def _windowgram_register_subadmin(
    platform: str, platform_user_id: int | None, username: str | None,
    display_name: str | None,
) -> tuple[bool, str]:
    """Регистрирует подадмина на бот-сервере. Идемпотентно — если уже есть,
    вернёт его."""
    import requests
    try:
        r = requests.post(
            f"{WINDOWGRAM_BASE_URL}/api/admins/external",
            headers={"Authorization": f"Bearer {WINDOWGRAM_API_KEY}"},
            json={
                "platform": platform,
                "platform_user_id": platform_user_id,
                "username": username or None,
                "display_name": display_name or None,
                "role": "sub",
            },
            timeout=10,
        )
        if r.status_code == 200:
            return True, "ok"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as exc:
        return False, str(exc)


def _windowgram_revoke_subadmin(
    platform: str, platform_user_id: int | None, username: str | None,
) -> tuple[bool, str]:
    """Удаляет подадмина с бот-сервера. Идемпотентно — если нет, вернёт ok."""
    import requests
    try:
        params = {"platform": platform}
        if platform_user_id is not None:
            params["platform_user_id"] = platform_user_id
        elif username:
            params["username"] = username
        else:
            return False, "no platform_user_id/username"
        r = requests.post(
            f"{WINDOWGRAM_BASE_URL}/api/admins/external/delete",
            headers={"Authorization": f"Bearer {WINDOWGRAM_API_KEY}"},
            params=params,
            timeout=10,
        )
        if r.status_code == 200:
            return True, "ok"
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    except Exception as exc:
        return False, str(exc)


# ════════════════════════════════════════════════════════════════════════════
# Гарды доступа
# ════════════════════════════════════════════════════════════════════════════

def _is_main_admin(user) -> bool:
    return user.is_authenticated and getattr(user, "role", None) == "main_admin"


def _is_admin_or_main(user) -> bool:
    return user.is_authenticated and getattr(user, "role", None) in ("admin", "main_admin")


def _is_manager_with_right(user) -> bool:
    return (
        user.is_authenticated
        and getattr(user, "role", None) == "user"
        and getattr(user, "can_create_group_reports", False)
    )


# ════════════════════════════════════════════════════════════════════════════
# Парсинг ID / username
# ════════════════════════════════════════════════════════════════════════════

_VK_ID_RE = re.compile(r"vk\.com/id(\d+)", re.IGNORECASE)
_VK_SCREEN_RE = re.compile(r"vk\.com/([a-zA-Z0-9_.]+)", re.IGNORECASE)
_DIGITS_RE = re.compile(r"^\s*(\d+)\s*$")


def _parse_link(text: str) -> tuple[int | None, str | None]:
    """Извлекает (numeric_id, username) из произвольного ввода.

    Поддерживает:
      - "123456789"           -> (123456789, None)
      - "@username"           -> (None, "username")
      - "vk.com/id12345"      -> (12345, None)
      - "https://vk.com/foo"  -> (None, "foo")
      - "https://t.me/ivan"   -> (None, "ivan")
    """
    if not text:
        return None, None
    s = text.strip()

    # Чистый id
    m = _DIGITS_RE.match(s)
    if m:
        try:
            return int(m.group(1)), None
        except ValueError:
            pass

    # vk.com/id12345
    m = _VK_ID_RE.search(s)
    if m:
        try:
            return int(m.group(1)), None
        except ValueError:
            pass

    # @username
    if s.startswith("@"):
        return None, s[1:].lower().strip()

    # t.me/username или t.me/+invite — берём первый сегмент
    if "t.me/" in s.lower():
        rest = s.lower().split("t.me/", 1)[1].split("?")[0].split("/")[0]
        if rest and not rest.startswith("+"):
            return None, rest

    # vk.com/screen_name (не id)
    m = _VK_SCREEN_RE.search(s)
    if m:
        rest = m.group(1)
        if not rest.startswith("id"):
            return None, rest.lower()

    # просто слово — считаем username'ом
    if re.match(r"^[a-zA-Z0-9_.]+$", s):
        return None, s.lower()

    return None, None


# ════════════════════════════════════════════════════════════════════════════
# Валидация против admin_task_progress на windowgram
# ════════════════════════════════════════════════════════════════════════════

def _validate_against_windowgram(
    platform: str,
    manager_id: int | None,
    manager_username: str | None,
    client_id: int | None,
    client_username: str | None,
) -> tuple[bool, str]:
    """Проверка по таблице admin_task_progress.

    Возвращает (is_complete, validation_note). is_complete=True только если:
      1. Найдена запись с указанным админом и клиентом
      2. artem_invited + link_done + offer_done + sozvon_done = ВСЕ True

    Если что-то одно не сошлось — is_complete=False с пояснением.
    """
    if not (manager_id or manager_username):
        return False, "Не указан ID/username менеджера — авто-валидация невозможна."
    if not (client_id or client_username):
        return False, "Не указан ID/username клиента — авто-валидация невозможна."

    where_admin = []
    params: list = []
    if manager_id:
        where_admin.append("admin_platform_user_id = %s")
        params.append(manager_id)
    if manager_username:
        where_admin.append("admin_username = %s")
        params.append(manager_username)
    where_client = []
    if client_id:
        where_client.append("client_platform_user_id = %s")
        params.append(client_id)
    if client_username:
        where_client.append("client_username = %s")
        params.append(client_username)

    sql = f"""
        SELECT artem_invited, link_done, offer_done, sozvon_done, completed_at,
               admin_platform_user_id, admin_username,
               client_platform_user_id, client_username
        FROM admin_task_progress
        WHERE platform = %s
          AND ({' OR '.join(where_admin)})
          AND ({' OR '.join(where_client)})
        ORDER BY updated_at DESC
        LIMIT 1
    """

    try:
        with connections["windowgram"].cursor() as cur:
            cur.execute(sql, [platform] + params)
            row = cur.fetchone()
    except Exception as exc:
        return False, f"Бот-сервер недоступен: {exc}"

    if not row:
        return False, "В admin_task_progress нет записи с такой связкой менеджер↔клиент."

    artem, link_done, offer_done, sozvon_done, *_ = row
    missing = []
    if not artem:
        missing.append("Артём не приглашён")
    if not link_done:
        missing.append("/линк не выполнен")
    if not offer_done:
        missing.append("/оффер не выполнен")
    if not sozvon_done:
        missing.append("/созвон не выполнен")

    if missing:
        return False, "; ".join(missing) + "."
    return True, "Все 4 этапа выполнены."


# ════════════════════════════════════════════════════════════════════════════
# Менеджер: список, форма, скринкаст
# ════════════════════════════════════════════════════════════════════════════

@login_required
def manager_group_reports_list(request: HttpRequest) -> HttpResponse:
    if not _is_manager_with_right(request.user):
        return HttpResponseForbidden("У вас нет права на отчёты по группам.")

    qs = GroupReport.objects.filter(user=request.user).order_by("-created_at")
    page_obj = Paginator(qs, 30).get_page(request.GET.get("page", 1))

    return render(request, "core/group_reports_my.html", {
        "page_obj": page_obj,
        "reward": GROUP_REPORT_APPROVE_REWARD,
    })


@login_required
def manager_group_report_create(request: HttpRequest) -> HttpResponse:
    if not _is_manager_with_right(request.user):
        return HttpResponseForbidden("У вас нет права на отчёты по группам.")

    if request.method == "POST":
        form = GroupReportCreateForm(request.POST, request.FILES)
        if form.is_valid():
            report: GroupReport = form.save(commit=False)
            report.user = request.user

            # Парсим client_link и manager_link
            client_id, client_username = _parse_link(form.cleaned_data["client_link"])
            manager_id, manager_username = _parse_link(form.cleaned_data["manager_link"])

            report.client_platform_id = client_id
            report.client_username = client_username or ""
            report.manager_platform_id = manager_id
            report.manager_username = manager_username or ""

            is_complete, note = _validate_against_windowgram(
                platform=report.platform,
                manager_id=manager_id,
                manager_username=manager_username,
                client_id=client_id,
                client_username=client_username,
            )
            report.is_complete = is_complete
            report.validation_note = note
            report.save()

            if is_complete:
                messages.success(
                    request,
                    "Отчёт принят и отправлен на проверку. Все 4 этапа подтверждены ботом.",
                )
            else:
                messages.warning(
                    request,
                    f"Отчёт сохранён, но без авто-подтверждения: {note}",
                )
            return redirect("manager_group_reports_list")
    else:
        form = GroupReportCreateForm(initial={"report_date": timezone.localtime(timezone.now()).date()})

    return render(request, "core/group_report_create.html", {
        "form": form,
        "reward": GROUP_REPORT_APPROVE_REWARD,
    })


@login_required
def manager_group_report_attachment(request: HttpRequest, report_id: int) -> HttpResponse:
    """Свой скринкаст. Менеджер может смотреть только свои."""
    report = get_object_or_404(GroupReport, pk=report_id, user=request.user)
    if not report.screencast:
        return HttpResponseForbidden("Файл не приложен.")
    return FileResponse(report.screencast.open("rb"), as_attachment=False,
                        filename=report.screencast.name.rsplit("/", 1)[-1])


# ════════════════════════════════════════════════════════════════════════════
# Главный админ: управление правами
# ════════════════════════════════════════════════════════════════════════════

@login_required
def admin_group_report_permissions(request: HttpRequest) -> HttpResponse:
    """Карточка управления правом can_create_group_reports.

    Показывает:
      - Список юзеров с правом (можно отозвать)
      - Поиск активных юзеров для выдачи
    """
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только главный админ.")

    granted = User.objects.filter(can_create_group_reports=True).order_by("username")

    q = (request.GET.get("q") or "").strip()
    candidates = []
    if q:
        candidates = list(
            User.objects.filter(
                role=User.Role.USER,
                can_create_group_reports=False,
            ).filter(
                Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            ).exclude(status=User.Status.BANNED)[:20]
        )

    return render(request, "core/admin_group_report_permissions.html", {
        "granted": granted,
        "candidates": candidates,
        "q": q,
    })


def _parse_vk_link(s: str) -> str:
    """`https://vk.com/id12345` / `vk.com/foo` / `@foo` / `foo` → `foo` / `id12345`."""
    s = (s or "").strip().lstrip("@")
    if not s:
        return ""
    low = s.lower()
    for marker in ("vk.com/", "vk.ru/"):
        if marker in low:
            idx = low.find(marker)
            rest = s[idx + len(marker):].split("?")[0].strip().rstrip("/")
            return rest.lower()
    return s.lower()


@login_required
@require_POST
def admin_group_report_grant(request: HttpRequest, user_id: int) -> HttpResponse:
    """Выдать право + зарегистрировать на бот-сервере как подадмина.

    Поддерживается ОБА аккаунта одновременно — менеджер часто работает
    и через TG, и через VK. Хотя бы одно поле обязательно.
    """
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только главный админ.")
    target = get_object_or_404(User, pk=user_id, role=User.Role.USER)

    tg_username = (request.POST.get("tg_username") or "").strip().lstrip("@").lower()
    vk_screen = _parse_vk_link(request.POST.get("vk_link") or "")

    if not tg_username and not vk_screen:
        messages.error(
            request,
            "Укажите Telegram @username и/или VK ссылку — хотя бы одно обязательно.",
        )
        return redirect("admin_group_report_permissions")

    display_name = " ".join(filter(None, [target.first_name, target.last_name])) or target.username

    # Регистрируем на бот-сервере — отдельная запись на каждую платформу.
    # Если хоть один sync упал — право не выдаём, чтобы не было рассинхрона.
    errors: list[str] = []
    if tg_username:
        ok, note = _windowgram_register_subadmin(
            platform="telegram", platform_user_id=None,
            username=tg_username, display_name=display_name,
        )
        if not ok:
            errors.append(f"TG: {note}")
    if vk_screen:
        ok, note = _windowgram_register_subadmin(
            platform="vk", platform_user_id=None,
            username=vk_screen, display_name=display_name,
        )
        if not ok:
            errors.append(f"VK: {note}")

    if errors:
        messages.error(
            request,
            f"Не удалось зарегистрировать @{target.username} на бот-сервере: "
            f"{'; '.join(errors)}. Право не выдано.",
        )
        return redirect("admin_group_report_permissions")

    target.can_create_group_reports = True
    target.bot_admin_tg_username = tg_username
    target.bot_admin_vk_screen_name = vk_screen
    target.save(update_fields=[
        "can_create_group_reports",
        "bot_admin_tg_username", "bot_admin_vk_screen_name",
    ])
    parts = []
    if tg_username:
        parts.append(f"TG @{tg_username}")
    if vk_screen:
        parts.append(f"VK {vk_screen}")
    messages.success(
        request,
        f"@{target.username} получил право и зарегистрирован как подадмин ({', '.join(parts)}).",
    )
    return redirect("admin_group_report_permissions")


@login_required
@require_POST
def admin_group_report_revoke(request: HttpRequest, user_id: int) -> HttpResponse:
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только главный админ.")
    target = get_object_or_404(User, pk=user_id)
    if not target.can_create_group_reports:
        messages.info(request, f"У @{target.username} и так нет права.")
        return redirect("admin_group_report_permissions")

    # Снимаем подадмина с бот-сервера на КАЖДОЙ привязанной платформе.
    # Soft-fail — если бот недоступен, в нашей БД право снимем всё равно.
    bot_errors: list[str] = []
    if target.bot_admin_tg_username:
        ok, note = _windowgram_revoke_subadmin(
            platform="telegram", platform_user_id=None,
            username=target.bot_admin_tg_username,
        )
        if not ok:
            bot_errors.append(f"TG: {note}")
    if target.bot_admin_vk_screen_name:
        ok, note = _windowgram_revoke_subadmin(
            platform="vk", platform_user_id=None,
            username=target.bot_admin_vk_screen_name,
        )
        if not ok:
            bot_errors.append(f"VK: {note}")
    if bot_errors:
        messages.warning(
            request,
            f"Право у @{target.username} снято локально, но на бот-сервере не получилось: "
            f"{'; '.join(bot_errors)}. Снимите вручную через /api/admins.",
        )

    target.can_create_group_reports = False
    target.bot_admin_tg_username = ""
    target.bot_admin_vk_screen_name = ""
    target.save(update_fields=[
        "can_create_group_reports",
        "bot_admin_tg_username", "bot_admin_vk_screen_name",
    ])
    messages.success(request, f"У @{target.username} отозвано право на отчёты по группам.")
    return redirect("admin_group_report_permissions")


# ════════════════════════════════════════════════════════════════════════════
# Модерация (admin + main_admin)
# ════════════════════════════════════════════════════════════════════════════

VALID_TABS = ("pending", "rework", "approved", "rejected", "incomplete")


@login_required
def admin_group_reports_list(request: HttpRequest) -> HttpResponse:
    if not _is_admin_or_main(request.user):
        return HttpResponseForbidden("Недостаточно прав.")

    is_main = _is_main_admin(request.user)

    tab = request.GET.get("tab", "pending")
    if tab not in VALID_TABS:
        tab = "pending"
    # Вкладка «Не полные» — только для главного админа
    if tab == "incomplete" and not is_main:
        tab = "pending"

    qs = GroupReport.objects.select_related("user", "reviewed_by").order_by("-created_at")

    # Обычный админ ВООБЩЕ не видит is_complete=False
    if not is_main:
        qs = qs.filter(is_complete=True)

    if tab == "pending":
        qs = qs.filter(status=GroupReport.Status.PENDING)
        if is_main:
            qs = qs.filter(is_complete=True)  # на проверке у админа — только полные
    elif tab == "rework":
        qs = qs.filter(status=GroupReport.Status.REWORK)
    elif tab == "approved":
        qs = qs.filter(status=GroupReport.Status.APPROVED)
    elif tab == "rejected":
        qs = qs.filter(status=GroupReport.Status.REJECTED)
    elif tab == "incomplete":
        # «Не полные» — только если is_complete=False, любой статус
        qs = qs.filter(is_complete=False)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(user__username__icontains=q)
            | Q(client_username__icontains=q)
            | Q(manager_username__icontains=q)
            | Q(client_platform_id__icontains=q if q.isdigit() else None)
        ) if q.isdigit() else qs.filter(
            Q(user__username__icontains=q)
            | Q(client_username__icontains=q)
            | Q(manager_username__icontains=q)
        )

    page_obj = Paginator(qs, 30).get_page(request.GET.get("page", 1))

    counts = {
        "pending": GroupReport.objects.filter(
            status=GroupReport.Status.PENDING,
            is_complete=True,
        ).count(),
        "rework": GroupReport.objects.filter(status=GroupReport.Status.REWORK).count(),
    }
    if is_main:
        counts["incomplete"] = GroupReport.objects.filter(is_complete=False).count()

    return render(request, "core/admin_group_reports.html", {
        "page_obj": page_obj,
        "tab": tab,
        "q": q,
        "is_main": is_main,
        "counts": counts,
        "reward": GROUP_REPORT_APPROVE_REWARD,
    })


@login_required
def admin_group_report_attachment(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _is_admin_or_main(request.user):
        return HttpResponseForbidden("Недостаточно прав.")
    report = get_object_or_404(GroupReport, pk=report_id)
    # Обычный админ не видит вложения incomplete-отчётов
    if not _is_main_admin(request.user) and not report.is_complete:
        return HttpResponseForbidden("Этот отчёт не прошёл авто-валидацию.")
    if not report.screencast:
        return HttpResponseForbidden("Файл не приложен.")
    return FileResponse(report.screencast.open("rb"), as_attachment=False,
                        filename=report.screencast.name.rsplit("/", 1)[-1])


def _split_group_report_payout(manager: "User") -> tuple[int, int, "User | None"]:
    """Считает разделение `GROUP_REPORT_APPROVE_REWARD` между рефералом
    (менеджером) и его рефоводом/партнёром.

    Возвращает `(referral_reward, owner_cut, owner_user)`.
    Если у менеджера нет `partner_owner` — owner_user=None, owner_cut=0.

    Для роли `partner` берётся общая ставка `User.partner_group_report_cut`.
    Для обычного рефовода (role=user) — `PartnerLink.ref_group_report_cut`
    из той ссылки, по которой реферал зарегистрировался.
    """
    pool = GROUP_REPORT_APPROVE_REWARD
    owner = getattr(manager, "partner_owner", None)
    if not owner:
        return pool, 0, None
    if owner.role == "partner":
        cut = owner.partner_group_report_cut or 50
    else:
        link = getattr(manager, "partner_link", None)
        cut = link.ref_group_report_cut if link else 50
    cut = max(0, min(pool, int(cut)))
    return pool - cut, cut, owner


@login_required
@require_POST
def admin_group_report_approve(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _is_admin_or_main(request.user):
        return HttpResponseForbidden("Недостаточно прав.")

    from .models import PartnerEarning

    with transaction.atomic():
        report = (
            GroupReport.objects.select_for_update()
            .select_related("user", "user__partner_owner", "user__partner_link")
            .filter(pk=report_id)
            .first()
        )
        if not report:
            messages.error(request, "Отчёт не найден.")
            return redirect("admin_group_reports_list")
        if not _is_main_admin(request.user) and not report.is_complete:
            return HttpResponseForbidden("Этот отчёт не прошёл авто-валидацию.")
        if report.status == GroupReport.Status.APPROVED:
            messages.info(request, f"Отчёт #{report_id} уже одобрен.")
            return redirect("admin_group_reports_list")

        ref_reward, owner_cut, owner_user = _split_group_report_payout(report.user)

        report.status = GroupReport.Status.APPROVED
        report.rejection_reason = ""
        report.rework_comment = ""
        report.reviewed_at = timezone.now()
        report.reviewed_by = request.user
        report.paid_reward = ref_reward
        report.save(update_fields=[
            "status", "rejection_reason", "rework_comment",
            "reviewed_at", "reviewed_by", "paid_reward", "updated_at",
        ])

        # Реферал / менеджер
        manager = User.objects.select_for_update().get(pk=report.user_id)
        _old = manager.balance or 0
        manager.balance = _old + ref_reward
        manager.save(update_fields=["balance"])
        log_balance_change(
            manager, "balance", _old, manager.balance,
            f"group_report_approve#{report_id} +{ref_reward}",
            request.user,
        )

        # Рефовод/партнёр (если есть)
        if owner_user and owner_cut > 0:
            PartnerEarning.objects.create(
                partner=owner_user, group_report=report, amount=owner_cut,
            )

        GroupReportReviewLog.objects.create(
            report=report, admin=request.user,
            action=GroupReportReviewLog.Action.APPROVED,
        )

    if owner_user and owner_cut > 0:
        messages.success(
            request,
            f"Отчёт #{report_id} одобрен. Реф @{report.user.username} +{ref_reward} ₽, "
            f"рефовод @{owner_user.username} +{owner_cut} ₽.",
        )
    else:
        messages.success(
            request,
            f"Отчёт #{report_id} одобрен. @{report.user.username} начислено {ref_reward} ₽.",
        )
    return redirect("admin_group_reports_list")


@login_required
def admin_group_report_reject(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _is_admin_or_main(request.user):
        return HttpResponseForbidden("Недостаточно прав.")
    report = get_object_or_404(GroupReport, pk=report_id)
    if not _is_main_admin(request.user) and not report.is_complete:
        return HttpResponseForbidden("Этот отчёт не прошёл авто-валидацию.")

    if request.method == "POST":
        form = GroupReportRejectForm(request.POST)
        if form.is_valid():
            from .models import PartnerEarning
            with transaction.atomic():
                # Если был approved — откат начисления реферала + удаление partner earning
                if report.status == GroupReport.Status.APPROVED and report.paid_reward:
                    owner = User.objects.select_for_update().get(pk=report.user_id)
                    _old = owner.balance or 0
                    owner.balance = _old - report.paid_reward
                    owner.save(update_fields=["balance"])
                    log_balance_change(
                        owner, "balance", _old, owner.balance,
                        f"group_report_reject_rollback#{report_id} -{report.paid_reward}",
                        request.user,
                    )
                    report.paid_reward = 0
                # Откат начисления рефоводу
                PartnerEarning.objects.filter(group_report=report).delete()
                report.status = GroupReport.Status.REJECTED
                report.rejection_reason = form.cleaned_data["rejection_reason"]
                report.reviewed_at = timezone.now()
                report.reviewed_by = request.user
                report.save(update_fields=[
                    "status", "rejection_reason", "reviewed_at", "reviewed_by",
                    "paid_reward", "updated_at",
                ])
                GroupReportReviewLog.objects.create(
                    report=report, admin=request.user,
                    action=GroupReportReviewLog.Action.REJECTED,
                )
            messages.success(request, f"Отчёт #{report_id} отклонён.")
            return redirect("admin_group_reports_list")
    else:
        form = GroupReportRejectForm()

    return render(request, "core/admin_group_report_reject.html", {
        "form": form, "report": report,
    })


@login_required
def admin_group_report_rework(request: HttpRequest, report_id: int) -> HttpResponse:
    if not _is_admin_or_main(request.user):
        return HttpResponseForbidden("Недостаточно прав.")
    report = get_object_or_404(GroupReport, pk=report_id)
    if not _is_main_admin(request.user) and not report.is_complete:
        return HttpResponseForbidden("Этот отчёт не прошёл авто-валидацию.")

    if request.method == "POST":
        form = GroupReportReworkForm(request.POST)
        if form.is_valid():
            from .models import PartnerEarning
            with transaction.atomic():
                if report.status == GroupReport.Status.APPROVED and report.paid_reward:
                    owner = User.objects.select_for_update().get(pk=report.user_id)
                    _old = owner.balance or 0
                    owner.balance = _old - report.paid_reward
                    owner.save(update_fields=["balance"])
                    log_balance_change(
                        owner, "balance", _old, owner.balance,
                        f"group_report_rework_rollback#{report_id} -{report.paid_reward}",
                        request.user,
                    )
                    report.paid_reward = 0
                # Откат начисления рефоводу
                PartnerEarning.objects.filter(group_report=report).delete()
                report.status = GroupReport.Status.REWORK
                report.rework_comment = form.cleaned_data.get("rework_comment", "")
                report.rejection_reason = ""
                report.reviewed_at = timezone.now()
                report.reviewed_by = request.user
                report.save(update_fields=[
                    "status", "rework_comment", "rejection_reason",
                    "reviewed_at", "reviewed_by", "paid_reward", "updated_at",
                ])
                GroupReportReviewLog.objects.create(
                    report=report, admin=request.user,
                    action=GroupReportReviewLog.Action.REWORK,
                )
            messages.success(request, f"Отчёт #{report_id} отправлен на доработку.")
            return redirect("admin_group_reports_list")
    else:
        form = GroupReportReworkForm()

    return render(request, "core/admin_group_report_rework.html", {
        "form": form, "report": report,
    })


# ════════════════════════════════════════════════════════════════════════════
# Свободные слоты (урезанный календарь для одобренных менеджеров)
# ════════════════════════════════════════════════════════════════════════════

# Должно совпадать с /opt/windowgram/backend/app/api/booking.py — расписание
# и параметры слотов на бот-сервере. При изменении на боте — обновить тут.
MSK = ZoneInfo("Europe/Moscow")
SLOT_STEP_MIN = 15
MIN_GAP_MIN = 15
SLOT_CAPACITY = 2
HORIZON_DAYS = 14
SCHEDULE: dict[int, list[tuple[str, str]]] = {
    0: [("10:00", "16:00"), ("19:45", "21:00")],
    1: [("10:00", "16:00"), ("19:45", "21:00")],
    2: [("10:00", "16:00"), ("19:45", "21:00")],
    3: [("10:00", "16:00"), ("19:45", "21:00")],
    4: [("10:00", "16:00"), ("19:45", "21:00")],
    5: [],
    6: [("13:00", "18:00")],
}
SCHEDULE_OVERRIDES: dict[date, list[tuple[str, str]]] = {
    date(2026, 5, 7): [("10:00", "16:00")],
    date(2026, 5, 8): [("10:00", "15:00")],
}
NON_BOOKING_PREFIXES = ("[Жду бабки]", "[Ответ]", "[Жду без даты]", "[Просрочка")


def _get_ranges_for_day(d: date) -> list[tuple[str, str]]:
    if d in SCHEDULE_OVERRIDES:
        return SCHEDULE_OVERRIDES[d]
    return SCHEDULE.get(d.weekday(), [])


def _generate_slots_for_day(d: date) -> list[datetime]:
    slots: list[datetime] = []
    for start_str, end_str in _get_ranges_for_day(d):
        sh, sm = map(int, start_str.split(":"))
        eh, em = map(int, end_str.split(":"))
        start = datetime.combine(d, time(sh, sm), tzinfo=MSK)
        end = datetime.combine(d, time(eh, em), tzinfo=MSK)
        cur = start
        while cur < end:
            slots.append(cur)
            cur += timedelta(minutes=SLOT_STEP_MIN)
    return slots


def _bookings_by_minute_window(start: date, end: date) -> dict[date, dict[int, int]]:
    """{date: {minutes_from_midnight: count}} в окне [start, end]. Учитываем
    только реальные созвоны (без статусных напоминалок)."""
    sql = """
        SELECT event_date, event_time, user_name FROM calendar_events
        WHERE event_date BETWEEN %s AND %s
    """
    out: dict[date, dict[int, int]] = {}
    try:
        with connections["windowgram"].cursor() as cur:
            cur.execute(sql, [start, end])
            for ev_date, ev_time, user_name in cur.fetchall():
                if not ev_time:
                    continue
                name = (user_name or "").strip()
                if any(name.startswith(p) for p in NON_BOOKING_PREFIXES):
                    continue
                parts = ev_time.replace(".", ":").split(":")
                if len(parts) != 2:
                    continue
                try:
                    h, m = int(parts[0]), int(parts[1])
                except ValueError:
                    continue
                key = h * 60 + m
                day_map = out.setdefault(ev_date, {})
                day_map[key] = day_map.get(key, 0) + 1
    except Exception:
        return {}
    return out


def _is_slot_free(bookings: dict[date, dict[int, int]], d: date, h: int, m: int) -> bool:
    """Тот же алгоритм что в booking.py: capacity на тот же минут + min_gap к
    остальным."""
    day_map = bookings.get(d, {})
    slot_min = h * 60 + m
    if day_map.get(slot_min, 0) >= SLOT_CAPACITY:
        return False
    for other_min in day_map.keys():
        if other_min == slot_min:
            continue
        if abs(other_min - slot_min) < MIN_GAP_MIN:
            return False
    return True


@login_required
def free_slots_calendar(request: HttpRequest) -> HttpResponse:
    """Урезанный календарь свободных дат для менеджера. БЕЗ имён клиентов и
    деталей — только зелёные/серые слоты."""
    user = request.user
    if not _is_manager_with_right(user):
        return HttpResponseForbidden("У вас нет права на отчёты по группам.")
    if user.status != User.Status.APPROVED:
        return HttpResponseForbidden("Доступ к календарю — после одобрения профиля.")

    today = timezone.localtime(timezone.now()).date()
    horizon = today + timedelta(days=HORIZON_DAYS)
    now_msk = datetime.now(MSK)
    min_dt = now_msk + timedelta(hours=1)  # ближе чем за час — не показываем

    bookings = _bookings_by_minute_window(today, horizon)

    days = []
    for i in range(HORIZON_DAYS):
        d = today + timedelta(days=i)
        slots = []
        free_count = 0
        for dt in _generate_slots_for_day(d):
            if dt < min_dt:
                continue
            free = _is_slot_free(bookings, d, dt.hour, dt.minute)
            if free:
                free_count += 1
            slots.append({
                "time": f"{dt.hour:02d}:{dt.minute:02d}",
                "free": free,
            })
        if slots:
            days.append({
                "date": d,
                "weekday": d.weekday(),
                "is_today": d == today,
                "slots": slots,
                "free_count": free_count,
                "total_count": len(slots),
            })

    return render(request, "core/free_slots_calendar.html", {
        "days": days,
        "horizon_days": HORIZON_DAYS,
        "today": today,
    })
