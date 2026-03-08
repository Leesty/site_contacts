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
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from .forms import BaseRequestForm, LeadReportForm, LeadReworkUserForm, UserRegistrationForm
from .lead_utils import (
    LEAD_VIDEO_EXTENSIONS,
    _get_attachment_extension,
    compress_lead_attachment,
    determine_base_type_for_contact,
    extract_username_from_contact,
    normalize_lead_contact,
)
from django.conf import settings

from .models import BaseType, Contact, ContactRequest, Lead, SupportMessage, SupportThread, User, UserBaseLimit, WithdrawalRequest

logger = logging.getLogger(__name__)

_bg_executor = ThreadPoolExecutor(max_workers=4)


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
        or getattr(user, "role", None) in ("support", "admin")
    )


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
        from django.db.models import Sum
        from .models import LeadReviewLog

        # Всего одобрений лидов (все админы, все времена), кроме лидов партнёрских пользователей
        base_log_qs = LeadReviewLog.objects.filter(
            action=LeadReviewLog.Action.APPROVED,
            lead__user__partner_owner__isnull=True,
        )
        total_approved = base_log_qs.count()
        earned = total_approved * 5
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
            },
        )
    if _is_standalone_admin(user):
        from .models import WorkerReport, WorkerSelfLead, WorkerWithdrawalRequest
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
        return render(request, "core/dashboard_standalone_admin.html", {
            "user": user,
            "pending_worker_reports_count": pending_worker_reports_count,
            "workers_count": workers_count,
            "pending_worker_withdrawals_count": pending_worker_withdrawals_count,
            "pending_worker_self_leads_count": pending_worker_self_leads_count,
        })
    if _is_admin(user):
        pending_count = User.objects.filter(status=User.Status.PENDING).count()
        unread_threads_count = SupportThread.objects.filter(
            Q(last_read_at__isnull=True) | Q(updated_at__gt=F("last_read_at"))
        ).count()
        contact_requests_pending_count = ContactRequest.objects.filter(status="pending").count()
        withdrawal_requests_pending_count = WithdrawalRequest.objects.filter(status="pending").count()
        pending_leads_count = Lead.objects.filter(
            status__in=(Lead.Status.PENDING, Lead.Status.REWORK)
        ).count()
        return render(
            request,
            "core/dashboard_admin.html",
            {
                "user": user,
                "pending_requests_count": pending_count,
                "unread_threads_count": unread_threads_count,
                "contact_requests_pending_count": contact_requests_pending_count,
                "withdrawal_requests_pending_count": withdrawal_requests_pending_count,
                "pending_leads_count": pending_leads_count,
            },
        )
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    balance = getattr(user, "balance", 0) or 0
    withdrawal_pending = WithdrawalRequest.objects.filter(user=user, status="pending").exists()
    can_request_withdrawal = balance >= withdrawal_min and not withdrawal_pending
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
    return render(
        request,
        "core/dashboard.html",
        {
            "user": user,
            "withdrawal_min_balance": withdrawal_min,
            "withdrawal_pending": withdrawal_pending,
            "can_request_withdrawal": can_request_withdrawal,
            "support_has_unread": support_has_unread,
            "rework_leads_count": rework_leads_count,
        },
    )


@login_required
def account_updates_api(request: HttpRequest) -> HttpResponse:
    """JSON API для опроса обновлений: уведомления, баланс, лиды, счётчики админа. Для автообновления без перезагрузки."""
    user = request.user
    data = {
        "support_has_unread": False,
        "balance": getattr(user, "balance", 0) or 0,
        "leads_updated_at": None,
    }
    if _is_admin(user):
        # Счётчики для админ-панели и метка обновления диалогов (для перезагрузки страницы при новом сообщении)
        threads_agg = SupportThread.objects.aggregate(m=Max("updated_at"))
        data["admin"] = {
            "unread_threads_count": SupportThread.objects.filter(
                Q(last_read_at__isnull=True) | Q(updated_at__gt=F("last_read_at"))
            ).count(),
            "pending_requests_count": User.objects.filter(status=User.Status.PENDING).count(),
            "contact_requests_pending_count": ContactRequest.objects.filter(status="pending").count(),
            "withdrawal_requests_pending_count": WithdrawalRequest.objects.filter(status="pending").count(),
            "pending_leads_count": Lead.objects.filter(
                status__in=(Lead.Status.PENDING, Lead.Status.REWORK)
            ).count(),
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
    if getattr(user, "status", None) != "approved":
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
            current = Contact.objects.filter(base_type=selected_base, assigned_to=user).count()
            if current >= total_allowed:
                reason = "already_got"
            else:
                can_give = total_allowed - current
                free_qs = (
                    Contact.objects.select_for_update()
                    .filter(base_type=selected_base, assigned_to__isnull=True, is_active=True)
                    .order_by("id")
                )
                free_count = free_qs.count()
                if free_count < can_give:
                    reason = "not_enough"
                else:
                    now = timezone.now()
                    contacts_to_give = list(free_qs[:can_give])
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
    except (ValueError, paginator.InvalidPage):
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
def request_withdrawal_create(request: HttpRequest) -> HttpResponse:
    """Создать заявку на вывод средств (доступно при балансе >= WITHDRAWAL_MIN_BALANCE).

    GET  — показать форму с указанием реквизитов.
    POST — создать заявку и обнулить баланс.
    """
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    user = request.user
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)

    # Специальная логика для баланс‑админа: баланс считается по логу одобренных лидов.
    if getattr(user, "role", None) == "balance_admin":
        from django.db.models import Sum
        from .models import LeadReviewLog

        total_approved = LeadReviewLog.objects.filter(
            action=LeadReviewLog.Action.APPROVED,
            lead__user__partner_owner__isnull=True,
        ).count()
        earned = total_approved * 5
        withdrawn = (
            WithdrawalRequest.objects.filter(user=user, status__in=("pending", "approved"))
            .aggregate(s=Sum("amount"))
            .get("s")
            or 0
        )
        balance = max(0, earned - withdrawn)
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
                },
            )
        with transaction.atomic():
            user_refresh = User.objects.select_for_update().get(pk=user.pk)
            # Повторно считаем баланс внутри транзакции, чтобы учесть параллельные изменения.
            if getattr(user_refresh, "role", None) == "balance_admin":
                from django.db.models import Sum
                from .models import LeadReviewLog

                total_approved = LeadReviewLog.objects.filter(
                    action=LeadReviewLog.Action.APPROVED,
                    lead__user__partner_owner__isnull=True,
                ).count()
                earned = total_approved * 5
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
            else:
                current_balance = getattr(user_refresh, "balance", 0) or 0
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
                user_refresh.balance = 0
                user_refresh.save(update_fields=["balance"])
        messages.success(
            request,
            f"Заявка на вывод {current_balance} руб. отправлена. {'Баланс обнулён. ' if getattr(user_refresh, 'role', None) != 'balance_admin' else ''}Ожидайте решения администратора.",
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
            "payout_details": "",
        },
    )


