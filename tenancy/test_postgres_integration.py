"""
PostgreSQL-only integration tests.

These exercise real PostgreSQL behaviour that ordinary SQLite unit tests cannot
prove:

  * Quota checks are serialized with their writes by a transaction-scoped
    ``pg_advisory_xact_lock`` (the concurrency test).
  * A tenant schema survives a real ``pg_dump`` -> ``pg_restore`` round trip
    (the restore test).

Both create *uniquely named, disposable* tenant schemas and drop only the
schemas they created. They never drop databases.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import connection, connections
from django.db.models.signals import pre_save
from django.test import TransactionTestCase, override_settings
from django_pgschemas.schema import Schema

from core.models import Dormitory, House
from tenancy.models import PlanTier, Tenant, TenantSubscription


def _pg_client_available() -> bool:
    return bool(shutil.which("pg_dump") and shutil.which("pg_restore"))


class _DisposableTenantSchemaTestCase(TransactionTestCase):
    """Base case that provisions (and later drops) a uniquely named schema."""

    def setUp(self):
        super().setUp()
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL is required for this integration test.")
        if not settings.TENANCY_ENABLED:
            self.skipTest("TENANCY_ENABLED must be true for this integration test.")

        # Uniquely named so parallel/repeat runs never collide.
        self.schema_name = f"it_{uuid.uuid4().hex[:12]}"
        self.tenant = Tenant.objects.create(
            name="Integration Test School",
            schema_name=self.schema_name,
        )

    def tearDown(self):
        try:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP SCHEMA IF EXISTS "{self.schema_name}" CASCADE')
        except Exception:  # pragma: no cover - best-effort cleanup
            pass
        super().tearDown()


class QuotaLockConcurrencyTests(_DisposableTenantSchemaTestCase):
    """Prove the advisory lock prevents quota overshoot under contention."""

    THREADS = 8
    LIMIT = 3

    def setUp(self):
        super().setUp()
        TenantSubscription.objects.create(
            tenant=self.tenant,
            plan_tier=PlanTier.STANDARD,
            features={"max_dormitories": self.LIMIT},
        )
        with self.tenant:
            self.house = House.objects.create(name="Concurrency House")
        self.barrier = threading.Barrier(self.THREADS)

    def _create_dormitory(self, index, results):
        # Each thread needs its own Schema instance: pgschemas stores the
        # activation token on the object, and a token created in one thread
        # cannot be reset in another.
        schema = Schema.create(self.schema_name)
        try:
            self.barrier.wait(timeout=30)
            with schema:
                Dormitory.objects.create(
                    house_id=self.house.pk,
                    name=f"Block {index}",
                    gender="M",
                )
            results.append("created")
        except ValidationError:
            results.append("blocked")
        except Exception as error:  # pragma: no cover - diagnostic aid
            results.append(f"error:{error!r}")
        finally:
            connections.close_all()

    def test_dormitory_quota_is_serialized_under_concurrency(self):
        results = []
        threads = [
            threading.Thread(target=self._create_dormitory, args=(index, results))
            for index in range(self.THREADS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with self.tenant:
            created = Dormitory.objects.count()

        self.assertEqual(
            [r for r in results if r.startswith("error")],
            [],
            msg=f"Unexpected worker errors: {results}",
        )
        self.assertEqual(sum(1 for r in results if r == "created"), self.LIMIT)
        self.assertEqual(sum(1 for r in results if r == "blocked"), self.THREADS - self.LIMIT)
        self.assertEqual(created, self.LIMIT)


class StaffQuotaLockConcurrencyTests(_DisposableTenantSchemaTestCase):
    """Staff creation in autocommit must still be serialized by the lock.

    Unlike model saves that already run inside a transaction, ``User`` has no
    parent model, so Django's ``save_base`` opens no transaction and the
    ``pre_save`` receiver cannot hold the advisory lock across the ``INSERT``.
    The ``User.save`` wrapper installed by ``CoreConfig`` closes that gap; this
    test exercises it with no surrounding transaction.
    """

    THREADS = 8
    LIMIT = 2

    def setUp(self):
        super().setUp()
        TenantSubscription.objects.create(
            tenant=self.tenant,
            plan_tier=PlanTier.STANDARD,
            features={"max_staff": self.LIMIT},
        )
        self.barrier = threading.Barrier(self.THREADS)
        self.user_model = get_user_model()
        # Widen the window between the quota check and the INSERT so the race is
        # reliably observable. Django dispatches receivers in connection order,
        # so this runs *after* enforce_staff_account_quota -- i.e. between its
        # check and the actual write.
        pre_save.connect(
            self._slowdown_staff_save,
            sender=self.user_model,
            dispatch_uid="staff_save_slowdown",
        )

    def tearDown(self):
        pre_save.disconnect(
            sender=self.user_model,
            dispatch_uid="staff_save_slowdown",
        )
        super().tearDown()

    @staticmethod
    def _slowdown_staff_save(sender, instance, **kwargs):
        if getattr(instance, "is_staff", False):
            time.sleep(0.25)

    def _create_staff(self, index, results):
        schema = Schema.create(self.schema_name)
        try:
            self.barrier.wait(timeout=30)
            with schema:
                self.user_model.objects.create_user(
                    username=f"staff_{index}",
                    password="not-used-here",
                    is_staff=True,
                )
            results.append("created")
        except ValidationError:
            results.append("blocked")
        except Exception as error:  # pragma: no cover - diagnostic aid
            results.append(f"error:{error!r}")
        finally:
            connections.close_all()

    def test_staff_quota_is_serialized_in_autocommit(self):
        results = []
        threads = [
            threading.Thread(target=self._create_staff, args=(index, results))
            for index in range(self.THREADS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with self.tenant:
            staff_count = self.user_model.objects.filter(is_staff=True).count()

        self.assertEqual(
            [r for r in results if r.startswith("error")],
            [],
            msg=f"Unexpected worker errors: {results}",
        )
        self.assertEqual(sum(1 for r in results if r == "created"), self.LIMIT)
        self.assertEqual(staff_count, self.LIMIT)


class TenantRestoreRoundTripTests(_DisposableTenantSchemaTestCase):
    """Back up a real schema with pg_dump and restore it with pg_restore."""

    def setUp(self):
        super().setUp()
        if not _pg_client_available():
            self.skipTest("pg_dump/pg_restore are required for the round-trip test.")
        self.media_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_temp.cleanup)
        self.backup_dir = Path(self.media_temp.name) / "backups" / "tenants"
        media_root = Path(self.media_temp.name) / "media"
        photo_root = Path(self.media_temp.name) / "private-photos"
        project_root = Path(self.media_temp.name) / "project"
        self.media_settings = override_settings(
            MEDIA_ROOT=media_root,
            PRIVATE_STUDENT_PHOTO_ROOT=photo_root,
            BASE_DIR=project_root,
            MEDIA_STORAGE_BACKEND="filesystem",
        )
        self.media_settings.enable()
        self.addCleanup(self.media_settings.disable)
        self.public_media_file = (
            media_root
            / "tenants"
            / self.schema_name
            / "school"
            / "logos"
            / "crest.png"
        )
        self.private_photo_file = (
            photo_root
            / "tenants"
            / self.schema_name
            / "students"
            / "photos"
            / "student.png"
        )
        self.export_file = (
            project_root
            / ".private"
            / "student_id_exports"
            / self.schema_name
            / "student-ids.zip"
        )
        for path, content in (
            (self.public_media_file, b"crest-bytes"),
            (self.private_photo_file, b"photo-bytes"),
            (self.export_file, b"export-bytes"),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        with self.tenant:
            house = House.objects.create(name="Round Trip House")
            Dormitory.objects.create(house=house, name="RT Block", gender="M")

    def test_backup_then_restore_round_trip(self):
        # 1. Back up the schema with the real pg_dump.
        backup_out = StringIO()
        call_command(
            "tenant_backup",
            f"--schema={self.schema_name}",
            f"--output-dir={self.backup_dir}",
            "--include-media",
            "--json",
            stdout=backup_out,
        )
        summary = json.loads(backup_out.getvalue())
        self.assertEqual(summary["successful"], 1)
        dump_file = Path(summary["backups"][0]["dump_file"])
        self.assertTrue(dump_file.exists())
        self.assertEqual(summary["backups"][0]["media_files_count"], 3)
        media_file = Path(summary["backups"][0]["media_file"])
        self.assertTrue(media_file.exists())

        # 2. Simulate disaster: de-register the tenant and drop the schema.
        self.tenant.delete()
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA "{self.schema_name}" CASCADE')
        self.public_media_file.unlink()
        self.private_photo_file.unlink()
        self.export_file.unlink()

        # 3. Restore the database and media from their verified backup files.
        restore_out = StringIO()
        call_command(
            "tenant_restore",
            f"--schema={self.schema_name}",
            f"--dump={dump_file}",
            f"--media-archive={media_file}",
            f"--backup-dir={self.backup_dir}",
            "--json",
            stdout=restore_out,
        )
        result = json.loads(restore_out.getvalue())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["media_files_count"], 3)
        self.assertEqual(self.public_media_file.read_bytes(), b"crest-bytes")
        self.assertEqual(self.private_photo_file.read_bytes(), b"photo-bytes")
        self.assertEqual(self.export_file.read_bytes(), b"export-bytes")

        # 4. The database data is back.
        with self.tenant:
            self.assertEqual(House.objects.filter(name="Round Trip House").count(), 1)
            self.assertEqual(Dormitory.objects.filter(name="RT Block").count(), 1)
