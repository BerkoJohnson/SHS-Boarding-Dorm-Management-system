All Tier-3 fields verified. Two things worth calling out that shaped the design:

- **`DisciplinaryRecord.logged_by` is a FK to `HouseMaster`** (models.py 1365–1367, no `related_name`), *not* `User` — while `corrected_by`, `RollCallSession.created_by`, `Exeat.created_by`, `RollCallAbsenceFollowUp.resolved_by`, and `ConfigurationAuditLog.actor` are `User`. So the Staff Activity report is built **HouseMaster-centric** and reaches the `User`-based resolution counts through `HouseMaster.user`.
- **`ConfigurationAuditLog.Event`** has six explicit choices (models.py 1254–1263) and `details` is a `JSONField`. Because audit data is school-wide privileged info, its view is gated to `ADMIN_ROLES` rather than the report roles.

Integration summary:

| # | Report | Integration |
|---|---|---|
| 13 | Staff Activity / Workload | `get_detailed_report` branch + form choice |
| 14 | Welfare / Infirmary Referrals | `get_detailed_report` branch + form choice |
| 15 | Data Completeness / Register Hygiene | `get_detailed_report` branch + form choice |
| 16 | Movement Concern Reports | `get_detailed_report` branch + form choice |
| 17 | Conduct Normalized by House Size | `get_detailed_report` branch + form choice |
| 18 | Intervention / Correction Effectiveness | `get_detailed_report` branch + form choice |
| 19 | Configuration Audit Trail | dedicated view (ADMIN_ROLES) + dedicated screen template, reuses `print.html` |

---

## 1. Service layer — additions to `core/services/reports.py`

**Update the model import block** at the top of `reports.py`:

```python
# core/services/reports.py
from django.db.models import Count, F, Max, Q, Sum

from core.models import (
    Student,
    Exeat,
    ExeatReconciliation,
    DisciplinaryRecord,
    RollCallSession,
    RollCallEntry,
    RollCallAbsenceFollowUp,
    AcademicTerm,
    House,
    HouseMaster,
    Dormitory,
    DormitoryRoom,
    SMSDelivery,
    MovementConcernReport,
    ConfigurationAuditLog,
)
```

**Dispatch lines** — insert at the top of `get_detailed_report`, with the Tier-1/Tier-2 branches:

```python
# core/services/reports.py  — inside get_detailed_report(...)
    if report_type == "staff_activity":
        return _staff_activity_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
    if report_type == "welfare_infirmary":
        return _welfare_infirmary_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
    if report_type == "data_completeness":
        return _data_completeness_report(house_id=house_id)
    if report_type == "movement_concerns":
        return _movement_concern_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
    if report_type == "conduct_normalized":
        return _conduct_normalized_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
    if report_type == "intervention_effectiveness":
        return _intervention_effectiveness_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
```

### 1a. Staff Activity / Workload (`_staff_activity_report`)