@login_required
def request_contact_create(request: HttpRequest) -> HttpResponse:
    """Создать заявку на дополнительный лимит контактов (кнопка «Обратиться»)."""
    if not _ensure_user_approved(request):
        return redirect("dashboard")
    if ContactRequest.objects.filter(user=request.user, status="pending").exists():
        messages.info(request, "У вас уже есть активная заявка. Ожидайте ответа менеджера.")
        return redirect("contacts")
    base_type_id = request.GET.get("base_type") or request.POST.get("base_type")
    base_type = None
    if base_type_id:
        try:
            base_type = BaseType.objects.get(pk=base_type_id)
        except (BaseType.DoesNotExist, ValueError):
            pass
    ContactRequest.objects.create(user=request.user, base_type=base_type, status="pending")
    messages.success(request, "Заявка отправлена. Менеджер свяжется с вами по поводу лимита контактов.")
    return redirect("contacts")


def _lead_exists_globally(raw_contact: str, exclude_lead_id: int | None = None) -> bool:
    """Проверяет, есть ли в базе уже лид с таким контактом (любой пользователь). Комплексная нормализация: @user=user, ссылки и т.д."""
    normalized = normalize_lead_contact(raw_contact)
    if not normalized:
        return False
    try:
        qs = Lead.objects.filter(normalized_contact=normalized)
        if exclude_lead_id is not None:
            qs = qs.exclude(pk=exclude_lead_id)
        if qs.exists():
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
            .select_related("lead_type")
            .order_by("-created_at")
        )
        paginator = Paginator(leads_qs, 30)
        try:
            page_number = int(request.GET.get("page", 1))
        except (TypeError, ValueError):
            page_number = 1
        page_obj = paginator.get_page(page_number)
        lead_approve_reward = getattr(settings, "LEAD_APPROVE_REWARD", 40)
        agg = Lead.objects.filter(user=user).aggregate(m=Max("updated_at"))
        leads_updated_at = agg.get("m").isoformat() if agg.get("m") else ""
        return render(
            request,
            "core/leads_my_list.html",
            {
                "page_obj": page_obj,
                "lead_approve_reward": lead_approve_reward,
                "leads_updated_at": leads_updated_at,
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
                        else:
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

    today_count = (
        Lead.objects.filter(
            user=user,
            status=Lead.Status.APPROVED,
            created_at__gte=today_start,
            created_at__lt=today_end,
        ).count()
    )
    yesterday_count = (
        Lead.objects.filter(
            user=user,
            status=Lead.Status.APPROVED,
            created_at__gte=yesterday_start,
            created_at__lt=yesterday_end,
        ).count()
    )
    total_count = Lead.objects.filter(user=user, status=Lead.Status.APPROVED).count()

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
    """Страница чата с поддержкой: диалог и форма с текстом и вложением. Доступна и до одобрения аккаунта."""
    user = request.user
    thread, _ = SupportThread.objects.get_or_create(user=user, is_closed=False)

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
    """Виджет поддержки: плавающее окно чата. GET — панель, POST — сохранить и вернуть список сообщений. Доступен и до одобрения."""
    user = request.user
    thread, _ = SupportThread.objects.get_or_create(user=user, is_closed=False)

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
        return render(request, "auth/ref_register.html", {"error": "Реферальная ссылка недействительна или устарела.", "code": code})

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

    return render(request, "auth/ref_register.html", {"form": form, "code": code, "standalone_admin": ref_link.standalone_admin})

