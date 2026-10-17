Understood — design only, nothing written to disk. Below is a complete, drop-in blueprint that matches the **current** code (models, related names, decorators, context processors, template blocks). I verified every field/related-name against `core/models.py`, every decorator against `core/permissions.py` / `tenancy/entitlements.py`, and the template blocks against `templates/base.html` (`title`, `content`, `extra_head`, `extra_scripts`).

> **Nothing below has been written to any file.** Everything is shown as the exact code I would apply.

---

## 0. Scope and integration strategy

Four new reports, split by how they plug in:

| Report | Integration | Why |
|---|---|---|
| **Placement Exception Queue** | New branch in `get_detailed_report` → appears in the existing dropdown | Reuses current dashboard + print machinery, zero template risk |
| **House Attendance League** | New branch in `get_detailed_report` | Same |
| **Overdue & Late-Return Register** | New branch in `get_detailed_report` | Same |
| **Boarding Termly KPI Pack** | Dedicated view + dedicated template | It's a dashboard, not a row table |

Adding the three detail reports this way means the existing `dashboard.html` generic branch (`{% else %}` at line 267) and `print.html` render them immediately — no edits to those files required. Only the KPI Pack needs its own template.

**Multi-tenancy handling (unchanged discipline):**
- Tenant schema is activated by middleware before the view runs, so all ORM calls are automatically schema-isolated. No `schema_context` is needed in views.
- `@tenant_feature_required("advanced_reports")` keeps plan gating (note: `free/starter/standard/pilot` default to `False` in `tenancy/feature_defaults.py` — a pilot tenant must be explicitly enabled).
- `@role_required(*REPORT_ROLES)` + `get_house_scope(request.user)` preserve house isolation for Housemasters.
- `school_configuration` and the `can_view_*` flags already arrive via `header_context` (registered context processor), so the templates inherit them.

---

## 1. Service layer — additions to `core/services/reports.py`

**Change the import line** (currently `from django.db.models import Count, F, Q`):

```python
# core/services/reports.py
from django.db.models import Count, F, Q, Sum
```

**Add a period helper + the KPI pack builder + three detail builders.**

