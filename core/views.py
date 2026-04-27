import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone as dt_utc
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.db.models import F, Max, Q
from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.core.paginator import InvalidPage, Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from .forms import BaseRequestForm, DozhimLeadReportForm, LeadReportForm, LeadReworkUserForm, UserRegistrationForm
from .lead_utils import (
    LEAD_VIDEO_EXTENSIONS,
    _get_attachment_extension,
    compress_lead_attachment,
    determine_base_type_for_contact,
    extract_username_from_contact,
    normalize_lead_contact,
)
from django.conf import settings

from .models import BaseType, Contact, ContactRequest, DozhimIssuedLead, Lead, LeadType, SupportMessage, SupportThread, User, UserBaseLimit, WithdrawalRequest

logger = logging.getLogger(__name__)

_bg_executor = ThreadPoolExecutor(max_workers=1)


def health_check(request: HttpRequest) -> HttpResponse:
    """Лёгкая проверка состояния без БД и сессий — для health check платформы (Timeweb и т.д.)."""
    return HttpResponse("ok", content_type="text/plain")


def index(request: HttpRequest) -> HttpResponse:
    """Лендинг: короткое описание сервиса и ссылки на вход/регистрацию."""
    if request.user.is_authenticated:
        return redirect("dashboard")
    return render(request, "core/index.html")


def register(request: HttpRequest) -> HttpResponse:
    """Регистрация нового пользователя.

    После регистрации перенаправляем на форму входа.
    Статус пользователя по умолчанию `pending` — модерация через админку.
    """

    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        try:
            if form.is_valid():
                user = form.save(commit=False)
                try:
                    from .models import SiteSettings
                    site_settings = SiteSettings.get_settings()
                    if site_settings.auto_approve_users:
                        user.status = User.Status.APPROVED
                except Exception:
                    pass
                user.save()
                if user.status == User.Status.APPROVED:
                    messages.success(
                        request,
                        "Регистрация прошла успешно. Ваш аккаунт уже активен — войдите в личный кабинет.",
                    )
                else:
                    messages.success(
                        request,
                        "Регистрация прошла успешно. Войдите в личный кабинет, используя логин и пароль.",
                    )
                return redirect("login")
        except Exception as e:
            logger.exception("Ошибка при регистрации: %s", e)
            messages.error(
                request,
                "Не удалось завершить регистрацию. Проверьте логин (возможно, он уже занят) или попробуйте позже.",
            )
    else:
        form = UserRegistrationForm()

    return render(request, "auth/register.html", {"form": form})


def _is_worker(user) -> bool:
    """Пользователь — исполнитель (воркер)."""
    return getattr(user, "role", None) == "worker"


