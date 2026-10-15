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
        middleware = VerifiedDomainRoutingMiddleware(lambda req: "response")

        response = middleware(request)

        self.assertEqual(response, "response")
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
        set_urlconf.assert_any_call("schoolsystem.urls")
        self.assertEqual(set_urlconf.call_args_list[-1], ((None,),))
        self.assertEqual(activate_public.call_count, 2)

    @override_settings(ALLOWED_HOSTS=["*"])
    @patch("tenancy.middleware.Domain")
    @patch("tenancy.middleware.activate_public")
    def test_unknown_domains_fail_closed(self, activate_public, domain_model):
        domain_model.objects.select_related.return_value.filter.return_value.first.return_value = (
            None
        )
        request = RequestFactory().get("/", HTTP_HOST="unknown.example.org")
        middleware = VerifiedDomainRoutingMiddleware(lambda req: None)

        with self.assertRaises(Http404):
            middleware(request)

        activate_public.assert_called_once()

    def test_health_check_bypasses_tenant_resolution(self):
        middleware = VerifiedDomainRoutingMiddleware(lambda req: None)
        for path in ("/healthz", "/live"):
            request = RequestFactory().get(path, HTTP_HOST="unmapped-probe.internal")
            response = middleware(request)
            self.assertEqual(response.status_code, 200)

    @override_settings(ALLOWED_HOSTS=["*"])
    def test_ipv6_host_extraction(self):
        request = RequestFactory().get("/", HTTP_HOST="[::1]:8000")
        extracted = VerifiedDomainRoutingMiddleware._extract_hostname(request)
        self.assertEqual(extracted, "::1")

    @override_settings(ALLOWED_HOSTS=["*"], PLATFORM_ADMIN_DOMAIN="admin.platform.internal")
    @patch("tenancy.middleware.Domain")
    def test_platform_admin_domain_returns_json_status(self, domain_model):
        import json

        domain_model.objects.values.return_value.distinct.return_value.count.return_value = 5
        middleware = VerifiedDomainRoutingMiddleware(lambda req: self.fail("downstream pipeline called"))
        request = RequestFactory().get("/", HTTP_HOST="admin.platform.internal")
        response = middleware(request)
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["platform"], "eden-multi-tenant")
        self.assertEqual(data["tenant_count"], 5)


class DynamicCsrfMiddlewareTests(SimpleTestCase):
    def setUp(self):
        from tenancy.middleware import DynamicCsrfMiddleware
        DynamicCsrfMiddleware.invalidate_cache()

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.middleware.Domain.objects")
    def test_verifies_origin_from_active_verified_tenant_domain(self, domain_objects):
        from tenancy.middleware import DynamicCsrfMiddleware

        domain_objects.filter.return_value.values_list.return_value = ["school.example.org"]
        middleware = DynamicCsrfMiddleware(lambda req: None)
        request = RequestFactory().post(
            "/login/",
            HTTP_HOST="school.example.org",
            HTTP_ORIGIN="https://school.example.org",
            secure=True,
        )

        self.assertTrue(middleware._origin_verified(request))

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.middleware.Domain.objects")
    def test_rejects_protocol_downgrade_origin(self, domain_objects):
        from tenancy.middleware import DynamicCsrfMiddleware

        domain_objects.filter.return_value.values_list.return_value = ["school.example.org"]
        middleware = DynamicCsrfMiddleware(lambda req: None)
        request = RequestFactory().post(
            "/login/",
            HTTP_HOST="school.example.org",
            HTTP_ORIGIN="http://school.example.org",
            secure=True,
        )

        self.assertFalse(middleware._origin_verified(request))

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.middleware.Domain.objects")
    def test_rejects_unverified_origin(self, domain_objects):
        from tenancy.middleware import DynamicCsrfMiddleware

        domain_objects.filter.return_value.values_list.return_value = ["school.example.org"]
        middleware = DynamicCsrfMiddleware(lambda req: None)
        request = RequestFactory().post(
            "/login/",
            HTTP_HOST="school.example.org",
            HTTP_ORIGIN="https://attacker.example.org",
            secure=True,
        )

        self.assertFalse(middleware._origin_verified(request))


class TenantModelValidationTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    def test_save_calls_full_clean_and_rejects_reserved_schema_name(self):
        tenant = Tenant(schema_name="public", name="Invalid Public Tenant")
        with self.assertRaises(ValidationError) as ctx:
            tenant.save()
        self.assertIn("schema_name", ctx.exception.message_dict)

    @override_settings(TENANCY_ENABLED=True)
    def test_normalize_domain_error_is_string_message(self):
        try:
            normalize_domain("invalid:port:here")
            self.fail("Did not raise ValidationError")
        except ValidationError as error:
            self.assertIsInstance(error.message, str)
            self.assertNotIn("domain", error.message)

    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.models.Domain.objects")
    def test_primary_domain_demotes_existing_primary_on_save(self, mock_domain_objects):
        from django_pgschemas.routing.models import DomainModel

        filter_qs = MagicMock()
        mock_domain_objects.filter.return_value = filter_qs
        filter_qs.exclude.return_value = filter_qs

        tenant = Tenant(pk=42, schema_name="school_one", name="School One")
        domain = Domain(
            pk=10,
            tenant=tenant,
            domain="newprimary.example.org",
            is_primary=True,
        )
        domain.full_clean = Mock()

        with patch.object(DomainModel, "save"):
            domain.save()

        mock_domain_objects.filter.assert_called_once_with(
            tenant_id=42,
            is_primary=True,
        )
        filter_qs.exclude.assert_called_once_with(pk=10)
        filter_qs.update.assert_called_once_with(is_primary=False)


class CsrfCacheInvalidationSignalTests(SimpleTestCase):
    @patch("tenancy.middleware.DynamicCsrfMiddleware.invalidate_cache")
    def test_signals_trigger_csrf_cache_invalidation(self, mock_invalidate):
        from django.db.models.signals import post_delete, post_save

        tenant = Tenant(schema_name="school_one", name="School One")
        post_save.send(sender=Tenant, instance=tenant, created=True)
        self.assertEqual(mock_invalidate.call_count, 1)

        post_delete.send(sender=Tenant, instance=tenant)
        self.assertEqual(mock_invalidate.call_count, 2)

        domain = Domain(domain="school.example.org")
        post_save.send(sender=Domain, instance=domain, created=True)
        self.assertEqual(mock_invalidate.call_count, 3)

        post_delete.send(sender=Domain, instance=domain)
        self.assertEqual(mock_invalidate.call_count, 4)


class TenantTaskDecoratorTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    @patch("core.tasks._tenant_context")
    def test_with_tenant_schema_passes_tenant_schema_kwarg(self, mock_context):
        from core.tasks import _with_tenant_schema

        mock_context.return_value.__enter__.return_value = None
        mock_context.return_value.__exit__.return_value = None

        received_kwargs = {}

        @_with_tenant_schema
        def sample_task(arg1, tenant_schema=None, **kwargs):
            received_kwargs["tenant_schema"] = tenant_schema
            received_kwargs["arg1"] = arg1

        sample_task("val1", tenant_schema="test_schema")

        self.assertEqual(received_kwargs["tenant_schema"], "test_schema")
        self.assertEqual(received_kwargs["arg1"], "val1")