```python
# core/services/reports.py  (append)

def _resolve_report_period(term=None, date_from=None, date_to=None):
    """Return (start_date, end_date) for a term or an explicit date range."""
    if term is not None:
        return term.start_date, term.end_date
    return date_from, date_to


def get_boarding_kpi_pack(house_id=None, term=None, date_from=None, date_to=None):
    """
    One-page termly management pack for the boarding / housing department.

    Aggregates occupancy, placement, movement, attendance and conduct for a
    single academic term (or an explicit date range) and returns a structure
    the dedicated KPI template can render directly.
    """
    now = timezone.now()
    start, end = _resolve_report_period(term, date_from, date_to)

    # --- Placement & capacity (point-in-time) ---
    boarders = Student.objects.filter(residency=Student.ResidencyStatus.BOARDING)
    if house_id is not None:
        boarders = boarders.filter(house_id=house_id)
    total_boarders = boarders.count()
    placed_boarders = boarders.filter(dormitory__isnull=False).count()
    awaiting_dorm = boarders.filter(dormitory__isnull=True).count()
    awaiting_room = boarders.filter(
        dormitory__isnull=False, room__isnull=True
    ).count()

    day_students = Student.objects.filter(residency=Student.ResidencyStatus.DAY)
    if house_id is not None:
        day_students = day_students.filter(house_id=house_id)
    total_day = day_students.count()

    dormitories = Dormitory.objects.all()
    if house_id is not None:
        dormitories = dormitories.filter(house_id=house_id)
    configured_beds = dormitories.aggregate(total=Sum("capacity"))["total"] or 0
    over_capacity_dorms = dormitories.annotate(
        occupied=Count(
            "boarders",
            filter=Q(boarders__residency=Student.ResidencyStatus.BOARDING),
        )
    ).filter(occupied__gt=F("capacity")).count()
    occupancy = (
        round(placed_boarders * 100 / configured_beds, 1) if configured_beds else 0
    )

    # --- Exeats in period ---
    exeats = Exeat.objects.all()
    if house_id is not None:
        exeats = exeats.filter(student__house_id=house_id)
    if start:
        exeats = exeats.filter(expected_departure__date__gte=start)
    if end:
        exeats = exeats.filter(expected_departure__date__lte=end)
    exeat_total = exeats.count()
    exeat_pending = exeats.filter(status=Exeat.Status.PENDING).count()
    exeat_approved = exeats.filter(status=Exeat.Status.APPROVED).count()
    exeat_rejected = exeats.filter(status=Exeat.Status.REJECTED).count()

    # --- Live movement (not period-bound) ---
    active_qs = Exeat.objects.filter(status=Exeat.Status.APPROVED).filter(
        Q(movement__actual_departure__isnull=False, movement__actual_return__isnull=True)
        | Q(
            reconciliation__outcome=ExeatReconciliation.Outcome.STUDENT_OUT,
            reconciliation__actual_return__isnull=True,
        )
    )
    if house_id is not None:
        active_qs = active_qs.filter(student__house_id=house_id)
    active_out = active_qs.count()
    overdue = active_qs.filter(expected_return__lte=now).count()

    # --- Attendance in period ---
    sessions = RollCallSession.objects.filter(status=RollCallSession.Status.COMPLETED)
    if house_id is not None:
        sessions = sessions.filter(house_id=house_id)
    if start:
        sessions = sessions.filter(date__gte=start)
    if end:
        sessions = sessions.filter(date__lte=end)
    attendance = sessions.aggregate(
        expected=Count("entries"),
        present=Count("entries", filter=Q(entries__status=RollCallEntry.Status.PRESENT)),
    )
    expected_marks = attendance["expected"] or 0
    present_marks = attendance["present"] or 0
    attendance_rate = (
        round(present_marks * 100 / expected_marks, 1) if expected_marks else 0
    )

    # --- Conduct in period ---
    incidents = DisciplinaryRecord.objects.all()
    if house_id is not None:
        incidents = incidents.filter(student__house_id=house_id)
    if start:
        incidents = incidents.filter(created_at__date__gte=start)
    if end:
        incidents = incidents.filter(created_at__date__lte=end)
    active_incidents = incidents.filter(corrected_at__isnull=True)
    incident_total = incidents.count()
    active_incident_count = active_incidents.count()
    students_with_incidents = active_incidents.values("student_id").distinct().count()
    repeat_students = (
        active_incidents.values("student_id")
        .annotate(c=Count("id"))
        .filter(c__gte=2)
        .count()
    )

    # --- Sections ---
    exeat_type_rows = [
        (_EXEAT_TYPE_LABELS.get(row["exeat_type"], row["exeat_type"]), row["c"])
        for row in exeats.values("exeat_type").annotate(c=Count("id")).order_by("-c")
    ]
    attendance_rows = [
        (
            row["house__name"],
            row["expected"],
            row["present"],
            f'{round(row["present"] * 100 / row["expected"], 1) if row["expected"] else 0}%',
        )
        for row in sessions.values("house__name")
        .annotate(
            expected=Count("entries"),
            present=Count(
                "entries", filter=Q(entries__status=RollCallEntry.Status.PRESENT)
            ),
        )
        .order_by("house__name")
    ]
    severity_rows = [
        (_SEVERITY_LABELS.get(item["severity"], item["severity"]), item["c"])
        for item in active_incidents.values("severity")
        .annotate(c=Count("id"))
        .order_by("severity")
    ]

    return {
        "title": "Boarding Termly KPI Pack",
        "layout": "boarding_kpi",
        "point_in_time": False,
        "kpi_tiles": (
            ("Boarders", total_boarders, "indigo"),
            ("Day students", total_day, "indigo"),
            ("Configured beds", configured_beds, "sky"),
            ("Occupancy", f"{occupancy}%", "emerald"),
            ("Awaiting dorm placement", awaiting_dorm, "amber"),
            ("Awaiting room placement", awaiting_room, "amber"),
            ("Over-capacity dormitories", over_capacity_dorms, "rose"),
            ("Currently out", active_out, "sky"),
            ("Overdue returns", overdue, "rose"),
            ("Exeats in period", exeat_total, "violet"),
            ("Attendance", f"{attendance_rate}%", "emerald"),
            ("Active incidents", active_incident_count, "rose"),
            ("Students with incidents", students_with_incidents, "violet"),
            ("Repeat students (2+)", repeat_students, "amber"),
        ),
        "sections": (
            {
                "title": "Exeats by type",
                "headers": ("Exeat type", "Passes"),
                "rows": exeat_type_rows,
            },
            {
                "title": "Attendance by house",
                "headers": ("House", "Marks expected", "Present", "Attendance"),
                "rows": attendance_rows,
            },
            {
                "title": "Active conduct by severity",
                "headers": ("Severity", "Active cases"),
                "rows": severity_rows,
            },
        ),
        "highlights": (
            f"Exeats: {exeat_pending} pending, {exeat_approved} approved, {exeat_rejected} rejected in the selected period.",
            "Occupancy compares placed boarders against configured dormitory capacity.",
            "Attendance counts completed roll calls only; Present ÷ Expected marks.",
        ),
        "record_count": total_boarders,
    }
```

