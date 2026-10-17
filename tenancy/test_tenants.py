from datetime import datetime, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from django.core.exceptions import ValidationError
from django.core.management.base import CommandError
from django.http import Http404
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)

from tenancy.management.commands.migrate_legacy_school import (
    Command as LegacyMigrationCommand,
)
from tenancy.management.commands.set_school_status import (
    _pending_tenant_migrations,
)
from tenancy.middleware import VerifiedDomainRoutingMiddleware
from tenancy.models import Domain, Tenant, normalize_domain


class TenantFileIsolationTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    @patch(
        "core.models.timezone.now",
        return_value=datetime(2026, 10, 17, tzinfo=datetime_timezone.utc),
    )
    @patch(
        "core.models.get_current_schema",
        return_value=SimpleNamespace(schema_name="school_one"),
    )
    def test_new_photo_and_logo_uploads_are_namespaced_by_tenant(
        self, schema, _now
    ):
        from core.models import school_logo_upload_to, student_photo_upload_to

        self.assertEqual(
            student_photo_upload_to(None, "portrait.png"),
            "tenants/school_one/students/photos/2026/10/portrait.png",
        )
        self.assertEqual(
            school_logo_upload_to(None, "crest.png"),
            "tenants/school_one/school/logos/crest.png",
        )

    @override_settings(TENANCY_ENABLED=False)
    @patch(
        "core.models.timezone.now",
        return_value=datetime(2026, 10, 17, tzinfo=datetime_timezone.utc),
    )
    def test_legacy_upload_paths_remain_unchanged(self, _now):
        from core.models import school_logo_upload_to, student_photo_upload_to

        self.assertEqual(
            student_photo_upload_to(None, "portrait.png"),
            "students/photos/2026/10/portrait.png",
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


class LegacyMigrationDependencyTests(SimpleTestCase):
    def test_tables_are_sorted_by_foreign_key_dependencies(self):
        tables = ["core_child", "core_parent", "core_unrelated"]
        dependencies = {
            "core_child": {"core_parent"},
            "core_parent": set(),
            "core_unrelated": set(),
        }

        result = LegacyMigrationCommand._stable_topological_sort(
            tables,
            dependencies,
        )

        self.assertLess(result.index("core_parent"), result.index("core_child"))
        self.assertEqual(set(result), set(tables))

    def test_circular_foreign_keys_abort_legacy_copy(self):
        tables = ["core_first", "core_second"]
        dependencies = {
            "core_first": {"core_second"},
            "core_second": {"core_first"},
        }

        with self.assertRaisesMessage(
            CommandError,
            "circular foreign-key dependencies: core_first, core_second",
        ):
            LegacyMigrationCommand._stable_topological_sort(tables, dependencies)


class LegacyMigrationOrderingTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    @patch("tenancy.management.commands.migrate_legacy_school.Domain.objects")
    @patch("tenancy.management.commands.migrate_legacy_school.Tenant.objects")
    @patch("tenancy.management.commands.migrate_legacy_school.call_command")
    def test_target_schema_migrates_before_legacy_rows_are_copied(
        self,
        call_command,
        tenant_objects,
        domain_objects,
    ):
        tenant = MagicMock()
        tenant.name = "School One"
        tenant.schema_name = "school_one"
        tenant.is_active = False
        tenant_objects.filter.return_value.first.return_value = tenant
        domain_objects.filter.return_value.exists.return_value = False
        operations = []
        call_command.side_effect = lambda *args, **kwargs: operations.append(
            ("migrate", args, kwargs)
        )

        command = LegacyMigrationCommand()
        with (
            patch(
                "tenancy.management.commands.migrate_legacy_school.connection.vendor",
                "postgresql",
            ),
            patch(
                "tenancy.management.commands.migrate_legacy_school.transaction.atomic"
            ),
            patch.object(command, "_legacy_tables", return_value={"core_student"}),
            patch.object(
                command,
                "_copy_rows",
                side_effect=lambda *_args: operations.append(("copy_rows",)),
            ),
            patch.object(
                command,
                "_copy_files",
                side_effect=lambda *_args: operations.append(("copy_files",)),
            ),
        ):
            command.handle(
                name="School One",
                schema="school_one",
                domain="school.example.org",
                confirm_domain_verified=True,
            )

        self.assertEqual(
            [operation[0] for operation in operations],
            ["migrate", "copy_rows", "copy_files"],
        )
        self.assertEqual(
            operations[0][1:],
            (
                ("migrate",),
                {"schemas": ["school_one"], "verbosity": 0},
            ),
        )


class TenantActivationMigrationCheckTests(SimpleTestCase):
    @override_settings(TENANTS={"default": {"APPS": ["django.contrib.auth"]}})
    @patch("tenancy.management.commands.set_school_status.MigrationExecutor")
    def test_reports_unapplied_tenant_migrations(self, migration_executor):
        tenant = MagicMock()
        executor = migration_executor.return_value
        executor.loader.graph.leaf_nodes.return_value = [("auth", "0001_initial")]
        executor.migration_plan.return_value = [
            (SimpleNamespace(app_label="auth", name="0001_initial"), False),
            (SimpleNamespace(app_label="tenancy", name="0001_initial"), False),
        ]

        pending = _pending_tenant_migrations(tenant)

        self.assertEqual(pending, ["auth.0001_initial"])
        tenant.__enter__.assert_called_once_with()
        tenant.__exit__.assert_called_once()


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
    @patch("tenancy.models.transaction.atomic")
    @patch("tenancy.models.Tenant.objects")
    @patch("tenancy.models.Domain.objects")
    def test_primary_domain_demotes_existing_primary_on_save(
        self,
        mock_domain_objects,
        mock_tenant_objects,
        mock_atomic,
    ):
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

        mock_atomic.assert_called_once_with()
        mock_tenant_objects.select_for_update.return_value.get.assert_called_once_with(
            pk=42
        )
        mock_domain_objects.filter.assert_called_once_with(
            tenant_id=42,
            is_primary=True,
        )
        filter_qs.exclude.assert_called_once_with(pk=10)
        filter_qs.update.assert_called_once_with(is_primary=False)

    @patch("tenancy.models.transaction.atomic")
    @patch("tenancy.models.Tenant.objects")
    @patch("tenancy.models.Domain.objects")
    @override_settings(TENANCY_ENABLED=True)
    def test_primary_domain_save_failure_keeps_demotion_inside_transaction(
        self,
        mock_domain_objects,
        mock_tenant_objects,
        mock_atomic,
    ):
        from django_pgschemas.routing.models import DomainModel

        domain = Domain(
            pk=10,
            tenant=Tenant(pk=42, schema_name="school_one", name="School One"),
            domain="newprimary.example.org",
            is_primary=True,
        )
        domain.full_clean = Mock(
            side_effect=ValidationError({"domain": "Invalid domain."})
        )

        with patch.object(DomainModel, "save"), self.assertRaises(ValidationError):
            domain.save()

        mock_atomic.assert_called_once_with()
        mock_atomic.return_value.__exit__.assert_called_once()
        self.assertIsNotNone(mock_atomic.return_value.__exit__.call_args.args[0])
        mock_tenant_objects.select_for_update.return_value.get.assert_called_once_with(
            pk=42
        )
        mock_domain_objects.filter.return_value.exclude.return_value.update.assert_called_once_with(
            is_primary=False
        )

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


class CeleryWorkerLifecycleTests(SimpleTestCase):
    @patch("django.db.connections.all")
    def test_worker_process_init_closes_inherited_connections(self, mock_connections_all):
        from schoolsystem.celery import close_db_connections_on_worker_init

        conn1 = Mock()
        conn2 = Mock()
        mock_connections_all.return_value = [conn1, conn2]

        close_db_connections_on_worker_init()

        conn1.close.assert_called_once()
        conn2.close.assert_called_once()

    @override_settings(TENANCY_ENABLED=True)
    @patch("django_pgschemas.schema.activate_public")
    @patch("django.db.close_old_connections")
    def test_task_prerun_resets_schema_and_cleans_old_connections(
        self, mock_close_old, mock_activate_public
    ):
        from schoolsystem.celery import reset_connections_and_schema_on_task_prerun

        reset_connections_and_schema_on_task_prerun()

        mock_close_old.assert_called_once()
        mock_activate_public.assert_called_once()

    @override_settings(TENANCY_ENABLED=True)
    @patch("django_pgschemas.schema.activate_public")
    @patch("django.db.close_old_connections")
    def test_task_postrun_resets_schema_and_cleans_old_connections(
        self, mock_close_old, mock_activate_public
    ):
        from schoolsystem.celery import reset_connections_and_schema_on_task_postrun

        reset_connections_and_schema_on_task_postrun()

        mock_activate_public.assert_called_once()
        mock_close_old.assert_called_once()


class SmsServiceNullSchemaTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=True)
    @patch("core.services.sms.get_current_schema", return_value=None)
    def test_enqueue_sms_delivery_task_handles_none_schema(self, mock_schema):
        from core.services.sms import enqueue_sms_delivery_task

        with self.assertRaisesMessage(
            RuntimeError,
            "SMS delivery tasks must be queued with a school tenant.",
        ):
            enqueue_sms_delivery_task("delivery-uuid-123")

    @override_settings(TENANCY_ENABLED=True)
    @patch("core.models.SMSConfiguration.is_enabled", return_value=True)
    @patch("core.services.sms.get_exeat_sms_targets", return_value=[("Parent", "0240000000", "guardian")])
    @patch("core.services.sms.get_current_schema", return_value=None)
    def test_enqueue_exeat_event_sms_handles_none_schema(
        self, mock_schema, mock_targets, mock_enabled
    ):
        from core.services.sms import enqueue_exeat_event_sms

        with self.assertRaisesMessage(
            RuntimeError,
            "SMS deliveries must be queued inside a school tenant.",
        ):
            enqueue_exeat_event_sms(Mock(), "departure")
