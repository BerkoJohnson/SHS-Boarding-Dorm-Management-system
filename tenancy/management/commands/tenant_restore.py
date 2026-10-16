"""
Management command to safely restore a tenant PostgreSQL schema from a backup.

Safety-first, strictly non-destructive restore:

  * Verifies the dump's SHA-256 checksum *and* its ``pg_restore`` table of
    contents before touching the database.
  * Refuses the public/reserved schemas, schemas already registered to a
    tenant, and schemas that already exist in PostgreSQL (nonempty schemas are
    rejected outright).
  * Refuses dumps whose embedded schema name does not match the target
    (schema mismatch) and dump paths outside the approved backup directory
    (unsafe paths, symlinks, path traversal).
  * Never issues ``DROP``/``--clean``/``--if-exists``: existing objects are
    never removed or overwritten.

Usage:
    python manage.py tenant_restore --schema=atebubu_shs --dump=/path/to.dump
    python manage.py tenant_restore --schema=atebubu_shs --dump=/path/to.dump --dry-run
    python manage.py tenant_restore --schema=atebubu_shs --dump=/path/to.dump --json
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Dict, Optional, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from tenancy.models import PlatformAuditLog, Tenant

#: Schemas that must never be the target of a restore.
RESERVED_SCHEMAS = {
    "public",
    "information_schema",
    "pg_catalog",
    "pg_toast",
}

#: Conservative, injection-proof identifier pattern (matches pgschemas).
SCHEMA_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

#: Only pg_dump custom-format archives are supported for a verifiable restore.
SAFE_DUMP_SUFFIXES = {".dump", ".backup", ".custom", ".tar"}

CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")


class Command(BaseCommand):
    """Safely restore a tenant schema from a verified backup archive."""

    help = (
        "Restore an isolated tenant PostgreSQL schema from a verified pg_dump "
        "archive without ever dropping or cleaning existing database objects."
    )

    def add_arguments(self, parser):
        """Register CLI options for tenant restore."""
        parser.add_argument(
            "--schema",
            type=str,
            required=True,
            help="Target tenant schema name to restore into.",
        )
        parser.add_argument(
            "--dump",
            type=str,
            default=None,
            help=(
                "Path to the pg_dump custom archive. If omitted, the newest "
                "archive under <backup-dir>/<schema>/ is used."
            ),
        )
        parser.add_argument(
            "--checksum",
            type=str,
            default=None,
            help="Path to the .sha256 checksum file (default: <dump>.sha256).",
        )
        parser.add_argument(
            "--backup-dir",
            type=str,
            default=None,
            help=(
                "Approved backup directory (default: "
                ".private/backups/tenants/ inside the project base directory)."
            ),
        )
        parser.add_argument(
            "--operator",
            type=str,
            default="cli",
            help="Operator identifier for audit logging.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run every integrity and safety check but do not restore.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="Output machine-readable JSON summary.",
        )

    def handle(self, *args, **options):
        """Validate and execute a safe tenant restore."""
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before using platform management commands."
            )

        pg_restore_path = shutil.which("pg_restore")
        if not pg_restore_path:
            raise CommandError(
                "pg_restore executable not found in system PATH. Ensure PostgreSQL "
                "client tools are installed and accessible."
            )

        result = self._restore(
            schema_name=options["schema"],
            dump=options.get("dump"),
            checksum=options.get("checksum"),
            backup_dir=options.get("backup_dir"),
            operator=options["operator"],
            dry_run=options["dry_run"],
            pg_restore_path=pg_restore_path,
        )

        if options.get("json"):
            self.stdout.write(json.dumps(result, indent=2))
        else:
            self._print_result(result)

    # ------------------------------------------------------------------ #
    # Validation helpers
    # ------------------------------------------------------------------ #
    def _validate_target_schema(self, schema_name: str) -> None:
        """Refuse reserved or syntactically unsafe schema names."""
        if not schema_name:
            raise CommandError("A target schema name is required.")
        if schema_name in RESERVED_SCHEMAS or schema_name.startswith("pg_"):
            raise CommandError(
                f"Refusing to restore into the reserved schema '{schema_name}'."
            )
        if not SCHEMA_NAME_RE.match(schema_name):
            raise CommandError(
                f"'{schema_name}' is not a valid, safe PostgreSQL schema identifier."
            )

    def _resolve_backup_dir(self, backup_dir: Optional[str]) -> Path:
        """Return the approved, resolved backup directory."""
        base = (
            Path(backup_dir)
            if backup_dir
            else Path(settings.BASE_DIR) / ".private" / "backups" / "tenants"
        )
        return base.resolve()

    def _validate_dump_path(
        self, dump: Optional[str], schema_name: str, base_dir: Path
    ) -> Path:
        """Resolve and validate the dump path stays inside the backup directory."""
        if not dump:
            dump = self._newest_archive(schema_name, base_dir)

        raw = Path(dump)
        if raw.is_symlink():
            raise CommandError(f"Refusing to restore from a symlink: '{raw}'.")

        path = raw.resolve()
        if not path.exists() or not path.is_file():
            raise CommandError(f"Backup file '{raw}' does not exist.")

        if path.suffix.lower() not in SAFE_DUMP_SUFFIXES:
            raise CommandError(
                f"Unsupported backup format '{path.suffix}'. A pg_dump custom "
                "archive (.dump) is required for a verifiable restore."
            )

        if not self._is_within(path, base_dir):
            raise CommandError(
                f"Backup path '{path}' is outside the approved backup "
                f"directory '{base_dir}'."
            )
        return path

    def _newest_archive(self, schema_name: str, base_dir: Path) -> str:
        """Locate the newest custom-format archive for a schema."""
        tenant_dir = base_dir / schema_name
        candidates = (
            sorted(
                (
                    item
                    for item in tenant_dir.glob("*")
                    if item.is_file()
                    and not item.is_symlink()
                    and item.suffix.lower() in SAFE_DUMP_SUFFIXES
                ),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            if tenant_dir.is_dir()
            else []
        )
        if not candidates:
            raise CommandError(
                f"No backup archive found for schema '{schema_name}' under "
                f"'{tenant_dir}'. Pass --dump explicitly."
            )
        return str(candidates[0])

    def _validate_checksum_path(
        self, dump: Path, checksum: Optional[str], base_dir: Path
    ) -> Path:
        """Resolve and validate the checksum file path."""
        path = (
            Path(checksum).resolve()
            if checksum
            else dump.with_name(f"{dump.name}.sha256")
        )
        if not path.exists() or not path.is_file():
            raise CommandError(
                f"Checksum file '{path}' not found; cannot verify backup integrity."
            )
        if not self._is_within(path, base_dir):
            raise CommandError(
                f"Checksum path '{path}' is outside the approved backup "
                f"directory '{base_dir}'."
            )
        return path

    def _verify_checksum(self, dump: Path, checksum_file: Path) -> str:
        """Recompute the SHA-256 of the dump and compare it with the record."""
        content = checksum_file.read_text(encoding="utf-8").strip()
        if not content:
            raise CommandError(f"Checksum file '{checksum_file}' is empty.")

        expected = content.split()[0].lower()
        if not CHECKSUM_RE.match(expected):
            raise CommandError(
                f"Checksum file '{checksum_file}' does not contain a valid "
                "SHA-256 hash."
            )

        digest = hashlib.sha256()
        with open(dump, "rb") as handle:
            for block in iter(lambda: handle.read(65536), b""):
                digest.update(block)
        actual = digest.hexdigest()

        if actual != expected:
            raise CommandError(
                f"Backup integrity check failed for '{dump.name}': expected "
                f"{expected[:12]}..., computed {actual[:12]}...."
            )
        return actual

    def _dump_schema_name(self, dump: Path, pg_restore_path: str) -> Optional[str]:
        """Read the archive table of contents and return its schema name."""
        proc = subprocess.run(
            [pg_restore_path, "-l", str(dump)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise CommandError(
                f"pg_restore could not read the archive '{dump.name}':\n{proc.stderr}"
            )

        schemas = []
        for line in proc.stdout.splitlines():
            match = re.search(r"\bSCHEMA\s+-\s+(\S+)", line)
            if match:
                schemas.append(match.group(1))

        unique = list(dict.fromkeys(schemas))
        if len(unique) > 1:
            raise CommandError(
                f"Dump '{dump.name}' contains multiple schemas {unique}; "
                "refusing to restore an ambiguous archive."
            )
        return unique[0] if unique else None

    def _schema_state(self, schema_name: str) -> Tuple[bool, bool]:
        """Return ``(exists, empty)`` for a PostgreSQL schema."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname = %s",
                [schema_name],
            )
            exists = cursor.fetchone() is not None
            if not exists:
                return False, True

            cursor.execute(
                """
                SELECT count(*)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = %s
                  AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
                """,
                [schema_name],
            )
            relation_count = cursor.fetchone()[0]
        return True, relation_count == 0

    @staticmethod
    def _is_within(path: Path, base_dir: Path) -> bool:
        try:
            path.relative_to(base_dir)
        except ValueError:
            return False
        return True

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #
    def _restore(
        self,
        *,
        schema_name: str,
        dump: Optional[str],
        checksum: Optional[str],
        backup_dir: Optional[str],
        operator: str,
        dry_run: bool,
        pg_restore_path: str,
    ) -> Dict[str, Any]:
        """Run every safety check and (unless dry-run) restore the schema."""
        start_time = time.time()

        self._validate_target_schema(schema_name)
        base_dir = self._resolve_backup_dir(backup_dir)
        dump_path = self._validate_dump_path(dump, schema_name, base_dir)
        checksum_path = self._validate_checksum_path(dump_path, checksum, base_dir)
        checksum_value = self._verify_checksum(dump_path, checksum_path)

        source_schema = self._dump_schema_name(dump_path, pg_restore_path)
        if source_schema is None:
            raise CommandError(
                f"Could not determine the source schema from '{dump_path.name}'. "
                "Refusing to restore an unverifiable archive."
            )
        if source_schema != schema_name:
            raise CommandError(
                f"Schema mismatch: the archive contains schema '{source_schema}' "
                f"but the restore target is '{schema_name}'. Refusing to restore "
                "across schemas."
            )

        if Tenant.objects.filter(schema_name=schema_name).exists():
            raise CommandError(
                f"Schema '{schema_name}' is registered to a tenant. Refusing to "
                "overwrite a registered tenant; decommission it before restoring."
            )

        exists, empty = self._schema_state(schema_name)
        if exists and not empty:
            raise CommandError(
                f"Schema '{schema_name}' already exists and is not empty. "
                "Refusing to restore into a nonempty schema."
            )
        if exists:
            raise CommandError(
                f"Schema '{schema_name}' already exists. Refusing to restore into "
                "an existing schema (existing objects are never dropped)."
            )

        base_result: Dict[str, Any] = {
            "schema_name": schema_name,
            "dump_file": str(dump_path),
            "dump_filename": dump_path.name,
            "sha256": checksum_value,
            "source_schema": source_schema,
            "dry_run": dry_run,
        }

        if dry_run:
            base_result["status"] = "dry_run"
            base_result["duration_seconds"] = round(time.time() - start_time, 2)
            return base_result

        db_conf = settings.DATABASES["default"]
        env = os.environ.copy()
        if db_conf.get("PASSWORD"):
            env["PGPASSWORD"] = str(db_conf["PASSWORD"])

        cmd = [
            pg_restore_path,
            "-h",
            str(db_conf.get("HOST", "localhost") or "localhost"),
            "-p",
            str(db_conf.get("PORT", "5432") or "5432"),
            "-U",
            str(db_conf.get("USER", "")),
            "-d",
            str(db_conf["NAME"]),
            "--no-owner",
            "--no-privileges",
            "--exit-on-error",
            str(dump_path),
        ]

        proc = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise CommandError(
                f"pg_restore failed for schema '{schema_name}' with code "
                f"{proc.returncode}:\n{proc.stderr}"
            )

        # Confirm the schema now exists with objects.
        exists, empty = self._schema_state(schema_name)
        if not exists or empty:
            raise CommandError(
                f"pg_restore reported success but schema '{schema_name}' is "
                "missing or empty."
            )

        duration = round(time.time() - start_time, 2)

        PlatformAuditLog.objects.create(
            action="tenant_restore",
            tenant_schema=schema_name,
            detail=(
                f"Schema restored from {dump_path.name} "
                f"(SHA256: {checksum_value[:12]}...)."
            ),
            operator=operator,
            metadata={
                "dump_path": str(dump_path),
                "sha256": checksum_value,
                "source_schema": source_schema,
                "duration_seconds": duration,
            },
        )

        base_result.update(
            {
                "status": "success",
                "duration_seconds": duration,
            }
        )
        return base_result

    def _print_result(self, result: Dict[str, Any]) -> None:
        """Render a terminal summary of the restore."""
        header_line = "=" * 70
        if result["status"] == "dry_run":
            self.stdout.write(
                self.style.WARNING(
                    f"DRY RUN OK: schema '{result['schema_name']}' passed all "
                    "integrity and safety checks; nothing was restored."
                )
            )
            self.stdout.write(f"  Archive:  {result['dump_file']}")
            self.stdout.write(f"  SHA-256:  {result['sha256']}")
            return

        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write(
            self.style.NOTICE("|  ")
            + self.style.SUCCESS(
                f"TENANT RESTORE COMPLETED: {result['schema_name']}"
            )
        )
        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write(f"  Archive:  {result['dump_file']}")
        self.stdout.write(f"  SHA-256:  {result['sha256']}")
        self.stdout.write(f"  Finished in {result['duration_seconds']}s.")