```python
# core/services/reports.py  (append)

def _staff_activity_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Per-housemaster operational workload across supervisable actions."""
    house_masters = HouseMaster.objects.select_related("house", "user")
    if house_id is not None:
        house_masters = house_masters.filter(house_id=house_id)

    rows = []
    for hm in house_masters:
        roll_calls = RollCallSession.objects.filter(conducted_by=hm)
        approvals = Exeat.objects.filter(approved_by=hm, status=Exeat.Status.APPROVED)
        incidents = DisciplinaryRecord.objects.filter(logged_by=hm)
        resolutions = RollCallAbsenceFollowUp.objects.filter(
            resolved_by_id=hm.user_id, resolved_at__isnull=False
        ) if hm.user_id else RollCallAbsenceFollowUp.objects.none()

        if date_from:
            roll_calls = roll_calls.filter(date__gte=date_from)
            approvals = approvals.filter(updated_at__date__gte=date_from)
            incidents = incidents.filter(created_at__date__gte=date_from)
            resolutions = resolutions.filter(resolved_at__date__gte=date_from)
        if date_to:
            roll_calls = roll_calls.filter(date__lte=date_to)
            approvals = approvals.filter(updated_at__date__lte=date_to)
            incidents = incidents.filter(created_at__date__lte=date_to)
            resolutions = resolutions.filter(resolved_at__date__lte=date_to)
        if term_id:
            roll_calls = roll_calls.filter(academic_term_id=term_id)
            approvals = approvals.filter(academic_term_id=term_id)
            incidents = incidents.filter(academic_term_id=term_id)
            resolutions = resolutions.filter(
                entry__session__academic_term_id=term_id
            )

        rc = roll_calls.count()
        ap = approvals.count()
        di = incidents.count()
        rs = resolutions.count()
        rows.append((
            hm.full_name,
            hm.house.name if hm.house else "School-wide",
            rc,
            ap,
            di,
            rs,
            rc + ap + di + rs,
        ))

    rows.sort(key=lambda r: r[6], reverse=True)

    # Unattributed work (records with no named officer) — a data-quality signal.
    unlogged_roll_calls = RollCallSession.objects.filter(conducted_by__isnull=True)
    unlogged_incidents = DisciplinaryRecord.objects.filter(logged_by__isnull=True)
    if house_id is not None:
        unlogged_roll_calls = unlogged_roll_calls.filter(house_id=house_id)
        unlogged_incidents = unlogged_incidents.filter(student__house_id=house_id)
    if date_from:
        unlogged_roll_calls = unlogged_roll_calls.filter(date__gte=date_from)
        unlogged_incidents = unlogged_incidents.filter(created_at__date__gte=date_from)
    if date_to:
        unlogged_roll_calls = unlogged_roll_calls.filter(date__lte=date_to)
        unlogged_incidents = unlogged_incidents.filter(created_at__date__lte=date_to)
    if term_id:
        unlogged_roll_calls = unlogged_roll_calls.filter(academic_term_id=term_id)
        unlogged_incidents = unlogged_incidents.filter(academic_term_id=term_id)

    total_actions = sum(r[6] for r in rows)

    return {
        "title": "Staff Activity & Workload",
        "headers": (
            "Housemaster", "House", "Roll calls", "Passes approved",
            "Incidents logged", "Absences resolved", "Total actions",
        ),
        "rows": rows,
        "summary": (
            ("Staff listed", len(rows)),
            ("Total actions", total_actions),
            ("Most active", rows[0][0] if rows else "—"),
            ("Roll calls conducted", sum(r[2] for r in rows)),
            ("Incidents logged", sum(r[4] for r in rows)),
        ),
        "sections": (
            {
                "title": "Unattributed records (no officer named)",
                "headers": ("Record type", "Count"),
                "rows": (
                    ("Roll calls without a conductor", unlogged_roll_calls.count()),
                    ("Incidents without a logger", unlogged_incidents.count()),
                ),
            },
        ),
        "highlights": (
            "Approvals are counted by updated_at, the closest available proxy for when the decision was recorded.",
            "Absence resolutions are matched to a housemaster through their linked login account.",
        ),
        "point_in_time": False,
    }
```

### 1b. Welfare / Infirmary Referrals (`_welfare_infirmary_report`)

