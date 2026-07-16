"""Вьюхи для партнёрского кабинета."""
import logging
from functools import wraps
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


def dozhim_required(view_func):
    """Гард: партнёрские дожим-вьюхи доступны только при DOZHIM_ENABLED=true.

    Отдел дожима скрыт на проде (2026-07); код сохранён. Вернуть = env
    DOZHIM_ENABLED=true. При скрытом отделе — редирект на партнёрский дашборд.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not getattr(settings, "DOZHIM_ENABLED", False):
            return redirect("partner_dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


def legacy_rewards_required(view_func):
    """Гард: старые реф-кабинеты/настройки ставок доступны только при
    LEGACY_REWARDS_ENABLED=true. По умолчанию выключено (2026-07) — начисляем
    только через новую воронку. Данные/связи partner_owner сохранены; вернуть =
    env LEGACY_REWARDS_ENABLED=true. Иначе — редирект на дашборд.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not getattr(settings, "LEGACY_REWARDS_ENABLED", False):
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


def referral_system_required(view_func):
    """Гард: кабинет рефералов + редактирование реф-ставок новой воронки.
    Включён по умолчанию (REFERRAL_SYSTEM_ENABLED=true) — это текущая реф-система
    (per-рефовод доли с созвона/сделки реферала), НЕ завязана на старые отчёты.
    Выключить = env REFERRAL_SYSTEM_ENABLED=false → редирект на дашборд.
    """
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not getattr(settings, "REFERRAL_SYSTEM_ENABLED", True):
            return redirect("dashboard")
        return view_func(request, *args, **kwargs)
    return _wrapped


def _fixed_ref_rate_context() -> dict:
    """Фиксированные реф-ставки для шаблонов (редактирование отключено 2026-07-13).

    Одинаковы для всех рефоводов и берутся из settings — те же константы, что
    использует воронка при начислении. Менять = env SEARCH_SOZVON_REFERRER /
    SEARCH_DEAL_REFERRER.
    """
    sozvon_total = getattr(settings, "SEARCH_SOZVON_REWARD", 150)
    deal_total = getattr(settings, "SEARCH_DEAL_REWARD", 4000)
    sozvon_ref = getattr(settings, "SEARCH_SOZVON_REFERRER", 50)
    deal_ref = getattr(settings, "SEARCH_DEAL_REFERRER", 1000)
    return {
        "sozvon_total_reward": sozvon_total,
        "deal_total_reward": deal_total,
        "ref_sozvon_cut": sozvon_ref,
        "ref_sozvon_ref_share": max(0, sozvon_total - sozvon_ref),
        "ref_deal_cut": deal_ref,
        "ref_deal_ref_share": max(0, deal_total - deal_ref),
    }


def _require_partner(request: HttpRequest) -> bool:
    """Только роль «partner» со статусом approved. Забаненный партнёр теряет доступ."""
    user = request.user
    if getattr(user, "role", None) != User.Role.PARTNER:
        return False
    return getattr(user, "status", None) == "approved"


def _referral_earnings_breakdown(partner_user) -> dict[int, dict]:
    """Доход рефовода в разрезе каждого реферала — по НОВОЙ воронке.

    Считает начисления рефоводу из BalanceLog (reason `sozvon_ref#<link>` /
    `deal_ref#<link>`), привязывая каждое к рефералу через SearchLink.user.
    Возвращает `{referral_user_id: {sozvon_cnt, sozvon_amt, deal_cnt,
    deal_amt, total}}`.
    """
    import re
    from django.db.models import Q
    from .models import BalanceLog, SearchLink
    out: dict[int, dict] = {}

    def _slot(uid: int) -> dict:
        if uid not in out:
            out[uid] = {
                "sozvon_cnt": 0, "sozvon_amt": 0,
                "deal_cnt": 0, "deal_amt": 0,
                "total": 0,
            }
        return out[uid]

    logs = (
        BalanceLog.objects
        .filter(user=partner_user, field="balance")
        .filter(Q(reason__startswith="sozvon_ref#") | Q(reason__startswith="deal_ref#"))
        .values_list("reason", "delta")
    )
    parsed: list[tuple[str, int, int]] = []
    link_ids: set[int] = set()
    for reason, delta in logs:
        m = re.match(r"(sozvon_ref|deal_ref)#(\d+)", reason or "")
        if not m:
            continue
        lid = int(m.group(2))
        link_ids.add(lid)
        parsed.append((m.group(1), lid, delta or 0))
    # link_id -> реферал (владелец ссылки)
    link_user = dict(SearchLink.objects.filter(id__in=link_ids).values_list("id", "user_id"))
    for kind, lid, amt in parsed:
        uid = link_user.get(lid)
        if uid is None:
            continue
        s = _slot(uid)
        if kind == "sozvon_ref":
            s["sozvon_cnt"] += 1
            s["sozvon_amt"] += amt
        else:
            s["deal_cnt"] += 1
            s["deal_amt"] += amt
        s["total"] += amt

    return out


