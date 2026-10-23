"""Bootstrap, reference data, and the public onboarding directory."""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Q

from core.api.base import APIError, api_endpoint, error, success
from core.api.auth import feature_flags
from core.api.serializers import (
    reference_payload,
    school_payload,
    staff_payload,
    user_payload,
)
from core.throttling import client_ip, is_rate_limited
from core.models import (
    AcademicTerm,
    AcademicYear,
    ClassGroup,
    DisputeCase,
    Dormitory,
    DormitoryRoom,
    Exeat,
    House,
    Programme,
    RollCallSession,
    StaffNotification,
    StaffRole,
    Student,
)

User = get_user_model()


def _origin(request):
    return request.build_absolute_uri("/")[:-1]


def _api_base(request):
    return request.build_absolute_uri("/api/v1/").rstrip("/")


def _enforce_public_rate_limit(request):
    limit = getattr(settings, "API_PUBLIC_RATE_LIMIT", 60)
    if limit <= 0:
        return
    window = getattr(settings, "API_PUBLIC_RATE_WINDOW", 60)
    if is_rate_limited("api-public", client_ip(request), limit, window):
        raise APIError(
            "rate_limited",
            "Too many requests. Try again later.",
            status=429,
            headers={"Retry-After": str(window)},
        )


@api_endpoint(["GET"])
def meta(request):
    user = request.api_user
    return success(
        request,
        {
            "user": user_payload(user),
            "school": school_payload(),
            "canonical_origin": _origin(request),
            "api_base_url": _api_base(request),
            "features": feature_flags(),
            "minimum_app_version": getattr(settings, "API_MIN_APP_VERSION", "") or None,
            "enums": {
                "roles": [
                    {"value": value, "label": label}
                    for value, label in StaffRole.choices
                ],
                "exeat_types": [
                    {"value": value, "label": label}
                    for value, label in Exeat.ExeatType.choices
                ],
                "exeat_statuses": [
                    {"value": value, "label": label}
                    for value, label in Exeat.Status.choices
                ],
                "student_genders": [
                    {"value": value, "label": label}
                    for value, label in Student.Gender.choices
                ],
                "student_residency": [
                    {"value": value, "label": label}
                    for value, label in Student.ResidencyStatus.choices
                ],
                "enrollment_statuses": [
                    {"value": value, "label": label}
                    for value, label in Student.EnrollmentStatus.choices
                ],
                "roll_call_statuses": [
                    {"value": value, "label": label}
                    for value, label in RollCallSession.Status.choices
                ],
                "notification_kinds": [
                    {"value": value, "label": label}
                    for value, label in StaffNotification.Kind.choices
                ],
                "dispute_statuses": [
                    {"value": value, "label": label}
                    for value, label in DisputeCase.Status.choices
                ],
            },
        },
    )


@api_endpoint(["GET"])
def reference(request):
    terms = list(
        AcademicTerm.objects.select_related("academic_year").order_by(
            "-academic_year__start_date", "level", "term"
        )
    )
    years = list(AcademicYear.objects.order_by("-start_date"))
    houses = list(House.objects.order_by("display_order", "name"))
    dormitories = list(
        Dormitory.objects.select_related("house").order_by(
            "house__display_order", "house__name", "gender", "name"
        )
    )
    rooms = list(
        DormitoryRoom.objects.select_related("dormitory", "dormitory__house").order_by(
            "dormitory__house__display_order", "dormitory__name", "name"
        )
    )
    programmes = list(Programme.objects.order_by("name"))
    classes = list(ClassGroup.objects.select_related("programme").order_by("name"))

    data = reference_payload(
        terms=terms,
        years=years,
        houses=houses,
        dormitories=dormitories,
        rooms=rooms,
        programmes=programmes,
        classes=classes,
    )
    data["staff"] = [
        staff_payload(user)
        for user in User.objects.filter(is_active=True, profile__isnull=False)
        .exclude(profile__role="UNASSIGNED")
        .select_related("profile", "housemaster__house")
        .order_by("first_name", "last_name", "username")
    ]
    data["counts"] = {
        "houses": len(houses),
        "dormitories": len(dormitories),
        "students": Student.objects.filter(
            status=Student.EnrollmentStatus.ENROLLED
        ).count(),
    }
    return success(request, data)


# ---------------------------------------------------------------------------
# Public endpoints (no token)
# ---------------------------------------------------------------------------
@api_endpoint(["GET"], auth=False)
def public_school(request):
    """Identity of the school serving this host, for the onboarding preview."""
    _enforce_public_rate_limit(request)
    return success(
        request,
        {
            "school": school_payload(),
            "canonical_origin": _origin(request),
            "api_base_url": _api_base(request),
            "features": feature_flags(),
            "api_version": getattr(settings, "API_VERSION", "v1"),
        },
    )


@api_endpoint(["GET"], auth=False)
def public_schools(request):
    """Onboarding directory: active schools that have a verified domain.

    Only the school name and hostname are exposed. No per-tenant data is
    touched, and the query runs in the public schema.
    """
    _enforce_public_rate_limit(request)
    query = (request.GET.get("q") or "").strip()
    limit = max(1, min(100, int(getattr(settings, "API_SCHOOL_DIRECTORY_LIMIT", 25))))

    if not settings.TENANCY_ENABLED:
        from core.models import SchoolConfiguration

        configuration = SchoolConfiguration.get_solo()
        host = request.get_host().split(":")[0]
        scheme = "https" if request.is_secure() else "http"
        return success(
            request,
            [
                {
                    "name": configuration.name,
                    "short_name": configuration.short_name,
                    "domain": host,
                    "api_base_url": f"{scheme}://{host}/api/v1",
                }
            ],
        )

    try:
        from tenancy.models import Domain
    except Exception:  # pragma: no cover - tenancy is installed when enabled
        return success(request, [])

    queryset = (
        Domain.objects.select_related("tenant")
        .filter(
            is_verified=True,
            is_primary=True,
            folder="",
            tenant__is_active=True,
        )
        .order_by("tenant__name")
    )
    if query:
        queryset = queryset.filter(
            Q(tenant__name__icontains=query) | Q(domain__icontains=query)
        )
    domains = list(queryset[:limit])
    scheme = "https" if request.is_secure() else "http"
    return success(
        request,
        [
            {
                "name": domain.tenant.name,
                "short_name": "",
                "domain": domain.domain,
                "api_base_url": f"{scheme}://{domain.domain}/api/v1",
            }
            for domain in domains
            if domain.domain
        ],
    )


def not_found(request, *args, **kwargs):
    """JSON fallback for unknown ``/api/v1/`` routes."""
    return error(
        request,
        "not_found",
        "No such API endpoint exists in this version.",
        status=404,
    )