```python
# core/services/reports.py  (append)

def _welfare_infirmary_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Students repeatedly in sick bay and/or on medical leave."""
    infirmary = RollCallEntry.objects.filter(status=RollCallEntry.Status.INFIRMARY)
    if house_id is not None:
        infirmary = infirmary.filter(session__house_id=house_id)
    if date_from:
        infirmary = infirmary.filter(session__date__gte=date_from)
    if date_to:
        infirmary = infirmary.filter(session__date__lte=date_to)
    if term_id:
        infirmary = infirmary.filter(session__academic_term_id=term_id)
    infirmary_rows = infirmary.values("student_id").annotate(
        c=Count("id"), last=Max("session__date")
    )

    medical = Exeat.objects.filter(exeat_type=Exeat.ExeatType.MEDICAL)
    if house_id is not None:
        medical = medical.filter(student__house_id=house_id)
    if date_from:
        medical = medical.filter(expected_departure__date__gte=date_from)
    if date_to:
        medical = medical.filter(expected_departure__date__lte=date_to)
    if term_id:
        medical = medical.filter(academic_term_id=term_id)
    medical_rows = medical.values("student_id").annotate(c=Count("id"))

    inf_map = {r["student_id"]: (r["c"], r["last"]) for r in infirmary_rows}
    med_map = {r["student_id"]: r["c"] for r in medical_rows}
    student_ids = set(inf_map) | set(med_map)

    students = Student.objects.filter(pk__in=student_ids).values(
        "id", "full_name", "house__name", "dormitory__name"
    )
    info = {s["id"]: s for s in students}

    rows = []
    watchlist = []
    for sid in student_ids:
        inf_count, last = inf_map.get(sid, (0, None))
        med_count = med_map.get(sid, 0)
        meta = info.get(sid, {})
        row = (
            meta.get("full_name", "Unknown"),
            meta.get("house__name") or "Unassigned",
            meta.get("dormitory__name") or "Unassigned",
            inf_count,
            med_count,
            inf_count + med_count,
            last or "—",
        )
        rows.append(row)
        if inf_count >= 2 or med_count >= 2:
            watchlist.append(row)

    rows.sort(key=lambda r: r[5], reverse=True)
    watchlist.sort(key=lambda r: r[5], reverse=True)

    return {
        "title": "Welfare & Infirmary Referrals",
        "headers": (
            "Student", "House", "Dormitory", "Infirmary marks",
            "Medical exeats", "Total referrals", "Last infirmary",
        ),
        "rows": rows,
        "summary": (
            ("Students referred", len(rows)),
            ("Infirmary marks", sum(r[3] for r in rows)),
            ("Medical exeats", sum(r[4] for r in rows)),
            ("Students on watchlist (2+)", len(watchlist)),
        ),
        "sections": (
            {
                "title": "Repeat welfare watchlist",
                "headers": (
                    "Student", "House", "Dormitory", "Infirmary marks",
                    "Medical exeats", "Total referrals", "Last infirmary",
                ),
                "rows": watchlist,
            },
        ),
        "highlights": (
            "Combines roll-call 'Infirmary' marks with 'Medical' exeats for the same student.",
            "A watchlist student has at least two infirmary marks or at least two medical exeats in the selected period.",
        ),
        "point_in_time": False,
    }
```

### 1c. Data Completeness / Register Hygiene (`_data_completeness_report`)

