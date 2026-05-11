"""Ручная привязка клиента к SearchLink менеджера.

Менеджер уже создал реф-ссылку, но клиент не пришёл по ней (перехода нет,
`link.bot_started=False`). Прямо в карточке этой ссылки менеджер вводит
telegram_id / @username / vk-ссылку клиента и отправляет на проверку.

Если по идентификатору:
- никто другой не привязан → заявка PENDING, ждёт админа;
- кто-то уже привязан → заявка REJECTED сразу (auto).

Админ на /staff/manual-claims/ одобряет → +150 ₽ менеджеру + проставляются
identifier-поля на SearchLink + bot_started=True (теперь дедуп SR работает
для этого клиента).
"""

from __future__ import annotations

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import ManualSearchClaim, SearchLink, User, log_balance_change


MANUAL_CLAIM_REWARD = 150  # ₽ за approved ручную привязку


def _require_approved_user(request: HttpRequest) -> bool:
    user = request.user
    return (
        user.is_authenticated
        and getattr(user, "role", None) == "user"
        and getattr(user, "status", None) == "approved"
    )


def _parse_manual_claim_input(raw: str) -> dict:
    """Парсит ввод менеджера в идентификаторы клиента.

    Поддерживает:
      - 123456789           → telegram_id (чистое число)
      - @ivanov             → telegram_username
      - t.me/ivanov         → telegram_username
      - https://vk.com/id12 → vk_user_id
      - vk.com/ivanov       → vk_screen_name
    """
    s = (raw or "").strip()
    if not s:
        return {}
    low = s.lower()

    for marker in ("vk.com/", "vk.ru/"):
        if marker in low:
            idx = low.find(marker)
            rest = s[idx + len(marker):].split("?")[0].strip().rstrip("/").lower()
            if rest.startswith("id") and rest[2:].isdigit():
                vid = int(rest[2:])
                return {"platform": "vk", "vk_user_id": vid,
                        "normalized_identifier": f"vk:id{vid}"}
            if rest and re.match(r"^[a-z0-9_.]+$", rest):
                return {"platform": "vk", "vk_screen_name": rest,
                        "normalized_identifier": f"vk:{rest}"}
            return {}

    for marker in ("t.me/", "telegram.me/", "telegram.dog/"):
        if marker in low:
            idx = low.find(marker)
            rest = s[idx + len(marker):].split("?")[0].strip().rstrip("/").lstrip("@").lower()
            if rest.startswith("+"):
                return {}
            if rest.isdigit():
                tid = int(rest)
                return {"platform": "telegram", "telegram_id": tid,
                        "normalized_identifier": f"telegram:{tid}"}
            if rest and re.match(r"^[a-z0-9_]+$", rest):
                return {"platform": "telegram", "telegram_username": rest,
                        "normalized_identifier": f"telegram:{rest}"}
            return {}

    if s.startswith("@"):
        rest = s[1:].strip().lower()
        if rest.isdigit():
            tid = int(rest)
            return {"platform": "telegram", "telegram_id": tid,
                    "normalized_identifier": f"telegram:{tid}"}
        if rest and re.match(r"^[a-z0-9_]+$", rest):
            return {"platform": "telegram", "telegram_username": rest,
                    "normalized_identifier": f"telegram:{rest}"}
        return {}

    if s.isdigit():
        tid = int(s)
        return {"platform": "telegram", "telegram_id": tid,
                "normalized_identifier": f"telegram:{tid}"}

    if re.match(r"^[a-zA-Z0-9_]{3,}$", s):
        u = s.lower()
        return {"platform": "telegram", "telegram_username": u,
                "normalized_identifier": f"telegram:{u}"}
    return {}