def _is_admin(user) -> bool:
    """Пользователь — сотрудник поддержки или администратор (без спец‑кабинетов)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "role", None) in ("standalone_admin", "balance_admin"):
        return False
    return bool(
        user.is_staff
        or user.is_superuser
        or getattr(user, "role", None) in ("support", "admin", "main_admin")
    )


def _is_main_admin(user) -> bool:
    """Пользователь — главный админ (полный контроль)."""
    return getattr(user, "role", None) == "main_admin"


def _is_standalone_admin(user) -> bool:
    """Пользователь — самостоятельный админ (СС лиды)."""
    return getattr(user, "role", None) == "standalone_admin"


def _is_balance_admin(user) -> bool:
    """Пользователь — баланс‑админ (отдельный кабинет с историей начислений)."""
    return getattr(user, "role", None) == "balance_admin"


def _is_partner(user) -> bool:
    """Пользователь — партнёр (кабинет с реф-ссылками и начислениями)."""
    return getattr(user, "role", None) == "partner"




@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    """Главная страница кабинета:
    - для баланс‑админа — отдельный кабинет баланса;
    - для воркера — перенаправление в кабинет исполнителя;
    - для админов — админ-дашборд;
    - для standalone‑админа — кабинет СС-лидов;
    - для пользователей — обычный кабинет.
    """
    user = request.user
    if _is_worker(user):
        return redirect("worker_dashboard")
    if _is_partner(user):
        return redirect("partner_dashboard")
    if _is_balance_admin(user):
        from django.db.models import Sum, Q as _Q
        from .models import LeadReviewLog

        # Всего одобрений лидов (все админы, все времена), кроме лидов партнёрских пользователей
        base_log_qs = LeadReviewLog.objects.filter(
            action=LeadReviewLog.Action.APPROVED,
            lead__user__partner_owner__isnull=True,
        )
        total_approved = base_log_qs.count()
        from decimal import Decimal
        rate = getattr(user, "balance_admin_rate", None) or Decimal("5")
        offset = getattr(user, "balance_admin_earnings_offset", None) or Decimal("0")
        earned = int(total_approved * rate + offset)
        withdrawn = (
            WithdrawalRequest.objects.filter(user=user, status__in=("pending", "approved"))
            .aggregate(s=Sum("amount"))
            .get("s")
            or 0
        )
        available = max(0, earned - withdrawn)
        logs = (
            base_log_qs
            .select_related("lead", "admin", "lead__user")
            .order_by("-created_at")[:200]
        )
        withdrawals = WithdrawalRequest.objects.filter(user=user).order_by("-created_at")
        return render(
            request,
            "core/dashboard_balance_admin.html",
            {
                "user": user,
                "earned_total": earned,
                "withdrawn_total": withdrawn,
                "available_balance": available,
                "logs": logs,
                "withdrawals": withdrawals,
                "withdrawal_min_balance": getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500),
                "current_rate": rate,
                "receiptless_withdrawals": list(
                    WithdrawalRequest.objects.filter(user=user, status="approved")
                    .exclude(receipt_status__in=["approved", "waived"])
                    .order_by("-created_at")[:5]
                ),
            },
        )
    if _is_standalone_admin(user):
        from .models import LeadAssignment, WorkerReport, WorkerSelfLead, WorkerWithdrawalRequest
        pending_worker_reports_count = WorkerReport.objects.filter(
            standalone_admin=user, status=WorkerReport.Status.PENDING
        ).count()
        workers_count = User.objects.filter(standalone_admin_owner=user, role=User.Role.WORKER).count()
        pending_worker_withdrawals_count = WorkerWithdrawalRequest.objects.filter(
            standalone_admin=user, status="pending"
        ).count()
        pending_worker_self_leads_count = WorkerSelfLead.objects.filter(
            standalone_admin=user, status=WorkerSelfLead.Status.PENDING
        ).count()
        refused_count = LeadAssignment.objects.filter(
            worker__standalone_admin_owner=user, refused=True
        ).count()
        return render(request, "core/dashboard_standalone_admin.html", {
            "user": user,
            "pending_worker_reports_count": pending_worker_reports_count,
            "workers_count": workers_count,
            "pending_worker_withdrawals_count": pending_worker_withdrawals_count,
            "pending_worker_self_leads_count": pending_worker_self_leads_count,
            "refused_count": refused_count,
        })
    if _is_admin(user):
        from .models import LeadReviewLog
        from decimal import Decimal
        from django.db.models import Count, Sum
        pending_count = User.objects.filter(status=User.Status.PENDING).count()
        unread_threads_count = SupportThread.objects.annotate(
            msg_count=Count("messages")
        ).filter(
            msg_count__gt=0,
        ).filter(
            Q(last_read_at__isnull=True) | Q(updated_at__gt=F("last_read_at"))
        ).count()
        contact_requests_pending_count = ContactRequest.objects.filter(status="pending").count()
        withdrawal_requests_pending_count = WithdrawalRequest.objects.filter(status="pending").count()
        pending_leads_count = Lead.objects.filter(
            status=Lead.Status.PENDING
        ).exclude(lead_type__slug="dozhim").count()
        dozhim_pending_count = Lead.objects.filter(
            status=Lead.Status.PENDING,
            lead_type__slug="dozhim",
        ).count()
        # Заработок админа: 2.5₽ за Lead-action + 10₽ за SearchLink-action.
        from .admin_earnings import total_actions as _ae_actions, total_earned as _ae_earned
        admin_actions = _ae_actions(user)
        admin_earned = _ae_earned(user)
        admin_withdrawn = WithdrawalRequest.objects.filter(user=user, status__in=("pending", "approved")).aggregate(s=Sum("amount")).get("s") or 0
        admin_balance = max(0, admin_earned - admin_withdrawn)
        admin_withdrawal_pending = WithdrawalRequest.objects.filter(user=user, status="pending").exists()
        admin_smz_ok = getattr(user, "smz_status", "none") == "approved" or getattr(user, "role", None) == "main_admin"
        admin_can_withdraw = admin_balance >= getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500) and not admin_withdrawal_pending and admin_smz_ok

        from .models import SearchReport, SearchLink
        search_pending_count = SearchReport.objects.filter(
            search_link__bot_started=True,
            status=SearchReport.Status.PENDING,
        ).count()
        search_total_links = SearchLink.objects.count()
        search_total_approved = SearchReport.objects.filter(status=SearchReport.Status.APPROVED).count()
        smz_pending_count = User.objects.filter(smz_status="pending").count()
        unchecked_receipts_count = WithdrawalRequest.objects.filter(receipt_status="pending").count()

        ctx = {
            "user": user,
            "pending_requests_count": pending_count,
            "unread_threads_count": unread_threads_count,
            "contact_requests_pending_count": contact_requests_pending_count,
            "withdrawal_requests_pending_count": withdrawal_requests_pending_count,
            "pending_leads_count": pending_leads_count,
            "dozhim_pending_count": dozhim_pending_count,
            "admin_balance": admin_balance,
            "admin_earned": admin_earned,
            "admin_actions": admin_actions,
            "admin_can_withdraw": admin_can_withdraw,
            "admin_withdrawal_pending": admin_withdrawal_pending,
            "search_pending_count": search_pending_count,
            "search_total_links": search_total_links,
            "search_total_approved": search_total_approved,
            "smz_pending_count": smz_pending_count,
            "unchecked_receipts_count": unchecked_receipts_count,
            "receiptless_withdrawals": list(
                WithdrawalRequest.objects.filter(user=user, status="approved")
                .exclude(receipt_status__in=["approved", "waived"])
                .order_by("-created_at")[:5]
            ),
        }

        if _is_main_admin(user):
            # Статистика всех админов для main_admin
            from .models import PartnerEarning
            all_staff = User.objects.filter(role__in=("admin", "partner", "balance_admin")).exclude(pk=3).order_by("role", "username")
            admin_stats_list = []
            for a in all_staff:
                if a.role in ("admin", "main_admin"):
                    a_actions = _ae_actions(a)
                    a_earned = _ae_earned(a)
                    a_withdrawn = WithdrawalRequest.objects.filter(user=a, status__in=("pending", "approved")).aggregate(s=Sum("amount")).get("s") or 0
                    admin_stats_list.append({"user": a, "role_label": "Админ", "actions": a_actions, "earned": a_earned, "available": max(0, a_earned - a_withdrawn)})
                elif a.role == "partner":
                    p_earned = PartnerEarning.objects.filter(partner=a).aggregate(s=Sum("amount")).get("s") or 0
                    p_withdrawn = WithdrawalRequest.objects.filter(user=a, status__in=("pending", "approved")).aggregate(s=Sum("amount")).get("s") or 0
                    p_referrals = User.objects.filter(partner_owner=a).count()
                    admin_stats_list.append({"user": a, "role_label": f"Партнёр ({a.partner_rate}₽)", "actions": p_referrals, "earned": p_earned, "available": max(0, (a.balance or 0))})
                elif a.role == "balance_admin":
                    ba_total = LeadReviewLog.objects.filter(action=LeadReviewLog.Action.APPROVED, lead__user__partner_owner__isnull=True).count()
                    ba_rate = a.balance_admin_rate or Decimal("5")
                    ba_offset = a.balance_admin_earnings_offset or Decimal("0")
                    ba_earned = int(ba_total * ba_rate + ba_offset)
                    ba_withdrawn = WithdrawalRequest.objects.filter(user=a, status__in=("pending", "approved")).aggregate(s=Sum("amount")).get("s") or 0
                    admin_stats_list.append({"user": a, "role_label": f"Баланс-админ ({ba_rate}₽)", "actions": "—", "earned": ba_earned, "available": max(0, ba_earned - ba_withdrawn)})
            ctx["admin_stats_list"] = admin_stats_list
            return render(request, "core/dashboard_main_admin.html", ctx)

        return render(request, "core/dashboard_admin.html", ctx)
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    balance = getattr(user, "balance", 0) or 0
    dozhim_balance = getattr(user, "dozhim_balance", 0) or 0
    pending_wr = WithdrawalRequest.objects.filter(user=user, status="pending").first()
    withdrawal_pending = pending_wr is not None
    withdrawal_pending_amount = pending_wr.amount if pending_wr else 0
    smz_ok = getattr(user, "smz_status", "none") == "approved"
    can_withdraw_search = balance >= withdrawal_min and not withdrawal_pending and smz_ok
    can_withdraw_dozhim = dozhim_balance >= withdrawal_min and not withdrawal_pending and smz_ok
    # Есть ли непрочитанные сообщения от поддержки
    support_has_unread = False
    thread = SupportThread.objects.filter(user=user).order_by("-updated_at").first()
    if thread:
        if thread.user_last_read_at is None:
            support_has_unread = thread.messages.filter(is_from_support=True).exists()
        else:
            support_has_unread = thread.messages.filter(
                is_from_support=True, created_at__gt=thread.user_last_read_at
            ).exists()
    # Лиды, отправленные админом на доработку — показываем уведомление на главной
    rework_leads_count = Lead.objects.filter(user=user, status=Lead.Status.REWORK).count()
    from django.db.models import Q as _Q
    receiptless_withdrawals = list(
        WithdrawalRequest.objects.filter(user=user, status="approved")
        .exclude(receipt_status__in=["approved", "waived"])
        .order_by("-created_at")[:5]
    )
    # Плашка «Заявка отклонена» только если последняя заявка юзера — reject.
    # Если после неё была создана новая (pending/approved), плашку не показываем.
    _last_wr = (
        WithdrawalRequest.objects.filter(user=user)
        .order_by("-created_at")
        .first()
    )
    last_rejected_wr = _last_wr if _last_wr and _last_wr.status == "rejected" else None
    return render(
        request,
        "core/dashboard.html",
        {
            "user": user,
            "withdrawal_min_balance": withdrawal_min,
            "withdrawal_pending": withdrawal_pending,
            "withdrawal_pending_amount": withdrawal_pending_amount,
            "can_request_withdrawal": can_withdraw_search,
            "can_withdraw_dozhim": can_withdraw_dozhim,
            "support_has_unread": support_has_unread,
            "rework_leads_count": rework_leads_count,
            "receiptless_withdrawals": receiptless_withdrawals,
            "last_rejected_withdrawal": last_rejected_wr,
            "search_reward": (getattr(settings, "SEARCH_REPORT_REWARD", 100) - user.ref_searchlink_manager_cut) if (user.partner_owner_id and user.ref_searchlink_enabled) else getattr(settings, "SEARCH_REPORT_REWARD", 100),
        },
    )


@login_required
def account_updates_api(request: HttpRequest) -> HttpResponse:
    """JSON API для опроса обновлений: уведомления, баланс, лиды, счётчики админа. Для автообновления без перезагрузки."""
    user = request.user
    balance = getattr(user, "balance", 0) or 0
    # Для role=admin/main_admin баланс считается из LeadReviewLog
    if getattr(user, "role", None) in ("admin", "main_admin"):
        from django.db.models import Sum
        from .admin_earnings import total_earned as _ae_total_earned
        _admin_earned = _ae_total_earned(user)
        _admin_withdrawn = WithdrawalRequest.objects.filter(user=user, status__in=("pending", "approved")).aggregate(s=Sum("amount")).get("s") or 0
        balance = max(0, _admin_earned - _admin_withdrawn)
    dozhim_balance = getattr(user, "dozhim_balance", 0) or 0
    data = {
        "support_has_unread": False,
        "balance": balance,
        "dozhim_balance": dozhim_balance,
        "leads_updated_at": None,
    }
    if _is_admin(user):
        # Счётчики для админ-панели и метка обновления диалогов (для перезагрузки страницы при новом сообщении)
        threads_agg = SupportThread.objects.aggregate(m=Max("updated_at"))
        data["admin"] = {
            "unread_threads_count": SupportThread.objects.annotate(
                msg_count=Count("messages")
            ).filter(
                msg_count__gt=0,
            ).filter(
                Q(last_read_at__isnull=True) | Q(updated_at__gt=F("last_read_at"))
            ).count(),
            "pending_requests_count": User.objects.filter(status=User.Status.PENDING).count(),
            "contact_requests_pending_count": ContactRequest.objects.filter(status="pending").count(),
            "withdrawal_requests_pending_count": WithdrawalRequest.objects.filter(status="pending").count(),
            "pending_leads_count": Lead.objects.filter(
                status=Lead.Status.PENDING
            ).exclude(lead_type__slug="dozhim").count(),
            "threads_updated_at": threads_agg["m"].isoformat() if threads_agg.get("m") else None,
        }
    else:
        if getattr(user, "status", None) != "approved":
            data["rework_leads_count"] = 0
        else:
            data["rework_leads_count"] = Lead.objects.filter(user=user, status=Lead.Status.REWORK).count()
        thread = SupportThread.objects.filter(user=user).order_by("-updated_at").first()
        if thread:
            data["thread_updated_at"] = thread.updated_at.isoformat()
            if thread.user_last_read_at is None:
                data["support_has_unread"] = thread.messages.filter(is_from_support=True).exists()
            else:
                data["support_has_unread"] = thread.messages.filter(
                    is_from_support=True, created_at__gt=thread.user_last_read_at
                ).exists()
        agg = Lead.objects.filter(user=user).aggregate(m=Max("updated_at"))
        if agg.get("m"):
            data["leads_updated_at"] = agg["m"].isoformat()
    return JsonResponse(data)


@require_http_methods(["POST"])
def logout_view(request: HttpRequest) -> HttpResponse:
    """Logout по POST (защита от CSRF-атак через GET)."""
    logout(request)
    return redirect("index")


def _ensure_user_approved(request: HttpRequest) -> bool:
    """Проверяет, одобрен ли пользователь. Если нет — показывает сообщение и возвращает False.
    Воркеры всегда перенаправляются на свой дашборд."""
    user = request.user
    if _is_worker(user):
        return False
    status = getattr(user, "status", None)
    if status == "banned":
        messages.error(
            request,
            "Ваш аккаунт заблокирован. Функции кабинета недоступны.",
        )
        return False
    if status != "approved":
        messages.warning(
            request,
            "Ваш аккаунт ещё не одобрен. Дождитесь одобрения от администратора, "
            "после чего функции кабинета станут доступны.",
        )
        return False
    return True


@login_required
def contacts_placeholder(request: HttpRequest) -> HttpResponse:
    """Страница получения списков контактов с учётом лимитов."""

    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    form = BaseRequestForm(request.POST or None)
    allocated_contacts: list[Contact] = []
    selected_base: BaseType | None = None
    reason: str | None = None

    # Телефонные базы: при выдаче исключаем номера, уже выданные в другой телефонной базе
    PHONE_BASE_SLUGS = ("whatsapp", "max", "viber")

    if request.method == "POST" and form.is_valid():
        selected_base = form.cleaned_data["base_type"]

        base_limit = selected_base.default_daily_limit
        extra_limit = (
            UserBaseLimit.objects.filter(user=user, base_type=selected_base)
            .values_list("extra_daily_limit", flat=True)
            .first()
            or 0
        )
        total_allowed = base_limit + extra_limit

        with transaction.atomic():
            if user.is_accredited:
                # Аккредитированные — без лимита, выдаём пачку base_limit за клик
                can_give = base_limit
                partial_ok = True
            else:
                current = Contact.objects.filter(base_type=selected_base, assigned_to=user).count()
                if current >= total_allowed:
                    reason = "already_got"
                    can_give = 0
                else:
                    can_give = total_allowed - current
                partial_ok = False

            if can_give > 0:
                free_qs = (
                    Contact.objects.select_for_update()
                    .filter(base_type=selected_base, assigned_to__isnull=True, is_active=True)
                    .order_by("id")
                )
                # Для телефонных баз — исключаем номера, уже выданные пользователю в других телефонных базах
                if selected_base.slug in PHONE_BASE_SLUGS:
                    other_phone_bases = BaseType.objects.filter(slug__in=PHONE_BASE_SLUGS).exclude(pk=selected_base.pk)
                    already_issued_values = set(
                        Contact.objects.filter(
                            base_type__in=other_phone_bases,
                            assigned_to=user,
                        ).values_list("value", flat=True)
                    )
                    if already_issued_values:
                        free_qs = free_qs.exclude(value__in=already_issued_values)
                free_count = free_qs.count()
                if free_count == 0 or (not partial_ok and free_count < can_give):
                    reason = "not_enough"
                else:
                    now = timezone.now()
                    actual_give = min(can_give, free_count)
                    contacts_to_give = list(free_qs[:actual_give])
                    ids = [c.pk for c in contacts_to_give]
                    Contact.objects.filter(pk__in=ids).update(
                        assigned_to=user, assigned_at=now
                    )
                    # Обновляем объекты в памяти для отображения в шаблоне
                    for c in contacts_to_give:
                        c.assigned_to = user
                        c.assigned_at = now
                    allocated_contacts = contacts_to_give

        if allocated_contacts:
            messages.success(
                request,
                f"Вы получили {len(allocated_contacts)} контактов из базы «{selected_base.name}».",
            )

    # Выданные пользователю контакты по базам (для кнопки «Скачать .txt»)
    issued_by_base = []
    if user.is_authenticated:
        from django.db.models import Count

        counts = dict(
            Contact.objects.filter(assigned_to=user)
            .values_list("base_type_id")
            .annotate(count=Count("id"))
            .values_list("base_type_id", "count")
        )
        for base in BaseType.objects.filter(id__in=counts).order_by("order"):
            issued_by_base.append((base, counts[base.id]))

    return render(
        request,
        "core/contacts.html",
        {
            "form": form,
            "allocated_contacts": allocated_contacts,
            "selected_base": selected_base,
            "reason": reason,
            "issued_by_base": issued_by_base,
        },
    )


@login_required
def contacts_view(request: HttpRequest) -> HttpResponse:
    """Просмотр выданных контактов: таблица с пагинацией и кнопкой копирования."""
    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")

    base_type_id = request.GET.get("base_type")
    base_type = None
    if base_type_id:
        try:
            base_type = BaseType.objects.get(pk=base_type_id)
        except (BaseType.DoesNotExist, ValueError):
            pass

    qs = Contact.objects.filter(assigned_to=user, assigned_at__isnull=False)
    if base_type:
        qs = qs.filter(base_type=base_type)
    qs = qs.order_by("assigned_at")

    paginator = Paginator(qs, 50)
    page = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(int(page))
    except (ValueError, InvalidPage):
        page_obj = paginator.page(1)

    # Список баз для ссылок на страницу (все базы с выданными контактами)
    from django.db.models import Count
    issued_by_base = []
    counts = dict(
        Contact.objects.filter(assigned_to=user)
        .values_list("base_type_id")
        .annotate(count=Count("id"))
        .values_list("base_type_id", "count")
    )
    for b in BaseType.objects.filter(id__in=counts).order_by("order"):
        issued_by_base.append((b, counts[b.id]))

    return render(
        request,
        "core/contacts_view.html",
        {
            "page_obj": page_obj,
            "base_type": base_type,
            "issued_by_base": issued_by_base,
        },
    )


@login_required
def download_my_contacts_txt(request: HttpRequest) -> HttpResponse:
    """Скачать выданные пользователю контакты в виде .txt (один контакт на строку).
    Группировка по дням выдачи. GET: base_type — только эта база; date — YYYY-MM-DD за один день."""
    from collections import OrderedDict

    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")

    tz = ZoneInfo(settings.TIME_ZONE)
    base_type_id = request.GET.get("base_type")
    date_str = request.GET.get("date")

    base_type = None
    if base_type_id:
        try:
            base_type = BaseType.objects.get(pk=base_type_id)
        except (BaseType.DoesNotExist, ValueError):
            return HttpResponse("Неверная база.", status=400)

    filter_date = None
    if date_str:
        try:
            filter_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass

    qs = Contact.objects.filter(assigned_to=user, assigned_at__isnull=False)
    if base_type:
        qs = qs.filter(base_type=base_type)
    qs = qs.order_by("assigned_at")

    if base_type:
        rows = list(qs.values_list("value", "assigned_at"))
        by_date = OrderedDict()
        for value, assigned_at in rows:
            d = assigned_at.astimezone(tz).date()
            if filter_date and d != filter_date:
                continue
            by_date.setdefault(d, []).append(value)
        parts = []
        for d in sorted(by_date.keys()):
            parts.append("=== %s ===" % d.strftime("%d.%m.%Y") + "\n" + "\n".join(by_date[d]))
        content = "\n\n".join(parts) if parts else ""
        slug = base_type.slug
        filename = f"contacts_{slug}_{filter_date}.txt" if filter_date else f"contacts_{slug}.txt"
    else:
        rows = list(qs.values_list("value", "assigned_at", "base_type__name"))
        by_date = OrderedDict()
        for value, assigned_at, base_name in rows:
            d = assigned_at.astimezone(tz).date()
            if filter_date and d != filter_date:
                continue
            by_date.setdefault(d, []).append((value, base_name or ""))
        parts = []
        for d in sorted(by_date.keys()):
            day_lines = ["=== %s ===" % d.strftime("%d.%m.%Y")]
            by_base = OrderedDict()
            for value, base_name in by_date[d]:
                by_base.setdefault(base_name, []).append(value)
            for base_name, values in by_base.items():
                day_lines.append("=== %s ===" % base_name + "\n" + "\n".join(values))
            parts.append("\n".join(day_lines))
        content = "\n\n".join(parts) if parts else ""
        filename = f"contacts_all_{filter_date}.txt" if filter_date else "contacts_all.txt"

    if filter_date and not content.strip():
        messages.warning(
            request,
            "За выбранную дату вам не выдавались контакты. Выберите другую дату или скачайте все контакты.",
        )
        return redirect("contacts")

    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def smz_registration(request: HttpRequest) -> HttpResponse:
    """Страница верификации СМЗ: заполнение ФИО для выплат."""
    user = request.user

    if request.method == "POST":
        fio = (request.POST.get("smz_fio") or "").strip()
        not_self = request.POST.get("smz_not_self") == "on"
        if not fio:
            messages.error(request, "Укажите ФИО.")
            return render(request, "core/smz_registration.html", {"user": user})
        user.smz_fio = fio
        user.smz_not_self = not_self
        # Любое изменение → статус сбрасывается на pending (заявка летит админу)
        user.smz_status = "pending"
        user.smz_submitted_at = timezone.now()
        user.smz_reject_reason = ""
        user.save(update_fields=["smz_fio", "smz_not_self", "smz_status", "smz_submitted_at", "smz_reject_reason"])
        messages.success(request, "Данные отправлены на проверку администратору.")
        return redirect("smz_registration")

    # Если уже одобрен и не редактирует — отправляем на вывод
    if user.smz_status == "approved" and not request.GET.get("edit"):
        return redirect("request_withdrawal_create")

    return render(request, "core/smz_registration.html", {"user": user})


@login_required
def receipt_upload(request: HttpRequest, wr_id: int) -> HttpResponse:
    """Загрузка чеков для выплаты. Теперь поддерживает несколько файлов разом
    (выплата может быть частями)."""
    if request.method != "POST":
        return redirect("dashboard")
    user = request.user
    wr = get_object_or_404(WithdrawalRequest, pk=wr_id, user=user, status="approved")

    # Принимаем сразу несколько файлов: name="receipt" с multiple
    files = request.FILES.getlist("receipt")
    if not files:
        # Старая форма с одним полем — fallback
        single = request.FILES.get("receipt")
        if single:
            files = [single]
    if not files:
        messages.error(request, "Прикрепите хотя бы один файл чека.")
        return redirect("dashboard")

    from .models import WithdrawalReceipt
    saved = 0
    for f in files:
        WithdrawalReceipt.objects.create(withdrawal_request=wr, file=f)
        saved += 1

    wr.receipt_uploaded_at = timezone.now()
    if wr.receipt_status not in ("approved", "waived"):
        wr.receipt_status = "pending"
    wr.receipt_reject_reason = ""
    wr.save(update_fields=["receipt_uploaded_at", "receipt_status", "receipt_reject_reason", "updated_at"])
    messages.success(request, f"Загружено чеков: {saved}. Отправлено на проверку.")
    return redirect("dashboard")


@login_required
def request_withdrawal_create(request: HttpRequest) -> HttpResponse:
    """Создать заявку на вывод средств (доступно при балансе >= WITHDRAWAL_MIN_BALANCE).

    GET  — показать форму с указанием реквизитов.
    POST — создать заявку и обнулить баланс.
    """
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    user = request.user

    # СМЗ-гейт: для всех кроме main_admin
    if getattr(user, "role", None) != "main_admin":
        if getattr(user, "smz_status", "none") != "approved":
            _smz = getattr(user, "smz_status", "none")
            if _smz == "pending":
                messages.warning(request, "Вывод недоступен: заявка на СМЗ ещё на рассмотрении. Дождитесь одобрения администратором.")
            elif _smz == "rejected":
                messages.warning(request, "Вывод недоступен: заявка на СМЗ отклонена. Исправьте данные и отправьте заново.")
            else:
                messages.warning(request, "Для вывода средств необходимо заполнить данные самозанятости (СМЗ).")
            return redirect("smz_registration")
        # Блокировка: есть выплата без одобренного чека
        has_unchecked = WithdrawalRequest.objects.filter(
            user=user, status="approved",
        ).exclude(receipt_status__in=["approved", "waived"]).exists()
        if has_unchecked:
            messages.warning(request, "Загрузите и дождитесь одобрения чека по предыдущей выплате.")
            return redirect("dashboard")

    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    dept = request.GET.get("dept") or request.POST.get("dept") or "search"

    # Специальная логика для баланс‑админа: баланс считается по логу одобренных лидов.
    if getattr(user, "role", None) == "balance_admin":
        from django.db.models import Sum
        from .models import LeadReviewLog

        total_approved = LeadReviewLog.objects.filter(
            action=LeadReviewLog.Action.APPROVED,
            lead__user__partner_owner__isnull=True,
        ).count()
        from decimal import Decimal
        rate = getattr(user, "balance_admin_rate", None) or Decimal("5")
        offset = getattr(user, "balance_admin_earnings_offset", None) or Decimal("0")
        earned = int(total_approved * rate + offset)
        withdrawn = (
            WithdrawalRequest.objects.filter(user=user, status__in=("pending", "approved"))
            .aggregate(s=Sum("amount"))
            .get("s")
            or 0
        )
        balance = max(0, earned - withdrawn)
    elif getattr(user, "role", None) in ("admin", "main_admin"):
        from django.db.models import Sum
        from .admin_earnings import total_earned as _ae_total_earned
        earned = _ae_total_earned(user)
        withdrawn = (
            WithdrawalRequest.objects.filter(user=user, status__in=("pending", "approved"))
            .aggregate(s=Sum("amount"))
            .get("s")
            or 0
        )
        balance = max(0, earned - withdrawn)
    else:
        if dept == "dozhim":
            balance = getattr(user, "dozhim_balance", 0) or 0
        else:
            balance = getattr(user, "balance", 0) or 0
    if balance < withdrawal_min:
        messages.warning(request, f"Заявка на вывод доступна при балансе от {withdrawal_min} руб.")
        return redirect("dashboard")
    if WithdrawalRequest.objects.filter(user=user, status="pending").exists():
        messages.info(request, "У вас уже есть заявка на вывод на рассмотрении.")
        return redirect("dashboard")

    if request.method == "POST":
        payout_details = (request.POST.get("payout_details") or "").strip()
        if not payout_details:
            messages.error(request, "Укажите способ вывода: номер карты или телефона и банк.")
            return render(
                request,
                "core/withdrawal_request.html",
                {
                    "user": user,
                    "balance": balance,
                    "withdrawal_min_balance": withdrawal_min,
                    "payout_details": payout_details,
                    "dept": dept,
                },
            )
        with transaction.atomic():
            user_refresh = User.objects.select_for_update().get(pk=user.pk)
            # Повторно считаем баланс внутри транзакции, чтобы учесть параллельные изменения.
            _role = getattr(user_refresh, "role", None)
            if _role == "balance_admin":
                from django.db.models import Sum
                from .models import LeadReviewLog

                total_approved = LeadReviewLog.objects.filter(
                    action=LeadReviewLog.Action.APPROVED,
                    lead__user__partner_owner__isnull=True,
                ).count()
                from decimal import Decimal
                rate = getattr(user_refresh, "balance_admin_rate", None) or Decimal("5")
                offset = getattr(user_refresh, "balance_admin_earnings_offset", None) or Decimal("0")
                earned = int(total_approved * rate + offset)
                withdrawn = (
                    WithdrawalRequest.objects.filter(user=user_refresh, status__in=("pending", "approved"))
                    .aggregate(s=Sum("amount"))
                    .get("s")
                    or 0
                )
                current_balance = max(0, earned - withdrawn)
                if WithdrawalRequest.objects.filter(user=user_refresh, status="pending").exists():
                    messages.info(request, "У вас уже есть заявка на вывод на рассмотрении.")
                    return redirect("dashboard")
                if current_balance < withdrawal_min:
                    messages.warning(request, f"Заявка на вывод доступна при балансе от {withdrawal_min} руб.")
                    return redirect("dashboard")
                WithdrawalRequest.objects.create(
                    user=user_refresh,
                    amount=current_balance,
                    payout_details=payout_details,
                    status="pending",
                )
            elif _role in ("admin", "main_admin"):
                from django.db.models import Sum
                from .admin_earnings import total_earned as _ae_total_earned
                earned = _ae_total_earned(user_refresh)
                withdrawn = (
                    WithdrawalRequest.objects.filter(user=user_refresh, status__in=("pending", "approved"))
                    .aggregate(s=Sum("amount"))
                    .get("s")
                    or 0
                )
                current_balance = max(0, earned - withdrawn)
                if WithdrawalRequest.objects.filter(user=user_refresh, status="pending").exists():
                    messages.info(request, "У вас уже есть заявка на вывод на рассмотрении.")
                    return redirect("dashboard")
                if current_balance < withdrawal_min:
                    messages.warning(request, f"Заявка на вывод доступна при балансе от {withdrawal_min} руб.")
                    return redirect("dashboard")
                # Админ может указать сумму вывода
                try:
                    requested_amount = int(request.POST.get("amount") or 0)
                except (TypeError, ValueError):
                    requested_amount = 0
                if requested_amount > 0 and requested_amount <= current_balance:
                    withdraw_amount = requested_amount
                else:
                    withdraw_amount = current_balance
                if withdraw_amount < withdrawal_min:
                    messages.warning(request, f"Минимальная сумма вывода: {withdrawal_min} руб.")
                    return redirect("dashboard")
                WithdrawalRequest.objects.create(
                    user=user_refresh,
                    amount=withdraw_amount,
                    payout_details=payout_details,
                    status="pending",
                )
            else:
                if dept == "dozhim":
                    current_balance = getattr(user_refresh, "dozhim_balance", 0) or 0
                else:
                    current_balance = getattr(user_refresh, "balance", 0) or 0
                if WithdrawalRequest.objects.filter(user=user_refresh, status="pending").exists():
                    messages.info(request, "У вас уже есть заявка на вывод на рассмотрении.")
                    return redirect("dashboard")
                if current_balance < withdrawal_min:
                    messages.warning(request, f"Заявка на вывод доступна при балансе от {withdrawal_min} руб.")
                    return redirect("dashboard")
                # Произвольная сумма вывода
                try:
                    requested_amount = int(request.POST.get("amount") or 0)
                except (TypeError, ValueError):
                    requested_amount = 0
                if requested_amount >= withdrawal_min and requested_amount <= current_balance:
                    withdraw_amount = requested_amount
                else:
                    withdraw_amount = current_balance
                if withdraw_amount < withdrawal_min:
                    messages.warning(request, f"Минимальная сумма вывода: {withdrawal_min} руб.")
                    return redirect("dashboard")
                if withdraw_amount > current_balance:
                    messages.error(request, "Сумма превышает баланс.")
                    return redirect("dashboard")
                dept_label = "Дожим" if dept == "dozhim" else "Поиск"
                WithdrawalRequest.objects.create(
                    user=user_refresh,
                    amount=withdraw_amount,
                    payout_details=f"[{dept_label}] {payout_details}",
                    status="pending",
                )
                from .models import log_balance_change
                if dept == "dozhim":
                    user_refresh.dozhim_balance = current_balance - withdraw_amount
                    user_refresh.save(update_fields=["dozhim_balance"])
                    log_balance_change(user_refresh, "dozhim_balance", current_balance, user_refresh.dozhim_balance, f"withdrawal -{withdraw_amount}", None)
                else:
                    user_refresh.balance = current_balance - withdraw_amount
                    user_refresh.save(update_fields=["balance"])
                    log_balance_change(user_refresh, "balance", current_balance, user_refresh.balance, f"withdrawal -{withdraw_amount}", None)
        _withdrawn_amount = withdraw_amount if _role in ("admin", "main_admin") else withdraw_amount
        messages.success(
            request,
            f"Заявка на вывод {_withdrawn_amount} руб. отправлена. {'Баланс обнулён. ' if _role not in ('balance_admin', 'admin', 'main_admin') else ''}Ожидайте решения администратора.",
        )
        return redirect("dashboard")

    # GET — показать форму c реквизитами
    return render(
        request,
        "core/withdrawal_request.html",
        {
            "user": user,
            "balance": balance,
            "withdrawal_min_balance": withdrawal_min,
            "payout_details": getattr(user, "smz_fio", "") or "",
            "dept": dept,
        },
    )


@login_required
@require_http_methods(["POST"])
def request_contact_create(request: HttpRequest) -> HttpResponse:
    """Создать заявку на дополнительный лимит контактов (кнопка «Обратиться»)."""
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    if ContactRequest.objects.filter(user=request.user, status="pending").exists():
        messages.info(request, "У вас уже есть активная заявка. Ожидайте ответа менеджера.")
        return redirect("contacts")
    base_type_id = request.POST.get("base_type")
    base_type = None
    if base_type_id:
        try:
            base_type = BaseType.objects.get(pk=base_type_id)
        except (BaseType.DoesNotExist, ValueError):
            pass
    ContactRequest.objects.create(user=request.user, base_type=base_type, status="pending")
    messages.success(request, "Заявка отправлена. Менеджер свяжется с вами по поводу лимита контактов.")
    return redirect("contacts")


def _dozhim_lead_exists(raw_contact: str, exclude_lead_id: int | None = None) -> bool:
    """Проверяет дубликат контакта ТОЛЬКО среди дожим-лидов."""
    normalized = normalize_lead_contact(raw_contact)
    if not normalized:
        return False
    try:
        qs = Lead.objects.filter(normalized_contact=normalized, lead_type__slug="dozhim")
        if exclude_lead_id is not None:
            qs = qs.exclude(pk=exclude_lead_id)
        if qs.exists():
            return True
        username = extract_username_from_contact(normalized)
        if username and len(username) >= 3:
            cross_q = Q()
            for prefix in ("telegram:", "vk:", "ig:", "ok:"):
                cross_q |= Q(normalized_contact=prefix + username)
            cross_qs = Lead.objects.filter(cross_q, lead_type__slug="dozhim")
            if exclude_lead_id is not None:
                cross_qs = cross_qs.exclude(pk=exclude_lead_id)
            if cross_qs.exists():
                return True
        return False
    except (OperationalError, ProgrammingError):
        return False


def _lead_exists_globally(raw_contact: str, exclude_lead_id: int | None = None) -> bool:
    """Проверяет, есть ли в базе уже лид с таким контактом (любой пользователь). Комплексная нормализация: @user=user, ссылки и т.д."""
    from .models import WorkerSelfLead
    normalized = normalize_lead_contact(raw_contact)
    if not normalized:
        return False
    try:
        qs = Lead.objects.filter(normalized_contact=normalized)
        if exclude_lead_id is not None:
            qs = qs.exclude(pk=exclude_lead_id)
        if qs.exists():
            return True
        # Проверка в таблице самостоятельных лидов воркеров (raw_contact, т.к. нет normalized_contact)
        if WorkerSelfLead.objects.filter(raw_contact__iexact=raw_contact.strip()).exists():
            return True
        # Кросс-платформенная проверка: тот же username на другой платформе
        username = extract_username_from_contact(normalized)
        if username and len(username) >= 3:
            cross_q = Q()
            for prefix in ("telegram:", "vk:", "ig:", "ok:"):
                cross_q |= Q(normalized_contact=prefix + username)
            cross_qs = Lead.objects.filter(cross_q)
            if exclude_lead_id is not None:
                cross_qs = cross_qs.exclude(pk=exclude_lead_id)
            if cross_qs.exists():
                return True
        return False
    except (OperationalError, ProgrammingError):
        return False


@login_required
def leads_report_placeholder(request: HttpRequest) -> HttpResponse:
    """Страница отправки отчёта по лидам. После отправки остаёмся на странице — можно добавить ещё лид."""

    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    if request.method == "POST":
        form = LeadReportForm(request.POST, request.FILES)
        if form.is_valid():
            raw = form.cleaned_data.get("raw_contact") or ""
            if _lead_exists_globally(raw):
                messages.error(
                    request,
                    "Такой контакт уже есть в базе отчётов (у вас или другого пользователя). "
                    "Дубликаты не принимаются — один контакт можно отправить только один раз, даже на разных платформах.",
                )
            else:
                try:
                    lead: Lead = form.save(commit=False)
                    lead.user = user
                    raw_safe = (lead.raw_contact or "").strip()
                    lead.source = raw_safe or ""
                    lead.normalized_contact = normalize_lead_contact(lead.raw_contact or "")
                    lead.base_type = determine_base_type_for_contact(raw_safe, user)
                    contact_qs = Contact.objects.filter(value=raw_safe)
                    if lead.base_type:
                        contact_qs = contact_qs.filter(base_type=lead.base_type)
                    lead.contact = contact_qs.first()
                    lead.save()
                    ext = _get_attachment_extension(lead.attachment)
                    if ext in LEAD_VIDEO_EXTENSIONS:
                        lead_id = lead.id
                        def _compress_video_bg(lid=lead_id):
                            try:
                                l = Lead.objects.filter(pk=lid).select_related().first()
                                if l and l.attachment:
                                    compress_lead_attachment(l)
                            except Exception as e:
                                logger.warning("Фоновая компрессия видео (lead %s): %s", lid, e)
                        _bg_executor.submit(_compress_video_bg)
                    else:
                        compress_lead_attachment(lead)
                    messages.success(request, "Лид сохранён. Можете добавить ещё один.")
                    form = LeadReportForm()
                except (OperationalError, ProgrammingError) as e:
                    logger.exception("Ошибка БД при сохранении лида (выполните: python manage.py migrate, нужна миграция 0015_lead_date): %s", e)
                    messages.error(
                        request,
                        "Ошибка базы данных. Убедитесь, что выполнены миграции: python manage.py migrate",
                    )
                except RuntimeError as e:
                    if "S3" in str(e) or "хранилищ" in str(e).lower():
                        logger.warning("Сохранение отчёта: %s", e)
                        messages.error(
                            request,
                            "Сейчас нельзя сохранить файл: не настроено облачное хранилище или ошибка подключения к S3. Обратитесь к администратору сайта (админка → Настройки хранилища медиа (S3)).",
                        )
                    else:
                        raise
                except Exception as e:
                    err_str = str(e).lower()
                    if "timeout" in err_str or "timed out" in err_str or "connection" in err_str:
                        logger.warning("Таймаут при сохранении лида: %s", e)
                        messages.error(
                            request,
                            "Загрузка файла заняла слишком много времени. Попробуйте: 1) Сжать видео перед загрузкой, 2) Попробовать позже при более стабильном интернете.",
                        )
                    else:
                        logger.exception("Ошибка при сохранении лида (отчёт): %s", e)
                        messages.error(
                            request,
                            "Не удалось сохранить отчёт. Попробуйте ещё раз или обратитесь в поддержку.",
                        )
    else:
        form = LeadReportForm()

    example_video_url = None
    example_video_description = "Пример идеального видео-отчёта"
    try:
        from .models import SiteSettings
        site_settings = SiteSettings.get_settings()
        if site_settings.example_video:
            example_video_url = site_settings.example_video.url
        example_video_description = site_settings.example_video_description or example_video_description
    except Exception:
        pass
    
    return render(request, "core/leads_report.html", {
        "form": form,
        "example_video_url": example_video_url,
        "example_video_description": example_video_description,
    })


@login_required
def leads_my_list(request: HttpRequest) -> HttpResponse:
    """Страница «Мои лиды»: список лидов пользователя со статусами и доработкой (с пагинацией)."""
    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    try:
        leads_qs = (
            Lead.objects.filter(user=user)
            .exclude(lead_type__slug="dozhim")
            .select_related("lead_type")
            .order_by("-created_at")
        )
        q = (request.GET.get("q") or "").strip()
        if q:
            from django.db.models import Q
            leads_qs = leads_qs.filter(
                Q(raw_contact__icontains=q) | Q(normalized_contact__icontains=q) | Q(pk__icontains=q)
            )
        paginator = Paginator(leads_qs, 30)
        try:
            page_number = int(request.GET.get("page", 1))
        except (TypeError, ValueError):
            page_number = 1
        page_obj = paginator.get_page(page_number)
        # Если пользователь — реф (partner_link), показываем его ставку, иначе стандартные 40
        if user.partner_link_id and user.partner_owner_id:
            lead_approve_reward = user.partner_link.ref_reward if user.partner_link else getattr(settings, "LEAD_APPROVE_REWARD", 40)
        else:
            lead_approve_reward = getattr(settings, "LEAD_APPROVE_REWARD", 40)
        agg = Lead.objects.filter(user=user).exclude(lead_type__slug="dozhim").aggregate(m=Max("updated_at"))
        leads_updated_at = agg.get("m").isoformat() if agg.get("m") else ""
        return render(
            request,
            "core/leads_my_list.html",
            {
                "page_obj": page_obj,
                "lead_approve_reward": lead_approve_reward,
                "leads_updated_at": leads_updated_at,
                "q": q,
            },
        )
    except Exception as e:
        logger.exception("leads_my_list: %s", e)
        messages.error(
            request,
            "Ошибка загрузки лидов. Проверьте логи сервера. Если недавно применяли миграции — перезапустите приложение.",
        )
        return redirect("dashboard")


@login_required
def lead_redo(request: HttpRequest, lead_id: int) -> HttpResponse:
    """Доработка лида пользователем (только если статус «На доработке»)."""
    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    lead = get_object_or_404(Lead, pk=lead_id, user=user)
    if lead.status != Lead.Status.REWORK:
        messages.warning(request, "Этот лид не требует доработки или уже обработан.")
        return redirect("dashboard")
    if request.method == "POST":
        form = LeadReworkUserForm(request.POST, request.FILES)
        if form.is_valid():
            # Вложение обязательно: либо уже есть у лида, либо загружено в форме
            if not lead.attachment and not form.cleaned_data.get("attachment"):
                form.add_error(
                    "attachment",
                    "Приложите скриншот или видео. Без вложения отправить на проверку нельзя.",
                )
            else:
                new_contact = form.cleaned_data["raw_contact"].strip()
                if _lead_exists_globally(new_contact, exclude_lead_id=lead.id):
                    messages.error(
                        request,
                        "Такой контакт уже есть в базе отчётов (в том числе на другой платформе). Укажите другой контакт.",
                    )
                else:
                    try:
                        lead.raw_contact = new_contact
                        lead.source = lead.raw_contact or ""
                        lead.normalized_contact = normalize_lead_contact(lead.raw_contact)
                        lead.comment = form.cleaned_data.get("comment") or ""
                        lead.lead_date = form.cleaned_data["lead_date"]
                        update_fields = ["raw_contact", "source", "normalized_contact", "comment", "lead_date", "status", "rework_comment", "updated_at"]
                        new_lead_type = form.cleaned_data.get("lead_type")
                        if new_lead_type and new_lead_type.pk != lead.lead_type_id:
                            lead.lead_type = new_lead_type
                            update_fields.append("lead_type")
                        if form.cleaned_data.get("attachment"):
                            lead.attachment = form.cleaned_data["attachment"]
                            update_fields.append("attachment")
                        lead.status = Lead.Status.PENDING
                        lead.rework_comment = ""
                        lead.save(update_fields=update_fields)
                        ext = _get_attachment_extension(lead.attachment) if lead.attachment else None
                        if ext in LEAD_VIDEO_EXTENSIONS:
                            lead_id = lead.id
                            def _compress_rework_bg(lid=lead_id):
                                try:
                                    l = Lead.objects.filter(pk=lid).select_related().first()
                                    if l and l.attachment:
                                        compress_lead_attachment(l)
                                except Exception as e:
                                    logger.warning("Фоновая компрессия видео (rework lead %s): %s", lid, e)
                            _bg_executor.submit(_compress_rework_bg)
                        elif lead.attachment:
                            compress_lead_attachment(lead)
                        messages.success(request, "Лид отправлен на повторную проверку.")
                        return redirect("leads_my_list")
                    except RuntimeError as e:
                        if "S3" in str(e) or "хранилищ" in str(e).lower():
                            messages.error(
                                request,
                                "Не удалось сохранить файл: не настроено облачное хранилище. Обратитесь к администратору.",
                            )
                        else:
                            raise
    else:
        form = LeadReworkUserForm(
            initial={
                "raw_contact": lead.raw_contact,
                "lead_date": lead.lead_date,
                "comment": lead.comment,
                "lead_type": lead.lead_type_id,
            }
        )
    return render(request, "core/lead_redo.html", {"form": form, "lead": lead})


@login_required
def leads_stats_placeholder(request: HttpRequest) -> HttpResponse:
    """Страница статистики по лидам для пользователя."""

    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")

    # Логика «дня» с границей 20:00 по Москве, аналогичная боту
    tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)
    from_day = now.date()
    if now.hour >= 20:
        # Текущий «день» считается уже следующим календарным
        from_day = now.date() + timedelta(days=1)

    def day_bounds(day: date) -> tuple[datetime, datetime]:
        start = datetime.combine(day - timedelta(days=1), time(hour=20), tzinfo=tz)
        end = datetime.combine(day, time(hour=20), tzinfo=tz)
        return start.astimezone(dt_utc.utc), end.astimezone(dt_utc.utc)

    today_start, today_end = day_bounds(from_day)
    yesterday_start, yesterday_end = day_bounds(from_day - timedelta(days=1))

    _exclude_dozhim = Q(lead_type__slug="dozhim")
    today_count = (
        Lead.objects.filter(
            user=user,
            status=Lead.Status.APPROVED,
            created_at__gte=today_start,
            created_at__lt=today_end,
        ).exclude(_exclude_dozhim).count()
    )
    yesterday_count = (
        Lead.objects.filter(
            user=user,
            status=Lead.Status.APPROVED,
            created_at__gte=yesterday_start,
            created_at__lt=yesterday_end,
        ).exclude(_exclude_dozhim).count()
    )
    total_count = Lead.objects.filter(user=user, status=Lead.Status.APPROVED).exclude(_exclude_dozhim).count()

    return render(
        request,
        "core/leads_stats.html",
        {
            "today_count": today_count,
            "yesterday_count": yesterday_count,
            "total_count": total_count,
        },
    )


@login_required
def support_placeholder(request: HttpRequest) -> HttpResponse:
    """Страница чата с поддержкой: диалог и форма с текстом и вложением.
    Доступна и до одобрения аккаунта (pending), НО не для забаненных."""
    user = request.user
    if getattr(user, "status", None) == "banned":
        messages.warning(request, "Ваш аккаунт заблокирован.")
        return redirect("dashboard")
    thread = SupportThread.objects.filter(user=user, is_closed=False).order_by("-created_at").first()
    if thread is None:
        thread = SupportThread.objects.create(user=user, is_closed=False)

    # Пользователь открыл чат — помечаем сообщения от поддержки как прочитанные
    thread.user_last_read_at = timezone.now()
    thread.save(update_fields=["user_last_read_at"])

    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()
        attachment = request.FILES.get("attachment")
        if text or attachment:
            SupportMessage.objects.create(
                thread=thread,
                sender=user,
                is_from_support=False,
                text=text,
                attachment=attachment,
            )
            thread.updated_at = timezone.now()
            thread.save(update_fields=["updated_at"])
        return redirect("support")

    messages_qs = thread.messages.select_related("sender").order_by("created_at")

    return render(
        request,
        "core/support_user.html",
        {
            "thread": thread,
            "support_messages": messages_qs,
            "thread_updated_at": thread.updated_at.isoformat(),
            "disable_polling": True,
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def support_widget(request: HttpRequest) -> HttpResponse:
    """Виджет поддержки: плавающее окно чата. GET — панель, POST — сохранить и вернуть список сообщений.
    Доступен и до одобрения (pending), НО не для забаненных."""
    user = request.user
    if getattr(user, "status", None) == "banned":
        return HttpResponseForbidden("Аккаунт заблокирован.")
    thread = SupportThread.objects.filter(user=user, is_closed=False).order_by("-created_at").first()
    if thread is None:
        thread = SupportThread.objects.create(user=user, is_closed=False)

    # При открытии виджета (GET) помечаем сообщения от поддержки как прочитанные
    if request.method == "GET":
        thread.user_last_read_at = timezone.now()
        thread.save(update_fields=["user_last_read_at"])

    if request.method == "POST":
        text = (request.POST.get("text") or "").strip()
        attachment = request.FILES.get("attachment")
        if text or attachment:
            SupportMessage.objects.create(
                thread=thread,
                sender=user,
                is_from_support=False,
                text=text,
                attachment=attachment,
            )
            thread.updated_at = timezone.now()
            thread.save(update_fields=["updated_at"])
        messages_qs = thread.messages.select_related("sender").order_by("created_at")
        return render(
            request,
            "core/partials/support_messages.html",
            {"support_messages": messages_qs},
        )

    messages_qs = thread.messages.select_related("sender").order_by("created_at")
    return render(
        request,
        "core/partials/support_widget_panel.html",
        {"thread": thread, "support_messages": messages_qs},
    )


def ref_register(request: HttpRequest, code: str) -> HttpResponse:
    """Регистрация через реферальную ссылку: пользователь получает роль воркера и сразу одобряется."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    from .models import ReferralLink

    try:
        ref_link = ReferralLink.objects.select_related("standalone_admin").get(code=code, is_active=True)
    except ReferralLink.DoesNotExist:
        return render(request, "auth/ref_register.html", {"error": "Реферальная ссылка недействительна или устарела.", "code": code, "hide_nav_auth": True})

    from .forms import UserRegistrationForm
    if request.method == "POST":
        form = UserRegistrationForm(request.POST)
        try:
            if form.is_valid():
                user = form.save(commit=False)
                user.role = User.Role.WORKER
                user.status = User.Status.APPROVED
                user.standalone_admin_owner = ref_link.standalone_admin
                user.save()
                messages.success(request, "Регистрация прошла успешно. Войдите в личный кабинет.")
                return redirect("login")
        except Exception as e:
            logger.exception("Ошибка при регистрации воркера: %s", e)
            messages.error(request, "Не удалось завершить регистрацию. Возможно, логин уже занят.")
    else:
        form = UserRegistrationForm()

    return render(request, "auth/ref_register.html", {"form": form, "code": code, "standalone_admin": ref_link.standalone_admin, "hide_nav_auth": True})