```python
# core/services/reports.py  (append)

def _data_completeness_report(house_id=None):
    """Students with missing guardian, biography, photo or placement data."""
    students = Student.objects.select_related("house", "dormitory").order_by("full_name")
    if house_id is not None:
        students = students.filter(house_id=house_id)

    rows = []
    counters = {"guardian": 0, "dob": 0, "photo": 0, "dorm": 0, "room": 0}
    complete = 0
    total = 0

    for s in students:
        total += 1
        gaps = []
        if not s.guardian_id:
            gaps.append("No guardian")
            counters["guardian"] += 1
        if not s.date_of_birth:
            gaps.append("No date of birth")
            counters["dob"] += 1
        if not s.photo:
            gaps.append("No photo")
            counters["photo"] += 1
        if s.residency == Student.ResidencyStatus.BOARDING:
            if not s.dormitory_id:
                gaps.append("No dormitory")
                counters["dorm"] += 1
            if not s.room_id:
                gaps.append("No room")
                counters["room"] += 1

        if gaps:
            rows.append((
                s.full_name,
                s.admission_no,
                s.house.name if s.house else "Unassigned",
                s.get_residency_display(),
                len(gaps),
                ", ".join(gaps),
            ))
        else:
            complete += 1

    rows.sort(key=lambda r: r[4], reverse=True)
    completion = round(complete * 100 / total, 1) if total else 0

    return {
        "title": "Data Completeness / Register Hygiene",
        "headers": (
            "Student", "Admission No", "House", "Residency",
            "Missing fields", "Missing",
        ),
        "rows": rows,
        "summary": (
            ("Students", total),
            ("Fully complete", complete),
            ("With gaps", total - complete),
            ("Completion", f"{completion}%"),
            ("No guardian", counters["guardian"]),
            ("No photo", counters["photo"]),
            ("No dormitory", counters["dorm"]),
        ),
        "sections": (
            {
                "title": "Gap checklist",
                "headers": ("Field", "Students missing"),
                "rows": (
                    ("No guardian contact", counters["guardian"]),
                    ("No date of birth", counters["dob"]),
                    ("No photo", counters["photo"]),
                    ("Boarders without dormitory", counters["dorm"]),
                    ("Boarders without room", counters["room"]),
                ),
            },
        ),
        "highlights": (
            "Day students are not expected to have a dormitory or room, so those fields are not counted against them.",
            "Missing guardian contacts block SMS notifications; missing photos block ID card printing.",
        ),
        "point_in_time": True,
    }
```

### 1d. Movement Concern Reports (`_movement_concern_report`)

```python
# core/services/reports.py  (append)

def _movement_concern_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Public QR-scan concerns raised against student movements."""
    concerns = MovementConcernReport.objects.select_related(
        "student", "student__house", "exeat"
    )
    if house_id is not None:
        concerns = concerns.filter(student__house_id=house_id)
    if date_from:
        concerns = concerns.filter(created_at__date__gte=date_from)
    if date_to:
        concerns = concerns.filter(created_at__date__lte=date_to)
    if term_id:
        concerns = concerns.filter(exeat__academic_term_id=term_id)

    headers = ("Raised", "Student", "House", "Pass", "Status", "Details")
    rows = []
    open_rows = []
    for c in concerns:
        details = (c.details or "").strip()
        if len(details) > 90:
            details = details[:87] + "…"
        row = (
            c.created_at,
            c.student.full_name,
            c.student.house.name if c.student.house else "Unassigned",
            c.exeat.exeat_number if c.exeat else "—",
            c.get_status_display(),
            details or "—",
        )
        rows.append(row)
        if c.status == MovementConcernReport.Status.OPEN:
            open_rows.append(row)

    return {
        "title": "Movement Concern Reports",
        "headers": headers,
        "rows": rows,
        "summary": (
            ("Concerns raised", len(rows)),
            ("Open", len(open_rows)),
            ("Reviewed", len(rows) - len(open_rows)),
        ),
        "sections": (
            {"title": "Open concerns", "headers": headers, "rows": open_rows},
        ),
        "highlights": (
            "Concerns are raised by scanning a student ID QR code; details are optional and free text.",
        ),
        "point_in_time": False,
    }
```

### 1e. Conduct Normalized by House Size (`_conduct_normalized_report`)

