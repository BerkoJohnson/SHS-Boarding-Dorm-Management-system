import io
import tarfile
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.base import ContentFile
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from core.services.student_id_images import student_id_export_name
from core.storage import PrivateStudentPhotoStorage
from tenancy.management.commands.tenant_restore import Command as TenantRestoreCommand


class StudentIdExportNameTests(SimpleTestCase):
    @override_settings(TENANCY_ENABLED=False)
    def test_export_name_is_flat_without_tenancy(self):
        self.assertEqual(
            student_id_export_name("student-id-export-1.zip"),
            "student-id-export-1.zip",
        )

    @override_settings(TENANCY_ENABLED=True)
    @patch(
        "django_pgschemas.schema.get_current_schema",
        return_value=SimpleNamespace(schema_name="school_one"),
    )
    def test_export_name_is_namespaced_by_the_current_tenant(self, _schema):
        self.assertEqual(
            student_id_export_name("student-id-export-1.zip"),
            "school_one/student-id-export-1.zip",
        )
        self.assertEqual(
            student_id_export_name(
                "student-id-export-1.zip",
                schema_name="school_two",
            ),
            "school_two/student-id-export-1.zip",
        )

    def test_export_name_rejects_path_traversal_and_non_zip(self):
        for bad_name in ("../escape.zip", "sub/dir/export.zip", "export.txt"):
            with self.subTest(bad_name=bad_name):
                with self.assertRaises(ValueError):
                    student_id_export_name(bad_name)


class PrivateStorageS3DelegationTests(SimpleTestCase):
    def _fake_backend(self, calls, existing):
        return type(
            "FakeS3Storage",
            (),
            {
                "location": "private/student_photos",
                "_save": lambda self, name, content: (
                    calls.append(("save", name)),
                    name,
                )[1],
                "_open": lambda self, name, mode="rb": (
                    calls.append(("open", name)),
                    b"remote-bytes",
                )[1],
                "exists": lambda self, name: (
                    calls.append(("exists", name)),
                    name in existing,
                )[1],
                "delete": lambda self, name: calls.append(("delete", name)),
                "size": lambda self, name: 12,
                "listdir": lambda self, path: ([], []),
            },
        )()

    @override_settings(MEDIA_STORAGE_BACKEND="neon_s3")
    @patch("core.storage.build_private_s3_storage")
    def test_private_photo_operations_delegate_to_s3(self, build):
        calls = []
        build.return_value = self._fake_backend(calls, {"students/photos/old.jpg"})

        storage = PrivateStudentPhotoStorage()
        self.assertTrue(storage.uses_s3())
        self.assertEqual(
            storage.save("students/photos/new.jpg", ContentFile(b"x")),
            "students/photos/new.jpg",
        )
        self.assertEqual(storage.open("students/photos/old.jpg"), b"remote-bytes")
        self.assertTrue(storage.exists("students/photos/old.jpg"))
        storage.delete("students/photos/old.jpg")

        with self.assertRaises(NotImplementedError):
            storage.path("students/photos/old.jpg")
        with self.assertRaises(ValueError):
            storage.url("students/photos/old.jpg")

        self.assertIn(("save", "students/photos/new.jpg"), calls)

    @override_settings(MEDIA_STORAGE_BACKEND="filesystem")
    def test_filesystem_mode_does_not_delegate(self):
        storage = PrivateStudentPhotoStorage()
        self.assertFalse(storage.uses_s3())


