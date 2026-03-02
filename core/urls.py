from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from . import views_support_admin
from . import views_worker


urlpatterns = [
    path("health/", views.health_check, name="health_check"),
    path("", views.index, name="index"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("account/updates/", views.account_updates_api, name="account_updates_api"),
    path("register/", views.register, name="register"),
    path("ref/<str:code>/", views.ref_register, name="ref_register"),
    # Worker sub-system
    path("worker/", views_worker.worker_dashboard, name="worker_dashboard"),
    path("worker/tasks/", views_worker.worker_tasks, name="worker_tasks"),
    path("worker/tasks/<int:assignment_id>/", views_worker.worker_task_detail, name="worker_task_detail"),
    path("worker/tasks/<int:assignment_id>/report/redo/", views_worker.worker_report_redo, name="worker_report_redo"),
    path("worker/tasks/<int:assignment_id>/attachment/", views_worker.worker_report_attachment, name="worker_report_attachment"),
    path("worker/tasks/<int:assignment_id>/lead-attachment/", views_worker.worker_lead_attachment, name="worker_lead_attachment"),
    path("worker/balance/withdraw/", views_worker.worker_request_withdrawal, name="worker_request_withdrawal"),
    path(
        "login/",
        auth_views.LoginView.as_view(template_name="auth/login.html"),
        name="login",
    ),
    path("logout/", views.logout_view, name="logout"),
    # Основные разделы кабинета
    path("contacts/", views.contacts_placeholder, name="contacts"),
    path("contacts/view/", views.contacts_view, name="contacts_view"),
    path("contacts/download/", views.download_my_contacts_txt, name="download_my_contacts_txt"),
    path("contacts/request/", views.request_contact_create, name="request_contact_create"),
    path("balance/withdraw/", views.request_withdrawal_create, name="request_withdrawal_create"),
    path("leads/report/", views.leads_report_placeholder, name="leads_report"),
    path("leads/my/", views.leads_my_list, name="leads_my_list"),
    path("leads/<int:lead_id>/redo/", views.lead_redo, name="lead_redo"),
    path("leads/stats/", views.leads_stats_placeholder, name="leads_stats"),
    path("support/", views.support_placeholder, name="support"),
    path("support/widget/", views.support_widget, name="support_widget"),
    # Разделы для поддержки/админов (отдельный staff-префикс, чтобы не конфликтовать с /admin/ Django)
    path(
        "staff/users/pending/",
        views_support_admin.admin_users_pending,
        name="admin_users_pending",
    ),
    path(
        "staff/contact-requests/",
        views_support_admin.admin_contact_requests,
        name="admin_contact_requests",
    ),
    path(
        "staff/withdrawal-requests/",
        views_support_admin.admin_withdrawal_requests,
        name="admin_withdrawal_requests",
    ),
    path(
        "staff/users/",
        views_support_admin.admin_all_users,
        name="admin_all_users",
    ),
    path(
        "staff/users/search/",
        views_support_admin.admin_user_search,
        name="admin_user_search",
    ),
    path(
        "staff/users/<int:user_id>/leads-stats/",
        views_support_admin.admin_user_lead_stats,
        name="admin_user_lead_stats",
    ),
    path(
        "staff/leads/new/",
        views_support_admin.admin_leads_all_new,
        name="admin_leads_all_new",
    ),
    path(
        "staff/media-status/",
        views_support_admin.admin_media_storage_status,
        name="admin_media_storage_status",
    ),
    path(
        "staff/site-settings/",
        views_support_admin.admin_site_settings,
        name="admin_site_settings",
    ),
    path(
        "staff/standalone/ss-leads/",
        views_support_admin.standalone_admin_ss_leads,
        name="standalone_admin_ss_leads",
    ),
    path(
        "staff/standalone/ref-links/",
        views_support_admin.standalone_admin_ref_links,
        name="standalone_admin_ref_links",
    ),
    path(
        "staff/standalone/workers/",
        views_support_admin.standalone_admin_workers,
        name="standalone_admin_workers",
    ),
    path(
        "staff/standalone/leads/<int:lead_id>/assign/",
        views_support_admin.standalone_admin_assign_lead,
        name="standalone_admin_assign_lead",
    ),
    path(
        "staff/standalone/reports/",
        views_support_admin.standalone_admin_worker_reports,
        name="standalone_admin_worker_reports",
    ),
    path(
        "staff/standalone/reports/<int:report_id>/approve/",
        views_support_admin.standalone_admin_report_approve,
        name="standalone_admin_report_approve",
    ),
    path(
        "staff/standalone/reports/<int:report_id>/reject/",
        views_support_admin.standalone_admin_report_reject,
        name="standalone_admin_report_reject",
    ),
    path(
        "staff/standalone/reports/<int:report_id>/rework/",
        views_support_admin.standalone_admin_report_rework,
        name="standalone_admin_report_rework",
    ),
    path(
        "staff/standalone/reports/<int:report_id>/attachment/",
        views_support_admin.standalone_admin_worker_report_attachment,
        name="standalone_admin_worker_report_attachment",
    ),
    path(
        "staff/standalone/worker-withdrawals/",
        views_support_admin.standalone_admin_worker_withdrawal_requests,
        name="standalone_admin_worker_withdrawal_requests",
    ),
    path(
        "staff/standalone/leads/<int:lead_id>/attachment/",
        views_support_admin.standalone_admin_lead_attachment,
        name="standalone_admin_lead_attachment",
    ),
    path(
        "staff/users/<int:user_id>/leads/",
        views_support_admin.admin_user_leads_list,
        name="admin_user_leads_list",
    ),
    path(
        "staff/users/<int:user_id>/leads/<int:lead_id>/approve/",
        views_support_admin.admin_lead_approve,
        name="admin_lead_approve",
    ),
    path(
        "staff/users/<int:user_id>/leads/<int:lead_id>/reject/",
        views_support_admin.admin_lead_reject,
        name="admin_lead_reject",
    ),
    path(
        "staff/users/<int:user_id>/leads/<int:lead_id>/rework/",
        views_support_admin.admin_lead_rework,
        name="admin_lead_rework",
    ),
    path(
        "staff/users/<int:user_id>/leads/<int:lead_id>/attachment/",
        views_support_admin.admin_lead_attachment,
        name="admin_lead_attachment",
    ),
    path(
        "staff/users/<int:user_id>/leads-export/<slug:period>/",
        views_support_admin.admin_user_leads_export,
        name="admin_user_leads_export",
    ),
    path(
        "staff/users/<int:user_id>/limits/",
        views_support_admin.admin_user_limits,
        name="admin_user_limits",
    ),
    path(
        "staff/users/<int:user_id>/balance/",
        views_support_admin.admin_user_balance,
        name="admin_user_balance",
    ),
    path(
        "support/threads/by-user/<int:user_id>/",
        views_support_admin.support_thread_by_user,
        name="support_thread_by_user",
    ),
    path(
        "support/threads/",
        views_support_admin.support_threads_list,
        name="support_threads_list",
    ),
    path(
        "support/threads/<int:pk>/",
        views_support_admin.support_thread_detail,
        name="support_thread_detail",
    ),
    path(
        "support/threads/<int:pk>/delete/",
        views_support_admin.support_thread_delete,
        name="support_thread_delete",
    ),
    path(
        "support/messages/<int:pk>/delete/",
        views_support_admin.support_message_delete,
        name="support_message_delete",
    ),
    path(
        "staff/stats/",
        views_support_admin.admin_stats,
        name="admin_stats",
    ),
    path(
        "staff/bases/",
        views_support_admin.bases_excel,
        name="bases_excel",
    ),
    path(
        "staff/bases/upload/",
        views_support_admin.upload_bases_excel,
        name="upload_bases_excel",
    ),
    path(
        "staff/bases/download/",
        views_support_admin.download_bases_excel,
        name="download_bases_excel",
    ),
    path(
        "staff/bases/download/<int:base_type_id>/",
        views_support_admin.download_bases_excel_category,
        name="download_bases_excel_category",
    ),
    path(
        "staff/leads/download/",
        views_support_admin.download_leads_excel,
        name="download_leads_excel",
    ),
]