# ──────────────────────────────────────────────────────────
#  Отдел дожима
# ──────────────────────────────────────────────────────────

@login_required
@require_http_methods(["POST"])
def switch_department(request: HttpRequest) -> HttpResponse:
    """Переключение между «Отдел поиска» и «Отдел дожима» (session)."""
    dept = request.POST.get("department", "search")
    if dept not in ("search", "dozhim"):
        dept = "search"
    request.session["department"] = dept
    return redirect("dashboard")


@login_required
def dozhim_contacts(request: HttpRequest) -> HttpResponse:
    """Выдача 10 одобренных лидов из Отдела поиска для дожима с фильтром по категории."""
    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")

    batch_size = getattr(settings, "DOZHIM_BATCH_SIZE", 10)
    daily_limit = 20
    allocated = []
    reason = None
    # Категории для фильтра
    lead_types = LeadType.objects.exclude(slug__in=("dozhim", "self")).order_by("order", "id")
    selected_type_id = request.POST.get("lead_type") or request.GET.get("lead_type") or ""

    # Сколько лидов выдано сегодня
    from datetime import timedelta
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    issued_today = DozhimIssuedLead.objects.filter(user=user, created_at__gte=today_start).count()
    remaining_today = max(0, daily_limit - issued_today)

    if request.method == "POST" and "get_leads" in request.POST:
        if remaining_today <= 0:
            reason = "limit_reached"
        else:
            give_count = min(batch_size, remaining_today)
            with transaction.atomic():
                available = (
                    Lead.objects.select_for_update()
                    .filter(status=Lead.Status.APPROVED)
                    .exclude(lead_type__slug="dozhim")
                    .exclude(dozhim_issues__isnull=False)
                    .order_by("reviewed_at", "id")
                )
                if selected_type_id:
                    available = available.filter(lead_type_id=selected_type_id)
                leads_to_issue = list(available[:give_count])
                for lead in leads_to_issue:
                    DozhimIssuedLead.objects.create(user=user, lead=lead)
                allocated = leads_to_issue

            if allocated:
                issued_today += len(allocated)
                remaining_today = max(0, daily_limit - issued_today)
                messages.success(request, f"Вы получили {len(allocated)} лидов для дожима.")
            else:
                reason = "not_enough"

    # Ранее выданные лиды (последние 50)
    issued = (
        DozhimIssuedLead.objects.filter(user=user)
        .select_related("lead", "lead__lead_type")
        .order_by("-created_at")[:50]
    )

    return render(request, "core/dozhim_contacts.html", {
        "allocated": allocated,
        "issued": issued,
        "batch_size": min(batch_size, remaining_today) if remaining_today > 0 else 0,
        "lead_types": lead_types,
        "selected_type_id": int(selected_type_id) if selected_type_id else "",
        "daily_limit": daily_limit,
        "issued_today": issued_today,
        "remaining_today": remaining_today,
        "reason": reason,
    })


