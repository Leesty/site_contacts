"""Представления для исполнителей (воркеров): задания, отчёты, вывод средств.

Доступно только пользователям с ролью «worker». Полностью изолировано от основного сайта.
"""
import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import WorkerReportForm, WorkerReportReworkForm
from .models import LeadAssignment, User, WithdrawalRequest, WorkerReport, WorkerWithdrawalRequest

logger = logging.getLogger(__name__)


def _require_worker(request: HttpRequest) -> bool:
    """Только роль «worker»."""
    return getattr(request.user, "role", None) == "worker"


@login_required
def worker_dashboard(request: HttpRequest) -> HttpResponse:
    """Дашборд исполнителя: статистика задач, баланс."""
    if not _require_worker(request):
        return HttpResponseForbidden("Только для исполнителей.")
    user = request.user
    total_tasks = LeadAssignment.objects.filter(worker=user).count()
    rework_count = WorkerReport.objects.filter(worker=user, status=WorkerReport.Status.REWORK).count()
    pending_count = WorkerReport.objects.filter(worker=user, status=WorkerReport.Status.PENDING).count()
    approved_count = WorkerReport.objects.filter(worker=user, status=WorkerReport.Status.APPROVED).count()
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    balance = getattr(user, "balance", 0) or 0
    withdrawal_pending = WorkerWithdrawalRequest.objects.filter(worker=user, status="pending").exists()
    can_request_withdrawal = balance >= withdrawal_min and not withdrawal_pending
    return render(request, "worker/dashboard.html", {
        "user": user,
        "total_tasks": total_tasks,
        "rework_count": rework_count,
        "pending_count": pending_count,
        "approved_count": approved_count,
        "balance": balance,
        "withdrawal_min_balance": withdrawal_min,
        "withdrawal_pending": withdrawal_pending,
        "can_request_withdrawal": can_request_withdrawal,
    })


@login_required
def worker_tasks(request: HttpRequest) -> HttpResponse:
    """Список назначенных задач исполнителя."""
    if not _require_worker(request):
        return HttpResponseForbidden("Только для исполнителей.")
    user = request.user
    assignments = list(
        LeadAssignment.objects.filter(worker=user)
        .select_related("lead", "lead__lead_type")
        .order_by("-created_at")
    )
    # Annotate each assignment with a safe report status to avoid RelatedObjectDoesNotExist in templates
    for a in assignments:
        try:
            a.report_obj = a.report
        except WorkerReport.DoesNotExist:
            a.report_obj = None
    return render(request, "worker/tasks.html", {"assignments": assignments})


@login_required
def worker_task_detail(request: HttpRequest, assignment_id: int) -> HttpResponse:
    """Детальная страница задания: контакт, описание задачи, форма отчёта (если ещё не отправлен)."""
    if not _require_worker(request):
        return HttpResponseForbidden("Только для исполнителей.")
    user = request.user
    assignment = get_object_or_404(LeadAssignment, pk=assignment_id, worker=user)
    lead = assignment.lead

    existing_report = getattr(assignment, "report", None)
    try:
        existing_report = assignment.report
    except WorkerReport.DoesNotExist:
        existing_report = None

    if request.method == "POST" and existing_report is None:
        form = WorkerReportForm(request.POST, request.FILES)
        if form.is_valid():
            standalone_admin = user.standalone_admin_owner
            if not standalone_admin:
                messages.error(request, "Ошибка: не найден ваш самостоятельный админ. Обратитесь за помощью.")
                return redirect("worker_tasks")
            try:
                report = WorkerReport(
                    assignment=assignment,
                    worker=user,
                    standalone_admin=standalone_admin,
                    raw_contact=form.cleaned_data["raw_contact"].strip(),
                    comment=form.cleaned_data.get("comment") or "",
                    status=WorkerReport.Status.PENDING,
                )
                if form.cleaned_data.get("attachment"):
                    report.attachment = form.cleaned_data["attachment"]
                report.save()
                messages.success(request, "Отчёт отправлен на проверку.")
                return redirect("worker_tasks")
            except Exception as e:
                logger.exception("Ошибка при сохранении отчёта воркера: %s", e)
                messages.error(request, "Не удалось сохранить отчёт. Попробуйте ещё раз.")
    else:
        form = WorkerReportForm()

    return render(request, "worker/task_detail.html", {
        "assignment": assignment,
        "lead": lead,
        "form": form,
        "existing_report": existing_report,
    })