def _find_existing_claim_owner(parsed: dict, exclude_link_id=None) -> tuple[SearchLink | None, ManualSearchClaim | None]:
    """Кто уже привязан к этому клиенту: SearchLink (bot_started=True) или
    предыдущий PENDING/APPROVED ManualSearchClaim. Возвращает первого."""
    if not parsed:
        return None, None
    sl_q = Q()
    mc_q = Q()
    has = False
    if parsed.get("telegram_id"):
        sl_q |= Q(telegram_id=parsed["telegram_id"])
        mc_q |= Q(telegram_id=parsed["telegram_id"])
        has = True
    if parsed.get("telegram_username"):
        sl_q |= Q(telegram_username__iexact=parsed["telegram_username"])
        mc_q |= Q(telegram_username__iexact=parsed["telegram_username"])
        has = True
    if parsed.get("vk_user_id"):
        sl_q |= Q(vk_user_id=parsed["vk_user_id"])
        mc_q |= Q(vk_user_id=parsed["vk_user_id"])
        has = True
    if parsed.get("vk_screen_name"):
        sl_q |= Q(vk_screen_name__iexact=parsed["vk_screen_name"])
        mc_q |= Q(vk_screen_name__iexact=parsed["vk_screen_name"])
        has = True
    if not has:
        return None, None
    existing_sl = SearchLink.objects.filter(sl_q).select_related("user").order_by("created_at")
    if exclude_link_id:
        existing_sl = existing_sl.exclude(pk=exclude_link_id)
    existing_sl = existing_sl.first()
    if existing_sl:
        return existing_sl, None
    existing_mc_qs = (
        ManualSearchClaim.objects.filter(
            mc_q,
            status__in=[ManualSearchClaim.Status.PENDING, ManualSearchClaim.Status.APPROVED],
        ).select_related("user").order_by("created_at")
    )
    if exclude_link_id:
        existing_mc_qs = existing_mc_qs.exclude(search_link_id=exclude_link_id)
    return None, existing_mc_qs.first()