@login_required
def dozhim_leads_report(request: HttpRequest) -> HttpResponse:
    """Отправка отчёта в Отделе дожима."""
    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")

    if request.method == "POST":
        form = DozhimLeadReportForm(request.POST, request.FILES)
        if form.is_valid():
            raw = form.cleaned_data.get("raw_contact") or ""
            if _dozhim_lead_exists(raw):
                messages.error(
                    request,
                    "Такой контакт уже есть в отчётах дожима. Дубликаты не принимаются.",
                )
            else:
                try:
                    lead = form.save(commit=False)
                    lead.user = user
                    lead.raw_contact = raw.strip()
                    lead.source = raw.strip()
                    lead.normalized_contact = normalize_lead_contact(raw)
                    lead.lead_type = LeadType.objects.get(slug="dozhim")
                    lead.base_type = determine_base_type_for_contact(raw, user)
                    # needs_team_contact берётся из формы
                    lead.save()
                    ext = _get_attachment_extension(lead.attachment)
                    if ext in LEAD_VIDEO_EXTENSIONS:
                        lead_id = lead.id
                        def _compress_bg(lid=lead_id):
                            try:
                                l = Lead.objects.filter(pk=lid).first()
                                if l and l.attachment:
                                    compress_lead_attachment(l)
                            except Exception as e:
                                logger.warning("Компрессия видео (dozhim lead %s): %s", lid, e)
                        _bg_executor.submit(_compress_bg)
                    else:
                        compress_lead_attachment(lead)
                    messages.success(request, "Отчёт (дожим) отправлен на проверку.")
                    form = DozhimLeadReportForm()
                except Exception as e:
                    logger.exception("Ошибка при сохранении дожим-лида: %s", e)
                    messages.error(request, "Не удалось сохранить отчёт. Попробуйте ещё раз.")
    else:
        form = DozhimLeadReportForm()

    return render(request, "core/dozhim_leads_report.html", {"form": form})