@login_required
def worker_report_redo(request: HttpRequest, assignment_id: int) -> HttpResponse:
    """Доработка отчёта исполнителем (только если статус «На доработке»)."""
    if not _require_worker(request):
        return HttpResponseForbidden("Только для исполнителей.")
    user = request.user
    assignment = get_object_or_404(LeadAssignment, pk=assignment_id, worker=user)
    try:
        report = assignment.report
    except WorkerReport.DoesNotExist:
        messages.warning(request, "Отчёт не найден.")
        return redirect("worker_tasks")

    if report.status != WorkerReport.Status.REWORK:
        messages.warning(request, "Этот отчёт не требует доработки.")
        return redirect("worker_tasks")

    if request.method == "POST":
        form = WorkerReportReworkForm(request.POST, request.FILES)
        if form.is_valid():
            report.raw_contact = form.cleaned_data["raw_contact"].strip()
            report.comment = form.cleaned_data.get("comment") or ""
            report.status = WorkerReport.Status.PENDING
            report.rework_comment = ""
            update_fields = ["raw_contact", "comment", "status", "rework_comment", "updated_at"]
            if form.cleaned_data.get("attachment"):
                report.attachment = form.cleaned_data["attachment"]
                update_fields.append("attachment")
            report.save(update_fields=update_fields)
            messages.success(request, "Отчёт отправлен на повторную проверку.")
            return redirect("worker_tasks")
    else:
        form = WorkerReportReworkForm(initial={
            "raw_contact": report.raw_contact,
            "comment": report.comment,
        })

    return render(request, "worker/report_redo.html", {
        "form": form,
        "report": report,
        "assignment": assignment,
    })


@login_required
def worker_request_withdrawal(request: HttpRequest) -> HttpResponse:
    """Создать заявку на вывод средств (для воркеров — обрабатывается их самостоятельным админом)."""
    if not _require_worker(request):
        return HttpResponseForbidden("Только для исполнителей.")
    user = request.user
    withdrawal_min = getattr(settings, "WITHDRAWAL_MIN_BALANCE", 500)
    balance = getattr(user, "balance", 0) or 0
    if balance < withdrawal_min:
        messages.warning(request, f"Заявка на вывод доступна при балансе от {withdrawal_min} руб.")
        return redirect("worker_dashboard")
    if WorkerWithdrawalRequest.objects.filter(worker=user, status="pending").exists():
        messages.info(request, "У вас уже есть заявка на вывод на рассмотрении.")
        return redirect("worker_dashboard")

    if request.method == "POST":
        payout_details = (request.POST.get("payout_details") or "").strip()
        if not payout_details:
            messages.error(request, "Укажите реквизиты для вывода.")
            return render(request, "worker/withdrawal.html", {
                "user": user, "balance": balance, "withdrawal_min_balance": withdrawal_min,
            })
        standalone_admin = user.standalone_admin_owner
        if not standalone_admin:
            messages.error(request, "Ошибка: не найден ваш самостоятельный админ.")
            return redirect("worker_dashboard")
        with transaction.atomic():
            user_refresh = User.objects.select_for_update().get(pk=user.pk)
            current_balance = getattr(user_refresh, "balance", 0) or 0
            if WorkerWithdrawalRequest.objects.filter(worker=user_refresh, status="pending").exists():
                messages.info(request, "У вас уже есть заявка на вывод на рассмотрении.")
                return redirect("worker_dashboard")
            if current_balance < withdrawal_min:
                messages.warning(request, f"Заявка на вывод доступна при балансе от {withdrawal_min} руб.")
                return redirect("worker_dashboard")
            WorkerWithdrawalRequest.objects.create(
                worker=user_refresh,
                standalone_admin=standalone_admin,
                amount=current_balance,
                payout_details=payout_details,
                status="pending",
            )
            user_refresh.balance = 0
            user_refresh.save(update_fields=["balance"])
        messages.success(request, f"Заявка на вывод {current_balance} руб. отправлена. Баланс обнулён.")
        return redirect("worker_dashboard")

    return render(request, "worker/withdrawal.html", {
        "user": user,
        "balance": balance,
        "withdrawal_min_balance": withdrawal_min,
    })


@login_required
def worker_lead_attachment(request: HttpRequest, assignment_id: int) -> HttpResponse:
    """Отдаёт вложение лида исполнителю (только если лид назначен этому воркеру)."""
    if not _require_worker(request):
        return HttpResponseForbidden("Только для исполнителей.")
    assignment = get_object_or_404(LeadAssignment, pk=assignment_id, worker=request.user)
    lead = assignment.lead
    if not lead.attachment:
        return HttpResponseForbidden("У этого лида нет вложения.")
    from .views_support_admin import _serve_lead_attachment as _serve
    return _serve(lead)


@login_required
def worker_report_attachment(request: HttpRequest, assignment_id: int) -> HttpResponse:
    """Отдаёт вложение к отчёту воркера (только для владельца отчёта)."""
    if not _require_worker(request):
        return HttpResponseForbidden("Только для исполнителей.")
    assignment = get_object_or_404(LeadAssignment, pk=assignment_id, worker=request.user)
    try:
        report = assignment.report
    except WorkerReport.DoesNotExist:
        return HttpResponseForbidden("Отчёт не найден.")
    if not report.attachment:
        return HttpResponseForbidden("Вложение отсутствует.")
    from .views_support_admin import _serve_lead_attachment as _serve
    # Adapt: create a dummy object with .attachment attribute
    class _FakeLeadLike:
        pass
    obj = _FakeLeadLike()
    obj.attachment = report.attachment
    obj.user = request.user
    obj.pk = report.pk
    return _serve(obj)