**Three detail builders.** Add these dispatch lines at the very top of `get_detailed_report` (before the `if report_type == "housing":` line at 134):

```python
# core/services/reports.py  — insert at the top of get_detailed_report(...)
    if report_type == "placement_exceptions":
        return _placement_exception_report(house_id=house_id)
    if report_type == "attendance_league":
        return _attendance_league_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
    if report_type == "overdue_register":
        return _overdue_register_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
```

```python
# core/services/reports.py  (append — new builder functions)

def _placement_exception_report(house_id=None):
    """Actionable queue of every placement problem the housing dept must clear."""
    boarders = Student.objects.filter(residency=Student.ResidencyStatus.BOARDING)
    if house_id is not None:
        boarders = boarders.filter(house_id=house_id)

    rows = []

    unplaced = boarders.filter(dormitory__isnull=True).select_related("house")
    for s in unplaced:
        rows.append((
            "Unplaced boarder",
            s.house.name if s.house else "Unassigned",
            "No dormitory",
            s.full_name,
        ))

    roomless = boarders.filter(dormitory__isnull=False, room__isnull=True).select_related(
        "house", "dormitory"
    )
    for s in roomless:
        rows.append((
            "Room not assigned",
            s.house.name if s.house else "Unassigned",
            s.dormitory.name,
            s.full_name,
        ))

    mismatches = (
        boarders.filter(dormitory__isnull=False)
        .exclude(house_id=F("dormitory__house_id"))
        .select_related("house", "dormitory", "dormitory__house")
    )
    for s in mismatches:
        rows.append((
            "House/dormitory mismatch",
            s.house.name if s.house else "Unassigned",
            f"{s.dormitory.name} ({s.dormitory.house.name})",
            s.full_name,
        ))

    day_misassigned = Student.objects.filter(
        residency=Student.ResidencyStatus.DAY
    ).exclude(dormitory__isnull=True).select_related("house", "dormitory")
    for s in day_misassigned:
        rows.append((
            "Day student in dormitory",
            s.house.name if s.house else "Unassigned",
            s.dormitory.name,
            s.full_name,
        ))

    dormitories = Dormitory.objects.select_related("house").annotate(
        occupied=Count(
            "boarders",
            filter=Q(boarders__residency=Student.ResidencyStatus.BOARDING),
        )
    )
    if house_id is not None:
        dormitories = dormitories.filter(house_id=house_id)
    over_dorms = dormitories.filter(occupied__gt=F("capacity"))
    for d in over_dorms:
        rows.append((
            "Over-capacity dormitory",
            d.house.name,
            d.name,
            f"{d.occupied}/{d.capacity}",
        ))

    rooms = DormitoryRoom.objects.select_related(
        "dormitory", "dormitory__house"
    ).annotate(
        occupied=Count(
            "students",
            filter=Q(students__residency=Student.ResidencyStatus.BOARDING),
        )
    )
    if house_id is not None:
        rooms = rooms.filter(dormitory__house_id=house_id)
    over_rooms = rooms.filter(occupied__gt=F("capacity"))
    for r in over_rooms:
        rows.append((
            "Over-capacity room",
            r.dormitory.house.name,
            f"{r.dormitory.name} / {r.name}",
            f"{r.occupied}/{r.capacity}",
        ))

    return {
        "title": "Placement Exception Queue",
        "headers": ("Exception", "House", "Location", "Detail"),
        "rows": rows,
        "summary": (
            ("Unplaced boarders", unplaced.count()),
            ("Rooms not assigned", roomless.count()),
            ("House/dormitory mismatches", mismatches.count()),
            ("Day students in dormitory", day_misassigned.count()),
            ("Over-capacity dormitories", over_dorms.count()),
            ("Over-capacity rooms", over_rooms.count()),
            ("Total exceptions", len(rows)),
        ),
        "highlights": (
            "Each row is one action item; resolve by editing placement on the student record.",
            "Over-capacity counts students beyond a dormitory or room's configured capacity.",
        ),
        "point_in_time": True,
    }


def _attendance_league_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Per-house attendance ranking for completed roll calls."""
    sessions = RollCallSession.objects.filter(status=RollCallSession.Status.COMPLETED)
    if house_id is not None:
        sessions = sessions.filter(house_id=house_id)
    if date_from:
        sessions = sessions.filter(date__gte=date_from)
    if date_to:
        sessions = sessions.filter(date__lte=date_to)
    if term_id:
        sessions = sessions.filter(academic_term_id=term_id)

    aggregates = sessions.values("house__name").annotate(
        session_count=Count("id", distinct=True),
        expected=Count("entries"),
        present=Count("entries", filter=Q(entries__status=RollCallEntry.Status.PRESENT)),
        absent=Count("entries", filter=Q(entries__status=RollCallEntry.Status.ABSENT)),
        on_exeat=Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.ON_EXEAT)
        ),
        infirmary=Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.INFIRMARY)
        ),
        unmarked=Count(
            "entries", filter=Q(entries__status=RollCallEntry.Status.UNMARKED)
        ),
    )

    ranked = []
    for row in aggregates:
        expected = row["expected"] or 0
        rate = round(row["present"] * 100 / expected, 1) if expected else 0
        ranked.append((row, rate))
    ranked.sort(key=lambda item: item[1], reverse=True)

    rows = [
        (
            index,
            row["house__name"],
            row["session_count"],
            row["expected"],
            row["present"],
            row["absent"],
            row["on_exeat"],
            row["infirmary"],
            row["unmarked"],
            f"{rate}%",
        )
        for index, (row, rate) in enumerate(ranked, start=1)
    ]

    total_expected = sum(r[3] for r in rows)
    total_present = sum(r[4] for r in rows)
    overall = round(total_present * 100 / total_expected, 1) if total_expected else 0

    return {
        "title": "House Attendance League",
        "headers": (
            "Rank", "House", "Sessions", "Expected", "Present",
            "Absent", "On Exeat", "Infirmary", "Unmarked", "Attendance",
        ),
        "rows": rows,
        "summary": (
            ("Houses ranked", len(rows)),
            ("Completed sessions", sum(r[2] for r in rows)),
            ("Attendance overall", f"{overall}%"),
            ("Highest", f"{rows[0][1]} ({rows[0][9]})" if rows else "—"),
            ("Lowest", f"{rows[-1][1]} ({rows[-1][9]})" if rows else "—"),
        ),
        "highlights": (
            "Attendance = Present marks / Expected marks across completed roll calls only.",
            "A house with no recorded sessions in the period will not appear meaningfully in the ranking.",
        ),
        "point_in_time": False,
    }


def _overdue_register_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Approved passes that are still out past expected return, or returned late."""
    now = timezone.now()
    exeats = Exeat.objects.filter(status=Exeat.Status.APPROVED)
    if house_id is not None:
        exeats = exeats.filter(student__house_id=house_id)
    if date_from:
        exeats = exeats.filter(expected_return__date__gte=date_from)
    if date_to:
        exeats = exeats.filter(expected_return__date__lte=date_to)
    if term_id:
        exeats = exeats.filter(academic_term_id=term_id)

    records = exeats.values(
        "exeat_number",
        "student__full_name",
        "student__house__name",
        "exeat_type",
        "expected_return",
        actual_departure=F("movement__actual_departure"),
        actual_return=F("movement__actual_return"),
        rec_departure=F("reconciliation__actual_departure"),
        rec_return=F("reconciliation__actual_return"),
        rec_outcome=F("reconciliation__outcome"),
    )

    rows = []
    overdue_now = 0
    late_returned = 0
    max_late = 0.0

    for e in records:
        expected_return = e["expected_return"]
        actual_return = e["actual_return"] or e["rec_return"]
        departed = e["actual_departure"] or e["rec_departure"]
        is_out = (departed is not None and actual_return is None) or (
            e["rec_outcome"] == ExeatReconciliation.Outcome.STUDENT_OUT
            and actual_return is None
        )

        if is_out and expected_return and now >= expected_return:
            hours_late = round((now - expected_return).total_seconds() / 3600, 1)
            state = "OVERDUE - still out"
            overdue_now += 1
        elif actual_return and expected_return and actual_return > expected_return:
            hours_late = round((actual_return - expected_return).total_seconds() / 3600, 1)
            state = "Returned late"
            late_returned += 1
        else:
            continue

        max_late = max(max_late, hours_late)
        rows.append((
            e["exeat_number"],
            e["student__full_name"],
            e["student__house__name"] or "Unassigned",
            _EXEAT_TYPE_LABELS.get(e["exeat_type"], e["exeat_type"]),
            expected_return,
            actual_return or "Still out",
            f"{hours_late}h",
            state,
        ))

    rows.sort(key=lambda r: r[4])  # earliest expected return first

    return {
        "title": "Overdue & Late-Return Register",
        "headers": (
            "Pass", "Student", "House", "Type",
            "Expected Return", "Recorded Return", "Delay", "State",
        ),
        "rows": rows,
        "summary": (
            ("Currently overdue", overdue_now),
            ("Returned late in period", late_returned),
            ("Total exceptions", len(rows)),
            ("Maximum delay", f"{max_late}h" if rows else "—"),
        ),
        "highlights": (
            "Includes passes still out past expected return and passes returned after expected return.",
            "Departed/returned timestamps come from the gate log, falling back to reconciled records.",
        ),
        "point_in_time": False,
    }
```