```python
# core/services/reports.py  (append)

def _conduct_normalized_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Active incidents per house, normalised per 100 boarders."""
    active = DisciplinaryRecord.objects.filter(corrected_at__isnull=True)
    if house_id is not None:
        active = active.filter(student__house_id=house_id)
    if date_from:
        active = active.filter(created_at__date__gte=date_from)
    if date_to:
        active = active.filter(created_at__date__lte=date_to)
    if term_id:
        active = active.filter(academic_term_id=term_id)

    boarders = dict(
        House.objects.annotate(
            c=Count("students", filter=Q(students__residency=Student.ResidencyStatus.BOARDING))
        ).values_list("id", "c")
    )
    house_names = dict(House.objects.values_list("id", "name"))

    aggregates = active.values("student__house_id").annotate(
        c=Count("id"),
        major=Count("id", filter=Q(severity=DisciplinaryRecord.Severity.MAJOR)),
        critical=Count("id", filter=Q(severity=DisciplinaryRecord.Severity.CRITICAL)),
    )

    rows = []
    for row in aggregates:
        hid = row["student__house_id"]
        house_boarders = boarders.get(hid, 0)
        per_100 = round(row["c"] * 100 / house_boarders, 1) if house_boarders else 0
        rows.append((
            house_names.get(hid, "Unassigned"),
            house_boarders,
            row["c"],
            per_100,
            row["major"],
            row["critical"],
        ))

    rows.sort(key=lambda r: r[3], reverse=True)
    total_incidents = sum(r[2] for r in rows)
    total_boarders = sum(r[1] for r in rows)
    overall = round(total_incidents * 100 / total_boarders, 1) if total_boarders else 0

    return {
        "title": "Conduct Normalized by House Size",
        "headers": (
            "House", "Boarders", "Active incidents", "Per 100 boarders",
            "Major", "Critical",
        ),
        "rows": rows,
        "summary": (
            ("Active incidents", total_incidents),
            ("Boarders", total_boarders),
            ("Per 100 boarders", overall),
            ("Highest rate", f"{rows[0][0]} ({rows[0][3]})" if rows else "—"),
            ("Lowest rate", f"{rows[-1][0]} ({rows[-1][3]})" if rows else "—"),
        ),
        "highlights": (
            "The 'per 100 boarders' rate lets small and large houses be compared fairly.",
            "Only active (uncorrected) records are counted; corrected records are excluded.",
        ),
        "point_in_time": False,
    }
```

### 1f. Intervention / Correction Effectiveness (`_intervention_effectiveness_report`)

```python
# core/services/reports.py  (append)

def _intervention_effectiveness_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Correction rates, time-to-correction and recurrence after correction."""
    records = DisciplinaryRecord.objects.all()
    if house_id is not None:
        records = records.filter(student__house_id=house_id)
    if date_from:
        records = records.filter(created_at__date__gte=date_from)
    if date_to:
        records = records.filter(created_at__date__lte=date_to)
    if term_id:
        records = records.filter(academic_term_id=term_id)

    data = records.values(
        "student_id",
        "student__full_name",
        "student__house__name",
        "created_at",
        "corrected_at",
    )

    acc = {}
    for r in data:
        entry = acc.setdefault(r["student_id"], {
            "name": r["student__full_name"],
            "house": r["student__house__name"] or "Unassigned",
            "total": 0,
            "corrected": 0,
            "active": 0,
            "durations": [],
            "corrected_times": [],
            "active_times": [],
        })
        entry["total"] += 1
        if r["corrected_at"]:
            entry["corrected"] += 1
            entry["durations"].append(
                (r["corrected_at"] - r["created_at"]).total_seconds() / 3600
            )
            entry["corrected_times"].append(r["corrected_at"])
        else:
            entry["active"] += 1
            entry["active_times"].append(r["created_at"])

    rows = []
    recurrence_rows = []
    recurrence_students = 0
    for entry in acc.values():
        avg_days = (
            round(sum(entry["durations"]) / len(entry["durations"]) / 24, 1)
            if entry["durations"]
            else None
        )
        recurrent = bool(
            entry["corrected_times"]
            and entry["active_times"]
            and max(entry["active_times"]) > min(entry["corrected_times"])
        )
        if recurrent:
            recurrence_students += 1
        row = (
            entry["name"],
            entry["house"],
            entry["total"],
            entry["corrected"],
            entry["active"],
            f"{avg_days}d" if avg_days is not None else "—",
            "Yes" if recurrent else "No",
        )
        rows.append(row)
        if recurrent:
            recurrence_rows.append(row)

    rows.sort(key=lambda r: r[2], reverse=True)
    corrected_total = sum(r[3] for r in rows)
    active_total = sum(r[4] for r in rows)
    decided = corrected_total + active_total
    correction_rate = round(corrected_total * 100 / decided, 1) if decided else 0

    return {
        "title": "Intervention / Correction Effectiveness",
        "headers": (
            "Student", "House", "Records", "Corrected", "Active",
            "Avg time to correct", "Recurrence after correction",
        ),
        "rows": rows,
        "summary": (
            ("Students", len(rows)),
            ("Records", decided),
            ("Corrected", corrected_total),
            ("Active", active_total),
            ("Correction rate", f"{correction_rate}%"),
            ("Recurring students", recurrence_students),
        ),
        "sections": (
            {
                "title": "Students who re-offended after a correction",
                "headers": (
                    "Student", "House", "Records", "Corrected", "Active",
                    "Avg time to correct", "Recurrence after correction",
                ),
                "rows": recurrence_rows,
            },
        ),
        "highlights": (
            "The system records actions taken but not intervention outcomes, so 'effectiveness' is inferred from correction and recurrence, not measured results.",
            "Recurrence = an active record created after an earlier record for the same student was corrected.",
        ),
        "point_in_time": False,
    }
```

