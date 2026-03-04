"""Вьюхи для партнёрского кабинета."""
import logging
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Sum
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render

from django.core.paginator import Paginator

from .models import PartnerEarning, PartnerLink, User, WithdrawalRequest

logger = logging.getLogger(__name__)

PARTNER_EARN_PER_LEAD = 10  # руб. за каждый одобренный лид


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