# ─── Кабинет партнёра ──────────────────────────────────────────────────────────

@login_required
def partner_dashboard(request: HttpRequest) -> HttpResponse:
    """Главная страница партнёрского кабинета."""
    if not _require_partner(request):
        return HttpResponseForbidden("Только для партнёров.")

    user = request.user
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    balance = user.balance or 0

    _breakdown = _referral_earnings_breakdown(user)
    total_earned = sum(v["total"] for v in _breakdown.values())
    users_count = User.objects.filter(partner_owner=user).count()
    # История воронки в разрезе рефералов (только с ненулевым доходом).
    _ref_usernames = dict(
        User.objects.filter(id__in=_breakdown.keys()).values_list("id", "username")
    )
    ref_earnings = sorted(
        ({"username": _ref_usernames.get(uid, "?"), **v}
         for uid, v in _breakdown.items() if v["total"] > 0),
        key=lambda x: x["total"], reverse=True,
    )[:100]
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

    SEARCH_TOTAL = getattr(settings, "SEARCH_REPORT_REWARD", 150)
    DOZHIM_TOTAL = getattr(settings, "DOZHIM_APPROVE_REWARD", 40)
    SOZVON_TOTAL = getattr(settings, "SEARCH_SOZVON_REWARD", 150)
    DEAL_TOTAL = getattr(settings, "SEARCH_DEAL_REWARD", 4000)

    return render(request, "partner/dashboard.html", {
        "user": user,
        "balance": balance,
        "total_earned": total_earned,
        "users_count": users_count,
        "ref_earnings": ref_earnings,
        "link": link,
        "withdrawals": withdrawals,
        "withdrawal_pending": withdrawal_pending,
        "withdrawal_pending_amount": withdrawal_pending_amount,
        "can_request_withdrawal": can_request_withdrawal,
        "withdrawal_min_balance": withdrawal_min,
        "dozhim_pending_count": dozhim_pending_count,
        "partner_rate": user.partner_rate or PARTNER_EARN_PER_LEAD_DEFAULT,
        "receiptless_withdrawals": receiptless_withdrawals,
        # Реф-ставки воронки (применяются ко всем рефералам сразу)
        # Реф-ставки — ФИКСИРОВАННЫЕ для всех (редактирование отключено)
        **_fixed_ref_rate_context(),
    })


@login_required
@referral_system_required
@require_http_methods(["POST"])
def partner_update_rates(request: HttpRequest) -> HttpResponse:
    """Редактирование реф-ставок ОТКЛЮЧЕНО (2026-07-13) — ставки фиксированы.

    No-op, чтобы старые формы/закладки не давали 404. См. user_update_ref_rates.
    """
    if not _require_partner(request):
        return HttpResponseForbidden()
    messages.info(request, "Реф-ставки фиксированы и не редактируются.")
    return redirect("partner_dashboard")


