from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("login/", views.StaffLoginView.as_view(), name="login"),
    path("", views.dashboard_view, name="dashboard"),
    path("exeats/", views.exeat_list_view, name="exeat_list"),
    path(
        "exeats/pending-approvals/",
        views.exeat_approvals_hub_view,
        name="exeat_approvals",
    ),
    path("exeats/new/", views.exeat_create_view, name="exeat_create"),
    path("exeats/<uuid:pk>/", views.exeat_detail_view, name="exeat_detail"),
    path(
        "exeats/<uuid:pk>/review/",
        views.exeat_approval_review_view,
        name="exeat_review",
    ),
    path(
        "exeats/<uuid:pk>/action/<str:action>/",
        views.exeat_action_view,
        name="exeat_action",
    ),
    path(
        "exeats/<uuid:pk>/reconcile/",
        views.exeat_reconcile_view,
        name="exeat_reconcile",
    ),
    path(
        "exeats/<uuid:pk>/reschedule/",
        views.exeat_reschedule_view,
        name="exeat_reschedule",
    ),
    path(
        "exeats/<uuid:pk>/override/",
        views.exeat_override_view,
        name="exeat_override",
    ),
    path(
        "exeats/reviews/",
        views.exeat_review_queue_view,
        name="exeat_review_queue",
    ),
    path(
        "exeats/reviews/<uuid:review_pk>/",
        views.exeat_review_decision_view,
        name="exeat_review_decision",
    ),
    path("exeats/search-students/", views.student_search_view, name="student_search"),
    path("houses/", views.house_list_view, name="house_list"),
    path("students/", views.student_registry_view, name="student_registry"),
    path("students/new/", views.student_registration_view, name="student_create"),
    path("students/promotion/", views.student_promotion_view, name="student_promotion"),
    path(
        "students/registration-options/",
        views.student_registration_options_view,
        name="student_registration_options",
    ),
    path("students/import/", views.student_roster_import_view, name="student_import"),
    path(
        "students/import/template.csv",
        views.student_import_template_view,
        name="student_import_template",
    ),
    path(
        "verify/student/<uuid:token>/",
        views.public_student_exeat_view,
        name="public_student_exeat",
    ),
    path(
        "students/<uuid:student_pk>/photo/",
        views.student_photo_view,
        name="student_photo",
    ),
    path(
        "students/<uuid:student_pk>/", views.student_record_view, name="student_record"
    ),
    path(
        "students/<uuid:student_pk>/id-card/",
        views.student_id_card_view,
        name="student_id_card",
    ),
    path(
        "students/<uuid:student_pk>/id-card/replace-qr/",
        views.student_rotate_public_token_view,
        name="student_rotate_public_token",
    ),
    path(
        "students/<uuid:student_pk>/edit/",
        views.student_registration_view,
        name="student_edit",
    ),
    path("conduct/", views.conduct_list_view, name="conduct_list"),
    path("conduct/new/", views.incident_create_view, name="incident_create"),
    path(
        "conduct/<uuid:pk>/detail/", views.incident_detail_view, name="incident_detail"
    ),
    path(
        "operations/roll-call/",
        views.roll_call_sessions_view,
        name="roll_call_sessions",
    ),
    path("operations/gate/", views.gate_security_view, name="gate_security"),
    path(
        "operations/roll-call/new/",
        views.roll_call_create_session_view,
        name="roll_call_create",
    ),
    path(
        "operations/roll-call/<uuid:session_pk>/complete/",
        views.roll_call_complete_session_view,
        name="roll_call_complete",
    ),
    path(
        "operations/roll-call/<uuid:session_pk>/delete/",
        views.roll_call_delete_session_view,
        name="roll_call_delete",
    ),
    path(
        "operations/roll-call/notifications/<uuid:notification_pk>/read/",
        views.roll_call_notification_read_view,
        name="roll_call_notification_read",
    ),
    path(
        "operations/roll-call/<uuid:session_pk>/mark-all-present/",
        views.roll_call_mark_all_present_view,
        name="roll_call_mark_all_present",
    ),
    path(
        "operations/roll-call/entry/<uuid:entry_pk>/",
        views.roll_call_update_entry_view,
        name="roll_call_update_entry",
    ),
    path(
        "operations/roll-call/absence-follow-up/<uuid:follow_up_pk>/",
        views.roll_call_resolve_absence_follow_up_view,
        name="roll_call_resolve_absence_follow_up",
    ),
    path(
        "operations/gate/<uuid:exeat_pk>/<str:action>/",
        views.gate_terminal_action_view,
        name="gate_terminal_action",
    ),
    path(
        "operations/gate/verify/", views.gate_verify_pass_view, name="gate_verify_pass"
    ),
    path(
        "operations/gate-duty/",
        views.gate_duty_assignments_view,
        name="gate_duty_assignments",
    ),
    path(
        "operations/gate-duty/<uuid:assignment_pk>/cancel/",
        views.gate_duty_cancel_view,
        name="gate_duty_cancel",
    ),
    path(
        "operations/late-returns/",
        views.late_return_incidents_view,
        name="late_return_incidents",
    ),
    path(
        "operations/late-returns/<uuid:incident_pk>/",
        views.late_return_incident_resolve_view,
        name="late_return_incident_resolve",
    ),
    path(
        "operations/roll-call/corrections/",
        views.roll_call_corrections_view,
        name="roll_call_corrections",
    ),
    path(
        "operations/roll-call/entry/<uuid:entry_pk>/correction/",
        views.roll_call_correction_request_view,
        name="roll_call_correction_request",
    ),
    path(
        "operations/roll-call/corrections/<uuid:correction_pk>/",
        views.roll_call_correction_review_view,
        name="roll_call_correction_review",
    ),
    path(
        "operations/capacity-exceptions/",
        views.dormitory_capacity_exceptions_view,
        name="dormitory_capacity_exceptions",
    ),
    path(
        "operations/settings/",
        views.operational_settings_view,
        name="operational_settings",
    ),
    path("notifications/", views.notifications_view, name="notifications"),
    path(
        "notifications/<uuid:notification_pk>/read/",
        views.notification_read_view,
        name="notification_read",
    ),
    path("disputes/", views.dispute_cases_view, name="dispute_cases"),
    path("disputes/new/", views.dispute_create_view, name="dispute_create"),
    path("disputes/<uuid:pk>/", views.dispute_detail_view, name="dispute_detail"),
    path("search/", views.global_search_view, name="global_search"),
    path("reports/", views.reports_dashboard_view, name="reports"),
    path("reports/print/", views.reports_print_view, name="reports_print"),
    path("reports/export/", views.report_export_view, name="report_export"),
    path(
        "reports/scheduled-email/",
        views.scheduled_report_emails_view,
        name="scheduled_report_emails",
    ),
    path("reports/boarding-kpi/", views.boarding_kpi_view, name="boarding_kpi"),
    path(
        "reports/boarding-kpi/export/",
        views.boarding_kpi_export_view,
        name="boarding_kpi_export",
    ),
    path(
        "reports/demographics/",
        views.boarding_demographics_view,
        name="boarding_demographics",
    ),
    path("reports/sms-usage/", views.sms_usage_view, name="sms_usage"),
    path("reports/sms-group/", views.group_sms_view, name="group_sms"),
    path("reports/config-audit/", views.config_audit_view, name="config_audit"),
    path("configuration/", views.configuration_hub_view, name="configuration"),
    path(
        "configuration/term/<uuid:term_id>/set-active/",
        views.set_active_term_view,
        name="set_active_term",
    ),
    path(
        "configuration/sms/set-enabled/",
        views.set_sms_enabled_view,
        name="set_sms_enabled",
    ),
    path(
        "configuration/student-id/export/",
        views.create_student_id_export_view,
        name="student_id_export_create",
    ),
    path(
        "configuration/student-id/export/<uuid:export_id>/download/",
        views.download_student_id_export_view,
        name="student_id_export_download",
    ),
    path("logout/", views.logout_view, name="logout"),
]
