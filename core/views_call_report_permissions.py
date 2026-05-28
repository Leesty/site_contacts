"""Управление правом `can_create_call_reports` (главный админ).

Полностью копирует pattern из `views_group_reports.admin_group_report_permissions`:
- Главный админ через UI выдаёт право конкретным менеджерам
- При grant — регистрирует их как подадминов на windowgram (TG и/или VK),
  так чтобы notify_bot реагировал на их команды
- При revoke — снимает подадмина с windowgram, НО только если у юзера
  больше нет другого права требующего подадмина (например, group_reports)
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import User
from .views_group_reports import (
    _is_main_admin,
    _parse_vk_link,
    _windowgram_register_subadmin,
    _windowgram_revoke_subadmin,
)


def _user_needs_subadmin(user: User) -> bool:
    """True если у юзера всё ещё есть хотя бы одно право требующее подадмина."""
    return bool(user.can_create_group_reports or user.can_create_call_reports)


@login_required
def admin_call_report_permissions(request: HttpRequest) -> HttpResponse:
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только главный админ.")

    granted = User.objects.filter(can_create_call_reports=True).order_by("username")

    q = (request.GET.get("q") or "").strip()
    candidates = []
    if q:
        candidates = list(
            User.objects.filter(
                role=User.Role.USER,
                can_create_call_reports=False,
            ).filter(
                Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            ).exclude(status=User.Status.BANNED)[:20]
        )

    return render(request, "core/admin_call_report_permissions.html", {
        "granted": granted,
        "candidates": candidates,
        "q": q,
    })


@login_required
@require_POST
def admin_call_report_grant(request: HttpRequest, user_id: int) -> HttpResponse:
    """Выдать can_create_call_reports + (идемпотентно) зарегистрировать подадмина."""
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только главный админ.")
    target = get_object_or_404(User, pk=user_id, role=User.Role.USER)

    # Если у юзера УЖЕ есть group_reports — у него уже зарегистрированы
    # bot_admin_tg/vk_screen_name. Можно использовать их без повторного ввода.
    existing_tg = target.bot_admin_tg_username or ""
    existing_vk = target.bot_admin_vk_screen_name or ""

    tg_username = (request.POST.get("tg_username") or existing_tg).strip().lstrip("@").lower()
    vk_screen = _parse_vk_link(request.POST.get("vk_link") or existing_vk)

    if not tg_username and not vk_screen:
        messages.error(
            request,
            "Укажите Telegram @username и/или VK ссылку — хотя бы одно обязательно.",
        )
        return redirect("admin_call_report_permissions")

    display_name = " ".join(filter(None, [target.first_name, target.last_name])) or target.username

    # Регистрируем (идемпотентно — windowgram возвращает существующего если есть).
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
        return redirect("admin_call_report_permissions")

    target.can_create_call_reports = True
    target.bot_admin_tg_username = tg_username
    target.bot_admin_vk_screen_name = vk_screen
    target.save(update_fields=[
        "can_create_call_reports",
        "bot_admin_tg_username", "bot_admin_vk_screen_name",
    ])
    parts = []
    if tg_username:
        parts.append(f"TG @{tg_username}")
    if vk_screen:
        parts.append(f"VK {vk_screen}")
    messages.success(
        request,
        f"@{target.username} получил право на «Прозвоны» и зарегистрирован как подадмин ({', '.join(parts)}).",
    )
    return redirect("admin_call_report_permissions")


@login_required
@require_POST
def admin_call_report_revoke(request: HttpRequest, user_id: int) -> HttpResponse:
    if not _is_main_admin(request.user):
        return HttpResponseForbidden("Только главный админ.")
    target = get_object_or_404(User, pk=user_id)
    if not target.can_create_call_reports:
        messages.info(request, f"У @{target.username} и так нет права.")
        return redirect("admin_call_report_permissions")

    # Снимаем флаг сначала — потом смотрим нужен ли ещё подадмин.
    target.can_create_call_reports = False

    update_fields = ["can_create_call_reports"]
    if not _user_needs_subadmin(target):
        # Никакое другое право не требует подадмина — снимаем с windowgram.
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
                f"{'; '.join(bot_errors)}.",
            )
        target.bot_admin_tg_username = ""
        target.bot_admin_vk_screen_name = ""
        update_fields += ["bot_admin_tg_username", "bot_admin_vk_screen_name"]
    target.save(update_fields=update_fields)
    messages.success(request, f"У @{target.username} отозвано право на «Прозвоны».")
    return redirect("admin_call_report_permissions")
