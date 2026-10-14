import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django_pgschemas.models import TenantModel
from django_pgschemas.routing.models import DomainModel


class Tenant(TenantModel):
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if self._state.adding and not settings.TENANCY_ENABLED:
            raise ValidationError(
                "Set TENANCY_ENABLED=true before creating a school tenant."
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Domain(DomainModel):
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta(DomainModel.Meta):
        ordering = ["domain", "folder"]

    def clean(self):
        super().clean()
        if self.domain:
            self.domain = normalize_domain(self.domain)
        if self.folder:
            self.folder = self.folder.strip("/").lower()
        if self.is_verified and self.verified_at is None:
            self.verified_at = timezone.now()
        elif not self.is_verified:
            self.verified_at = None

    def save(self, *args, **kwargs):
        if self._state.adding and not settings.TENANCY_ENABLED:
            raise ValidationError(
                "Set TENANCY_ENABLED=true before registering school domains."
            )
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.domain}/{self.folder}" if self.folder else self.domain


def normalize_domain(value: str) -> str:
    domain = value.strip().rstrip(".").lower()
    if not domain or any(character in domain for character in "/:@?#"):
        raise ValidationError({"domain": "Enter a hostname without a scheme, port, or path."})
    try:
        domain = domain.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValidationError({"domain": "Enter a valid internationalized hostname."}) from error
    if len(domain) > 253 or not re.fullmatch(
        r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
        r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
        domain,
    ):
        raise ValidationError({"domain": "Enter a valid DNS hostname."})
    return domain