### 1g. Configuration Audit Trail (`get_configuration_audit`)

```python
# core/services/reports.py  (append)

def get_configuration_audit(date_from=None, date_to=None):
    """Append-only audit of privileged configuration changes (school-wide)."""
    events = ConfigurationAuditLog.objects.select_related("actor")
    if date_from:
        events = events.filter(created_at__date__gte=date_from)
    if date_to:
        events = events.filter(created_at__date__lte=date_to)

    event_labels = dict(ConfigurationAuditLog.Event.choices)
    rows = []
    for e in events:
        if e.actor:
            actor = (e.actor.get_full_name() or "").strip() or e.actor.username
        else:
            actor = "System"
        rows.append((
            e.created_at,
            actor,
            event_labels.get(e.event, e.event),
            e.description,
        ))

    type_rows = [
        (event_labels.get(row["event"], row["event"]), row["c"])
        for row in events.values("event").annotate(c=Count("id")).order_by("-c")
    ]
    actors = {r[1] for r in rows}

    return {
        "title": "Configuration Audit Trail",
        "headers": ("When", "Actor", "Event", "Description"),
        "rows": rows,
        "summary": (
            ("Events", len(rows)),
            ("Distinct actors", len(actors)),
            ("First", rows[-1][0] if rows else "—"),
            ("Latest", rows[0][0] if rows else "—"),
        ),
        "sections": (
            {"title": "Events by type", "headers": ("Event", "Count"), "rows": type_rows},
        ),
        "highlights": (
            "This log is append-only and school-wide; it is not scoped to a single house.",
        ),
        "point_in_time": False,
    }
```

---

## 2. Forms — one edit in `core/forms.py`

Append the six table reports to `ReportFilterForm.REPORT_CHOICES` (after the Tier-2 entries):

```python
# core/forms.py  — ReportFilterForm.REPORT_CHOICES
        ("staff_activity", "Staff activity & workload"),
        ("welfare_infirmary", "Welfare & infirmary referrals"),
        ("data_completeness", "Data completeness / register hygiene"),
        ("movement_concerns", "Movement concern reports"),
        ("conduct_normalized", "Conduct normalized by house size"),
        ("intervention_effectiveness", "Intervention effectiveness"),
```

(Configuration Audit is reached by its own link and gated to admins.)

---

## 3. Views — `core/views.py`

Insert after `sms_usage_view`. Gated to `ADMIN_ROLES` for least privilege; `ADMIN_ROLES` is already imported (views.py:67).