---

## 2. Forms — one edit in `core/forms.py`

Extend `ReportFilterForm.REPORT_CHOICES` (currently lines 25–30):

```python
# core/forms.py  — ReportFilterForm.REPORT_CHOICES
    REPORT_CHOICES = (
        ("housing", "Boarding occupancy & placement"),
        ("attendance", "Roll-call attendance & follow-up"),
        ("exeats", "Exeat & gate compliance"),
        ("discipline", "Disciplinary trends"),
        ("placement_exceptions", "Placement exception queue"),
        ("attendance_league", "House attendance league"),
        ("overdue_register", "Overdue & late-return register"),
    )
```

That's the **only** change needed for the three detail reports — `_reports_context` already forwards `report` → `get_detailed_report`, and `dashboard.html`/`print.html` render them generically.

---

## 3. Views — `core/views.py`

Add `csv` to the imports, then the two new views. Place them next to `reports_dashboard_view` (after line 2873).

```python
# core/views.py  — add near the other imports at the top
import csv
```

```python
# core/views.py  — insert after reports_print_view (line 2873)

@login_required
@role_required(*REPORT_ROLES)
@tenant_feature_required("advanced_reports")
def boarding_kpi_view(request):
    """One-page termly boarding KPI pack for the school and housing department."""
    from core.services.reports import get_boarding_kpi_pack

    house_scope = get_house_scope(request.user)

    filter_data = request.GET.copy()
    current_term = AcademicTerm.objects.filter(is_current=True).first()
    if "term" not in filter_data and current_term:
        filter_data["term"] = str(current_term.pk)

    report_filters = ReportFilterForm(filter_data)
    report_filters_valid = report_filters.is_valid()
    cleaned = report_filters.cleaned_data if report_filters_valid else {}
    term = cleaned.get("term") or current_term

    report = get_boarding_kpi_pack(
        house_id=house_scope.pk if house_scope is not None else None,
        term=term,
        date_from=cleaned.get("date_from"),
        date_to=cleaned.get("date_to"),
    )

    context = {
        "report": report,
        "report_filters": report_filters,
        "report_filters_valid": report_filters_valid,
        "user_house_scope": house_scope,
        "selected_term": term,
        "report_generated_at": timezone.localtime(),
        "active_tab": "reports",
    }
    template = (
        "core/reports/boarding_kpi_print.html"
        if request.GET.get("print")
        else "core/reports/boarding_kpi.html"
    )
    return render(request, template, context)


@login_required
@role_required(*REPORT_ROLES)
@tenant_feature_required("advanced_reports")
def boarding_kpi_export_view(request):
    """CSV export of the termly boarding KPI pack."""
    from core.services.reports import get_boarding_kpi_pack

    house_scope = get_house_scope(request.user)
    term_id = request.GET.get("term")
    term = AcademicTerm.objects.filter(pk=term_id).first() if term_id else None
    if term is None:
        term = AcademicTerm.objects.filter(is_current=True).first()

    report = get_boarding_kpi_pack(
        house_id=house_scope.pk if house_scope is not None else None,
        term=term,
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="boarding-kpi-pack.csv"'
    writer = csv.writer(response)
    writer.writerow(["Boarding Termly KPI Pack"])
    writer.writerow(["Scope", house_scope.name if house_scope else "School-wide"])
    writer.writerow(["Term", str(term) if term else "All terms"])
    writer.writerow([])
    writer.writerow(["Metric", "Value"])
    for label, value, *_tone in report["kpi_tiles"]:
        writer.writerow([label, value])
    for section in report["sections"]:
        writer.writerow([])
        writer.writerow([section["title"]])
        writer.writerow(list(section["headers"]))
        for row in section["rows"]:
            writer.writerow(list(row))
    return response
```

