from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.core.exceptions import ValidationError
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, override_settings

from tenancy.middleware import VerifiedDomainRoutingMiddleware
from tenancy.models import Domain, Tenant, normalize_domain


class TenantFileIsolationTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    @patch(
        "core.models.get_current_schema",
        return_value=SimpleNamespace(schema_name="school_one"),
    )
    def test_new_photo_and_logo_uploads_are_namespaced_by_tenant(self, schema):
        from core.models import school_logo_upload_to, student_photo_upload_to

        self.assertEqual(
            student_photo_upload_to(None, "portrait.png"),
            "tenants/school_one/students/photos/%Y/%m/portrait.png",
        )
        self.assertEqual(
            school_logo_upload_to(None, "crest.png"),
            "tenants/school_one/school/logos/crest.png",
        )

    @override_settings(TENANCY_ENABLED=False)
    def test_legacy_upload_paths_remain_unchanged(self):
        from core.models import school_logo_upload_to, student_photo_upload_to

        self.assertEqual(
            student_photo_upload_to(None, "portrait.png"),
            "students/photos/%Y/%m/portrait.png",
        )
        self.assertEqual(
            school_logo_upload_to(None, "crest.png"),
            "school/logos/crest.png",
        )


class TenantTaskDispatchTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.models.Tenant")
    def test_periodic_operations_fan_out_only_to_active_tenants(self, tenant_model):
        from core.tasks import _run_for_each_tenant

        tenant_model.objects.filter.return_value.values_list.return_value = [
            "school_one",
            "school_two",
        ]
        task = Mock()

        count = _run_for_each_tenant(task, None, lambda: self.fail("ran in public schema"))

        self.assertEqual(count, 2)
        tenant_model.objects.filter.assert_called_once_with(is_active=True)
        task.apply_async.assert_any_call(
            kwargs={"tenant_schema": "school_one"}
        )
        task.apply_async.assert_any_call(
            kwargs={"tenant_schema": "school_two"}
        )

    @override_settings(TENANCY_ENABLED=True)
    @patch("core.tasks._tenant_context")
    def test_periodic_operation_runs_inside_the_requested_tenant(self, tenant_context):
        from core.tasks import _run_for_each_tenant

        context = MagicMock()
        tenant_context.return_value = context
        operation = Mock(return_value=7)

        result = _run_for_each_tenant(Mock(), "school_one", operation)

        self.assertEqual(result, 7)
        tenant_context.assert_called_once_with("school_one")
        context.__enter__.assert_called_once()
        context.__exit__.assert_called_once()
        operation.assert_called_once_with()


class DomainNormalizationTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=False)
    def test_tenant_records_cannot_be_created_in_legacy_mode(self):
        with self.assertRaisesMessage(
            ValidationError,
            "Set TENANCY_ENABLED=true before creating a school tenant.",
        ):
            Tenant(schema_name="school_one", name="School One").save()

        with self.assertRaisesMessage(
            ValidationError,
            "Set TENANCY_ENABLED=true before registering school domains.",
        ):
            Domain(domain="school.example.org").save()

    def test_normalizes_case_trailing_dot_and_internationalized_domains(self):
        self.assertEqual(normalize_domain("SCHOOL.Example.org."), "school.example.org")
        self.assertEqual(
            normalize_domain("münchen.example"),
            "xn--mnchen-3ya.example",
        )

    def test_rejects_urls_ports_and_invalid_hostnames(self):
        for value in (
            "https://school.example.org",
            "school.example.org:8443",
            "school.example.org/path",
            "-invalid.example",
            "school..example",
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                normalize_domain(value)


class VerifiedDomainRoutingTests(SimpleTestCase):
    @override_settings(ALLOWED_HOSTS=["*"])
    @patch("tenancy.middleware.activate")
    @patch("tenancy.middleware.activate_public")
    @patch("tenancy.middleware.set_urlconf")
    @patch("tenancy.middleware.Domain")
    def test_routes_only_a_verified_active_domain(
        self,
        domain_model,
        set_urlconf,
        activate_public,
        activate,
    ):
        tenant = SimpleNamespace(schema_name="atebubu", routing=None)
        domain_model.objects.select_related.return_value.filter.return_value.first.return_value = (
            SimpleNamespace(tenant=tenant)
        )
        request = RequestFactory().get("/", HTTP_HOST="SCHOOL.EXAMPLE.ORG")
        middleware = VerifiedDomainRoutingMiddleware(lambda request: None)

        response = middleware.process_request(request)

        self.assertIsNone(response)
        self.assertIs(request.tenant, tenant)
        self.assertEqual(request.urlconf, "schoolsystem.urls")
        self.assertEqual(
            domain_model.objects.select_related.return_value.filter.call_args.kwargs,
            {
                "domain": "school.example.org",
                "folder": "",
                "is_verified": True,
                "tenant__is_active": True,
            },
        )
        activate.assert_called_once_with(tenant)
        set_urlconf.assert_called_with("schoolsystem.urls")
        activate_public.assert_called_once()

    @override_settings(ALLOWED_HOSTS=["*"])
    @patch("tenancy.middleware.Domain")
    @patch("tenancy.middleware.activate_public")
    def test_unknown_domains_fail_closed(self, activate_public, domain_model):
        domain_model.objects.select_related.return_value.filter.return_value.first.return_value = (
            None
        )
        request = RequestFactory().get("/", HTTP_HOST="unknown.example.org")
        middleware = VerifiedDomainRoutingMiddleware(lambda request: None)

        with self.assertRaises(Http404):
            middleware.process_request(request)

        activate_public.assert_called_once()