```python
# core/views.py  — insert after sms_usage_view

@login_required
@role_required(*ADMIN_ROLES)
@tenant_feature_required("advanced_reports")
def config_audit_view(request):
    """School-wide append-only configuration audit trail (admin access)."""
    from core.services.reports import get_configuration_audit

    report_filters = ReportFilterForm(request.GET.copy())
    valid = report_filters.is_valid()
    cleaned = report_filters.cleaned_data if valid else {}

    report = get_configuration_audit(
        date_from=cleaned.get("date_from"),
        date_to=cleaned.get("date_to"),
    )
    report["record_count"] = len(report["rows"])

    context = {
        "report": report,
        "report_filters": report_filters,
        "report_filters_valid": valid,
        "user_house_scope": None,
        "selected_date_from": cleaned.get("date_from"),
        "selected_date_to": cleaned.get("date_to"),
        "selected_term": None,
        "report_generated_at": timezone.localtime(),
        "active_tab": "reports",
    }
    template = (
        "core/reports/print.html"
        if request.GET.get("print")
        else "core/reports/config_audit.html"
    )
    return render(request, template, context)
```

Note: `?print=1` reuses the existing `core/reports/print.html`, so no new print template is needed — the report dict satisfies that template's contract (`title`, `headers`, `rows`, `summary`, `sections`, `point_in_time`, `record_count`).

---

## 4. URLs — `core/urls.py`

Add beside the Tier-2 routes:

```python
# core/urls.py
    path('reports/config-audit/', views.config_audit_view, name='config_audit'),
```

---

## 5. Template

### `templates/core/reports/config_audit.html`

```html
{% extends "base.html" %}

{% block title %}Configuration Audit Trail - {{ school_configuration.name }}{% endblock %}

{% block content %}
<div class="space-y-6">
  <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">Configuration Audit Trail</h1>
      <p class="mt-1 text-sm text-slate-500">Privileged, append-only changes to system configuration · School-wide</p>
    </div>
    <div class="flex flex-wrap items-end gap-2">
      <form method="get" action="{% url 'core:config_audit' %}" class="flex items-end gap-2">
        <div>
          <label class="block mb-1 text-xs font-semibold text-slate-600">From</label>
          {{ report_filters.date_from }}
        </div>
        <div>
          <label class="block mb-1 text-xs font-semibold text-slate-600">To</label>
          {{ report_filters.date_to }}
        </div>
        <button type="submit" class="px-3 py-2 text-sm font-semibold text-white rounded-lg bg-slate-900 hover:bg-slate-700">Apply</button>
      </form>
      <a href="{% url 'core:config_audit' %}?print=1{% if request.GET %}&{{ request.GET.urlencode }}{% endif %}"
         target="_blank" rel="noopener"
         class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold border rounded-lg border-brand-300 bg-brand-50 text-brand-700 hover:bg-brand-100">
        Print / Save as PDF
      </a>
    </div>
  </div>

  {% if not report_filters_valid %}
    <div class="px-4 py-3 text-xs border rounded-lg border-rose-200 bg-rose-50 text-rose-700" role="alert">
      Correct the filters above before relying on these figures.
    </div>
  {% endif %}

  <section>
    <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {% for label, value in report.summary %}
        <div class="p-3 bg-white border rounded-lg shadow-sm border-slate-200">
          <p class="truncate text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ label }}</p>
          <p class="mt-0.5 text-lg font-bold leading-5 text-slate-900">{{ value|date:"M d, Y H:i"|default:value }}</p>
        </div>
      {% endfor %}
    </div>
  </section>

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
              <tr><td colspan="{{ section.headers|length }}" class="px-3 py-6 text-center text-slate-500">No events.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </section>
  {% endfor %}

  <section class="overflow-hidden bg-white border shadow-sm rounded-xl border-slate-200">
    <div class="flex items-center justify-between px-5 py-3 border-b border-slate-200 bg-slate-50">
      <h2 class="text-sm font-bold text-slate-800">Audit events</h2>
      <span class="text-xs font-semibold text-slate-600">{{ report.record_count }} event{{ report.record_count|pluralize }}</span>
    </div>
    <div class="overflow-x-auto">
      <table class="min-w-full text-xs text-left">
        <thead class="text-[10px] uppercase tracking-wide text-slate-500">
          <tr>{% for heading in report.headers %}<th class="whitespace-nowrap border-b border-slate-200 px-3 py-2.5 font-semibold">{{ heading }}</th>{% endfor %}</tr>
        </thead>
        <tbody class="divide-y divide-slate-100">
          {% for row in report.rows %}
            <tr class="hover:bg-slate-50">{% for value in row %}<td class="px-3 py-2.5 text-slate-700">{{ value|default:"—" }}</td>{% endfor %}</tr>
          {% empty %}
            <tr><td colspan="{{ report.headers|length }}" class="px-3 py-10 text-center text-slate-500">No configuration events in this period.</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </section>

  <a href="{% url 'core:reports' %}" class="text-xs font-semibold text-brand-600 hover:text-brand-700">← Back to Reports &amp; Stats</a>
</div>
{% endblock %}
```

