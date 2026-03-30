"""Вьюхи для партнёрского кабинета."""
import logging
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from django.core.paginator import Paginator

from .models import PartnerEarning, PartnerLink, User, WithdrawalRequest

LEAD_APPROVE_REWARD = getattr(settings, "LEAD_APPROVE_REWARD", 40)

logger = logging.getLogger(__name__)

PARTNER_EARN_PER_LEAD_DEFAULT = 10  # руб. за каждый одобренный лид (по умолчанию)


def _require_partner(request: HttpRequest) -> bool:
    return getattr(request.user, "role", None) == User.Role.PARTNER


# ─── Кабинет партнёра ──────────────────────────────────────────────────────────

@login_required
def partner_dashboard(request: HttpRequest) -> HttpResponse:
    """Главная страница партнёрского кабинета."""
    if not _require_partner(request):
        return HttpResponseForbidden("Только для партнёров.")

    user = request.user
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    balance = user.balance or 0

    total_earned = PartnerEarning.objects.filter(partner=user).aggregate(s=Sum("amount")).get("s") or 0
    users_count = User.objects.filter(partner_owner=user).count()
    earnings = (
        PartnerEarning.objects.filter(partner=user)
        .select_related("lead", "lead__user")
        .order_by("-created_at")[:100]
    )
    # Автоматически создаём единственную реф-ссылку, если её ещё нет
    link = PartnerLink.objects.filter(partner=user).first()
    if not link:
        from uuid import uuid4
        link = PartnerLink.objects.create(partner=user, code=uuid4().hex[:24])

    withdrawals = WithdrawalRequest.objects.filter(user=user).order_by("-created_at")
    withdrawal_pending = withdrawals.filter(status="pending").exists()
    can_request_withdrawal = balance >= withdrawal_min and not withdrawal_pending

    return render(request, "partner/dashboard.html", {
        "user": user,
        "balance": balance,
        "total_earned": total_earned,
        "users_count": users_count,
        "earnings": earnings,
        "link": link,
        "withdrawals": withdrawals,
        "withdrawal_pending": withdrawal_pending,
        "can_request_withdrawal": can_request_withdrawal,
        "withdrawal_min_balance": withdrawal_min,
        "partner_rate": user.partner_rate or PARTNER_EARN_PER_LEAD_DEFAULT,
    })


# ─── Реферальные ссылки ────────────────────────────────────────────────────────

@login_required
def partner_create_link(request: HttpRequest) -> HttpResponse:
    """Создать новую реферальную ссылку."""
    if not _require_partner(request):
        return HttpResponseForbidden()
    if request.method != "POST":
        return redirect("partner_dashboard")

    note = (request.POST.get("note") or "").strip()[:100]
    code = uuid4().hex[:24]
    PartnerLink.objects.create(partner=request.user, code=code, note=note)
    messages.success(request, "Реферальная ссылка создана.")
    return redirect("partner_dashboard")


@login_required
def partner_toggle_link(request: HttpRequest, link_id: int) -> HttpResponse:
    """Включить / выключить реферальную ссылку."""
    if not _require_partner(request):
        return HttpResponseForbidden()
    if request.method != "POST":
        return redirect("partner_dashboard")

    link = get_object_or_404(PartnerLink, pk=link_id, partner=request.user)
    link.is_active = not link.is_active
    link.save(update_fields=["is_active", "updated_at"])
    return redirect("partner_dashboard")


# ─── Вывод средств ─────────────────────────────────────────────────────────────

@login_required
def partner_withdrawal(request: HttpRequest) -> HttpResponse:
    """Запросить вывод средств (партнёрский баланс)."""
    if not _require_partner(request):
        return HttpResponseForbidden()

    user = request.user
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    balance = user.balance or 0

    if balance < withdrawal_min:
        messages.warning(request, f"Минимальная сумма вывода: {withdrawal_min} руб. Текущий баланс: {balance} руб.")
        return redirect("partner_dashboard")
    if WithdrawalRequest.objects.filter(user=user, status="pending").exists():
        messages.info(request, "У вас уже есть заявка на вывод на рассмотрении.")
        return redirect("partner_dashboard")

    if request.method == "POST":
        payout_details = (request.POST.get("payout_details") or "").strip()
        if not payout_details:
            messages.error(request, "Укажите реквизиты для вывода.")
            return render(request, "partner/withdrawal.html", {
                "balance": balance,
                "withdrawal_min_balance": withdrawal_min,
            })

        with transaction.atomic():
            user_refresh = User.objects.select_for_update().get(pk=user.pk)
            current_balance = user_refresh.balance or 0

            if current_balance < withdrawal_min:
                messages.warning(request, f"Недостаточно средств: {current_balance} руб.")
                return redirect("partner_dashboard")
            if WithdrawalRequest.objects.filter(user=user_refresh, status="pending").exists():
                messages.info(request, "У вас уже есть заявка на рассмотрении.")
                return redirect("partner_dashboard")

            WithdrawalRequest.objects.create(
                user=user_refresh,
                amount=current_balance,
                payout_details=payout_details,
                status="pending",
            )
            user_refresh.balance = 0
            user_refresh.save(update_fields=["balance"])

        messages.success(request, f"Заявка на вывод {current_balance} руб. отправлена. Баланс обнулён.")
        return redirect("partner_dashboard")

    return render(request, "partner/withdrawal.html", {
        "balance": balance,
        "withdrawal_min_balance": withdrawal_min,
    })