class TenantBackupMediaTests(SimpleTestCase):
    def test_archive_includes_public_and_private_media_roots(self):
        from tenancy.management.commands.tenant_backup import Command

        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            media_root = root / "media"
            photo_root = root / "photos"
            base_dir = root / "base"

            (media_root / "tenants" / "school_one" / "school" / "logos").mkdir(
                parents=True
            )
            (media_root / "tenants" / "school_one" / "school" / "logos" / "crest.png").write_bytes(
                b"crest"
            )
            (photo_root / "tenants" / "school_one" / "students" / "photos").mkdir(
                parents=True
            )
            (photo_root / "tenants" / "school_one" / "students" / "photos" / "a.jpg").write_bytes(
                b"photo"
            )
            (base_dir / ".private" / "student_id_exports" / "school_one").mkdir(
                parents=True
            )
            (base_dir / ".private" / "student_id_exports" / "school_one" / "exp.zip").write_bytes(
                b"zip"
            )

            with override_settings(
                MEDIA_ROOT=media_root,
                PRIVATE_STUDENT_PHOTO_ROOT=photo_root,
                BASE_DIR=base_dir,
                MEDIA_STORAGE_BACKEND="filesystem",
            ):
                archive = root / "out.tar.gz"
                count, sources = Command()._archive_tenant_media(
                    archive,
                    "school_one",
                )

            self.assertEqual(count, 3)
            self.assertEqual(len(sources), 3)
            with tarfile.open(archive, "r:gz") as tar:
                names = set(tar.getnames())
            self.assertIn("school/logos/crest.png", names)
            self.assertIn("private_student_photos/students/photos/a.jpg", names)
            self.assertIn("student_id_exports/exp.zip", names)

    def test_empty_media_produces_zero_count(self):
        from tenancy.management.commands.tenant_backup import Command

        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            with override_settings(
                MEDIA_ROOT=root / "media",
                PRIVATE_STUDENT_PHOTO_ROOT=root / "photos",
                BASE_DIR=root / "base",
                MEDIA_STORAGE_BACKEND="filesystem",
            ):
                archive = root / "empty.tar.gz"
                count, sources = Command()._archive_tenant_media(archive, "school_one")

            self.assertEqual(count, 0)
            self.assertEqual(sources, [])
            self.assertTrue(archive.exists())

    def test_s3_private_storage_is_unwrapped_for_archive_listing(self):
        from tenancy.management.commands.tenant_backup import Command

        item = SimpleNamespace(
            key="private/student_photos/tenants/school_one/students/a.jpg",
            size=5,
            last_modified=None,
        )
        remote_storage = Mock()
        remote_storage.location = "private/student_photos"
        remote_storage.bucket_name = "school-media"
        remote_storage.bucket.objects.filter.return_value = [item]
        remote_storage.open.return_value = io.BytesIO(b"photo")
        delegating_storage = Mock()
        delegating_storage.uses_s3.return_value = True
        delegating_storage._remote_storage.return_value = remote_storage

        with tempfile.TemporaryDirectory() as root:
            archive_path = Path(root) / "media.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                sources = []
                added = Command()._add_storage_tree(
                    archive,
                    delegating_storage,
                    "tenants/school_one",
                    "private_student_photos",
                    sources,
                )

            self.assertEqual(added, 1)
            delegating_storage._remote_storage.assert_called_once_with()
            remote_storage.bucket.objects.filter.assert_called_once_with(
                Prefix="private/student_photos/tenants/school_one"
            )
            remote_storage.open.assert_called_once_with(
                "tenants/school_one/students/a.jpg",
                "rb",
            )
            with tarfile.open(archive_path, "r:gz") as archive:
                member = archive.getmember(
                    "private_student_photos/students/a.jpg"
                )
                self.assertEqual(archive.extractfile(member).read(), b"photo")

    def test_empty_media_is_rejected_unless_explicitly_allowed(self):
        from tenancy.management.commands.tenant_backup import Command

        with self.assertRaisesMessage(
            CommandError,
            "--include-media found no files for tenant 'school_one'",
        ):
            Command._require_media_files(
                0,
                "school_one",
                allow_empty=False,
            )

        Command._require_media_files(
            0,
            "school_one",
            allow_empty=True,
        )


class TenantRestoreMediaTests(SimpleTestCase):
    @staticmethod
    def _create_archive(path, members):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(path, "w:gz") as archive:
            for name, content in members:
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

    def test_restore_writes_all_media_roots_through_configured_storage(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            backup_dir = root / "backups"
            archive_path = (
                backup_dir
                / "school_one"
                / "school_one_media_20261017_091800.tar.gz"
            )
            self._create_archive(
                archive_path,
                [
                    ("school/logos/crest.png", b"crest"),
                    ("private_student_photos/students/photos/a.jpg", b"photo"),
                    ("student_id_exports/export.zip", b"export"),
                ],
            )
            media_root = root / "media"
            photo_root = root / "private-photos"
            base_dir = root / "project"
            with override_settings(
                MEDIA_ROOT=media_root,
                PRIVATE_STUDENT_PHOTO_ROOT=photo_root,
                BASE_DIR=base_dir,
                MEDIA_STORAGE_BACKEND="filesystem",
            ):
                command = TenantRestoreCommand()
                resolved_path, members = command._validate_media_archive(
                    str(archive_path),
                    "school_one",
                    backup_dir,
                )
                count = command._restore_media_archive(resolved_path, members)

            self.assertEqual(count, 3)
            self.assertEqual(
                (media_root / "tenants" / "school_one" / "school" / "logos" / "crest.png").read_bytes(),
                b"crest",
            )
            self.assertEqual(
                (
                    photo_root
                    / "tenants"
                    / "school_one"
                    / "students"
                    / "photos"
                    / "a.jpg"
                ).read_bytes(),
                b"photo",
            )
            self.assertEqual(
                (
                    base_dir
                    / ".private"
                    / "student_id_exports"
                    / "school_one"
                    / "export.zip"
                ).read_bytes(),
                b"export",
            )

    def test_media_restore_rejects_traversal_and_existing_files(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            backup_dir = root / "backups"
            unsafe_archive = (
                backup_dir
                / "school_one"
                / "school_one_media_20261017_091800.tar.gz"
            )
            self._create_archive(unsafe_archive, [("../escape.txt", b"unsafe")])
            with override_settings(
                MEDIA_ROOT=root / "media",
                PRIVATE_STUDENT_PHOTO_ROOT=root / "photos",
                BASE_DIR=root / "project",
                MEDIA_STORAGE_BACKEND="filesystem",
            ):
                with self.assertRaisesMessage(CommandError, "Unsafe path"):
                    TenantRestoreCommand()._validate_media_archive(
                        str(unsafe_archive),
                        "school_one",
                        backup_dir,
                    )

                safe_archive = (
                    backup_dir
                    / "school_one"
                    / "school_one_media_20261017_092000.tar.gz"
                )
                self._create_archive(
                    safe_archive,
                    [("school/logos/crest.png", b"crest")],
                )
                existing = (
                    root / "media" / "tenants" / "school_one" / "school" / "logos"
                )
                existing.mkdir(parents=True)
                (existing / "crest.png").write_bytes(b"existing")

                with self.assertRaisesMessage(
                    CommandError,
                    "would overwrite existing file",
                ):
                    TenantRestoreCommand()._validate_media_archive(
                        str(safe_archive),
                        "school_one",
                        backup_dir,
                    )
