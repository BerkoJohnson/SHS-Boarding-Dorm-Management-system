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
        "verify/student/<uuid:token>/photo/",
        views.public_student_photo_view,
        name="public_student_photo",
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
    path("search/", views.global_search_view, name="global_search"),
    path("reports/", views.reports_dashboard_view, name="reports"),
    path("reports/print/", views.reports_print_view, name="reports_print"),
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