@login_required
def dozhim_leads_my_list(request: HttpRequest) -> HttpResponse:
    """Список дожим-лидов пользователя."""
    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    leads_qs = (
        Lead.objects.filter(user=user, lead_type__slug="dozhim")
        .select_related("lead_type")
        .order_by("-created_at")
    )
    paginator = Paginator(leads_qs, 30)
    page_obj = paginator.get_page(request.GET.get("page", 1))
    dozhim_reward = getattr(settings, "DOZHIM_APPROVE_REWARD", 40)
    return render(request, "core/dozhim_leads_my_list.html", {
        "page_obj": page_obj,
        "lead_approve_reward": dozhim_reward,
    })


@login_required
def dozhim_leads_stats(request: HttpRequest) -> HttpResponse:
    """Статистика дожим-лидов."""
    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")

    tz = ZoneInfo("Europe/Moscow")
    now = datetime.now(tz)
    from_day = now.date()
    if now.hour >= 20:
        from_day = now.date() + timedelta(days=1)

    def day_bounds(day):
        start = datetime.combine(day - timedelta(days=1), time(hour=20), tzinfo=tz)
        end = datetime.combine(day, time(hour=20), tzinfo=tz)
        return start.astimezone(dt_utc.utc), end.astimezone(dt_utc.utc)

    _dz = Q(lead_type__slug="dozhim")
    today_start, today_end = day_bounds(from_day)
    yesterday_start, yesterday_end = day_bounds(from_day - timedelta(days=1))

    today_count = Lead.objects.filter(
        _dz, user=user, status=Lead.Status.APPROVED,
        created_at__gte=today_start, created_at__lt=today_end,
    ).count()
    yesterday_count = Lead.objects.filter(
        _dz, user=user, status=Lead.Status.APPROVED,
        created_at__gte=yesterday_start, created_at__lt=yesterday_end,
    ).count()
    total_count = Lead.objects.filter(_dz, user=user, status=Lead.Status.APPROVED).count()

    return render(request, "core/dozhim_leads_stats.html", {
        "today_count": today_count,
        "yesterday_count": yesterday_count,
        "total_count": total_count,
        "dozhim_reward": getattr(settings, "DOZHIM_APPROVE_REWARD", 40),
    })