@login_required
@require_POST
def search_link_manual_claim(request: HttpRequest, code: str) -> HttpResponse:
    """POST с client_input — менеджер вручную привязывает клиента к своей
    SearchLink. Без перехода через бота."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Доступ запрещён.")

    link = get_object_or_404(SearchLink, code=code, user=request.user)

    if link.bot_started:
        messages.info(request, f"Ссылка #{link.display_id or link.id} уже подтверждена ботом.")
        return redirect("search_links_my")
    if hasattr(link, "manual_claim") and link.manual_claim:
        existing = link.manual_claim
        if existing.status == ManualSearchClaim.Status.PENDING:
            messages.info(request, f"Заявка на ручную привязку уже ждёт проверки.")
        elif existing.status == ManualSearchClaim.Status.APPROVED:
            messages.info(request, f"Клиент уже привязан к этой ссылке.")
        else:
            messages.info(
                request,
                f"Предыдущая ручная привязка отклонена: {existing.rejection_reason or '—'}. "
                f"Удалите её, чтобы попробовать снова.",
            )
        return redirect("search_links_my")

    raw = (request.POST.get("client_input") or "").strip()
    if not raw:
        messages.error(request, "Введите telegram_id / @username / VK-ссылку клиента.")
        return redirect("search_links_my")

    parsed = _parse_manual_claim_input(raw)
    if not parsed:
        messages.error(
            request,
            "Не удалось распознать формат. Используйте: 123456789, @username, "
            "https://t.me/username или https://vk.com/id123 / https://vk.com/screen_name.",
        )
        return redirect("search_links_my")

    existing_sl, existing_mc = _find_existing_claim_owner(parsed, exclude_link_id=link.id)

    with transaction.atomic():
        if existing_sl or existing_mc:
            owner = existing_sl.user if existing_sl else existing_mc.user
            short_id = (
                f"SearchLink #{existing_sl.display_id or existing_sl.id}"
                if existing_sl else f"ManualClaim #{existing_mc.id}"
            )
            ManualSearchClaim.objects.create(
                user=request.user,
                search_link=link,
                raw_input=raw,
                normalized_identifier=parsed["normalized_identifier"],
                platform=parsed["platform"],
                telegram_id=parsed.get("telegram_id"),
                telegram_username=parsed.get("telegram_username") or "",
                vk_user_id=parsed.get("vk_user_id"),
                vk_screen_name=parsed.get("vk_screen_name") or "",
                status=ManualSearchClaim.Status.REJECTED,
                rejection_reason=f"Клиент уже привязан к @{owner.username} ({short_id}).",
                paid_reward=0,
                matched_search_link=existing_sl,
                matched_manual_claim=existing_mc,
                reviewed_at=timezone.now(),
            )
            messages.warning(
                request,
                f"Клиент уже привязан к @{owner.username} ({short_id}). "
                f"Заявка отклонена — выплаты нет.",
            )
        else:
            ManualSearchClaim.objects.create(
                user=request.user,
                search_link=link,
                raw_input=raw,
                normalized_identifier=parsed["normalized_identifier"],
                platform=parsed["platform"],
                telegram_id=parsed.get("telegram_id"),
                telegram_username=parsed.get("telegram_username") or "",
                vk_user_id=parsed.get("vk_user_id"),
                vk_screen_name=parsed.get("vk_screen_name") or "",
                status=ManualSearchClaim.Status.PENDING,
                paid_reward=0,
            )
            messages.success(
                request,
                f"Заявка на ручную привязку отправлена на проверку. "
                f"При одобрении +{MANUAL_CLAIM_REWARD} ₽.",
            )

    return redirect("search_links_my")


# ════════════════════════════════════════════════════════════════════════════
# Админская модерация
# ════════════════════════════════════════════════════════════════════════════

def _is_admin_or_main(user) -> bool:
    return user.is_authenticated and getattr(user, "role", None) in ("admin", "main_admin")


VALID_ADMIN_TABS = ("pending", "approved", "rejected", "all")


@login_required
def admin_manual_claims_list(request: HttpRequest) -> HttpResponse:
    if not _is_admin_or_main(request.user):
        return HttpResponseForbidden("Недостаточно прав.")

    tab = request.GET.get("tab", "pending")
    if tab not in VALID_ADMIN_TABS:
        tab = "pending"

    qs = (
        ManualSearchClaim.objects
        .select_related("user", "search_link", "matched_search_link",
                        "matched_search_link__user", "matched_manual_claim__user", "reviewed_by")
        .order_by("-created_at")
    )
    if tab == "pending":
        qs = qs.filter(status=ManualSearchClaim.Status.PENDING)
    elif tab == "approved":
        qs = qs.filter(status=ManualSearchClaim.Status.APPROVED)
    elif tab == "rejected":
        qs = qs.filter(status=ManualSearchClaim.Status.REJECTED)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(user__username__icontains=q)
            | Q(raw_input__icontains=q)
            | Q(normalized_identifier__icontains=q)
        )

    page_obj = Paginator(qs, 30).get_page(request.GET.get("page", 1))
    counts = {
        "pending": ManualSearchClaim.objects.filter(
            status=ManualSearchClaim.Status.PENDING,
        ).count(),
    }
    return render(request, "core/admin_manual_claims.html", {
        "page_obj": page_obj,
        "tab": tab,
        "q": q,
        "counts": counts,
        "reward": MANUAL_CLAIM_REWARD,
    })


@login_required
@require_POST
def admin_manual_claim_approve(request: HttpRequest, claim_id: int) -> HttpResponse:
    if not _is_admin_or_main(request.user):
        return HttpResponseForbidden("Недостаточно прав.")

    with transaction.atomic():
        claim = (
            ManualSearchClaim.objects.select_for_update()
            .select_related("user", "search_link").filter(pk=claim_id).first()
        )
        if not claim:
            messages.error(request, "Заявка не найдена.")
            return redirect("admin_manual_claims_list")
        if claim.status != ManualSearchClaim.Status.PENDING:
            messages.info(request, f"Заявка #{claim_id} уже обработана.")
            return redirect("admin_manual_claims_list")

        # Повторная проверка — клиент мог быть привязан кем-то пока claim
        # лежал в pending.
        from django.db.models import Q as _Q
        sl_q = _Q()
        has_filter = False
        if claim.telegram_id:
            sl_q |= _Q(telegram_id=claim.telegram_id); has_filter = True
        if claim.telegram_username:
            sl_q |= _Q(telegram_username__iexact=claim.telegram_username); has_filter = True
        if claim.vk_user_id:
            sl_q |= _Q(vk_user_id=claim.vk_user_id); has_filter = True
        if claim.vk_screen_name:
            sl_q |= _Q(vk_screen_name__iexact=claim.vk_screen_name); has_filter = True

        conflicting_sl = None
        if has_filter:
            qs = SearchLink.objects.filter(sl_q).select_related("user")
            if claim.search_link_id:
                qs = qs.exclude(pk=claim.search_link_id)
            conflicting_sl = qs.order_by("created_at").first()
        if conflicting_sl:
            claim.status = ManualSearchClaim.Status.REJECTED
            claim.rejection_reason = (
                f"Пока заявка ждала проверки, клиент привязался к "
                f"@{conflicting_sl.user.username}."
            )
            claim.matched_search_link = conflicting_sl
            claim.reviewed_by = request.user
            claim.reviewed_at = timezone.now()
            claim.save(update_fields=[
                "status", "rejection_reason", "matched_search_link",
                "reviewed_by", "reviewed_at", "updated_at",
            ])
            messages.warning(
                request,
                f"За время ожидания клиент привязался к @{conflicting_sl.user.username}. "
                f"Заявка отклонена автоматически.",
            )
            return redirect("admin_manual_claims_list")

        claim.status = ManualSearchClaim.Status.APPROVED
        claim.paid_reward = MANUAL_CLAIM_REWARD
        claim.reviewed_by = request.user
        claim.reviewed_at = timezone.now()
        claim.save(update_fields=[
            "status", "paid_reward", "reviewed_by", "reviewed_at", "updated_at",
        ])

        # Записываем identifier'ы прямо на SearchLink — теперь дедуп SR
        # будет находить эту ссылку для будущих менеджеров.
        if claim.search_link_id:
            link = SearchLink.objects.select_for_update().get(pk=claim.search_link_id)
            link_fields = []
            if claim.telegram_id and not link.telegram_id:
                link.telegram_id = claim.telegram_id
                link_fields.append("telegram_id")
            if claim.telegram_username and not link.telegram_username:
                link.telegram_username = claim.telegram_username
                link_fields.append("telegram_username")
            if claim.vk_user_id and not link.vk_user_id:
                link.vk_user_id = claim.vk_user_id
                link_fields.append("vk_user_id")
            if claim.vk_screen_name and not link.vk_screen_name:
                link.vk_screen_name = claim.vk_screen_name
                link_fields.append("vk_screen_name")
            if not link.bot_started:
                link.bot_started = True
                link_fields.append("bot_started")
            if link_fields:
                link_fields.append("updated_at")
                link.save(update_fields=link_fields)

        manager = User.objects.select_for_update().get(pk=claim.user_id)
        _old = manager.balance or 0
        manager.balance = _old + MANUAL_CLAIM_REWARD
        manager.save(update_fields=["balance"])
        log_balance_change(
            manager, "balance", _old, manager.balance,
            f"manual_claim_approve#{claim.id} +{MANUAL_CLAIM_REWARD}",
            request.user,
        )

    messages.success(
        request,
        f"Заявка #{claim_id} одобрена. @{claim.user.username} +{MANUAL_CLAIM_REWARD} ₽.",
    )
    return redirect("admin_manual_claims_list")


@login_required
@require_POST
def admin_manual_claim_reject(request: HttpRequest, claim_id: int) -> HttpResponse:
    if not _is_admin_or_main(request.user):
        return HttpResponseForbidden("Недостаточно прав.")

    reason = (request.POST.get("rejection_reason") or "").strip()
    if not reason:
        messages.error(request, "Укажите причину отклонения.")
        return redirect("admin_manual_claims_list")

    with transaction.atomic():
        claim = (
            ManualSearchClaim.objects.select_for_update()
            .select_related("user", "search_link").filter(pk=claim_id).first()
        )
        if not claim:
            messages.error(request, "Заявка не найдена.")
            return redirect("admin_manual_claims_list")

        # Откат начисления + identifier-полей на SearchLink, если был approved
        if claim.status == ManualSearchClaim.Status.APPROVED:
            if claim.paid_reward:
                manager = User.objects.select_for_update().get(pk=claim.user_id)
                _old = manager.balance or 0
                manager.balance = _old - claim.paid_reward
                manager.save(update_fields=["balance"])
                log_balance_change(
                    manager, "balance", _old, manager.balance,
                    f"manual_claim_reject_rollback#{claim.id} -{claim.paid_reward}",
                    request.user,
                )
                claim.paid_reward = 0
            if claim.search_link_id:
                link = SearchLink.objects.select_for_update().get(pk=claim.search_link_id)
                rollback_fields = []
                if claim.telegram_id and link.telegram_id == claim.telegram_id:
                    link.telegram_id = None; rollback_fields.append("telegram_id")
                if claim.telegram_username and link.telegram_username and link.telegram_username.lower() == claim.telegram_username.lower():
                    link.telegram_username = ""; rollback_fields.append("telegram_username")
                if claim.vk_user_id and link.vk_user_id == claim.vk_user_id:
                    link.vk_user_id = None; rollback_fields.append("vk_user_id")
                if claim.vk_screen_name and link.vk_screen_name and link.vk_screen_name.lower() == claim.vk_screen_name.lower():
                    link.vk_screen_name = ""; rollback_fields.append("vk_screen_name")
                if link.bot_started:
                    link.bot_started = False; rollback_fields.append("bot_started")
                if rollback_fields:
                    rollback_fields.append("updated_at")
                    link.save(update_fields=rollback_fields)

        claim.status = ManualSearchClaim.Status.REJECTED
        claim.rejection_reason = reason
        claim.reviewed_by = request.user
        claim.reviewed_at = timezone.now()
        claim.save(update_fields=[
            "status", "rejection_reason", "paid_reward",
            "reviewed_by", "reviewed_at", "updated_at",
        ])

    messages.success(request, f"Заявка #{claim_id} отклонена.")
    return redirect("admin_manual_claims_list")
