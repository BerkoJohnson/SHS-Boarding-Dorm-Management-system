"""Admin-only mobile API for approval-gated guardian SMS campaigns."""
from __future__ import annotations

from django.db.models import QuerySet

from core.api.base import APIError, api_endpoint, idempotent, paginate, parse_json, success
from core.models import GroupSMSCampaign, House, SMSConfiguration, StaffRole


def _campaign_payload(campaign):
    return {
        "id": str(campaign.pk),
        "scope": campaign.scope,
        "house": (
            {"id": str(campaign.house_id), "name": campaign.house.name}
            if campaign.house_id
            else None
        ),
        "message": campaign.message,
        "status": campaign.status,
        "status_display": campaign.get_status_display(),
        "recipient_count": campaign.recipient_count,
        "decision_note": campaign.decision_note,
        "created_by": campaign.created_by.get_full_name()
        or campaign.created_by.get_username(),
        "decided_by": (
            campaign.decided_by.get_full_name()
            or campaign.decided_by.get_username()
            if campaign.decided_by
            else None
        ),
        "created_at": campaign.created_at.isoformat(),
        "decided_at": campaign.decided_at.isoformat()
        if campaign.decided_at
        else None,
    }


@api_endpoint(
    ["GET", "POST"],
    roles=(StaffRole.ADMIN,),
    features=("sms_dispatch",),
)
@idempotent
def campaigns(request):
    if request.method == "GET":
        queryset: QuerySet[GroupSMSCampaign] = GroupSMSCampaign.objects.select_related(
            "house", "created_by", "decided_by"
        )
        items, pagination = paginate(request, queryset, _campaign_payload)
        return success(
            request,
            {"items": items, "sms_enabled": SMSConfiguration.is_enabled()},
            pagination=pagination,
        )

    payload = parse_json(request)
    scope = str(payload.get("scope") or "").strip()
    if scope not in GroupSMSCampaign.Scope.values:
        raise APIError(
            "validation_error",
            "Choose a valid guardian recipient group.",
            fields={"scope": ["Use all_boarders or house."]},
        )
    message = str(payload.get("message") or "").strip()
    if not message or len(message) > 500:
        raise APIError(
            "validation_error",
            "Message must contain between 1 and 500 characters.",
            fields={"message": ["Enter a message of 1 to 500 characters."]},
        )
    house = None
    if scope == GroupSMSCampaign.Scope.HOUSE:
        house_id = str(payload.get("house_id") or "").strip()
        if not house_id:
            raise APIError(
                "validation_error",
                "house_id is required for a house campaign.",
                fields={"house_id": ["This field is required."]},
            )
        house = House.objects.filter(pk=house_id).first()
        if house is None:
            raise APIError(
                "validation_error",
                "The selected house was not found.",
                fields={"house_id": ["Choose a valid house."]},
            )
    elif payload.get("house_id"):
        raise APIError(
            "validation_error",
            "house_id is only valid for a house campaign.",
            fields={"house_id": ["Remove this field for an all-boarders campaign."]},
        )

    from core.services.group_sms import create_group_sms_campaign

    campaign = create_group_sms_campaign(
        actor=request.api_user,
        scope=scope,
        house=house,
        message=message,
    )
    campaign = GroupSMSCampaign.objects.select_related(
        "house", "created_by", "decided_by"
    ).get(pk=campaign.pk)
    return success(request, _campaign_payload(campaign), status=201)


@api_endpoint(
    ["POST"],
    roles=(StaffRole.ADMIN,),
    features=("sms_dispatch",),
)
@idempotent
def decide_campaign(request, campaign_id):
    payload = parse_json(request)
    if not isinstance(payload.get("approve"), bool):
        raise APIError(
            "validation_error",
            "approve must be a boolean.",
            fields={"approve": ["Use true or false."]},
        )
    note = str(payload.get("decision_note") or "").strip()
    from core.services.group_sms import decide_group_sms_campaign

    campaign = decide_group_sms_campaign(
        campaign_id=campaign_id,
        actor=request.api_user,
        approve=payload["approve"],
        decision_note=note,
    )
    campaign = GroupSMSCampaign.objects.select_related(
        "house", "created_by", "decided_by"
    ).get(pk=campaign.pk)
    return success(request, _campaign_payload(campaign))