@login_required
def dozhim_lead_redo(request: HttpRequest, lead_id: int) -> HttpResponse:
    """Доработка дожим-лида."""
    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    lead = get_object_or_404(Lead, pk=lead_id, user=user, lead_type__slug="dozhim", status=Lead.Status.REWORK)

    if request.method == "POST":
        form = DozhimLeadReportForm(request.POST, request.FILES, instance=lead)
        if form.is_valid():
            raw = form.cleaned_data.get("raw_contact") or ""
            if _dozhim_lead_exists(raw, exclude_lead_id=lead.pk):
                messages.error(request, "Такой контакт уже есть в отчётах дожима. Дубликаты не принимаются.")
            else:
                lead = form.save(commit=False)
                lead.raw_contact = raw.strip()
                lead.source = raw.strip()
                lead.normalized_contact = normalize_lead_contact(raw)
                lead.status = Lead.Status.PENDING
                lead.rejection_reason = ""
                lead.save()
                if lead.attachment:
                    ext = _get_attachment_extension(lead.attachment)
                    if ext in LEAD_VIDEO_EXTENSIONS:
                        lid = lead.id
                        def _compress_redo_bg(lid=lid):
                            try:
                                l = Lead.objects.filter(pk=lid).first()
                                if l and l.attachment:
                                    compress_lead_attachment(l)
                            except Exception as e:
                                logger.warning("Компрессия видео (dozhim redo %s): %s", lid, e)
                        _bg_executor.submit(_compress_redo_bg)
                    else:
                        compress_lead_attachment(lead)
                messages.success(request, "Отчёт отправлен на повторную проверку.")
                return redirect("dozhim_leads_my_list")
    else:
        form = DozhimLeadReportForm(instance=lead)

    return render(request, "core/dozhim_lead_redo.html", {"form": form, "lead": lead})


@login_required
def dozhim_download_txt(request: HttpRequest) -> HttpResponse:
    """Скачать выданные лиды для дожима в виде .txt (один контакт на строку)."""
    user = request.user
    if not _ensure_user_approved(request):
        return redirect("dashboard")


    issued = (
        DozhimIssuedLead.objects.filter(user=user)
        .select_related("lead", "lead__lead_type")
        .order_by("-created_at")
    )

    lines = []
    for item in issued:
        raw = item.lead.raw_contact or ""
        if raw.strip():
            lines.append(raw.strip())

    content = "\n".join(lines)
    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="dozhim_contacts.txt"'
    return response