@login_required
@referral_system_required
def partner_ref_rates(request: HttpRequest) -> HttpResponse:
    """Страница «Реф-ставки» партнёра — ТОЛЬКО ПРОСМОТР (2026-07-13).

    Ставки фиксированы для всех и берутся из settings: рефовод получает
    SEARCH_SOZVON_REFERRER с созвона и SEARCH_DEAL_REFERRER со сделки реферала,
    реф — остаток. Редактирование отключено, POST игнорируется.
    """
    if not _require_partner(request):
        return HttpResponseForbidden("Только для партнёров.")

    SOZVON_TOTAL = getattr(settings, "SEARCH_SOZVON_REWARD", 150)
    DEAL_TOTAL = getattr(settings, "SEARCH_DEAL_REWARD", 4000)

    if request.method == "POST":
        messages.info(request, "Реф-ставки фиксированы и не редактируются.")
        return redirect("partner_ref_rates")

    user = request.user
    return render(request, "partner/ref_rates.html", {
        "user": user,
        # Реф-ставки — ФИКСИРОВАННЫЕ для всех (редактирование отключено)
        **_fixed_ref_rate_context(),
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
            from .models import log_balance_change
            _old_bal = user_refresh.balance or 0
            user_refresh.balance = 0
            user_refresh.save(update_fields=["balance"])
            log_balance_change(user_refresh, "balance", _old_bal, 0, f"partner_withdrawal -{current_balance}", None)

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
@referral_system_required
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

    breakdown = _referral_earnings_breakdown(request.user)
    total_earned = sum(v["total"] for v in breakdown.values())
    for u in page_obj:
        u.earn = breakdown.get(u.id, {"sozvon_cnt": 0, "sozvon_amt": 0,
                                      "deal_cnt": 0, "deal_amt": 0, "total": 0})

    return render(request, "partner/referrals.html", {
        "page_obj": page_obj,
        "total": users_qs.count(),
        "total_earned": total_earned,
        # Реф-ставки — ФИКСИРОВАННЫЕ для всех (редактирование отключено)
        **_fixed_ref_rate_context(),
    })


# ═══════════════════════════════════════════════════════════════════════════════
# Проверка дожим-лидов партнёром
# ═══════════════════════════════════════════════════════════════════════════════

from django.views.decorators.http import require_http_methods


@login_required
@dozhim_required
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
@dozhim_required
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

        total_reward = getattr(settings, "DOZHIM_APPROVE_REWARD", 40)
        lead.status = Lead.Status.APPROVED
        lead.reviewed_at = timezone.now()
        lead.reviewed_by = request.user
        lead.save(update_fields=["status", "reviewed_at", "reviewed_by"])

        # Делим награду: партнёр забирает свой partner_dozhim_cut, реф — остаток.
        partner_locked = User.objects.select_for_update().get(pk=request.user.id)
        partner_cut = max(0, min(total_reward - 1, partner_locked.partner_dozhim_cut or 0))
        ref_reward = total_reward - partner_cut

        lead_owner = User.objects.select_for_update().get(pk=lead.user_id)
        _old = lead_owner.dozhim_balance or 0
        lead_owner.dozhim_balance = _old + ref_reward
        lead_owner.save(update_fields=["dozhim_balance"])
        log_balance_change(lead_owner, "dozhim_balance", _old, lead_owner.dozhim_balance, f"partner_dozhim_approve#{lead_id} +{ref_reward}", request.user)
        # Авто-аккредитация: баланс дожима был в минусе и перешёл в плюс
        if not lead_owner.is_accredited and _old < 0 and lead_owner.dozhim_balance >= 0:
            lead_owner.is_accredited = True
            lead_owner.save(update_fields=["is_accredited"])

        # Начисление партнёру за дожим-лид реферала.
        if partner_cut > 0:
            _old_pb = partner_locked.balance or 0
            partner_locked.balance = _old_pb + partner_cut
            partner_locked.save(update_fields=["balance"])
            log_balance_change(partner_locked, "balance", _old_pb, partner_locked.balance, f"partner_dozhim_earn#{lead_id} +{partner_cut}", request.user)

    msg = f"Лид #{lead_id} одобрен. +{ref_reward} ₽ @{lead.user.username}"
    if partner_cut > 0:
        msg += f", вам +{partner_cut} ₽"
    messages.success(request, msg + ".")
    return redirect("partner_dozhim_leads")


@login_required
@dozhim_required
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
            total_reward = getattr(settings, "DOZHIM_APPROVE_REWARD", 40)
            partner_locked = User.objects.select_for_update().get(pk=request.user.id)
            partner_cut = max(0, min(total_reward - 1, partner_locked.partner_dozhim_cut or 0))
            ref_reward = total_reward - partner_cut

            lead_owner = User.objects.select_for_update().get(pk=lead.user_id)
            _old = lead_owner.dozhim_balance or 0
            lead_owner.dozhim_balance = _old - ref_reward
            lead_owner.save(update_fields=["dozhim_balance"])
            log_balance_change(lead_owner, "dozhim_balance", _old, lead_owner.dozhim_balance, f"partner_dozhim_reject#{lead_id} -{ref_reward}", request.user)

            # Откат партнёрского заработка
            if partner_cut > 0:
                _old_pb = partner_locked.balance or 0
                partner_locked.balance = _old_pb - partner_cut
                partner_locked.save(update_fields=["balance"])
                log_balance_change(partner_locked, "balance", _old_pb, partner_locked.balance, f"partner_dozhim_earn_rollback#{lead_id} -{partner_cut}", request.user)

    messages.success(request, f"Лид #{lead_id} отклонён.")
    return redirect("partner_dozhim_leads")


@login_required
@dozhim_required
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
            total_reward = getattr(settings, "DOZHIM_APPROVE_REWARD", 40)
            partner_locked = User.objects.select_for_update().get(pk=request.user.id)
            partner_cut = max(0, min(total_reward - 1, partner_locked.partner_dozhim_cut or 0))
            ref_reward = total_reward - partner_cut

            lead_owner = User.objects.select_for_update().get(pk=lead.user_id)
            _old = lead_owner.dozhim_balance or 0
            lead_owner.dozhim_balance = _old - ref_reward
            lead_owner.save(update_fields=["dozhim_balance"])
            log_balance_change(lead_owner, "dozhim_balance", _old, lead_owner.dozhim_balance, f"partner_dozhim_rework#{lead_id} -{ref_reward}", request.user)

            # Откат партнёрского заработка
            if partner_cut > 0:
                _old_pb = partner_locked.balance or 0
                partner_locked.balance = _old_pb - partner_cut
                partner_locked.save(update_fields=["balance"])
                log_balance_change(partner_locked, "balance", _old_pb, partner_locked.balance, f"partner_dozhim_earn_rollback#{lead_id} -{partner_cut}", request.user)

    messages.success(request, f"Лид #{lead_id} отправлен на доработку.")
    return redirect("partner_dozhim_leads")


@login_required
@dozhim_required
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
    """Допуск к реф-системе: одобренный обычный юзер ИЛИ обычный админ.

    Раньше только role=user могли заводить рефералов. Теперь обычные
    админы (role=admin) тоже могут. Главный админ (main_admin) — нет,
    ему реф-система не нужна.
    """
    user = request.user
    if not getattr(user, "is_authenticated", False):
        return False
    role = getattr(user, "role", None)
    if role == "user":
        return getattr(user, "status", None) == "approved"
    return role == "admin"


@login_required
@referral_system_required
def user_referrals(request: HttpRequest) -> HttpResponse:
    """Реферальная система менеджера. Одна общая ссылка + общие ставки
    (SearchLink + GroupReport) на всех рефералов."""
    if not _require_user_approved(request):
        return HttpResponseForbidden("Только для одобренных пользователей.")

    user = request.user

    SOZVON_TOTAL = getattr(settings, "SEARCH_SOZVON_REWARD", 150)
    DEAL_TOTAL = getattr(settings, "SEARCH_DEAL_REWARD", 4000)

    # Одна реф-ссылка на всех. Если нет — создаём; если несколько (legacy) —
    # отдаём самую раннюю активную.
    link = (
        PartnerLink.objects.filter(partner=user, is_active=True)
        .order_by("created_at").first()
    )
    if not link:
        link = PartnerLink.objects.filter(partner=user).order_by("created_at").first()
    if not link:
        link = PartnerLink.objects.create(partner=user, code=uuid4().hex[:24])

    # Заработок по новой воронке в разрезе каждого реферала.
    breakdown = _referral_earnings_breakdown(user)
    total_earned = sum(v["total"] for v in breakdown.values())
    # «Активные» = рефералы, за которых уже было начисление воронки.
    active_ref_ids: set[int] = {uid for uid, v in breakdown.items() if v["total"] > 0}
    total_referrals_count = User.objects.filter(partner_owner=user).count()
    active_count = len(active_ref_ids)
    inactive_count = max(0, total_referrals_count - active_count)

    # По умолчанию показываем всех рефералов (воронка новая, начислений пока мало).
    show_param = (request.GET.get("show") or "all").lower()
    show_all = show_param != "active"
    referrals_qs = User.objects.filter(partner_owner=user)
    if not show_all:
        referrals_qs = referrals_qs.filter(id__in=active_ref_ids)
    referrals = list(referrals_qs.order_by("-date_joined")[:200])
    for r in referrals:
        r.earn = breakdown.get(r.id, {"sozvon_cnt": 0, "sozvon_amt": 0,
                                       "deal_cnt": 0, "deal_amt": 0, "total": 0})

    # Неаккредитованный рефовод: вместо % — разовый бонус за каждого реферала,
    # приведшего SUBREF_MILESTONE клиентов в бота. Показываем прогресс по каждому.
    from .lead_utils import is_milestone_referrer, SUBREF_MILESTONE, SUBREF_BONUS
    from .models import BalanceLog, SearchLink
    is_milestone = is_milestone_referrer(user)
    if is_milestone:
        # У milestone-рефовода нет %-начислений — доход это бонусы ref_milestone#.
        total_earned = (
            BalanceLog.objects.filter(user=user, field="balance",
                                      reason__startswith="ref_milestone#")
            .aggregate(s=Sum("delta")).get("s") or 0
        )
    if is_milestone and referrals:
        # Один запрос на всех рефералов вместо N (клиенты, нажавшие /start).
        starts = dict(
            SearchLink.objects.filter(user__in=referrals, bot_started=True)
            .values("user_id").annotate(c=Count("id")).values_list("user_id", "c")
        )
        for r in referrals:
            done = starts.get(r.id, 0)
            r.ms_done = done
            r.ms_left = max(0, SUBREF_MILESTONE - done)
            r.ms_paid = bool(r.subref_bonus_paid_at)
            r.ms_pct = min(100, int(100 * done / SUBREF_MILESTONE)) if SUBREF_MILESTONE else 0

    return render(request, "core/user_referrals.html", {
        "user": user,
        "total_earned": total_earned,
        # users_count — общее число рефералов (для шапки), не зависит от фильтра
        "users_count": total_referrals_count,
        "active_count": active_count,
        "inactive_count": inactive_count,
        "show_all": show_all,
        "shown_count": len(referrals),
        "link": link,
        "referrals": referrals,
        # Реф-ставки — ФИКСИРОВАННЫЕ для всех (редактирование отключено)
        **_fixed_ref_rate_context(),
        # Milestone-система (неаккредитованный рефовод)
        "is_milestone_referrer": is_milestone,
        "milestone_target": SUBREF_MILESTONE,
        "milestone_bonus": SUBREF_BONUS,
    })


@login_required
@referral_system_required
@require_http_methods(["POST"])
def user_update_ref_rates(request: HttpRequest) -> HttpResponse:
    """Редактирование реф-ставок ОТКЛЮЧЕНО (2026-07-13).

    Ставки фиксированы для всех и задаются в settings (SEARCH_SOZVON_REFERRER /
    SEARCH_DEAL_REFERRER). Вьюха оставлена как no-op, чтобы старые формы/закладки
    не давали 404. Вернуть редактирование = восстановить тело из git-истории.
    """
    if not _require_user_approved(request):
        return HttpResponseForbidden()
    messages.info(request, "Реф-ставки фиксированы и не редактируются.")
    return redirect("user_referrals")


@login_required
def user_referral_create_link(request: HttpRequest) -> HttpResponse:
    """Создать реферальную ссылку с настраиваемой долей рефовода с SearchLink-отчётов рефералов."""
    if not _require_user_approved(request):
        return HttpResponseForbidden()
    if request.method != "POST":
        return redirect("user_referrals")

    from django.conf import settings as _settings
    SEARCH_TOTAL = getattr(_settings, "SEARCH_REPORT_REWARD", 150)

    note = (request.POST.get("note") or "").strip()[:100]
    try:
        ref_searchlink_cut = int(request.POST.get("ref_searchlink_cut", 50))
    except (TypeError, ValueError):
        ref_searchlink_cut = 50
    ref_searchlink_cut = max(0, min(SEARCH_TOTAL, ref_searchlink_cut))

    GR_TOTAL = 80
    try:
        ref_group_report_cut = int(request.POST.get("ref_group_report_cut", 50))
    except (TypeError, ValueError):
        ref_group_report_cut = 50
    ref_group_report_cut = max(0, min(GR_TOTAL, ref_group_report_cut))

    code = uuid4().hex[:24]
    PartnerLink.objects.create(
        partner=request.user, code=code, note=note,
        ref_searchlink_cut=ref_searchlink_cut,
        ref_group_report_cut=ref_group_report_cut,
    )
    sl_share = SEARCH_TOTAL - ref_searchlink_cut
    gr_share = GR_TOTAL - ref_group_report_cut
    messages.success(
        request,
        f"Ссылка создана. SearchLink: реф {sl_share} ₽ / вы {ref_searchlink_cut} ₽. "
        f"Группы: реф {gr_share} ₽ / вы {ref_group_report_cut} ₽.",
    )
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
@referral_system_required
def user_referral_list(request: HttpRequest) -> HttpResponse:
    """Список рефералов менеджера (только просмотр) с разбивкой дохода.

    По умолчанию показываем только «активных» рефералов (>=1 одобренный
    отчёт). `?show=all` — все, включая неактивных.
    """
    if not _require_user_approved(request):
        return HttpResponseForbidden("Только для одобренных пользователей.")

    # По умолчанию показываем всех (воронка новая, начислений пока мало).
    show_param = (request.GET.get("show") or "all").lower()
    show_all = show_param != "active"

    breakdown = _referral_earnings_breakdown(request.user)
    active_ref_ids: set[int] = {uid for uid, v in breakdown.items() if v["total"] > 0}
    total_referrals_count = User.objects.filter(partner_owner=request.user).count()
    active_count = len(active_ref_ids)
    inactive_count = max(0, total_referrals_count - active_count)

    users_qs = User.objects.filter(partner_owner=request.user)
    if not show_all:
        users_qs = users_qs.filter(id__in=active_ref_ids)
    users_qs = users_qs.order_by("-date_joined")
    paginator = Paginator(users_qs, 50)
    page_obj = paginator.get_page(request.GET.get("page"))
    for u in page_obj:
        u.earn = breakdown.get(u.id, {"sozvon_cnt": 0, "sozvon_amt": 0,
                                       "deal_cnt": 0, "deal_amt": 0, "total": 0})

    return render(request, "core/user_referral_list.html", {
        "page_obj": page_obj,
        # total — общее число рефералов (не зависит от фильтра)
        "total": total_referrals_count,
        "active_count": active_count,
        "inactive_count": inactive_count,
        "show_all": show_all,
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
                # Применяем долю рефовода из ссылки к новому рефералу.
                user.ref_searchlink_enabled = True
                user.ref_searchlink_manager_cut = ref_link.ref_searchlink_cut
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