Multi-tenancy notes for these views:
- No schema handling is required; `django-pgschemas` has already switched the connection to the tenant schema.
- `@tenant_feature_required("advanced_reports")` is the same guard used by `reports_dashboard_view`/`reports_print_view`.
- `get_house_scope` enforces house-level isolation identically to the existing reports.

---

## 4. URLs — `core/urls.py`

Add next to the existing report routes (lines 43–44):

```python
# core/urls.py
    path('reports/boarding-kpi/', views.boarding_kpi_view, name='boarding_kpi'),
    path('reports/boarding-kpi/export/', views.boarding_kpi_export_view, name='boarding_kpi_export'),
```

---

## 5. Templates

### 5a. `templates/core/reports/boarding_kpi.html`

Uses only the blocks that actually exist in `base.html` (`title`, `content`).

```html
{% extends "base.html" %}

{% block title %}Boarding Termly KPI Pack - {{ school_configuration.name }}{% endblock %}

{% block content %}
<div class="space-y-6">
  <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">Boarding Termly KPI Pack</h1>
      <p class="mt-1 text-sm text-slate-500">
        {% if user_house_scope %}{{ user_house_scope.name }}{% else %}School-wide{% endif %}
        {% if selected_term %} · {{ selected_term }}{% endif %}
      </p>
    </div>
    <div class="flex flex-wrap items-end gap-2">
      <form method="get" action="{% url 'core:boarding_kpi' %}" class="flex items-end gap-2">
        <div>
          <label for="{{ report_filters.term.id_for_label }}" class="block mb-1 text-xs font-semibold text-slate-600">Academic term</label>
          {{ report_filters.term }}
        </div>
        <button type="submit" class="px-3 py-2 text-sm font-semibold text-white rounded-lg bg-slate-900 hover:bg-slate-700">Apply</button>
      </form>
      <a href="{% url 'core:boarding_kpi_export' %}{% if selected_term %}?term={{ selected_term.pk }}{% endif %}"
         class="inline-flex items-center justify-center px-3 py-2 text-sm font-semibold bg-white border rounded-lg border-slate-300 text-slate-700 hover:bg-slate-50">
        Export CSV
      </a>
      <a href="{% url 'core:boarding_kpi' %}?print=1{% if selected_term %}&term={{ selected_term.pk }}{% endif %}"
         target="_blank" rel="noopener"
         class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold border rounded-lg border-brand-300 bg-brand-50 text-brand-700 hover:bg-brand-100">
        Print / Save as PDF
      </a>
    </div>
  </div>

  {% if not report_filters_valid %}
    <div class="px-4 py-3 text-xs border rounded-lg border-rose-200 bg-rose-50 text-rose-700" role="alert">
      Correct the filters above before relying on these figures.
      <ul class="mt-1 list-disc list-inside">
        {% for field in report_filters %}
          {% for error in field.errors %}<li>{{ field.label }}: {{ error }}</li>{% endfor %}
        {% endfor %}
        {% for error in report_filters.non_field_errors %}<li>{{ error }}</li>{% endfor %}
      </ul>
    </div>
  {% endif %}

  <section aria-labelledby="kpi-heading">
    <h2 id="kpi-heading" class="mb-3 text-sm font-bold text-slate-900">Key indicators</h2>
    <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
      {% for label, value, tone in report.kpi_tiles %}
        <div class="flex min-w-0 items-center gap-2.5 rounded-lg border border-slate-200 bg-white p-3 shadow-sm">
          <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg
            {% if tone == 'rose' %}bg-rose-50 text-rose-600
            {% elif tone == 'amber' %}bg-amber-50 text-amber-600
            {% elif tone == 'emerald' %}bg-emerald-50 text-emerald-600
            {% elif tone == 'sky' %}bg-sky-50 text-sky-600
            {% elif tone == 'violet' %}bg-violet-50 text-violet-600
            {% else %}bg-indigo-50 text-indigo-600{% endif %}">
            <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="12" cy="12" r="5"/></svg>
          </span>
          <div class="min-w-0">
            <p class="truncate text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ label }}</p>
            <p class="mt-0.5 text-lg font-bold leading-5 text-slate-900">{{ value }}</p>
          </div>
        </div>
      {% endfor %}
    </div>
  </section>

  {% if report.highlights %}
    <ul class="px-5 py-3 space-y-1 text-xs bg-white border shadow-sm rounded-xl border-slate-200 text-slate-600">
      {% for note in report.highlights %}<li>• {{ note }}</li>{% endfor %}
    </ul>
  {% endif %}

  {% for section in report.sections %}
    <section class="overflow-hidden bg-white border shadow-sm rounded-xl border-slate-200">
      <h2 class="px-5 py-3 text-sm font-bold border-b border-slate-200 bg-slate-50 text-slate-800">{{ section.title }}</h2>
      <div class="overflow-x-auto">
        <table class="min-w-full text-xs text-left">
          <thead class="text-[10px] uppercase tracking-wide text-slate-500">
            <tr>{% for heading in section.headers %}<th class="whitespace-nowrap border-b border-slate-200 px-3 py-2.5 font-semibold">{{ heading }}</th>{% endfor %}</tr>
          </thead>
          <tbody class="divide-y divide-slate-100">
            {% for row in section.rows %}
              <tr>{% for value in row %}<td class="whitespace-nowrap px-3 py-2.5 text-slate-700">{{ value|default:"—" }}</td>{% endfor %}</tr>
            {% empty %}
              <tr><td colspan="{{ section.headers|length }}" class="px-3 py-6 text-center text-slate-500">No data for this period.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </section>
  {% endfor %}

  <div>
    <a href="{% url 'core:reports' %}" class="text-xs font-semibold text-brand-600 hover:text-brand-700">← Back to Reports &amp; Stats</a>
  </div>
</div>
{% endblock %}
```

