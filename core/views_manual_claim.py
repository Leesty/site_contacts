"""Ручная привязка клиента к менеджеру (SearchLink-аналог).

Менеджер вводит telegram_id / @username / vk-ссылку клиента, который
пришёл не по его реф-ссылке. Система проверяет — занят ли клиент:
- если по этому идентификатору уже есть SearchLink или предыдущий
  ManualSearchClaim → отклоняем;
- иначе → одобряем, начисляем 150 ₽, фиксируем клиента за менеджером.
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


MANUAL_CLAIM_REWARD = 150  # ₽ за успешный ручной claim


def _require_approved_user(request: HttpRequest) -> bool:
    user = request.user
    return (
        user.is_authenticated
        and getattr(user, "role", None) == "user"
        and getattr(user, "status", None) == "approved"
    )


def _parse_manual_claim_input(raw: str) -> dict:
    """Парсит ввод менеджера в идентификаторы клиента.

    Поддерживает форматы:
      - 123456789           → telegram_id (чистое число)
      - @ivanov             → telegram_username
      - t.me/ivanov         → telegram_username
      - https://vk.com/id12 → vk_user_id
      - vk.com/ivanov       → vk_screen_name

    Возвращает dict с ключами {platform, telegram_id, telegram_username,
    vk_user_id, vk_screen_name, normalized_identifier}. Пустой dict если
    не удалось распарсить.
    """
    s = (raw or "").strip()
    if not s:
        return {}
    low = s.lower()

    # VK ссылка
    for marker in ("vk.com/", "vk.ru/"):
        if marker in low:
            idx = low.find(marker)
            rest = s[idx + len(marker):].split("?")[0].strip().rstrip("/").lower()
            if rest.startswith("id") and rest[2:].isdigit():
                vid = int(rest[2:])
                return {
                    "platform": "vk",
                    "vk_user_id": vid,
                    "normalized_identifier": f"vk:id{vid}",
                }
            if rest and re.match(r"^[a-z0-9_.]+$", rest):
                return {
                    "platform": "vk",
                    "vk_screen_name": rest,
                    "normalized_identifier": f"vk:{rest}",
                }
            return {}

    # t.me / telegram.me
    for marker in ("t.me/", "telegram.me/", "telegram.dog/"):
        if marker in low:
            idx = low.find(marker)
            rest = s[idx + len(marker):].split("?")[0].strip().rstrip("/").lstrip("@").lower()
            if rest.startswith("+"):  # invite link — не распарсить в id
                return {}
            if rest.isdigit():
                tid = int(rest)
                return {
                    "platform": "telegram",
                    "telegram_id": tid,
                    "normalized_identifier": f"telegram:{tid}",
                }
            if rest and re.match(r"^[a-z0-9_]+$", rest):
                return {
                    "platform": "telegram",
                    "telegram_username": rest,
                    "normalized_identifier": f"telegram:{rest}",
                }
            return {}

    # @username
    if s.startswith("@"):
        rest = s[1:].strip().lower()
        if rest.isdigit():
            tid = int(rest)
            return {
                "platform": "telegram",
                "telegram_id": tid,
                "normalized_identifier": f"telegram:{tid}",
            }
        if rest and re.match(r"^[a-z0-9_]+$", rest):
            return {
                "platform": "telegram",
                "telegram_username": rest,
                "normalized_identifier": f"telegram:{rest}",
            }
        return {}

    # Чистое число → telegram_id
    if s.isdigit():
        tid = int(s)
        return {
            "platform": "telegram",
            "telegram_id": tid,
            "normalized_identifier": f"telegram:{tid}",
        }

    # Чистый username (буквы/цифры/подчёркивания, без пробелов)
    if re.match(r"^[a-zA-Z0-9_]{3,}$", s):
        u = s.lower()
        return {
            "platform": "telegram",
            "telegram_username": u,
            "normalized_identifier": f"telegram:{u}",
        }

    return {}


def _find_existing_claim_owner(parsed: dict) -> tuple[SearchLink | None, ManualSearchClaim | None]:
    """Ищем кто уже привязан к этому клиенту: SearchLink или прежний
    ManualSearchClaim. Возвращаем первого найденного владельца."""
    if not parsed:
        return None, None
    sl_q = Q()
    mc_q = Q()
    if parsed.get("telegram_id"):
        sl_q |= Q(telegram_id=parsed["telegram_id"])
        mc_q |= Q(telegram_id=parsed["telegram_id"])
    if parsed.get("telegram_username"):
        sl_q |= Q(telegram_username__iexact=parsed["telegram_username"])
        mc_q |= Q(telegram_username__iexact=parsed["telegram_username"])
    if parsed.get("vk_user_id"):
        sl_q |= Q(vk_user_id=parsed["vk_user_id"])
        mc_q |= Q(vk_user_id=parsed["vk_user_id"])
    if parsed.get("vk_screen_name"):
        sl_q |= Q(vk_screen_name__iexact=parsed["vk_screen_name"])
        mc_q |= Q(vk_screen_name__iexact=parsed["vk_screen_name"])
    sl_q_has = any([parsed.get("telegram_id"), parsed.get("telegram_username"),
                    parsed.get("vk_user_id"), parsed.get("vk_screen_name")])
    if not sl_q_has:
        return None, None
    existing_sl = (
        SearchLink.objects.filter(sl_q)
        .select_related("user")
        .order_by("created_at")
        .first()
    )
    if existing_sl:
        return existing_sl, None
    # Блокируем и pending, и approved: пока заявка не отвергнута, никто
    # другой не может подать на того же клиента.
    existing_mc = (
        ManualSearchClaim.objects.filter(
            mc_q,
            status__in=[ManualSearchClaim.Status.PENDING, ManualSearchClaim.Status.APPROVED],
        )
        .select_related("user")
        .order_by("created_at")
        .first()
    )
    return None, existing_mc


@login_required
def manual_search_claim(request: HttpRequest) -> HttpResponse:
    """Форма + список своих claim'ов. POST — обработка."""
    if not _require_approved_user(request):
        return HttpResponseForbidden("Только для одобренных пользователей.")

    if request.method == "POST":
        raw = (request.POST.get("client_input") or "").strip()
        if not raw:
            messages.error(request, "Введите telegram_id / @username / VK-ссылку клиента.")
            return redirect("manual_search_claim")

        parsed = _parse_manual_claim_input(raw)
        if not parsed:
            messages.error(
                request,
                "Не удалось распознать формат. Используйте: 123456789, "
                "@username, https://t.me/username, https://vk.com/id123 или https://vk.com/screen_name.",
            )
            return redirect("manual_search_claim")

        existing_sl, existing_mc = _find_existing_claim_owner(parsed)

        from django.utils import timezone as _tz
        with transaction.atomic():
            if existing_sl or existing_mc:
                owner = existing_sl.user if existing_sl else existing_mc.user
                short_id = (
                    f"SearchLink #{existing_sl.display_id or existing_sl.id}"
                    if existing_sl else f"ManualClaim #{existing_mc.id}"
                )
                ManualSearchClaim.objects.create(
                    user=request.user,
                    raw_input=raw,
                    normalized_identifier=parsed["normalized_identifier"],
                    platform=parsed["platform"],
                    telegram_id=parsed.get("telegram_id"),
                    telegram_username=parsed.get("telegram_username") or "",
                    vk_user_id=parsed.get("vk_user_id"),
                    vk_screen_name=parsed.get("vk_screen_name") or "",
                    status=ManualSearchClaim.Status.REJECTED,
                    rejection_reason=(
                        f"Клиент уже привязан к @{owner.username} ({short_id})."
                    ),
                    paid_reward=0,
                    matched_search_link=existing_sl,
                    matched_manual_claim=existing_mc,
                    reviewed_at=_tz.now(),
                )
                messages.warning(
                    request,
                    f"Клиент уже привязан к @{owner.username} ({short_id}). "
                    f"Заявка отклонена — выплаты нет.",
                )
            else:
                ManualSearchClaim.objects.create(
                    user=request.user,
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
                    f"Заявка отправлена на проверку администратору. "
                    f"При одобрении начисляется {MANUAL_CLAIM_REWARD} ₽.",
                )

        return redirect("manual_search_claim")

    qs = (
        ManualSearchClaim.objects.filter(user=request.user)
        .select_related("matched_search_link", "matched_search_link__user", "matched_manual_claim__user")
        .order_by("-created_at")
    )
    page_obj = Paginator(qs, 30).get_page(request.GET.get("page", 1))
    approved_count = ManualSearchClaim.objects.filter(
        user=request.user, status=ManualSearchClaim.Status.APPROVED,
    ).count()

    return render(request, "core/manual_search_claim.html", {
        "page_obj": page_obj,
        "reward": MANUAL_CLAIM_REWARD,
        "approved_count": approved_count,
    })