# ─── Регистрация через партнёрскую ссылку ─────────────────────────────────────

def partner_ref_register(request: HttpRequest, code: str) -> HttpResponse:
    """Регистрация пользователя через партнёрскую реферальную ссылку."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    try:
        ref_link = PartnerLink.objects.select_related("partner").get(code=code, is_active=True)
    except PartnerLink.DoesNotExist:
        return render(request, "auth/partner_ref_register.html", {
            "error": "Реферальная ссылка недействительна или устарела.",
            "code": code,
        })

    from .forms import UserRegistrationForm
    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        try:
            if form.is_valid():
                user = form.save(commit=False)
                user.status = User.Status.APPROVED
                user.partner_owner = ref_link.partner
                user.save()
                messages.success(request, "Регистрация прошла успешно. Войдите в личный кабинет.")
                return redirect("login")
        except Exception as e:
            logger.exception("Ошибка при регистрации через партнёрскую ссылку: %s", e)
            messages.error(request, "Не удалось завершить регистрацию. Возможно, логин уже занят.")
    else:
        form = UserRegistrationForm()

    return render(request, "auth/partner_ref_register.html", {
        "form": form,
        "code": code,
        "partner": ref_link.partner,
    })


# ─── Рефералы партнёра ─────────────────────────────────────────────────────────

@login_required
def partner_referrals(request: HttpRequest) -> HttpResponse:
    """Список пользователей, зарегистрированных по реферальной ссылке партнёра."""
    if not _require_partner(request):
        return HttpResponseForbidden("Только для партнёров.")

    users_qs = (
        User.objects.filter(partner_owner=request.user)
        .order_by("-date_joined")
    )
    paginator = Paginator(users_qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))

    return render(request, "partner/referrals.html", {
        "page_obj": page_obj,
        "total": users_qs.count(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Affiliate (role=affiliate) — партнёрка с настраиваемой ставкой
# ═══════════════════════════════════════════════════════════════════════════════

def _require_affiliate(request: HttpRequest) -> bool:
    return getattr(request.user, "role", None) == User.Role.AFFILIATE


@login_required
def affiliate_dashboard(request: HttpRequest) -> HttpResponse:
    """Дашборд affiliate-партнёра: ссылки, ставки, баланс, начисления."""
    if not _require_affiliate(request):
        return HttpResponseForbidden("Только для affiliate-партнёров.")

    user = request.user
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    balance = user.balance or 0

    total_earned = PartnerEarning.objects.filter(partner=user).aggregate(s=Sum("amount")).get("s") or 0
    users_count = User.objects.filter(partner_owner=user).count()
    earnings = (
        PartnerEarning.objects.filter(partner=user)
        .select_related("lead", "lead__user")
        .order_by("-created_at")[:100]
    )

    links_qs = (
        PartnerLink.objects.filter(partner=user)
        .annotate(ref_count=Count("registered_users"))
        .order_by("-created_at")
    )
    links = list(links_qs)
    for link in links:
        link.partner_cut = LEAD_APPROVE_REWARD - link.ref_reward

    withdrawals = WithdrawalRequest.objects.filter(user=user).order_by("-created_at")
    withdrawal_pending = withdrawals.filter(status="pending").exists()
    can_request_withdrawal = balance >= withdrawal_min and not withdrawal_pending

    return render(request, "affiliate/dashboard.html", {
        "user": user,
        "balance": balance,
        "total_earned": total_earned,
        "users_count": users_count,
        "earnings": earnings,
        "links": links,
        "withdrawals": withdrawals,
        "withdrawal_pending": withdrawal_pending,
        "can_request_withdrawal": can_request_withdrawal,
        "withdrawal_min_balance": withdrawal_min,
        "total_reward": LEAD_APPROVE_REWARD,
    })


@login_required
def affiliate_create_link(request: HttpRequest) -> HttpResponse:
    """Создать новую реферальную ссылку с настраиваемой ставкой."""
    if not _require_affiliate(request):
        return HttpResponseForbidden()
    if request.method != "POST":
        return redirect("affiliate_dashboard")

    note = (request.POST.get("note") or "").strip()[:100]
    try:
        ref_reward = int(request.POST.get("ref_reward", 20))
    except (TypeError, ValueError):
        ref_reward = 20
    ref_reward = max(1, min(39, ref_reward))

    code = uuid4().hex[:24]
    PartnerLink.objects.create(partner=request.user, code=code, note=note, ref_reward=ref_reward)
    messages.success(request, f"Ссылка создана. Реф получает {ref_reward} руб., вы — {LEAD_APPROVE_REWARD - ref_reward} руб.")
    return redirect("affiliate_dashboard")


@login_required
def affiliate_toggle_link(request: HttpRequest, link_id: int) -> HttpResponse:
    """Включить / выключить реферальную ссылку."""
    if not _require_affiliate(request):
        return HttpResponseForbidden()
    if request.method != "POST":
        return redirect("affiliate_dashboard")

    link = get_object_or_404(PartnerLink, pk=link_id, partner=request.user)
    link.is_active = not link.is_active
    link.save(update_fields=["is_active", "updated_at"])
    return redirect("affiliate_dashboard")


@login_required
def affiliate_withdrawal(request: HttpRequest) -> HttpResponse:
    """Запросить вывод средств (affiliate баланс)."""
    if not _require_affiliate(request):
        return HttpResponseForbidden()

    user = request.user
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    balance = user.balance or 0

    if balance < withdrawal_min:
        messages.warning(request, f"Минимальная сумма вывода: {withdrawal_min} руб. Текущий баланс: {balance} руб.")
        return redirect("affiliate_dashboard")
    if WithdrawalRequest.objects.filter(user=user, status="pending").exists():
        messages.info(request, "У вас уже есть заявка на вывод на рассмотрении.")
        return redirect("affiliate_dashboard")

    if request.method == "POST":
        payout_details = (request.POST.get("payout_details") or "").strip()
        if not payout_details:
            messages.error(request, "Укажите реквизиты для вывода.")
            return render(request, "affiliate/withdrawal.html", {
                "balance": balance,
                "withdrawal_min_balance": withdrawal_min,
            })

        with transaction.atomic():
            user_refresh = User.objects.select_for_update().get(pk=user.pk)
            current_balance = user_refresh.balance or 0

            if current_balance < withdrawal_min:
                messages.warning(request, f"Недостаточно средств: {current_balance} руб.")
                return redirect("affiliate_dashboard")
            if WithdrawalRequest.objects.filter(user=user_refresh, status="pending").exists():
                messages.info(request, "У вас уже есть заявка на рассмотрении.")
                return redirect("affiliate_dashboard")

            WithdrawalRequest.objects.create(
                user=user_refresh,
                amount=current_balance,
                payout_details=payout_details,
                status="pending",
            )
            user_refresh.balance = 0
            user_refresh.save(update_fields=["balance"])

        messages.success(request, f"Заявка на вывод {current_balance} руб. отправлена. Баланс обнулён.")
        return redirect("affiliate_dashboard")

    return render(request, "affiliate/withdrawal.html", {
        "balance": balance,
        "withdrawal_min_balance": withdrawal_min,
    })


def affiliate_ref_register(request: HttpRequest, code: str) -> HttpResponse:
    """Регистрация пользователя через affiliate реферальную ссылку."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    try:
        ref_link = PartnerLink.objects.select_related("partner").get(code=code, is_active=True)
    except PartnerLink.DoesNotExist:
        return render(request, "auth/affiliate_ref_register.html", {
            "error": "Реферальная ссылка недействительна или устарела.",
            "code": code,
        })

    from .forms import UserRegistrationForm
    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        try:
            if form.is_valid():
                user = form.save(commit=False)
                user.status = User.Status.APPROVED
                user.partner_owner = ref_link.partner
                user.partner_link = ref_link
                user.save()
                messages.success(request, "Регистрация прошла успешно. Войдите в личный кабинет.")
                return redirect("login")
        except Exception as e:
            logger.exception("Ошибка при регистрации через affiliate ссылку: %s", e)
            messages.error(request, "Не удалось завершить регистрацию. Возможно, логин уже занят.")
    else:
        form = UserRegistrationForm()

    return render(request, "auth/affiliate_ref_register.html", {
        "form": form,
        "code": code,
        "partner": ref_link.partner,
    })


@login_required
def affiliate_referrals(request: HttpRequest) -> HttpResponse:
    """Список рефералов affiliate-партнёра."""
    if not _require_affiliate(request):
        return HttpResponseForbidden("Только для affiliate-партнёров.")

    users_qs = (
        User.objects.filter(partner_owner=request.user)
        .select_related("partner_link")
        .order_by("-date_joined")
    )
    paginator = Paginator(users_qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    for u in page_obj:
        if u.partner_link:
            u.partner_cut = LEAD_APPROVE_REWARD - u.partner_link.ref_reward
        else:
            u.partner_cut = 0

    return render(request, "affiliate/referrals.html", {
        "page_obj": page_obj,
        "total": users_qs.count(),
    })
