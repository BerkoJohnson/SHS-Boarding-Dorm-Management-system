import json

from django.core.cache import cache
from django.test import TestCase, override_settings

from core.api.tests.factories import (
    auth,
    create_guardian,
    create_house,
    create_school,
    create_staff,
    create_student,
    login,
    token_from,
)
from core.models import AuditEvent, DataExportLog, GroupSMSCampaign, StaffRole


@override_settings(TENANCY_ENABLED=False)
class MobileOperationsApiTests(TestCase):
    def setUp(self):
        cache.clear()
        create_school()
        self.house = create_house("Unity House")
        guardian = create_guardian()
        create_student("MOB-001", house=self.house, guardian=guardian)
        self.admin = create_staff("admin", role=StaffRole.ADMIN)
        create_staff("second-admin", role=StaffRole.ADMIN)
        create_staff("senior", role=StaffRole.SENIOR_HOUSEMASTER, senior=True)
        create_staff("hm", role=StaffRole.HOUSEMASTER, house=self.house)

    def _token(self, username):
        return token_from(login(self.client, username))

    def _get(self, username, path):
        return self.client.get(path, **auth(self._token(username)))

    def _post(self, username, path, payload):
        return self.client.post(
            path,
            data=json.dumps(payload),
            content_type="application/json",
            **auth(self._token(username)),
        )

    def test_housemaster_can_read_house_scoped_report_data(self):
        response = self._get("hm", "/api/v1/reports/?report=housing")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertGreaterEqual(response.json()["data"]["record_count"], 1)

    def test_report_data_paginates_rows_across_report_sections(self):
        first = self._get(
            "admin",
            "/api/v1/reports/?report=exeat_analytics&page=1&page_size=1",
        )
        self.assertEqual(first.status_code, 200, first.content)
        first_body = first.json()
        first_data = first_body["data"]
        first_row_count = len(first_data.get("rows") or []) + sum(
            len(section.get("rows") or [])
            for section in first_data.get("sections") or []
        )
        self.assertEqual(first_row_count, 1)
        self.assertTrue(first_body["pagination"]["has_next"])

        second = self._get(
            "admin",
            "/api/v1/reports/?report=exeat_analytics&page=2&page_size=1",
        )
        self.assertEqual(second.status_code, 200, second.content)
        second_data = second.json()["data"]
        second_row_count = len(second_data.get("rows") or []) + sum(
            len(section.get("rows") or [])
            for section in second_data.get("sections") or []
        )
        self.assertEqual(second_row_count, 1)

    def test_housemaster_cannot_read_privileged_audit_events(self):
        response = self._get("hm", "/api/v1/audit/events/")
        self.assertEqual(response.status_code, 403)

    def test_scoped_report_export_is_csv_and_writes_export_audit_records(self):
        response = self._get(
            "hm",
            "/api/v1/reports/export/?report=housing&format=csv",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment;", response["Content-Disposition"])
        export = DataExportLog.objects.get()
        self.assertEqual(export.actor.get_username(), "hm")
        self.assertEqual(export.file_format, "csv")
        event = AuditEvent.objects.get(action=AuditEvent.Action.EXPORT)
        self.assertIn("csv", event.summary)

    def test_admin_audit_feed_is_paginated_and_excludes_private_metadata(self):
        AuditEvent.record(
            actor=self.admin,
            action=AuditEvent.Action.ADMIN_EDIT,
            summary="Updated a student allocation.",
            target_type="Student",
            target_id="student-1",
            metadata={"private": "must not be exposed"},
        )
        response = self._get("admin", "/api/v1/audit/events/?page_size=1")
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["pagination"]["total"], 1)
        self.assertEqual(body["data"][0]["summary"], "Updated a student allocation.")
        self.assertNotIn("metadata", body["data"][0])

    def test_sms_campaign_requires_admin_and_a_second_admin_decision(self):
        forbidden = self._post(
            "hm",
            "/api/v1/sms/campaigns/",
            {
                "scope": GroupSMSCampaign.Scope.ALL_BOARDERS,
                "message": "Boarding reminder",
            },
        )
        self.assertEqual(forbidden.status_code, 403)

        created = self._post(
            "admin",
            "/api/v1/sms/campaigns/",
            {
                "scope": GroupSMSCampaign.Scope.ALL_BOARDERS,
                "message": "Boarding reminder",
            },
        )
        self.assertEqual(created.status_code, 201, created.content)
        campaign = created.json()["data"]
        self.assertEqual(campaign["recipient_count"], 1)
        self.assertEqual(campaign["status"], GroupSMSCampaign.Status.PENDING)
        created_event = AuditEvent.objects.get(
            target_type="GroupSMSCampaign",
            target_id=campaign["id"],
            after__status=GroupSMSCampaign.Status.PENDING,
        )
        self.assertEqual(created_event.actor, self.admin)
        self.assertNotIn("message", created_event.after)

        same_admin = self._post(
            "admin",
            f"/api/v1/sms/campaigns/{campaign['id']}/decision/",
            {"approve": False, "decision_note": "Needs review"},
        )
        self.assertEqual(same_admin.status_code, 400)

        rejected = self._post(
            "second-admin",
            f"/api/v1/sms/campaigns/{campaign['id']}/decision/",
            {"approve": False, "decision_note": "Do not send this notice"},
        )
        self.assertEqual(rejected.status_code, 200, rejected.content)
        self.assertEqual(
            rejected.json()["data"]["status"], GroupSMSCampaign.Status.REJECTED
        )
        decision_event = AuditEvent.objects.get(
            target_type="GroupSMSCampaign",
            target_id=campaign["id"],
            after__status=GroupSMSCampaign.Status.REJECTED,
        )
        self.assertEqual(decision_event.actor.get_username(), "second-admin")
