"""Mobile report data and privileged audit history."""
from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_date

from core.api.base import APIError, api_endpoint, paginate, success
from core.forms import ReportFilterForm
from core.models import AuditEvent
from core.permissions import ADMIN_ROLES, REPORT_ROLES, get_house_scope


def _report_filters(request):
    values = request.GET.copy()
    if not values.get("report"):
        values["report"] = "housing"
    form = ReportFilterForm(values)
    if not form.is_valid():
        fields = {
            field: [str(error) for error in errors]
            for field, errors in form.errors.items()
        }
        raise APIError(
            "validation_error",
            "Correct the report filters and try again.",
            fields=fields,
        )
    return form.cleaned_data


@api_endpoint(["GET"], roles=REPORT_ROLES, features=("advanced_reports",))
def reports(request):
    from core.services.reports import get_detailed_report

    filters, house, report = _build_report(request, get_detailed_report)
    try:
        page = int(request.GET.get("page", 1))
        page_size = int(request.GET.get("page_size", 50))
    except (TypeError, ValueError):
        raise APIError("invalid_pagination", "page and page_size must be positive integers.")
    if page < 1 or page_size < 1 or page_size > 100:
        raise APIError(
            "invalid_pagination",
            "page must be positive and page_size must be between 1 and 100.",
        )
    offset = (page - 1) * page_size
    total = _paginate_report_rows(report, offset, page_size)
    report["record_count"] = total
    report["generated_at"] = timezone.localtime().isoformat()
    pages = (total + page_size - 1) // page_size if total else 0
    return success(
        request,
        report,
        pagination={
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "has_next": page < pages,
            "has_previous": page > 1 and total > 0,
        },
    )


def _paginate_report_rows(report, offset, page_size):
    tables = []
    if report.get("headers") and report.get("rows") is not None:
        tables.append(report)
    tables.extend(report.get("sections") or ())
    total = sum(len(table.get("rows") or ()) for table in tables)
    cursor = 0
    for table in tables:
        rows = list(table.get("rows") or ())
        start = max(0, offset - cursor)
        end = max(0, min(len(rows), offset + page_size - cursor))
        table["rows"] = rows[start:end]
        cursor += len(rows)
    return total


def _build_report(request, get_detailed_report):
    filters = _report_filters(request)
    house = get_house_scope(request.api_user)
    term = filters.get("term")
    report = get_detailed_report(
        report_type=filters.get("report") or "housing",
        house_id=house.pk if house else None,
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
        term_id=term.pk if term else None,
    )
    report["record_count"] = len(report.get("rows") or ())
    return filters, house, report


@api_endpoint(["GET"], roles=REPORT_ROLES, features=("advanced_reports",))
def export_report(request):
    from core.services.report_export import export_report_response
    from core.services.reports import get_detailed_report

    filters, house, report = _build_report(request, get_detailed_report)
    term = filters.get("term")
    try:
        return export_report_response(
            report,
            file_format=request.GET.get("format", "csv"),
            actor=request.api_user,
            filters={
                "report": filters.get("report") or "housing",
                "scope": house.name if house else "School-wide",
                "academic_term": str(term) if term else "All terms",
                "date_from": filters.get("date_from"),
                "date_to": filters.get("date_to"),
            },
            purpose=request.GET.get("purpose", ""),
        )
    except ValueError as error:
        raise APIError("invalid_export_format", str(error))


@api_endpoint(
    ["GET"],
    roles=ADMIN_ROLES,
    features=("advanced_reports",),
)
def audit_events(request):
    queryset = AuditEvent.objects.select_related("actor").all()
    action = (request.GET.get("action") or "").strip()
    if action:
        valid_actions = set(AuditEvent.Action.values)
        if action not in valid_actions:
            raise APIError("invalid_filter", "Unknown audit action.")
        queryset = queryset.filter(action=action)

    for parameter, lookup in (
        ("date_from", "created_at__date__gte"),
        ("date_to", "created_at__date__lte"),
    ):
        raw = (request.GET.get(parameter) or "").strip()
        if not raw:
            continue
        value = parse_date(raw)
        if value is None:
            raise APIError(
                "invalid_filter",
                f"{parameter} must be an ISO date (YYYY-MM-DD).",
            )
        queryset = queryset.filter(**{lookup: value})

    def serialize(event):
        actor = event.actor
        return {
            "id": str(event.pk),
            "action": event.action,
            "action_display": event.get_action_display(),
            "actor": (
                actor.get_full_name() or actor.get_username()
                if actor
                else "System"
            ),
            "target_type": event.target_type,
            "target_id": event.target_id,
            "summary": event.summary,
            "created_at": event.created_at.isoformat(),
        }

    items, pagination = paginate(request, queryset, serialize)
    return success(request, items, pagination=pagination)
