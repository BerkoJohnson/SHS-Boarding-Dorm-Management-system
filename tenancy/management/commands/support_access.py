"""
Management command to generate, verify, revoke, and inspect time-bound support access tokens.

Allows platform engineers to request emergency or troubleshooting access to a
tenant schema with cryptographic time-bound authorization.
Tokens are bcrypt-hashed (cost=12) and never stored in plaintext.

Usage:
    python manage.py support_access --schema=atebubu_test --generate --ttl-hours=4 --reason="Fixing SMS sync"
    python manage.py support_access --schema=atebubu_test --verify="TOKEN_STRING"
    python manage.py support_access --schema=atebubu_test --status
    python manage.py support_access --schema=atebubu_test --revoke --reason="Issue resolved"
    python manage.py support_access --schema=atebubu_test --status --json
"""

from datetime import timedelta
import json
import secrets
from typing import Any, Dict

import bcrypt
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tenancy.models import PlanTier, PlatformAuditLog, Tenant, TenantSubscription


class Command(BaseCommand):
    """Manage time-bound, cryptographically-secure support access tokens."""

    help = (
        "Generate, verify, inspect, or revoke time-bound support access tokens "
        "for troubleshooting a tenant schema. This is intentionally a CLI-only "
        "audit token flow; it does not create a remote support session or live "
        "interactive access channel."
    )

    def add_arguments(self, parser):
        """Register CLI options for support access."""
        parser.add_argument(
            "--schema",
            type=str,
            required=True,
            help="Target tenant schema name.",
        )
        parser.add_argument(
            "--generate",
            action="store_true",
            help="Generate a new support access token.",
        )
        parser.add_argument(
            "--ttl-hours",
            type=int,
            default=4,
            help="Token lifetime in hours (1 to 168, default: 4).",
        )
        parser.add_argument(
            "--verify",
            type=str,
            help="Verify a plaintext support token against stored hash.",
        )
        parser.add_argument(
            "--revoke",
            action="store_true",
            help="Revoke any active support access token for this tenant.",
        )
        parser.add_argument(
            "--status",
            action="store_true",
            help="Inspect active token validity and audit history.",
        )
        parser.add_argument(
            "--reason",
            type=str,
            default="",
            help="Administrative reason for granting or revoking access.",
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
            help="Output machine-readable JSON format.",
        )

    def handle(self, *args, **options):
        """Execute support access actions."""
        if not settings.TENANCY_ENABLED:
            raise CommandError(
                "Set TENANCY_ENABLED=true before using platform management commands."
            )

        schema_name = options["schema"]
        tenant = Tenant.objects.filter(schema_name=schema_name).first()
        if tenant is None:
            raise CommandError(f"Tenant schema '{schema_name}' was not found.")

        sub = getattr(tenant, "subscription", None)
        if sub is None:
            sub, _ = TenantSubscription.objects.get_or_create(
                tenant=tenant,
                defaults={"plan_tier": PlanTier.FREE},
            )

        # 1. Action: Generate
        if options.get("generate"):
            self._handle_generate(tenant, sub, options)
            return

        # 2. Action: Verify
        if options.get("verify"):
            self._handle_verify(tenant, sub, options["verify"], options)
            return

        # 3. Action: Revoke
        if options.get("revoke"):
            self._handle_revoke(tenant, sub, options)
            return

        # 4. Action: Status (default)
        self._handle_status(tenant, sub, options)

    def _handle_generate(
        self, tenant: Tenant, sub: TenantSubscription, options: Dict[str, Any]
    ):
        """Generate a cryptographically secure token and save its bcrypt hash."""
        ttl = options.get("ttl_hours", 4)
        if ttl < 1 or ttl > 168:
            raise CommandError("TTL must be between 1 and 168 hours (7 days maximum).")

        reason = options.get("reason", "").strip()
        if not reason:
            self.stderr.write(
                self.style.WARNING(
                    "Notice: Consider specifying --reason for compliance audit."
                )
            )

        operator = options.get("operator", "cli")

        # Generate 32 bytes (256 bits) of entropy
        plaintext_token = secrets.token_urlsafe(32)
        token_bytes = plaintext_token.encode("utf-8")
        token_hash = bcrypt.hashpw(token_bytes, bcrypt.gensalt(rounds=12)).decode(
            "ascii"
        )

        now = timezone.now()
        expires_at = now + timedelta(hours=ttl)

        sub.support_token_hash = token_hash
        sub.support_token_expires_at = expires_at
        sub.support_token_granted_by = operator
        sub.save(
            update_fields=[
                "support_token_hash",
                "support_token_expires_at",
                "support_token_granted_by",
                "updated_at",
            ]
        )

        PlatformAuditLog.objects.create(
            action="support_token_generate",
            tenant_schema=tenant.schema_name,
            detail=f"Support token generated (TTL: {ttl}h). Reason: '{reason}'.",
            operator=operator,
            metadata={
                "ttl_hours": ttl,
                "expires_at": expires_at.isoformat(),
                "reason": reason,
            },
        )

        if options.get("json"):
            payload = {
                "tenant": tenant.schema_name,
                "status": "generated",
                "token": plaintext_token,
                "ttl_hours": ttl,
                "expires_at": expires_at.isoformat(),
                "granted_by": operator,
                "reason": reason,
            }
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            header_line = "=" * 70
            self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
            self.stdout.write(
                self.style.NOTICE("|  ")
                + self.style.SUCCESS(
                    f"SUPPORT ACCESS TOKEN GENERATED: {tenant.schema_name}"
                )
            )
            self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "  +------------------------------------------------------+"
                )
            )
            self.stdout.write(f"  |  Token:  {self.style.SUCCESS(plaintext_token)}  |")
            self.stdout.write(
                self.style.WARNING(
                    "  |  COPY THIS TOKEN NOW -- it will NEVER be shown again! |"
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    "  +------------------------------------------------------+"
                )
            )
            self.stdout.write("")
            self.stdout.write(f"  Granted by:  {operator}")
            self.stdout.write(f"  TTL:         {ttl} hours")
            self.stdout.write(f"  Expires:     {expires_at:%Y-%m-%d %H:%M:%S UTC}")
            if reason:
                self.stdout.write(f"  Reason:      {reason}")
            self.stdout.write("")
            self.stdout.write(
                f"  To verify this token:\n"
                f"    python manage.py support_access --schema={tenant.schema_name} \\\n"
                f'        --verify="{plaintext_token}"\n'
            )

    def _handle_verify(
        self,
        tenant: Tenant,
        sub: TenantSubscription,
        token_candidate: str,
        options: Dict[str, Any],
    ):
        """Verify candidate plaintext token against bcrypt hash and expiry."""
        operator = options.get("operator", "cli")
        token_candidate = token_candidate.strip()

        if not sub.support_token_hash or not sub.support_token_expires_at:
            self._log_and_fail(tenant, operator, "No active support token exists.")
            return

        now = timezone.now()
        if now >= sub.support_token_expires_at:
            self._log_and_fail(
                tenant,
                operator,
                f"Token expired at {sub.support_token_expires_at:%Y-%m-%d %H:%M:%S UTC}.",
            )
            return

        is_valid = bcrypt.checkpw(
            token_candidate.encode("utf-8"),
            sub.support_token_hash.encode("ascii"),
        )

        if not is_valid:
            self._log_and_fail(tenant, operator, "Candidate token did not match.")
            return

        # Verification succeeded
        PlatformAuditLog.objects.create(
            action="support_token_verify",
            tenant_schema=tenant.schema_name,
            detail="Support token verified successfully.",
            operator=operator,
        )

        remaining = sub.support_token_expires_at - now
        hours, rem = divmod(int(remaining.total_seconds()), 3600)
        minutes, _ = divmod(rem, 60)

        if options.get("json"):
            payload = {
                "tenant": tenant.schema_name,
                "verified": True,
                "expires_at": sub.support_token_expires_at.isoformat(),
                "remaining_time": f"{hours}h {minutes}m",
                "granted_by": sub.support_token_granted_by,
            }
            self.stdout.write(json.dumps(payload, indent=2))
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n[VALID] Support access token for '{tenant.schema_name}' is VALID.\n"
                    f"  Granted by:  {sub.support_token_granted_by}\n"
                    f"  Expires in:  {hours}h {minutes}m ({sub.support_token_expires_at:%Y-%m-%d %H:%M:%S UTC})\n"
                )
            )

    def _log_and_fail(self, tenant: Tenant, operator: str, reason: str):
        """Record failed verification audit entry and print error."""
        PlatformAuditLog.objects.create(
            action="support_token_verify_failed",
            tenant_schema=tenant.schema_name,
            detail=f"Verification failed: {reason}",
            operator=operator,
        )
        self.stdout.write(
            self.style.ERROR(
                f"\n[INVALID] Support token verification failed for '{tenant.schema_name}': {reason}\n"
            )
        )

    def _handle_revoke(
        self, tenant: Tenant, sub: TenantSubscription, options: Dict[str, Any]
    ):
        """Immediately invalidate and clear active support token."""
        if not sub.support_token_hash:
            raise CommandError(
                f"No active support token exists for tenant '{tenant.schema_name}'."
            )

        operator = options.get("operator", "cli")
        reason = options.get("reason", "").strip()

        sub.support_token_hash = ""
        sub.support_token_expires_at = None
        sub.support_token_granted_by = ""
        sub.save(
            update_fields=[
                "support_token_hash",
                "support_token_expires_at",
                "support_token_granted_by",
                "updated_at",
            ]
        )

        PlatformAuditLog.objects.create(
            action="support_token_revoke",
            tenant_schema=tenant.schema_name,
            detail=f"Support token revoked. Reason: '{reason}'.",
            operator=operator,
            metadata={"reason": reason},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"[REVOKED] Support access token for '{tenant.schema_name}' has been revoked."
            )
        )

    def _handle_status(
        self, tenant: Tenant, sub: TenantSubscription, options: Dict[str, Any]
    ):
        """Inspect token status and recent audit trail."""
        now = timezone.now()
        is_valid = sub.is_support_token_valid

        recent_logs = list(
            PlatformAuditLog.objects.filter(
                tenant_schema=tenant.schema_name,
                action__startswith="support_token_",
            ).order_by("-timestamp")[:5]
        )

        if options.get("json"):
            payload = {
                "tenant": tenant.schema_name,
                "is_token_active": is_valid,
                "expires_at": (
                    sub.support_token_expires_at.isoformat()
                    if sub.support_token_expires_at
                    else None
                ),
                "granted_by": sub.support_token_granted_by,
                "audit_logs": [
                    {
                        "timestamp": log.timestamp.isoformat(),
                        "action": log.action,
                        "operator": log.operator,
                        "detail": log.detail,
                    }
                    for log in recent_logs
                ],
            }
            self.stdout.write(json.dumps(payload, indent=2))
            return

        header_line = "=" * 70
        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))
        self.stdout.write(
            self.style.NOTICE("|  ")
            + self.style.SUCCESS(f"SUPPORT ACCESS STATUS: {tenant.schema_name}")
        )
        self.stdout.write(self.style.NOTICE("+" + header_line + "+"))

        if is_valid:
            remaining = sub.support_token_expires_at - now
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes, _ = divmod(rem, 60)
            self.stdout.write(
                f"  Token:       {self.style.SUCCESS('[ACTIVE]')}\n"
                f"  Expires:     {sub.support_token_expires_at:%Y-%m-%d %H:%M:%S UTC} ({hours}h {minutes}m remaining)\n"
                f"  Granted by:  {sub.support_token_granted_by}\n"
            )
        else:
            self.stdout.write(
                f"  Token:       {self.style.ERROR('[NO ACTIVE TOKEN]')}\n"
            )

        self.stdout.write(self.style.NOTICE("  Recent Support Access Audit Trail:"))
        self.stdout.write("  " + "-" * 66)
        col_fmt = "  {:<20} {:<24} {:<15}"
        self.stdout.write(
            self.style.SQL_FIELD(col_fmt.format("Timestamp", "Action", "Operator"))
        )
        self.stdout.write("  " + "-" * 66)

        if not recent_logs:
            self.stdout.write("  No support token operations recorded.")
        else:
            for log in recent_logs:
                ts = f"{log.timestamp:%Y-%m-%d %H:%M}"
                self.stdout.write(col_fmt.format(ts, log.action, log.operator))

        self.stdout.write("  " + "-" * 66 + "\n")