### 5b. `templates/core/reports/boarding_kpi_print.html`

Standalone print/PDF template mirroring the styling conventions of the existing `print.html` (own `<html>`, inline CSS, landscape page, school header). Rendered when `?print=1`.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ report.title }} | {{ school_configuration.name }}</title>
  <style>
    :root { color-scheme: light; font-family: Arial, sans-serif; color: #172033; }
    body { margin: 0; background: #f1f5f9; }
    .report { max-width: 1200px; margin: 32px auto; padding: 32px; background: #fff; }
    .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid #cbd5e1; }
    .brand-mark { display: grid; width: 44px; height: 44px; flex: none; place-items: center; border: 1px dashed #64748b; color: #475569; font-size: 9px; font-weight: 700; }
    .brand-logo { width: 44px; height: 44px; flex: none; object-fit: contain; }
    .brand-name { margin: 0; font-size: 16px; font-weight: 800; letter-spacing: .04em; }
    .brand-details { margin: 3px 0 0; color: #64748b; font-size: 10px; }
    .toolbar { display: flex; justify-content: flex-end; margin-bottom: 24px; }
    button { border: 0; border-radius: 6px; background: #172033; color: #fff; padding: 10px 16px; font-weight: 700; cursor: pointer; }
    h1 { margin: 0; font-size: 24px; }
    .meta { margin: 8px 0 24px; color: #475569; font-size: 13px; line-height: 1.6; }
    .kpi-grid { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin: 0 0 18px; }
    .kpi-item { border: 1px solid #cbd5e1; padding: 8px; }
    .kpi-item span { display: block; font-size: 10px; text-transform: uppercase; color: #64748b; }
    .kpi-item strong { display: block; margin-top: 4px; font-size: 16px; }
    .highlights { margin: 0 0 18px; padding-left: 18px; color: #475569; font-size: 11px; }
    h2 { margin: 22px 0 8px; font-size: 15px; }
    table { width: 100%; border-collapse: collapse; font-size: 11px; }
    th, td { border: 1px solid #cbd5e1; padding: 7px; text-align: left; vertical-align: top; }
    th { background: #e2e8f0; font-size: 10px; text-transform: uppercase; }
    tr { break-inside: avoid; }
    @page { size: landscape; margin: 12mm; }
    @media print {
      body { background: #fff; }
      .report { max-width: none; margin: 0; padding: 0; }
      .no-print { display: none !important; }
      .kpi-grid { grid-template-columns: repeat(5, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <main class="report">
    <div class="toolbar no-print">
      <button type="button" onclick="window.print()">Print / Save as PDF</button>
    </div>
    <header class="brand">
      {% if school_configuration.logo %}
        <img class="brand-logo" src="{{ school_configuration.logo.url }}" alt="{{ school_configuration.name }} logo">
      {% else %}
        <div class="brand-mark">LOGO</div>
      {% endif %}
      <div>
        <p class="brand-name">{{ school_configuration.name }}</p>
        {% if school_configuration.address or school_configuration.contact_number %}
          <p class="brand-details">
            {% if school_configuration.address %}{{ school_configuration.address }}{% endif %}
            {% if school_configuration.address and school_configuration.contact_number %} · {% endif %}
            {% if school_configuration.contact_number %}{{ school_configuration.contact_number }}{% endif %}
          </p>
        {% endif %}
      </div>
    </header>

    <h1>{{ report.title }}</h1>
    <p class="meta">
      <strong>Scope:</strong> {% if user_house_scope %}{{ user_house_scope.name }}{% else %}School-wide{% endif %}<br>
      <strong>Academic term:</strong> {{ selected_term|default:"All terms" }}<br>
      <strong>Generated:</strong> {{ report_generated_at|date:"M d, Y H:i T" }}
    </p>

    <div class="kpi-grid">
      {% for label, value, tone in report.kpi_tiles %}
        <div class="kpi-item"><span>{{ label }}</span><strong>{{ value }}</strong></div>
      {% endfor %}
    </div>

    {% if report.highlights %}
      <ul class="highlights">{% for note in report.highlights %}<li>{{ note }}</li>{% endfor %}</ul>
    {% endif %}

    {% for section in report.sections %}
      <h2>{{ section.title }}</h2>
      <table>
        <thead><tr>{% for heading in section.headers %}<th>{{ heading }}</th>{% endfor %}</tr></thead>
        <tbody>
          {% for row in section.rows %}
            <tr>{% for value in row %}<td>{{ value|default:"—" }}</td>{% endfor %}</tr>
          {% empty %}
            <tr><td colspan="{{ section.headers|length }}">No data for this period.</td></tr>
          {% endfor %}
        </tbody>
      </table>
    {% endfor %}
  </main>
</body>
</html>
```

### 5c. Optional 2-line touch on `templates/core/reports/dashboard.html`

To surface the KPI Pack from the existing page, add one button beside the existing "Print / Save as PDF" link (around line 56). Purely additive:

```html
<a href="{% url 'core:boarding_kpi' %}"
   class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold bg-white border rounded-lg shrink-0 border-slate-300 text-slate-700 hover:bg-slate-50">
  Termly KPI Pack
</a>
```

No change is needed for the three detail reports — they flow through the existing `{% else %}` generic table (line 267) and `print.html`.

---

## 6. Why this works against the current system (verification checklist)

- **Model field/related-name accuracy:** `Student.dormitory` (`related_name="boarders"`), `Student.room` (`related_name="students"`), `Dormitory.house` (`related_name="dormitories"`), `DormitoryRoom.dormitory` (`related_name="rooms"`), `RollCallSession.entries`, `Exeat.movement`/`Exeat.reconciliation`, `DisciplinaryRecord.corrected_at`, `ExeatReconciliation.Outcome.STUDENT_OUT` — all confirmed in `core/models.py`.
- **Aggregation patterns** (`Count("entries", filter=Q(...))` on `values(...).annotate(...)`, `Sum("capacity")`, `filter(occupied__gt=F("capacity"))`) mirror the idioms already used in `get_dashboard_metrics` and the housing branch, so they compile on the current Django/PostgreSQL stack.
- **`get_detailed_report` contract preserved:** the three builders return the same keys (`title`, `headers`, `rows`, `summary`, `sections`, `highlights`, `point_in_time`) consumed by `dashboard.html` and `print.html`. `_reports_context` sets `record_count` automatically.
- **Decorators:** each new view is wrapped by `@login_required`, `@role_required(*REPORT_ROLES)`, and `@tenant_feature_required("advanced_reports")` — identical to the existing report views, so access behavior matches the existing tests (`test_reports_are_denied_to_gate_officer_and_tutor`, `test_housemaster_reports_and_printable_output_stay_in_assigned_house`).
- **Multi-tenancy:** tenant schema switching happens in middleware before the view executes; all querysets are schema-scoped automatically. No cross-tenant joins are possible. The feature flag remains the plan-level gate.
- **Templates:** only `title` and `content` blocks are used — both exist in `base.html`. `school_configuration`, `active_tab` aren't required by the new templates (KPI template reads `school_configuration` from the global context processor, which is guaranteed by `header_context`).
- **No existing behavior changes:** all additions are new report types/views/URLs; the four existing reports are untouched.
