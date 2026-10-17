"""
Management command to safely back up tenant PostgreSQL schemas and media files.

Executes schema-isolated pg_dump, generates SHA-256 checksums, bundles
tenant media uploads into tar.gz archives, and verifies dump integrity.

Usage:
    python manage.py tenant_backup --schema=atebubu_shs
    python manage.py tenant_backup --schema=atebubu_shs --include-media --verify
    python manage.py tenant_backup --all
    python manage.py tenant_backup --schema=atebubu_shs --json
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.files.storage import storages
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from core.storage import PrivateStudentIDExportStorage, PrivateStudentPhotoStorage
from tenancy.models import PlatformAuditLog, Tenant


class Command(BaseCommand):
    """Safely back up tenant database schemas and associated media assets."""

    help = (
        "Export an isolated tenant PostgreSQL schema via pg_dump, calculate "
        "SHA-256 integrity checksums, and optionally archive media uploads."
    )

    def add_arguments(self, parser):
        """Register CLI options for tenant backup."""
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--schema",
            type=str,
            help="Target tenant schema name to back up.",
        )
        group.add_argument(
            "--all",
            action="store_true",
            help="Sequentially back up all active tenants.",
        )
        parser.add_argument(
            "--output-dir",
            type=str,
            default=None,
            help=(
                "Destination directory for backups (default: "
                ".private/backups/tenants/ inside project base directory)."
            ),
        )
        parser.add_argument(
            "--format",
            type=str,
            choices=["custom", "sql"],
            default="custom",
            help="pg_dump format: 'custom' (-F c, compressed) or 'sql' (-F p, plain text).",
        )
        parser.add_argument(
            "--include-media",
            action="store_true",
            help="Also archive tenant-specific media and document files into a .tar.gz archive.",
        )
        parser.add_argument(
            "--allow-empty-media",
            action="store_true",
            help="Allow --include-media to succeed when the tenant has no media files.",
        )
        parser.add_argument(
            "--verify",
            action="store_true",
            help="Verify the generated dump file's table-of-contents using pg_restore.",
        )
        parser.add_argument(
            "--operator",
            type=str,
            default="cli",
            help="Operator identifier for audit logging.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output machine-readable JSON summary.",
        )

    def handle(self, *args, **options):
        """Execute tenant backup process."""
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before using platform management commands."
            )
        if options.get("allow_empty_media") and not options.get("include_media"):
            raise CommandError("--allow-empty-media requires --include-media.")

        pg_dump_path = shutil.which("pg_dump")
        if not pg_dump_path:
            raise CommandError(
                "pg_dump executable not found in system PATH. Ensure PostgreSQL "
                "client tools are installed and accessible."
            )

        # Resolve output directory
        base_output_dir = (
            Path(options["output_dir"])
            if options.get("output_dir")
            else Path(settings.BASE_DIR) / ".private" / "backups" / "tenants"
        )
        base_output_dir.mkdir(parents=True, exist_ok=True)

        # Determine target tenants
        if options.get("all"):
            tenants = list(Tenant.objects.filter(is_active=True).order_by("schema_name"))
            if not tenants:
                self.stdout.write(
                    self.style.WARNING("No active tenants found to back up.")
                )
                return
        else:
            schema_name = options["schema"]
            tenant = Tenant.objects.filter(schema_name=schema_name).first()
            if tenant is None:
                raise CommandError(f"Tenant schema '{schema_name}' was not found.")
            tenants = [tenant]

        results: List[Dict[str, Any]] = []

        for tenant in tenants:
            result = self._backup_tenant(
                tenant=tenant,
                base_output_dir=base_output_dir,
                dump_format=options["format"],
                include_media=options["include_media"],
                allow_empty_media=options["allow_empty_media"],
                verify=options["verify"],
                operator=options["operator"],
            )
            results.append(result)

            if not options.get("json"):
                self._print_backup_result(result)

        if options.get("json"):
            payload = {
                "total_backups": len(results),
                "successful": sum(1 for r in results if r["status"] == "success"),
                "failed": sum(1 for r in results if r["status"] == "failed"),
                "backups": results,
                "generated_at": timezone.now().isoformat(),
            }
            self.stdout.write(json.dumps(payload, indent=2))

    def _backup_tenant(
        self,
        *,
        tenant: Tenant,
        base_output_dir: Path,
        dump_format: str,
        include_media: bool,
        allow_empty_media: bool,
        verify: bool,
        operator: str,
    ) -> Dict[str, Any]:
        """Perform database and media backup for a single tenant."""
        start_time = time.time()
        schema_name = tenant.schema_name
        tenant_dir = base_output_dir / schema_name
        tenant_dir.mkdir(parents=True, exist_ok=True)

        timestamp = timezone.now().strftime("%Y%m%d_%H%M%S")
        ext = "dump" if dump_format == "custom" else "sql"
        dump_filename = f"{schema_name}_{timestamp}.{ext}"
        dump_path = tenant_dir / dump_filename
        sha_path = tenant_dir / f"{dump_filename}.sha256"

        db_conf = settings.DATABASES["default"]
        db_name = db_conf["NAME"]
        db_user = db_conf.get("USER", "")
        db_host = db_conf.get("HOST", "localhost") or "localhost"
        db_port = str(db_conf.get("PORT", "5432") or "5432")
        db_password = db_conf.get("PASSWORD", "")

        # Verify schema exists in PostgreSQL
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                [schema_name],
            )
            if not cursor.fetchone():
                return {
                    "schema_name": schema_name,
                    "status": "failed",
                    "error": f"Schema '{schema_name}' does not exist in PostgreSQL.",
                    "duration_seconds": round(time.time() - start_time, 2),
                }

        # Build pg_dump command
        fmt_flag = "c" if dump_format == "custom" else "p"
        cmd = [
            "pg_dump",
            "-h", db_host,
            "-p", db_port,
            "-U", db_user,
            "-d", db_name,
            f"--schema={schema_name}",
            "-F", fmt_flag,
            "-b",
            "-f", str(dump_path),
        ]

        env = os.environ.copy()
        if db_password:
            env["PGPASSWORD"] = str(db_password)

        try:
            proc = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise CommandError(
                    f"pg_dump failed for schema '{schema_name}' with code {proc.returncode}:\n{proc.stderr}"
                )
        except Exception as error:
            return {
                "schema_name": schema_name,
                "status": "failed",
                "error": str(error),
                "duration_seconds": round(time.time() - start_time, 2),
            }

        # Calculate SHA-256
        sha256_hash = hashlib.sha256()
        with open(dump_path, "rb") as f:
            for byte_block in iter(lambda: f.read(65536), b""):
                sha256_hash.update(byte_block)
        checksum = sha256_hash.hexdigest()

        with open(sha_path, "w", encoding="utf-8") as f:
            f.write(f"{checksum}  {dump_filename}\n")

        dump_size = dump_path.stat().st_size

        # Verification if requested
        verification_passed = None
        if verify and dump_format == "custom":
            pg_restore_path = shutil.which("pg_restore")
            if pg_restore_path:
                vproc = subprocess.run(
                    [pg_restore_path, "-l", str(dump_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                verification_passed = (vproc.returncode == 0)

        # Media archive if requested
        media_path_str: Optional[str] = None
        media_size: Optional[int] = None
        media_count = 0
        media_sources: List[str] = []
        if include_media:
            media_filename = f"{schema_name}_media_{timestamp}.tar.gz"
            media_archive_path = tenant_dir / media_filename

            media_count, media_sources = self._archive_tenant_media(
                media_archive_path,
                schema_name,
            )

            if media_count == 0 and not allow_empty_media:
                media_archive_path.unlink(missing_ok=True)
                self._require_media_files(
                    media_count,
                    schema_name,
                    allow_empty=allow_empty_media,
                )

            media_path_str = str(media_archive_path)
            media_size = media_archive_path.stat().st_size

        duration = round(time.time() - start_time, 2)

        # Record audit log
        PlatformAuditLog.objects.create(
            action="tenant_backup",
            tenant_schema=schema_name,
            detail=(
                f"Schema backup completed: {dump_filename} ({dump_size / (1024*1024):.2f} MB), "
                f"SHA256: {checksum[:12]}..., Media: {media_count} files."
            ),
            operator=operator,
            metadata={
                "dump_path": str(dump_path),
                "dump_size_bytes": dump_size,
                "sha256": checksum,
                "format": dump_format,
                "verified": verification_passed,
                "media_path": media_path_str,
                "media_files": media_count,
                "media_sources": media_sources,
                "duration_seconds": duration,
            },
        )

        return {
            "schema_name": schema_name,
            "school_name": tenant.name,
            "status": "success",
            "dump_file": str(dump_path),
            "dump_filename": dump_filename,
            "dump_size_bytes": dump_size,
            "sha256": checksum,
            "verified": verification_passed,
            "media_file": media_path_str,
            "media_size_bytes": media_size,
            "media_files_count": media_count,
            "media_sources": media_sources,
            "duration_seconds": duration,
        }

    @staticmethod
    def _uses_s3_media() -> bool:
        return (
            getattr(settings, "MEDIA_STORAGE_BACKEND", "filesystem") == "neon_s3"
        )

    @staticmethod
    def _require_media_files(
        count: int,
        schema_name: str,
        *,
        allow_empty: bool,
    ) -> None:
        if count == 0 and not allow_empty:
            raise CommandError(
                f"--include-media found no files for tenant '{schema_name}'. "
                "The backup is incomplete; use --allow-empty-media only "
                "when an empty media archive is expected."
            )

    def _archive_tenant_media(
        self,
        archive_path: Path,
        schema_name: str,
    ):
        """Archive every media root that belongs to a tenant.

        Returns ``(file_count, source_labels)``. In filesystem mode the
        configured local roots are walked; in S3 mode the matching object
        prefixes are streamed straight into the archive. Both the public
        tenant media (``MEDIA_ROOT/tenants/<schema>``) and the private roots
        (student photos and generated student-ID exports) are included, which
        the previous implementation silently omitted.
        """
        count = 0
        sources: List[str] = []
        with tarfile.open(archive_path, "w:gz") as tar:
            if self._uses_s3_media():
                count += self._add_storage_tree(
                    tar,
                    storages["default"],
                    f"tenants/{schema_name}",
                    "",
                    sources,
                )
                count += self._add_storage_tree(
                    tar,
                    PrivateStudentPhotoStorage(),
                    f"tenants/{schema_name}",
                    "private_student_photos",
                    sources,
                )
                count += self._add_storage_tree(
                    tar,
                    PrivateStudentIDExportStorage(),
                    schema_name,
                    "student_id_exports",
                    sources,
                )
            else:
                count += self._add_local_tree(
                    tar,
                    Path(settings.MEDIA_ROOT) / "tenants" / schema_name,
                    "",
                    sources,
                )
                count += self._add_local_tree(
                    tar,
                    Path(settings.PRIVATE_STUDENT_PHOTO_ROOT)
                    / "tenants"
                    / schema_name,
                    "private_student_photos",
                    sources,
                )
                count += self._add_local_tree(
                    tar,
                    Path(settings.BASE_DIR)
                    / ".private"
                    / "student_id_exports"
                    / schema_name,
                    "student_id_exports",
                    sources,
                )
        return count, sources

    def _add_local_tree(
        self,
        tar: tarfile.TarFile,
        root: Path,
        arc_prefix: str,
        sources: List[str],
    ) -> int:
        if not root.is_dir():
            return 0
        added = 0
        for dirpath, _directories, files in os.walk(root):
            for filename in files:
                full_path = Path(dirpath) / filename
                relative = full_path.relative_to(root).as_posix()
                arcname = f"{arc_prefix}/{relative}" if arc_prefix else relative
                tar.add(full_path, arcname=arcname, recursive=False)
                added += 1
        if added:
            sources.append(f"{arc_prefix or 'media'}:{root}")
        return added

    def _add_storage_tree(
        self,
        tar: tarfile.TarFile,
        storage,
        relative_prefix: str,
        arc_prefix: str,
        sources: List[str],
    ) -> int:
        uses_s3 = getattr(storage, "uses_s3", None)
        if callable(uses_s3) and uses_s3():
            storage = storage._remote_storage()
        bucket = getattr(storage, "bucket", None)
        if bucket is None:
            return 0
        location = str(getattr(storage, "location", "") or "").strip("/")
        key_prefix = (
            f"{location}/{relative_prefix}".strip("/")
            if location
            else relative_prefix
        )
        added = 0
        for obj in bucket.objects.filter(Prefix=key_prefix):
            key = obj.key
            if key.endswith("/"):
                continue
            name = (
                key[len(key_prefix):].lstrip("/")
                if key.startswith(key_prefix)
                else key
            )
            if not name:
                continue
            arcname = f"{arc_prefix}/{name}" if arc_prefix else name
            tarinfo = tarfile.TarInfo(arcname)
            tarinfo.size = obj.size
            last_modified = getattr(obj, "last_modified", None)
            if last_modified is not None:
                tarinfo.mtime = last_modified.timestamp()
            relative_name = (
                key[len(location) + 1:]
                if location and key.startswith(f"{location}/")
                else key
            )
            with storage.open(relative_name, "rb") as handle:
                tar.addfile(tarinfo, handle)
            added += 1
        if added:
            sources.append(
                f"{arc_prefix or 'media'}:s3://"
                f"{getattr(storage, 'bucket_name', '')}/{key_prefix}"
            )
        return added

    def _print_backup_result(self, result: Dict[str, Any]):
        """Render terminal progress and restore instructions."""
        header_line = "=" * 70
        sep_line = "-" * 70

        if result["status"] == "failed":
            self.stdout.write(
                self.style.ERROR(
                    f"Backup failed for schema '{result['schema_name']}': {result['error']}"
                )
            )
            return

        schema = result["schema_name"]
        school = result["school_name"]
        size_mb = result["dump_size_bytes"] / (1024 * 1024)

        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write(
            self.style.NOTICE("|  ")
            + self.style.SUCCESS(f"TENANT BACKUP COMPLETED: {schema} ({school})")
        )
        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))

        self.stdout.write(
            f"  [1/3] Database Schema Dump: {self.style.SUCCESS('[OK]')}\n"
            f"        File: {result['dump_file']}\n"
            f"        Size: {size_mb:.2f} MB"
        )

        self.stdout.write(
            f"  [2/3] SHA-256 Checksum:     {self.style.SUCCESS('[OK]')}\n"
            f"        Hash: {result['sha256']}"
        )

        if result["media_file"]:
            media_mb = (result["media_size_bytes"] or 0) / (1024 * 1024)
            self.stdout.write(
                f"  [3/3] Media Assets Archive: {self.style.SUCCESS('[OK]')}\n"
                f"        File:  {result['media_file']}\n"
                f"        Files: {result['media_files_count']} archived ({media_mb:.2f} MB)"
            )
        else:
            self.stdout.write("  [3/3] Media Assets:         [SKIPPED] (use --include-media to archive)")

        if result["verified"] is True:
            self.stdout.write(f"  Dump Header Verification:  {self.style.SUCCESS('[VERIFIED]')}")
        elif result["verified"] is False:
            self.stdout.write(f"  Dump Header Verification:  {self.style.ERROR('[VERIFICATION FAILED]')}")

        self.stdout.write(sep_line)
        self.stdout.write(
            f"Backup finished in {result['duration_seconds']}s.\n"
            f"To restore this schema:\n"
            f"  pg_restore -h localhost -U postgres -d schoolsystem_db \\\n"
            f"      --clean --if-exists -v \\\n"
            f"      \"{result['dump_file']}\"\n"
        )
