"""Approval and dispatch workflow for guardian group SMS campaigns."""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django_pgschemas.schema import get_current_schema

from core.models import (
    GroupSMSCampaign,
    SMSConfiguration,
    SMSDelivery,
    StaffRole,
    Student,
    get_user_profile,
)
from core.services.sms import (
    enqueue_sms_delivery_task,
    normalize_phone_number,
    sms_segment_count,
)
from tenancy.entitlements import quota_guard, sms_quota_allows


def _require_sms_administrator(actor):
    profile = get_user_profile(actor)
    if not actor.is_superuser and (not profile or profile.role != StaffRole.ADMIN):
        raise PermissionDenied(
            "Only the Headmaster account may manage group SMS until a bursar role exists."
        )


def get_group_sms_recipients(scope, house=None):
    students = Student.objects.filter(
        status__in=(
            Student.EnrollmentStatus.ENROLLED,
            Student.EnrollmentStatus.APPROVED,
            Student.EnrollmentStatus.IN_PROGRESS,
        ),
        residency=Student.ResidencyStatus.BOARDING,
        guardian__isnull=False,
    ).select_related("guardian")
    if scope == GroupSMSCampaign.Scope.HOUSE:
        if not house:
            raise ValidationError("Choose a house for this recipient group.")
        students = students.filter(house=house)
    elif scope != GroupSMSCampaign.Scope.ALL_BOARDERS:
        raise ValidationError("Choose a valid recipient group.")

    recipients = {}
    for student in students.order_by("full_name"):
        guardian = student.guardian
        for phone in (guardian.phone_number, guardian.alt_phone_number):
            normalized = normalize_phone_number(phone)
            if normalized:
                recipients.setdefault(
                    normalized,
                    guardian.full_name or f"Guardian of {student.full_name}",
                )
    if not recipients:
        raise ValidationError(
            "This recipient group has no valid guardian phone numbers."
        )
    return recipients


def create_group_sms_campaign(*, actor, scope, house, message):
    _require_sms_administrator(actor)
    recipients = get_group_sms_recipients(scope, house)
    return GroupSMSCampaign.objects.create(
        scope=scope,
        house=house,
        message=message,
        recipient_count=len(recipients),
        created_by=actor,
    )


@transaction.atomic
def decide_group_sms_campaign(*, campaign_id, actor, approve, decision_note=""):
    _require_sms_administrator(actor)
    campaign = GroupSMSCampaign.objects.select_for_update().get(pk=campaign_id)
    if campaign.status != GroupSMSCampaign.Status.PENDING:
        raise ValidationError("This campaign has already been decided.")
    if campaign.created_by_id == actor.pk:
        raise ValidationError("A different administrator must decide this campaign.")
    if not approve and not decision_note.strip():
        raise ValidationError("Record a reason when rejecting this campaign.")

    campaign.decided_by = actor
    campaign.decided_at = timezone.now()
    campaign.decision_note = decision_note.strip()[:240]
    if not approve:
        campaign.status = GroupSMSCampaign.Status.REJECTED
        campaign.save(
            update_fields=(
                "status",
                "decided_by",
                "decided_at",
                "decision_note",
                "updated_at",
            )
        )
        return campaign

    if not SMSConfiguration.is_enabled():
        raise ValidationError("SMS dispatch is disabled for this school.")

    recipients = get_group_sms_recipients(campaign.scope, campaign.house)
    schema = get_current_schema()
    tenant_schema = getattr(schema, "schema_name", None) if schema else None
    if settings.TENANCY_ENABLED and (not tenant_schema or tenant_schema == "public"):
        raise ValidationError("Group SMS must be approved inside a school tenant.")

    with quota_guard("max_sms_monthly", schema_name=tenant_schema):
        reserved_segments = len(recipients) * sms_segment_count(campaign.message)
        if not sms_quota_allows(reserved_segments, schema_name=tenant_schema):
            raise ValidationError(
                "The current monthly SMS quota cannot cover this recipient group."
            )
        deliveries = [
            SMSDelivery(
                campaign=campaign,
                event_type=SMSDelivery.EventType.GROUP,
                recipient_name=name,
                phone_number=phone,
                message=campaign.message,
                dedupe_key=f"group:{campaign.pk}:{phone}",
            )
            for phone, name in recipients.items()
        ]
        SMSDelivery.objects.bulk_create(deliveries)
        campaign.status = GroupSMSCampaign.Status.APPROVED
        campaign.recipient_count = len(deliveries)
        campaign.save(
            update_fields=(
                "status",
                "recipient_count",
                "decided_by",
                "decided_at",
                "decision_note",
                "updated_at",
            )
        )

    from core.services.sms_alerts import notify_sms_quota_low

    notify_sms_quota_low()
    for delivery in deliveries:
        transaction.on_commit(
            lambda delivery_id=delivery.pk, schema_name=tenant_schema: enqueue_sms_delivery_task(
                delivery_id, tenant_schema=schema_name
            )
        )
    return campaign