### Optional link on `templates/core/reports/dashboard.html`

Gate it to admins using the context flag already provided by `header_context`:

```html
{% if can_view_config %}
  <a href="{% url 'core:config_audit' %}" class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold bg-white border rounded-lg shrink-0 border-slate-300 text-slate-700 hover:bg-slate-50">Config Audit</a>
{% endif %}
```

The six table reports appear automatically in the dropdown — no dashboard or print template edits.

---

## 6. Verification against the current system

- **Disciplinary `logged_by` / `corrected_by`:** confirmed `logged_by` → `HouseMaster` (no related_name) and `corrected_by` → `User` (`related_name="corrected_disciplinary_records"`). Staff Activity keys on `HouseMaster` and bridges to `User` via `HouseMaster.user`; effectiveness uses `corrected_at`/`corrected_by` as stored.
- **User-based activity fields:** `RollCallSession.created_by`, `Exeat.created_by`, `RollCallAbsenceFollowUp.resolved_by`/`resolved_at`, `ConfigurationAuditLog.actor` all confirmed as `AUTH_USER_MODEL` FKs.
- **Welfare:** `RollCallEntry.Status.INFIRMARY` and `Exeat.ExeatType.MEDICAL` confirmed; `Max("session__date")` added to the `django.db.models` import.
- **Data completeness:** `Student.photo` (ImageField, blank), `date_of_birth` (null), `guardian` (null FK), `dormitory`/`room` (null FKs), `residency` confirmed. Guardian `phone_number` is required, so "no guardian" is the correct gap proxy.
- **Movement concerns:** `MovementConcernReport.student/exeat/details/status` (OPEN/REVIEWED) confirmed.
- **Normalized conduct:** reuses the proven `Count("students", filter=Q(...BOARDING))` idiom; normalises per 100 boarders.
- **Intervention effectiveness:** derives recurrence from `corrected_at` vs `created_at` ordering per student (in Python); the "no outcome data" caveat from the existing discipline report is carried into `highlights`.
- **Configuration audit:** `Event` choices and `JSONField details` confirmed; view restricted to `ADMIN_ROLES`; `?print=1` reuses `core/reports/print.html` (contract satisfied: `title`, `headers`, `rows`, `summary`, `sections`, `point_in_time`, `record_count`).
- **Access/tenancy:** the six branches inherit gating through `reports_dashboard_view`/`reports_print_view`; the dedicated audit view carries `@login_required` + `@role_required(*ADMIN_ROLES)` + `@tenant_feature_required("advanced_reports")`. Schema isolation is automatic via middleware; no cross-tenant queries.
- **Templates:** the audit screen template uses only `title` and `content`; `school_configuration` and `can_view_config` come from `header_context`; no custom tags beyond Django built-ins.