# ════════════════════════════════════════════════════════════════════════════
# Админская модерация (role=admin / main_admin)
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
        .select_related("user", "matched_search_link", "matched_search_link__user",
                        "matched_manual_claim__user", "reviewed_by")
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
            .select_related("user").filter(pk=claim_id).first()
        )
        if not claim:
            messages.error(request, "Заявка не найдена.")
            return redirect("admin_manual_claims_list")
        if claim.status != ManualSearchClaim.Status.PENDING:
            messages.info(request, f"Заявка #{claim_id} уже обработана.")
            return redirect("admin_manual_claims_list")

        # Повторно проверяем — клиент мог быть привязан кем-то пока заявка
        # лежала в pending.
        from django.db.models import Q
        sl_q = Q()
        has_filter = False
        if claim.telegram_id:
            sl_q |= Q(telegram_id=claim.telegram_id); has_filter = True
        if claim.telegram_username:
            sl_q |= Q(telegram_username__iexact=claim.telegram_username); has_filter = True
        if claim.vk_user_id:
            sl_q |= Q(vk_user_id=claim.vk_user_id); has_filter = True
        if claim.vk_screen_name:
            sl_q |= Q(vk_screen_name__iexact=claim.vk_screen_name); has_filter = True

        conflicting_sl = (
            SearchLink.objects.filter(sl_q).select_related("user")
            .exclude(user_id=claim.user_id).order_by("created_at").first()
            if has_filter else None
        )
        if conflicting_sl:
            messages.warning(
                request,
                f"За время ожидания клиент привязался к @{conflicting_sl.user.username} "
                f"(SearchLink #{conflicting_sl.display_id or conflicting_sl.id}). "
                f"Заявку отклонил автоматически.",
            )
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
            return redirect("admin_manual_claims_list")

        claim.status = ManualSearchClaim.Status.APPROVED
        claim.paid_reward = MANUAL_CLAIM_REWARD
        claim.reviewed_by = request.user
        claim.reviewed_at = timezone.now()
        claim.save(update_fields=[
            "status", "paid_reward", "reviewed_by", "reviewed_at", "updated_at",
        ])

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
            .select_related("user").filter(pk=claim_id).first()
        )
        if not claim:
            messages.error(request, "Заявка не найдена.")
            return redirect("admin_manual_claims_list")

        # Если был approved — откат начисления
        if claim.status == ManualSearchClaim.Status.APPROVED and claim.paid_reward:
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
