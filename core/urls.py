from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from . import views_partner
from . import views_search
from . import views_support_admin
from . import views_worker


urlpatterns = [
    path("health/", views.health_check, name="health_check"),
    path("", views.index, name="index"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("account/updates/", views.account_updates_api, name="account_updates_api"),
    path("register/", views.register, name="register"),
    path("ref/<str:code>/", views.ref_register, name="ref_register"),
    # Partner cabinet
    path("partner/", views_partner.partner_dashboard, name="partner_dashboard"),
    path("partner/referrals/", views_partner.partner_referrals, name="partner_referrals"),
    path("partner/withdrawal/", views_partner.partner_withdrawal, name="partner_withdrawal"),
    path("partner/links/create/", views_partner.partner_create_link, name="partner_create_link"),
    path("partner/links/<int:link_id>/toggle/", views_partner.partner_toggle_link, name="partner_toggle_link"),
    path("p/<str:code>/", views_partner.partner_ref_register, name="partner_ref_register"),
    # Partner dozhim lead review
    path("partner/dozhim/leads/", views_partner.partner_dozhim_leads, name="partner_dozhim_leads"),
    path("partner/dozhim/leads/<int:lead_id>/approve/", views_partner.partner_dozhim_lead_approve, name="partner_dozhim_lead_approve"),
    path("partner/dozhim/leads/<int:lead_id>/reject/", views_partner.partner_dozhim_lead_reject, name="partner_dozhim_lead_reject"),
    path("partner/dozhim/leads/<int:lead_id>/rework/", views_partner.partner_dozhim_lead_rework, name="partner_dozhim_lead_rework"),
    path("partner/dozhim/leads/<int:lead_id>/attachment/", views_partner.partner_dozhim_lead_attachment, name="partner_dozhim_lead_attachment"),
    # Referral system (native for all users)
    path("referrals/", views_partner.user_referrals, name="user_referrals"),
    path("referrals/list/", views_partner.user_referral_list, name="user_referral_list"),
    path("referrals/create/", views_partner.user_referral_create_link, name="user_referral_create_link"),
    path("referrals/<int:link_id>/toggle/", views_partner.user_referral_toggle_link, name="user_referral_toggle_link"),
    path("a/<str:code>/", views_partner.referral_ref_register, name="referral_ref_register"),
    # Dozhim department
    path("department/switch/", views.switch_department, name="switch_department"),
    path("dozhim/contacts/", views.dozhim_contacts, name="dozhim_contacts"),
    path("dozhim/contacts/download/", views.dozhim_download_txt, name="dozhim_download_txt"),
    path("dozhim/leads/report/", views.dozhim_leads_report, name="dozhim_leads_report"),
    path("dozhim/leads/my/", views.dozhim_leads_my_list, name="dozhim_leads_my_list"),
    path("dozhim/leads/<int:lead_id>/redo/", views.dozhim_lead_redo, name="dozhim_lead_redo"),
    path("dozhim/leads/stats/", views.dozhim_leads_stats, name="dozhim_leads_stats"),
    # Worker sub-system
    path("worker/", views_worker.worker_dashboard, name="worker_dashboard"),
    path("worker/tasks/", views_worker.worker_tasks, name="worker_tasks"),
    path("worker/available-leads/", views_worker.worker_available_leads, name="worker_available_leads"),
    path("worker/available-leads/<int:lead_id>/claim/", views_worker.worker_claim_lead, name="worker_claim_lead"),
    path("worker/tasks/<int:assignment_id>/cancel/", views_worker.worker_cancel_assignment, name="worker_cancel_assignment"),
    path("worker/tasks/<int:assignment_id>/refused/", views_worker.worker_mark_refused, name="worker_mark_refused"),
    path("worker/tasks/<int:assignment_id>/", views_worker.worker_task_detail, name="worker_task_detail"),
    path("worker/tasks/<int:assignment_id>/report/redo/", views_worker.worker_report_redo, name="worker_report_redo"),
    path("worker/tasks/<int:assignment_id>/attachment/", views_worker.worker_report_attachment, name="worker_report_attachment"),
    path("worker/tasks/<int:assignment_id>/lead-attachment/", views_worker.worker_lead_attachment, name="worker_lead_attachment"),
    path("worker/balance/withdraw/", views_worker.worker_request_withdrawal, name="worker_request_withdrawal"),
    # Worker self-leads
    path("worker/my-leads/", views_worker.worker_self_leads, name="worker_self_leads"),
    path("worker/my-leads/new/", views_worker.worker_self_lead_create, name="worker_self_lead_create"),
    path("worker/my-leads/<int:self_lead_id>/edit/", views_worker.worker_self_lead_edit, name="worker_self_lead_edit"),
    path("worker/my-leads/<int:self_lead_id>/redo/", views_worker.worker_self_lead_redo, name="worker_self_lead_redo"),
    path("worker/my-leads/<int:self_lead_id>/attachment/", views_worker.worker_self_lead_attachment, name="worker_self_lead_attachment"),
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
    path("balance/smz/", views.smz_registration, name="smz_registration"),
    path("balance/withdraw/", views.request_withdrawal_create, name="request_withdrawal_create"),
    path("balance/withdraw/<int:wr_id>/receipt/", views.receipt_upload, name="receipt_upload"),
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
        "balance-admin/contact-requests/",
        views_support_admin.balance_admin_contact_requests,
        name="balance_admin_contact_requests",
    ),
    path(
        "staff/withdrawal-requests/",
        views_support_admin.admin_withdrawal_requests,
        name="admin_withdrawal_requests",
    ),
    path("staff/smz-requests/", views_support_admin.admin_smz_requests, name="admin_smz_requests"),
    path("staff/receipts/", views_support_admin.admin_receipts, name="admin_receipts"),
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
        "staff/reset-password/",
        views_support_admin.admin_reset_password,
        name="admin_reset_password",
    ),
    path(
        "staff/admin-earnings/",
        views_support_admin.admin_earnings_stats,
        name="admin_earnings_stats",
    ),
    path(
        "staff/standalone/reset-password/",
        views_support_admin.standalone_admin_reset_password,
        name="standalone_admin_reset_password",
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
        "staff/standalone/worker-withdrawals/debug/",
        views_support_admin.standalone_admin_worker_withdrawal_debug,
        name="standalone_admin_worker_withdrawal_debug",
    ),
    path(
        "staff/standalone/worker-self-leads/",
        views_support_admin.standalone_admin_worker_self_leads,
        name="standalone_admin_worker_self_leads",
    ),
    path(
        "staff/standalone/worker-self-leads/<int:self_lead_id>/approve/",
        views_support_admin.standalone_admin_worker_self_lead_approve,
        name="standalone_admin_worker_self_lead_approve",
    ),
    path(
        "staff/standalone/worker-self-leads/<int:self_lead_id>/reject/",
        views_support_admin.standalone_admin_worker_self_lead_reject,
        name="standalone_admin_worker_self_lead_reject",
    ),
    path(
        "staff/standalone/worker-self-leads/<int:self_lead_id>/rework/",
        views_support_admin.standalone_admin_worker_self_lead_rework,
        name="standalone_admin_worker_self_lead_rework",
    ),
    path(
        "staff/standalone/worker-self-leads/<int:self_lead_id>/attachment/",
        views_support_admin.standalone_admin_worker_self_lead_attachment,
        name="standalone_admin_worker_self_lead_attachment",
    ),
    path(
        "staff/standalone/refused/",
        views_support_admin.standalone_admin_refused,
        name="standalone_admin_refused",
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
    # SearchLink system
    path("s/<str:code>/", views_search.search_link_landing, name="search_link_landing"),
    path("search/links/", views_search.search_links_my, name="search_links_my"),
    path("search/links/create/", views_search.search_link_create, name="search_link_create"),
    path("search/links/<str:code>/report/", views_search.search_report_create, name="search_report_create"),
    path("search/links/<str:code>/report/redo/", views_search.search_report_redo, name="search_report_redo"),
    path("api/search-bot-start/", views_search.search_bot_start_webhook, name="search_bot_start_webhook"),
    path("staff/search-reports/", views_search.admin_search_reports_list, name="admin_search_reports_list"),
    path("staff/search-reports/<int:report_id>/approve/", views_search.admin_search_report_approve, name="admin_search_report_approve"),
    path("staff/search-reports/<int:report_id>/reject/", views_search.admin_search_report_reject, name="admin_search_report_reject"),
    path("staff/search-reports/<int:report_id>/rework/", views_search.admin_search_report_rework, name="admin_search_report_rework"),
    path("staff/search-reports/<int:report_id>/attachment/", views_search.admin_search_report_attachment, name="admin_search_report_attachment"),
    # Ban/unban
    path("staff/users/<int:user_id>/toggle-ban/", views_support_admin.admin_toggle_ban, name="admin_toggle_ban"),
    path("staff/users/<int:user_id>/toggle-accredited/", views_support_admin.admin_toggle_accredited, name="admin_toggle_accredited"),
    # Balance admin: payment
    path("staff/payment/", views_support_admin.balance_admin_payment_list, name="balance_admin_payment_list"),
    path("staff/payment/<int:user_id>/", views_support_admin.balance_admin_payment_detail, name="balance_admin_payment_detail"),
    path("staff/payment/<int:user_id>/multiply/", views_support_admin.balance_admin_payment_multiply, name="balance_admin_payment_multiply"),
    path("staff/payment/<int:user_id>/subtract/", views_support_admin.balance_admin_payment_subtract, name="balance_admin_payment_subtract"),
    path("staff/payment/<int:user_id>/revert/", views_support_admin.balance_admin_payment_revert, name="balance_admin_payment_revert"),
]

