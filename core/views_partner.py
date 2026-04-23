"""Вьюхи для партнёрского кабинета."""
import logging
from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Sum
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from django.core.paginator import Paginator

from .models import PartnerEarning, PartnerLink, User, WithdrawalRequest

LEAD_APPROVE_REWARD = getattr(settings, "LEAD_APPROVE_REWARD", 40)

logger = logging.getLogger(__name__)

PARTNER_EARN_PER_LEAD_DEFAULT = 10  # руб. за каждый одобренный лид (по умолчанию)


def _require_partner(request: HttpRequest) -> bool:
    """Только роль «partner» со статусом approved. Забаненный партнёр теряет доступ."""
    user = request.user
    if getattr(user, "role", None) != User.Role.PARTNER:
        return False
    return getattr(user, "status", None) == "approved"


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
    pending_wr = withdrawals.filter(status="pending").first()
    withdrawal_pending = pending_wr is not None
    withdrawal_pending_amount = pending_wr.amount if pending_wr else 0
    partner_smz_ok = getattr(user, "smz_status", "none") == "approved"
    can_request_withdrawal = balance >= withdrawal_min and not withdrawal_pending and partner_smz_ok

    from .models import Lead
    dozhim_pending_count = Lead.objects.filter(
        user__partner_owner=user, lead_type__slug="dozhim", status=Lead.Status.PENDING
    ).count()

    receiptless_withdrawals = list(
        WithdrawalRequest.objects.filter(user=user, status="approved")
        .exclude(receipt_status__in=["approved", "waived"])
        .order_by("-created_at")[:5]
    )

    return render(request, "partner/dashboard.html", {
        "user": user,
        "balance": balance,
        "total_earned": total_earned,
        "users_count": users_count,
        "earnings": earnings,
        "link": link,
        "withdrawals": withdrawals,
        "withdrawal_pending": withdrawal_pending,
        "withdrawal_pending_amount": withdrawal_pending_amount,
        "can_request_withdrawal": can_request_withdrawal,
        "withdrawal_min_balance": withdrawal_min,
        "dozhim_pending_count": dozhim_pending_count,
        "partner_rate": user.partner_rate or PARTNER_EARN_PER_LEAD_DEFAULT,
        "receiptless_withdrawals": receiptless_withdrawals,
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
            "hide_nav_auth": True,
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
        "hide_nav_auth": True,
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

    # У partner-роли ставка за обычный лид фиксирована в partner.partner_rate.
    # Реф получает LEAD_APPROVE_REWARD - partner_rate, партнёр — partner_rate.
    partner_rate = request.user.partner_rate or 10
    search_reward_total = getattr(settings, "SEARCH_REPORT_REWARD", 100)
    for u in page_obj:
        u.current_ref_reward = max(0, LEAD_APPROVE_REWARD - partner_rate)
        u.partner_cut = partner_rate
        u.sl_ref_reward = search_reward_total - u.ref_searchlink_manager_cut if u.ref_searchlink_enabled else 0

    return render(request, "partner/referrals.html", {
        "page_obj": page_obj,
        "total": users_qs.count(),
        "total_reward": LEAD_APPROVE_REWARD,
        "search_reward": search_reward_total,
        "partner_rate": partner_rate,
        "ref_share": max(0, LEAD_APPROVE_REWARD - partner_rate),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Проверка дожим-лидов партнёром
# ═══════════════════════════════════════════════════════════════════════════════

from django.views.decorators.http import require_http_methods


@login_required
def partner_dozhim_leads(request: HttpRequest) -> HttpResponse:
    """Список дожим-лидов рефералов партнёра для модерации."""
    if not _require_partner(request):
        return HttpResponseForbidden("Только для партнёров.")

    from .models import Lead, LeadType
    from django.core.paginator import Paginator as Pag

    tab = request.GET.get("tab", "new")
    leads_qs = Lead.objects.filter(
        user__partner_owner=request.user,
        lead_type__slug="dozhim",
    ).select_related("user", "lead_type").order_by("-created_at")

    if tab == "approved":
        leads_qs = leads_qs.filter(status=Lead.Status.APPROVED)
    elif tab == "rejected":
        leads_qs = leads_qs.filter(status=Lead.Status.REJECTED)
    elif tab == "rework":
        leads_qs = leads_qs.filter(status=Lead.Status.REWORK)
    else:
        leads_qs = leads_qs.filter(status__in=(Lead.Status.PENDING, Lead.Status.REWORK))

    pending_count = Lead.objects.filter(
        user__partner_owner=request.user,
        lead_type__slug="dozhim",
        status=Lead.Status.PENDING,
    ).count()

    paginator = Pag(leads_qs, 30)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    lead_approve_reward = getattr(settings, "DOZHIM_APPROVE_REWARD", 40)

    return render(request, "partner/dozhim_leads.html", {
        "page_obj": page_obj,
        "tab": tab,
        "pending_count": pending_count,
        "lead_approve_reward": lead_approve_reward,
    })


@login_required
@require_http_methods(["POST"])
def partner_dozhim_lead_approve(request: HttpRequest, lead_id: int) -> HttpResponse:
    """Партнёр одобряет дожим-лид своего реферала."""
    if not _require_partner(request):
        return HttpResponseForbidden()

    from .models import Lead, log_balance_change
    from django.utils import timezone

    with transaction.atomic():
        lead = Lead.objects.select_for_update().select_related("user").filter(
            pk=lead_id, user__partner_owner=request.user, lead_type__slug="dozhim"
        ).first()
        if not lead:
            messages.error(request, "Лид не найден или не принадлежит вашим рефералам.")
            return redirect("partner_dozhim_leads")
        if lead.status == Lead.Status.APPROVED:
            messages.info(request, "Лид уже одобрен.")
            return redirect("partner_dozhim_leads")

        reward = getattr(settings, "DOZHIM_APPROVE_REWARD", 40)
        lead.status = Lead.Status.APPROVED
        lead.reviewed_at = timezone.now()
        lead.reviewed_by = request.user
        lead.save(update_fields=["status", "reviewed_at", "reviewed_by"])

        lead_owner = User.objects.select_for_update().get(pk=lead.user_id)
        _old = lead_owner.dozhim_balance or 0
        lead_owner.dozhim_balance = _old + reward
        lead_owner.save(update_fields=["dozhim_balance"])
        log_balance_change(lead_owner, "dozhim_balance", _old, lead_owner.dozhim_balance, f"partner_dozhim_approve#{lead_id} +{reward}", request.user)
        # Авто-аккредитация: баланс дожима был в минусе и перешёл в плюс
        if not lead_owner.is_accredited and _old < 0 and lead_owner.dozhim_balance >= 0:
            lead_owner.is_accredited = True
            lead_owner.save(update_fields=["is_accredited"])

    messages.success(request, f"Лид #{lead_id} одобрен. +{reward} руб. пользователю @{lead.user.username}.")
    return redirect("partner_dozhim_leads")


@login_required
@require_http_methods(["POST"])
def partner_dozhim_lead_reject(request: HttpRequest, lead_id: int) -> HttpResponse:
    """Партнёр отклоняет дожим-лид."""
    if not _require_partner(request):
        return HttpResponseForbidden()

    from .models import Lead, log_balance_change
    from django.utils import timezone

    reason = (request.POST.get("reason") or "").strip()
    with transaction.atomic():
        lead = Lead.objects.select_for_update().select_related("user").filter(
            pk=lead_id, user__partner_owner=request.user, lead_type__slug="dozhim"
        ).first()
        if not lead:
            return redirect("partner_dozhim_leads")

        was_approved = lead.status == Lead.Status.APPROVED
        lead.status = Lead.Status.REJECTED
        lead.rejection_reason = reason
        lead.reviewed_at = timezone.now()
        lead.reviewed_by = request.user
        lead.save(update_fields=["status", "rejection_reason", "reviewed_at", "reviewed_by"])

        if was_approved:
            reward = getattr(settings, "DOZHIM_APPROVE_REWARD", 40)
            lead_owner = User.objects.select_for_update().get(pk=lead.user_id)
            _old = lead_owner.dozhim_balance or 0
            lead_owner.dozhim_balance = _old - reward
            lead_owner.save(update_fields=["dozhim_balance"])
            log_balance_change(lead_owner, "dozhim_balance", _old, lead_owner.dozhim_balance, f"partner_dozhim_reject#{lead_id} -{reward}", request.user)

    messages.success(request, f"Лид #{lead_id} отклонён.")
    return redirect("partner_dozhim_leads")


@login_required
@require_http_methods(["POST"])
def partner_dozhim_lead_rework(request: HttpRequest, lead_id: int) -> HttpResponse:
    """Партнёр отправляет дожим-лид на доработку."""
    if not _require_partner(request):
        return HttpResponseForbidden()

    from .models import Lead, log_balance_change
    from django.utils import timezone

    comment = (request.POST.get("comment") or "").strip()
    with transaction.atomic():
        lead = Lead.objects.select_for_update().select_related("user").filter(
            pk=lead_id, user__partner_owner=request.user, lead_type__slug="dozhim"
        ).first()
        if not lead:
            return redirect("partner_dozhim_leads")

        was_approved = lead.status == Lead.Status.APPROVED
        lead.status = Lead.Status.REWORK
        lead.rework_comment = comment
        lead.reviewed_at = timezone.now()
        lead.reviewed_by = request.user
        lead.save(update_fields=["status", "rework_comment", "reviewed_at", "reviewed_by"])

        if was_approved:
            reward = getattr(settings, "DOZHIM_APPROVE_REWARD", 40)
            lead_owner = User.objects.select_for_update().get(pk=lead.user_id)
            _old = lead_owner.dozhim_balance or 0
            lead_owner.dozhim_balance = _old - reward
            lead_owner.save(update_fields=["dozhim_balance"])
            log_balance_change(lead_owner, "dozhim_balance", _old, lead_owner.dozhim_balance, f"partner_dozhim_rework#{lead_id} -{reward}", request.user)

    messages.success(request, f"Лид #{lead_id} отправлен на доработку.")
    return redirect("partner_dozhim_leads")


@login_required
def partner_dozhim_lead_attachment(request: HttpRequest, lead_id: int) -> HttpResponse:
    """Просмотр вложения дожим-лида."""
    if not _require_partner(request):
        return HttpResponseForbidden()
    from .models import Lead
    lead = get_object_or_404(Lead, pk=lead_id, user__partner_owner=request.user, lead_type__slug="dozhim")
    if not lead.attachment:
        return HttpResponse("Нет вложения.", status=404)
    return redirect(lead.attachment.url)


# ═══════════════════════════════════════════════════════════════════════════════
# Реферальная система для менеджеров (role=user) + регистрация /a/<code>/
# ═══════════════════════════════════════════════════════════════════════════════


def _require_user_approved(request: HttpRequest) -> bool:
    return getattr(request.user, "role", None) == "user" and getattr(request.user, "status", None) == "approved"


@login_required
def user_referrals(request: HttpRequest) -> HttpResponse:
    """Дашборд реферальной системы менеджера: ссылки, ставки, заработок."""
    if not _require_user_approved(request):
        return HttpResponseForbidden("Только для одобренных пользователей.")

    user = request.user
    total_earned = PartnerEarning.objects.filter(partner=user).aggregate(s=Sum("amount")).get("s") or 0
    users_count = User.objects.filter(partner_owner=user).count()
    earnings = (
        PartnerEarning.objects.filter(partner=user)
        .select_related("lead", "lead__user", "search_report", "search_report__user")
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

    return render(request, "core/user_referrals.html", {
        "user": user,
        "total_earned": total_earned,
        "users_count": users_count,
        "earnings": earnings,
        "links": links,
        "total_reward": LEAD_APPROVE_REWARD,
    })


@login_required
def user_referral_create_link(request: HttpRequest) -> HttpResponse:
    """Создать реферальную ссылку с настраиваемой ставкой."""
    if not _require_user_approved(request):
        return HttpResponseForbidden()
    if request.method != "POST":
        return redirect("user_referrals")

    note = (request.POST.get("note") or "").strip()[:100]
    try:
        ref_reward = int(request.POST.get("ref_reward", 20))
    except (TypeError, ValueError):
        ref_reward = 20
    ref_reward = max(1, min(39, ref_reward))

    code = uuid4().hex[:24]
    PartnerLink.objects.create(partner=request.user, code=code, note=note, ref_reward=ref_reward)
    messages.success(request, f"Ссылка создана. Реф получает {ref_reward} руб., вы — {LEAD_APPROVE_REWARD - ref_reward} руб.")
    return redirect("user_referrals")


@login_required
def user_referral_toggle_link(request: HttpRequest, link_id: int) -> HttpResponse:
    """Включить / выключить реферальную ссылку."""
    if not _require_user_approved(request):
        return HttpResponseForbidden()
    if request.method != "POST":
        return redirect("user_referrals")

    link = get_object_or_404(PartnerLink, pk=link_id, partner=request.user)
    link.is_active = not link.is_active
    link.save(update_fields=["is_active", "updated_at"])
    return redirect("user_referrals")


@login_required
def user_referral_list(request: HttpRequest) -> HttpResponse:
    """Список рефералов менеджера."""
    if not _require_user_approved(request):
        return HttpResponseForbidden("Только для одобренных пользователей.")

    users_qs = (
        User.objects.filter(partner_owner=request.user)
        .select_related("partner_link")
        .order_by("-date_joined")
    )
    paginator = Paginator(users_qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    _search_total = getattr(settings, "SEARCH_REPORT_REWARD", 100)
    for u in page_obj:
        # Текущая ставка рефу за обычный лид (override → link → 20)
        if u.ref_lead_reward is not None:
            u.current_ref_reward = u.ref_lead_reward
        elif u.partner_link:
            u.current_ref_reward = u.partner_link.ref_reward
        else:
            u.current_ref_reward = 20
        u.partner_cut = LEAD_APPROVE_REWARD - u.current_ref_reward
        u.sl_ref_reward = _search_total - u.ref_searchlink_manager_cut if u.ref_searchlink_enabled else 0

    return render(request, "core/user_referral_list.html", {
        "page_obj": page_obj,
        "total": users_qs.count(),
        "total_reward": LEAD_APPROVE_REWARD,
        "search_reward": getattr(settings, "SEARCH_REPORT_REWARD", 100),
    })


@login_required
@require_http_methods(["POST"])
def user_referral_searchlink_toggle(request: HttpRequest, user_id: int) -> HttpResponse:
    """AJAX: настройки реферала — ставка за лид + SearchLink (вкл/выкл, доля менеджера)."""
    # Доступ: approved-user (нативная ref-система) ИЛИ partner (role=partner).
    is_partner = getattr(request.user, "role", None) == User.Role.PARTNER
    if not _require_user_approved(request) and not is_partner:
        return HttpResponseForbidden()
    referral = get_object_or_404(User, pk=user_id, partner_owner=request.user)
    search_reward_total = getattr(settings, "SEARCH_REPORT_REWARD", 100)
    update_fields = []

    # Ставка рефералу за обычный лид — только для role=user (у партнёра ставка
    # фиксирована в partner.partner_rate, per-referral override не используется).
    ref_lead_reward_raw = request.POST.get("ref_lead_reward")
    if ref_lead_reward_raw is not None and not is_partner:
        try:
            val = int(ref_lead_reward_raw)
        except (TypeError, ValueError):
            val = 20
        val = max(1, min(LEAD_APPROVE_REWARD - 1, val))
        referral.ref_lead_reward = val
        update_fields.append("ref_lead_reward")

    # SearchLink: вкл/выкл
    enabled = request.POST.get("enabled")
    if enabled is not None:
        referral.ref_searchlink_enabled = enabled == "1"
        update_fields.append("ref_searchlink_enabled")

    # SearchLink: доля менеджера
    manager_cut = request.POST.get("manager_cut")
    if manager_cut is not None:
        try:
            cut = int(manager_cut)
        except (TypeError, ValueError):
            cut = 30
        cut = max(1, min(search_reward_total - 1, cut))
        referral.ref_searchlink_manager_cut = cut
        update_fields.append("ref_searchlink_manager_cut")

    if update_fields:
        referral.save(update_fields=update_fields)

    lead_ref_reward = referral.ref_lead_reward if referral.ref_lead_reward is not None else (referral.partner_link.ref_reward if referral.partner_link else 20)
    return JsonResponse({
        "success": True,
        "enabled": referral.ref_searchlink_enabled,
        "manager_cut": referral.ref_searchlink_manager_cut,
        "sl_ref_reward": search_reward_total - referral.ref_searchlink_manager_cut,
        "ref_lead_reward": lead_ref_reward,
        "partner_cut": LEAD_APPROVE_REWARD - lead_ref_reward,
    })


def referral_ref_register(request: HttpRequest, code: str) -> HttpResponse:
    """Регистрация пользователя через реферальную ссылку /a/<code>/."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    try:
        ref_link = PartnerLink.objects.select_related("partner").get(code=code, is_active=True)
    except PartnerLink.DoesNotExist:
        return render(request, "auth/affiliate_ref_register.html", {
            "error": "Реферальная ссылка недействительна или устарела.",
            "code": code,
            "hide_nav_auth": True,
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
            logger.exception("Ошибка при регистрации через реферальную ссылку: %s", e)
            messages.error(request, "Не удалось завершить регистрацию. Возможно, логин уже занят.")
    else:
        form = UserRegistrationForm()

    return render(request, "auth/affiliate_ref_register.html", {
        "form": form,
        "code": code,
        "partner": ref_link.partner,
        "hide_nav_auth": True,
    })
