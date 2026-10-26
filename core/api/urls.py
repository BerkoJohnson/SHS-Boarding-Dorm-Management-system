"""URL map for the versioned mobile API (mounted at ``/api/v1/``)."""
from django.urls import path, re_path

from core.api.views import (
    auth,
    dashboard,
    devices,
    exeats,
    gate,
    meta,
    notifications,
    reports,
    roll_call,
    sms,
    students,
)

app_name = "core_api"

urlpatterns = [
    # Authentication and session lifecycle
    path("auth/login/", auth.login, name="login"),
    path("auth/refresh/", auth.refresh, name="refresh"),
    path("auth/logout/", auth.logout, name="logout"),
    path("auth/logout-all/", auth.logout_all, name="logout_all"),
    path("auth/me/", auth.me, name="me"),
    path("auth/password/", auth.change_password, name="change_password"),
    path("auth/devices/", auth.devices, name="devices"),
    path(
        "auth/devices/<uuid:token_id>/",
        auth.revoke_device,
        name="revoke_device",
    ),
    # Staff onboarding
    path("auth/invites/validate/", auth.validate_invite, name="validate_invite"),
    path("auth/invites/redeem/", auth.redeem_invite, name="redeem_invite"),
    path("staff/invites/", auth.staff_invites, name="staff_invites"),
    path(
        "staff/invites/<uuid:invite_id>/",
        auth.revoke_invite,
        name="revoke_invite",
    ),
    # Bootstrap and reference data
    path("meta/", meta.meta, name="meta"),
    path("reference/", meta.reference, name="reference"),
    # Students
    path("students/", students.students, name="students"),
    path(
        "students/<uuid:student_id>/allocation/",
        students.update_allocation,
        name="student_allocation",
    ),
    path("students/search/", students.student_search, name="student_search"),
    path("students/lookup/", students.student_lookup, name="student_lookup"),
    path("students/options/", students.student_options, name="student_options"),
    path(
        "students/allocation-options/",
        students.allocation_options,
        name="student_allocation_options",
    ),
    path(
        "students/<uuid:student_id>/",
        students.student_detail_view,
        name="student_detail",
    ),
    path(
        "students/<uuid:student_id>/photo/",
        students.student_photo,
        name="student_photo",
    ),
    path(
        "students/<uuid:student_id>/id-card/",
        students.student_id_card,
        name="student_id_card",
    ),
    path(
        "students/<uuid:student_id>/rotate-public-token/",
        students.rotate_public_token,
        name="rotate_public_token",
    ),
    # Gate operations
    path("gate/verify/", gate.verify, name="gate_verify"),
    path("gate/depart/", gate.depart, name="gate_depart"),
    path("gate/return/", gate.record_return, name="gate_return"),
    # Exeats
    path("exeats/", exeats.exeats, name="exeats"),
    path(
        "exeats/<uuid:exeat_id>/",
        exeats.exeat_detail_view,
        name="exeat_detail",
    ),
    path(
        "exeats/<uuid:exeat_id>/approve/",
        exeats.approve_exeat,
        name="exeat_approve",
    ),
    path(
        "exeats/<uuid:exeat_id>/reject/",
        exeats.reject_exeat,
        name="exeat_reject",
    ),
    path(
        "exeats/<uuid:exeat_id>/override/",
        exeats.override_exeat,
        name="exeat_override",
    ),
    path(
        "exeats/<uuid:exeat_id>/cancel/",
        exeats.cancel_exeat_view,
        name="exeat_cancel",
    ),
    path(
        "exeats/<uuid:exeat_id>/reschedule/",
        exeats.reschedule_exeat_view,
        name="exeat_reschedule",
    ),
    path(
        "exeats/<uuid:exeat_id>/reconcile/",
        exeats.reconcile_exeat,
        name="exeat_reconcile",
    ),
    path(
        "exeats/<uuid:exeat_id>/review/",
        exeats.decide_review,
        name="exeat_review_decide",
    ),
    # Push devices
    path("devices/", devices.devices, name="push_devices"),
    path("devices/<uuid:device_id>/", devices.device_detail, name="push_device"),
    # Roll call
    path("roll-call/sessions/", roll_call.sessions, name="roll_call_sessions"),
    path(
        "roll-call/sessions/<uuid:session_id>/",
        roll_call.session_detail,
        name="roll_call_session",
    ),
    path(
        "roll-call/sessions/<uuid:session_id>/entries/<uuid:entry_id>/",
        roll_call.update_entry,
        name="roll_call_entry",
    ),
    path(
        "roll-call/sessions/<uuid:session_id>/mark-all-present/",
        roll_call.mark_session_present,
        name="roll_call_mark_all_present",
    ),
    path(
        "roll-call/sessions/<uuid:session_id>/complete/",
        roll_call.complete_session,
        name="roll_call_complete",
    ),
    # Notifications
    path("notifications/", notifications.notifications, name="notifications"),
    path(
        "notifications/read-all/",
        notifications.mark_all_read,
        name="notifications_read_all",
    ),
    path(
        "notifications/<str:notification_id>/read/",
        notifications.mark_read,
        name="notification_read",
    ),
    # Reporting, audit history, and guardian SMS campaigns
    path("reports/", reports.reports, name="reports"),
    path("reports/export/", reports.export_report, name="report_export"),
    path("audit/events/", reports.audit_events, name="audit_events"),
    path("sms/campaigns/", sms.campaigns, name="sms_campaigns"),
    path(
        "sms/campaigns/<uuid:campaign_id>/decision/",
        sms.decide_campaign,
        name="sms_campaign_decision",
    ),
    # Home summary
    path("dashboard/", dashboard.dashboard, name="dashboard"),
    # Public (unauthenticated)
    path("public/school/", meta.public_school, name="public_school"),
    path("public/schools/", meta.public_schools, name="public_schools"),
    # Unknown API routes must return JSON, never the web HTML 404 page.
    re_path(r"^.*$", meta.not_found, name="not_found"),
]
