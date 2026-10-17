Design-only, nothing written to disk. I verified every field, related-name, helper, and timestamp against the current code:

- `RollCallEntry.gate_conflict_at / gate_conflict_note / gate_conflict_reviewed_at / gate_conflict_reviewed_by` (models.py 1614–1623) and the reconcile service (services/roll_call.py 481–528).
- **`Exeat` has no `approved_at` field** (models.py 783–943). The existing `get_exeat_queue_stats` already uses `updated_at` as the decision-time proxy (models.py 1871–1884), so Tier-2 #8 follows that same convention and flags the approximation.
- `Student.level` is a **Python property**, not a DB column (models.py 766–777) → demographics groups by level in Python.
- `tenancy.entitlements` exposes `quota_limit("max_sms_monthly")`, `sms_monthly_usage()`, `current_month_start()` (entitlements.py 59–65, 148–164) — used for the SMS quota gauge.
- `SMSDelivery` fields (models.py 1128–1168); `Programme`/`ClassGroup` (199–219, 541–557).

Integration split (same rationale as Tier-1): four reports plug straight into the existing dropdown/table/print machinery; the two visual ones get dedicated views + templates.

| # | Report | Integration |
|---|---|---|
| 7 | Gate / Roll-call Conflict | `get_detailed_report` branch + form choice |
| 8 | Exeat Approval Turnaround & Rejection | `get_detailed_report` branch + form choice |
| 9 | Exeat Type / Destination / Duration Analytics | `get_detailed_report` branch + form choice |
| 11 | Boarders Demographics & Programme Distribution | dedicated view + template |
| 12 | Room Bed-Allocation & Vacancy Register | `get_detailed_report` branch + form choice |
| 10 | SMS Delivery & Usage | dedicated view + template |

---

## 1. Service layer — additions to `core/services/reports.py`

**Add one module-level label map** (next to `_SEVERITY_LABELS`, ~line 43):

```python
# core/services/reports.py
_ROLL_CALL_ENTRY_STATUS_LABELS = {
    choice.value: choice.label for choice in RollCallEntry.Status
}
```

**Dispatch lines** — insert at the top of `get_detailed_report`, alongside the Tier-1 additions:

```python
# core/services/reports.py  — inside get_detailed_report(...)
    if report_type == "gate_conflicts":
        return _gate_conflict_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
    if report_type == "approval_turnaround":
        return _approval_turnaround_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
    if report_type == "exeat_analytics":
        return _exeat_analytics_report(
            house_id=house_id, date_from=date_from, date_to=date_to, term_id=term_id
        )
    if report_type == "room_register":
        return _room_register_report(house_id=house_id)
```

### 1a. Gate / Roll-call Conflict (`_gate_conflict_report`)

```python
# core/services/reports.py  (append)

def _gate_conflict_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Roll-call entries flagged as conflicting with the gate log."""
    entries = RollCallEntry.objects.filter(gate_conflict_at__isnull=False)
    if house_id is not None:
        entries = entries.filter(session__house_id=house_id)
    if date_from:
        entries = entries.filter(session__date__gte=date_from)
    if date_to:
        entries = entries.filter(session__date__lte=date_to)
    if term_id:
        entries = entries.filter(session__academic_term_id=term_id)

    data = entries.values(
        "session__date",
        "session__session_type",
        "session__house__name",
        "student__full_name",
        "student__house__name",
        "student__dormitory__name",
        "status",
        "status_source",
        "gate_conflict_at",
        "gate_conflict_note",
        "gate_conflict_reviewed_at",
        "gate_conflict_reviewed_by__username",
    ).order_by("-gate_conflict_at")

    headers = (
        "Session Date", "Session", "House", "Student", "Student House",
        "Dormitory", "Roll-call status", "Conflict logged", "Note", "Review",
    )
    rows = []
    pending_rows = []
    for e in data:
        reviewed = bool(e["gate_conflict_reviewed_at"])
        review_state = (
            f"Reviewed {e['gate_conflict_reviewed_at']:%Y-%m-%d %H:%M}"
            if reviewed
            else "Pending review"
        )
        row = (
            e["session__date"],
            _ROLL_CALL_TYPE_LABELS.get(
                e["session__session_type"], e["session__session_type"]
            ),
            e["session__house__name"],
            e["student__full_name"],
            e["student__house__name"] or "Unassigned",
            e["student__dormitory__name"] or "Unassigned",
            _ROLL_CALL_ENTRY_STATUS_LABELS.get(e["status"], e["status"]),
            e["gate_conflict_at"],
            e["gate_conflict_note"] or "—",
            review_state,
        )
        rows.append(row)
        if not reviewed:
            pending_rows.append(row)

    total = len(rows)
    pending = len(pending_rows)

    return {
        "title": "Gate / Roll-call Conflict Report",
        "headers": headers,
        "rows": rows,
        "summary": (
            ("Conflicts", total),
            ("Pending review", pending),
            ("Reviewed", total - pending),
            (
                "Still marked Present",
                sum(
                    1
                    for r in rows
                    if r[6] == _ROLL_CALL_ENTRY_STATUS_LABELS.get(
                        RollCallEntry.Status.PRESENT, "Present"
                    )
                ),
            ),
            (
                "Marked Absent",
                sum(
                    1
                    for r in rows
                    if r[6] == _ROLL_CALL_ENTRY_STATUS_LABELS.get(
                        RollCallEntry.Status.ABSENT, "Absent"
                    )
                ),
            ),
        ),
        "sections": (
            {
                "title": "Conflicts still pending review",
                "headers": headers,
                "rows": pending_rows,
            },
        ),
        "highlights": (
            "A gate conflict = the student's roll-call mark disagreed with the gate log at the moment of departure reconciliation.",
            "Pending-review rows have not yet been acknowledged by a housemaster.",
        ),
        "point_in_time": False,
    }
```

### 1b. Exeat Approval Turnaround & Rejection (`_approval_turnaround_report`)

```python
# core/services/reports.py  (append)

def _approval_turnaround_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Decision volume and turnaround for passes that are no longer pending."""
    exeats = Exeat.objects.exclude(status=Exeat.Status.PENDING)
    if house_id is not None:
        exeats = exeats.filter(student__house_id=house_id)
    if date_from:
        exeats = exeats.filter(created_at__date__gte=date_from)
    if date_to:
        exeats = exeats.filter(created_at__date__lte=date_to)
    if term_id:
        exeats = exeats.filter(academic_term_id=term_id)

    data = exeats.values(
        "exeat_number",
        "student__full_name",
        "student__house__name",
        "exeat_type",
        "status",
        "created_at",
        "updated_at",
        "expected_departure",
        "approved_by__full_name",
    ).order_by("-created_at")

    rows = []
    turnarounds = []
    approver_acc = {}
    type_acc = {}
    approved = rejected = cancelled = 0

    for e in data:
        submitted = e["created_at"]
        decided = e["updated_at"]
        turnaround = (
            round((decided - submitted).total_seconds() / 3600, 1)
            if submitted and decided
            else None
        )
        lead = (
            round((e["expected_departure"] - submitted).total_seconds() / 3600, 1)
            if submitted and e["expected_departure"]
            else None
        )
        if turnaround is not None:
            turnarounds.append(turnaround)

        if e["status"] == Exeat.Status.APPROVED:
            approved += 1
        elif e["status"] == Exeat.Status.REJECTED:
            rejected += 1
        elif e["status"] == Exeat.Status.CANCELLED:
            cancelled += 1

        type_label = _EXEAT_TYPE_LABELS.get(e["exeat_type"], e["exeat_type"])
        rows.append((
            e["exeat_number"],
            e["student__full_name"],
            e["student__house__name"] or "Unassigned",
            type_label,
            e["created_at"],
            _EXEAT_STATUS_LABELS.get(e["status"], e["status"]),
            e["updated_at"],
            f"{turnaround}h" if turnaround is not None else "—",
            e["expected_departure"],
            f"{lead}h" if lead is not None else "—",
            e["approved_by__full_name"] or "—",
        ))

        key = e["approved_by__full_name"] or "Unassigned"
        acc = approver_acc.setdefault(key, {"count": 0, "hours": 0.0})
        acc["count"] += 1
        if turnaround is not None:
            acc["hours"] += turnaround
        type_acc[type_label] = type_acc.get(type_label, 0) + 1

    avg_turn = round(sum(turnarounds) / len(turnarounds), 1) if turnarounds else 0
    approver_rows = [
        (
            name,
            acc["count"],
            f"{round(acc['hours'] / acc['count'], 1)}h" if acc["count"] else "—",
        )
        for name, acc in sorted(approver_acc.items(), key=lambda i: -i[1]["count"])
    ]
    type_rows = [
        (name, count) for name, count in sorted(type_acc.items(), key=lambda i: -i[1])
    ]

    return {
        "title": "Exeat Approval Turnaround & Rejection",
        "headers": (
            "Pass", "Student", "House", "Type", "Submitted", "Decision",
            "Decided", "Turnaround", "Expected Departure", "Lead Time", "Approver",
        ),
        "rows": rows,
        "summary": (
            ("Decided passes", len(rows)),
            ("Approved", approved),
            ("Rejected", rejected),
            ("Cancelled", cancelled),
            ("Avg turnaround", f"{avg_turn}h"),
            ("Fastest", f"{min(turnarounds)}h" if turnarounds else "—"),
            ("Slowest", f"{max(turnarounds)}h" if turnarounds else "—"),
        ),
        "sections": (
            {
                "title": "Turnaround by approver",
                "headers": ("Approver", "Decisions", "Avg turnaround"),
                "rows": approver_rows,
            },
            {
                "title": "Decisions by leave type",
                "headers": ("Exeat type", "Decisions"),
                "rows": type_rows,
            },
        ),
        "highlights": (
            "The schema has no dedicated approval timestamp; turnaround is approximated from updated_at and can shift if a record is edited later.",
            "Lead time = scheduled departure minus request creation (how much notice was given).",
        ),
        "point_in_time": False,
    }
```

### 1c. Exeat Type / Destination / Duration Analytics (`_exeat_analytics_report`)

```python
# core/services/reports.py  (append)

def _exeat_analytics_report(house_id=None, date_from=None, date_to=None, term_id=None):
    """Distribution of leave by type, destination and duration."""
    exeats = Exeat.objects.all()
    if house_id is not None:
        exeats = exeats.filter(student__house_id=house_id)
    if date_from:
        exeats = exeats.filter(expected_departure__date__gte=date_from)
    if date_to:
        exeats = exeats.filter(expected_departure__date__lte=date_to)
    if term_id:
        exeats = exeats.filter(academic_term_id=term_id)

    records = list(
        exeats.values(
            "exeat_type", "destination", "expected_departure", "expected_return"
        )
    )
    total = len(records)

    type_acc = {}
    dest_acc = {}
    buckets = {"Under 6h": 0, "6–24h": 0, "1–3 days": 0, "3–7 days": 0, "Over 7 days": 0}
    all_durations = []

    for r in records:
        dep, ret = r["expected_departure"], r["expected_return"]
        hours = (ret - dep).total_seconds() / 3600 if dep and ret else None
        if hours is not None:
            all_durations.append(hours)

        label = _EXEAT_TYPE_LABELS.get(r["exeat_type"], r["exeat_type"])
        acc = type_acc.setdefault(label, {"count": 0, "hours": 0.0, "n": 0})
        acc["count"] += 1
        if hours is not None:
            acc["hours"] += hours
            acc["n"] += 1

        dest = (r["destination"] or "").strip() or "Not stated"
        dest_acc[dest] = dest_acc.get(dest, 0) + 1

        if hours is not None:
            if hours < 6:
                buckets["Under 6h"] += 1
            elif hours < 24:
                buckets["6–24h"] += 1
            elif hours < 72:
                buckets["1–3 days"] += 1
            elif hours < 168:
                buckets["3–7 days"] += 1
            else:
                buckets["Over 7 days"] += 1

    def _avg(acc):
        return round(acc["hours"] / acc["n"], 1) if acc["n"] else 0

    type_rows = [
        (
            label,
            acc["count"],
            f"{round(acc['count'] * 100 / total, 1) if total else 0}%",
            f"{_avg(acc)}h",
        )
        for label, acc in sorted(type_acc.items(), key=lambda i: -i[1]["count"])
    ]
    destination_rows = [
        (dest, count)
        for dest, count in sorted(dest_acc.items(), key=lambda i: -i[1])[:15]
    ]
    bucket_rows = [(name, count) for name, count in buckets.items()]
    avg_all = round(sum(all_durations) / len(all_durations), 1) if all_durations else 0

    return {
        "title": "Exeat Type, Destination & Duration Analytics",
        "headers": ("Exeat type", "Passes", "Share", "Avg duration"),
        "rows": type_rows,
        "summary": (
            ("Passes in period", total),
            ("Avg duration", f"{avg_all}h"),
            ("Longest", f"{round(max(all_durations), 1)}h" if all_durations else "—"),
            ("Shortest", f"{round(min(all_durations), 1)}h" if all_durations else "—"),
            ("Distinct destinations", len(dest_acc)),
        ),
        "sections": (
            {
                "title": "Top destinations",
                "headers": ("Destination", "Passes"),
                "rows": destination_rows,
            },
            {
                "title": "Duration distribution",
                "headers": ("Duration band", "Passes"),
                "rows": bucket_rows,
            },
        ),
        "highlights": (
            "Destinations are free text, so near-identical entries may appear separately.",
            "Duration uses scheduled departure and return; actual gate times are covered by the exeat compliance report.",
        ),
        "point_in_time": False,
    }
```

### 1d. Room Bed-Allocation & Vacancy Register (`_room_register_report`)

```python
# core/services/reports.py  (append)

def _room_register_report(house_id=None):
    """Per-room capacity, occupancy and vacancy across every dormitory."""
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
    rooms = rooms.order_by(
        "dormitory__house__display_order",
        "dormitory__house__name",
        "dormitory__name",
        "name",
    )

    headers = (
        "House", "Dormitory", "Room", "Gender",
        "Capacity", "Occupied", "Vacant", "Occupancy", "Status",
    )
    rows = []
    over_capacity_rows = []
    empty_rows = []
    total_capacity = total_occupied = 0

    for room in rooms:
        capacity = room.capacity
        occupied = room.occupied
        vacant = capacity - occupied
        occupancy = round(occupied * 100 / capacity, 1) if capacity else 0
        if occupied > capacity:
            status = f"Over capacity by {occupied - capacity}"
        elif vacant == 0:
            status = "At capacity"
        elif occupied == 0:
            status = "Empty"
        else:
            status = f"{vacant} spaces available"
        label = _DORMITORY_GENDER_LABELS.get(room.dormitory.gender, "—")
        row = (
            room.dormitory.house.name,
            room.dormitory.name,
            room.name,
            label,
            capacity,
            occupied,
            max(0, vacant),
            f"{occupancy}%",
            status,
        )
        rows.append(row)
        total_capacity += capacity
        total_occupied += occupied
        if occupied > capacity:
            over_capacity_rows.append(row)
        if occupied == 0:
            empty_rows.append(row)

    occupancy = round(total_occupied * 100 / total_capacity, 1) if total_capacity else 0

    return {
        "title": "Room Bed-Allocation & Vacancy Register",
        "headers": headers,
        "rows": rows,
        "summary": (
            ("Rooms", len(rows)),
            ("Total room capacity", total_capacity),
            ("Boarders in rooms", total_occupied),
            ("Vacant beds", max(0, total_capacity - total_occupied)),
            ("Room occupancy", f"{occupancy}%"),
            ("Over-capacity rooms", len(over_capacity_rows)),
            ("Empty rooms", len(empty_rows)),
        ),
        "sections": (
            {
                "title": "Over-capacity rooms",
                "headers": headers,
                "rows": over_capacity_rows,
            },
            {
                "title": "Empty rooms",
                "headers": headers,
                "rows": empty_rows,
            },
        ),
        "highlights": (
            "Vacancy is room capacity minus boarders currently assigned to that room.",
            "Empty rooms are ready for allocation and are worth surfacing during intake.",
        ),
        "point_in_time": True,
    }
```

### 1e. Boarders Demographics & Programme Distribution (`get_boarding_demographics`)

Grouped by level in Python because `Student.level` is a property.

```python
# core/services/reports.py  (append)

def get_boarding_demographics(house_id=None):
    """Population mix for the boarding department, by programme, house, gender and level."""
    boarders = Student.objects.filter(residency=Student.ResidencyStatus.BOARDING)
    if house_id is not None:
        boarders = boarders.filter(house_id=house_id)

    total = boarders.count()
    gender_rows = [
        (
            "Male",
            boarders.filter(gender=Student.Gender.MALE).count(),
        ),
        (
            "Female",
            boarders.filter(gender=Student.Gender.FEMALE).count(),
        ),
    ]
    programme_rows = [
        (
            row["programme__name"] or "Unassigned",
            row["total"],
            row["male"],
            row["female"],
        )
        for row in boarders.values("programme__name").annotate(
            total=Count("id"),
            male=Count("id", filter=Q(gender=Student.Gender.MALE)),
            female=Count("id", filter=Q(gender=Student.Gender.FEMALE)),
        ).order_by("-total")
    ]
    house_rows = [
        (
            row["house__name"] or "Unassigned",
            row["total"],
            row["male"],
            row["female"],
        )
        for row in boarders.values("house__name").annotate(
            total=Count("id"),
            male=Count("id", filter=Q(gender=Student.Gender.MALE)),
            female=Count("id", filter=Q(gender=Student.Gender.FEMALE)),
        ).order_by("house__name")
    ]

    level_acc = {}
    for student in boarders.select_related("class_group"):
        level = student.level
        label = getattr(level, "label", str(level))
        level_acc[label] = level_acc.get(label, 0) + 1
    level_rows = [(label, count) for label, count in sorted(level_acc.items())]

    return {
        "title": "Boarders Demographics & Programme Distribution",
        "layout": "distribution",
        "point_in_time": True,
        "distribution": (
            {"title": "By gender", "rows": gender_rows},
            {
                "title": "By programme",
                "rows": [(r[0], r[1]) for r in programme_rows],
            },
            {"title": "By form level", "rows": level_rows},
        ),
        "summary": (
            ("Boarders", total),
            ("Houses", len(house_rows)),
            ("Programmes", len(programme_rows)),
            ("Male", gender_rows[0][1]),
            ("Female", gender_rows[1][1]),
        ),
        "sections": (
            {
                "title": "Programme by house composition",
                "headers": ("Programme", "Boarders", "Male", "Female"),
                "rows": programme_rows,
            },
            {
                "title": "House by gender composition",
                "headers": ("House", "Boarders", "Male", "Female"),
                "rows": house_rows,
            },
        ),
        "record_count": total,
    }
```

### 1f. SMS Delivery & Usage (`get_sms_usage_summary`)

Uses the tenant-aware quota helpers.

```python
# core/services/reports.py  (append)

def get_sms_usage_summary(house_id=None, date_from=None, date_to=None, term_id=None):
    """Delivery health plus this-month quota consumption for parent SMS."""
    from tenancy.entitlements import quota_limit, sms_monthly_usage

    deliveries = SMSDelivery.objects.all()
    if date_from:
        deliveries = deliveries.filter(created_at__date__gte=date_from)
    if date_to:
        deliveries = deliveries.filter(created_at__date__lte=date_to)
    if term_id:
        deliveries = deliveries.filter(exeat__academic_term_id=term_id)
    if house_id is not None:
        deliveries = deliveries.filter(exeat__student__house_id=house_id)

    total = deliveries.count()
    by_status = {
        row["status"]: row["c"] for row in deliveries.values("status").annotate(c=Count("id"))
    }
    sent = by_status.get(SMSDelivery.Status.SENT, 0)
    queued = by_status.get(SMSDelivery.Status.QUEUED, 0)
    processing = by_status.get(SMSDelivery.Status.PROCESSING, 0)
    failed = by_status.get(SMSDelivery.Status.FAILED, 0)
    attempted = sent + failed
    success_rate = round(sent * 100 / attempted, 1) if attempted else 0

    event_rows = [
        (
            dict(SMSDelivery.EventType.choices).get(row["event_type"], row["event_type"]),
            row["c"],
        )
        for row in deliveries.values("event_type").annotate(c=Count("id")).order_by("-c")
    ]
    failure_rows = [
        (row["last_error"] or "No error text", row["c"])
        for row in deliveries.filter(status=SMSDelivery.Status.FAILED)
        .values("last_error")
        .annotate(c=Count("id"))
        .order_by("-c")[:10]
    ]

    used = sms_monthly_usage()
    limit = quota_limit("max_sms_monthly")
    percent = round(used * 100 / limit, 1) if limit else None

    return {
        "title": "SMS Delivery & Usage",
        "layout": "sms_usage",
        "point_in_time": False,
        "gauge": {"used": used, "limit": limit, "percent": percent},
        "kpi_tiles": (
            ("Sent", sent, "emerald"),
            ("Queued", queued, "amber"),
            ("Processing", processing, "sky"),
            ("Failed", failed, "rose"),
            ("Success rate", f"{success_rate}%", "violet"),
            ("Deliveries in period", total, "indigo"),
        ),
        "summary": (
            ("Sent", sent),
            ("Queued", queued),
            ("Processing", processing),
            ("Failed", failed),
            ("Success rate", f"{success_rate}%"),
            ("This month", f"{used}" + (f" / {limit}" if limit else "")),
        ),
        "sections": (
            {"title": "Deliveries by event", "headers": ("Event", "Messages"), "rows": event_rows},
            {"title": "Failure reasons", "headers": ("Error", "Messages"), "rows": failure_rows},
        ),
        "highlights": (
            "This-month usage counts sent messages plus outstanding queued/processing items, matching the platform quota calculation.",
            "A null monthly limit means the plan has no SMS cap configured.",
        ),
        "record_count": total,
    }
```

**One import for the service file** — add `SMSDelivery` to the `core.models` import block at the top of `reports.py`:

```python
from core.models import (
    Student, Exeat, ExeatReconciliation, DisciplinaryRecord,
    RollCallSession, RollCallEntry, AcademicTerm, House, Dormitory,
    DormitoryRoom, SMSDelivery,          # <-- add SMSDelivery
)
```

---

## 2. Forms — one edit in `core/forms.py`

Append the four table reports to `ReportFilterForm.REPORT_CHOICES` (after the Tier-1 entries):

```python
# core/forms.py  — ReportFilterForm.REPORT_CHOICES
        ("gate_conflicts", "Gate / roll-call conflicts"),
        ("approval_turnaround", "Exeat approval turnaround"),
        ("exeat_analytics", "Exeat type & destination analytics"),
        ("room_register", "Room bed-allocation register"),
```

(The two dedicated views — Demographics and SMS — are reached by their own links, not the dropdown.)

---

## 3. Views — `core/views.py`

Insert after the Tier-1 `boarding_kpi_export_view`. Both use the same decorator stack and house scoping.

```python
# core/views.py  — insert after boarding_kpi_export_view

@login_required
@role_required(*REPORT_ROLES)
@tenant_feature_required("advanced_reports")
def boarding_demographics_view(request):
    """Population mix of the boarding department."""
    from core.services.reports import get_boarding_demographics

    house_scope = get_house_scope(request.user)
    report = get_boarding_demographics(
        house_id=house_scope.pk if house_scope is not None else None,
    )
    context = {
        "report": report,
        "user_house_scope": house_scope,
        "report_generated_at": timezone.localtime(),
        "active_tab": "reports",
    }
    template = (
        "core/reports/boarding_demographics_print.html"
        if request.GET.get("print")
        else "core/reports/boarding_demographics.html"
    )
    return render(request, template, context)


@login_required
@role_required(*REPORT_ROLES)
@tenant_feature_required("advanced_reports")
def sms_usage_view(request):
    """SMS delivery health and monthly quota consumption."""
    from core.services.reports import get_sms_usage_summary

    house_scope = get_house_scope(request.user)
    report_filters = ReportFilterForm(request.GET.copy())
    valid = report_filters.is_valid()
    cleaned = report_filters.cleaned_data if valid else {}
    term = cleaned.get("term")

    report = get_sms_usage_summary(
        house_id=house_scope.pk if house_scope is not None else None,
        date_from=cleaned.get("date_from"),
        date_to=cleaned.get("date_to"),
        term_id=term.pk if term else None,
    )
    context = {
        "report": report,
        "report_filters": report_filters,
        "report_filters_valid": valid,
        "user_house_scope": house_scope,
        "selected_term": term,
        "report_generated_at": timezone.localtime(),
        "active_tab": "reports",
    }
    template = (
        "core/reports/sms_usage_print.html"
        if request.GET.get("print")
        else "core/reports/sms_usage.html"
    )
    return render(request, template, context)
```

Multi-tenancy notes (identical discipline to Tier-1): schema isolation is handled by middleware before the view runs; `@tenant_feature_required("advanced_reports")` keeps plan gating; `quota_limit` / `sms_monthly_usage` read the tenant's `TenantSubscription` from the public schema and return `None`/unbounded when tenancy is disabled.

---

## 4. URLs — `core/urls.py`

Add beside the Tier-1 routes:

```python
# core/urls.py
    path('reports/demographics/', views.boarding_demographics_view, name='boarding_demographics'),
    path('reports/sms-usage/', views.sms_usage_view, name='sms_usage'),
```

---

## 5. Templates

Only the two visual reports need templates. The four table reports render through the existing `dashboard.html` generic branch and `print.html` with zero changes.

### 5a. `templates/core/reports/boarding_demographics.html`

```html
{% extends "base.html" %}

{% block title %}Boarders Demographics - {{ school_configuration.name }}{% endblock %}

{% block content %}
<div class="space-y-6">
  <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">Boarders Demographics &amp; Programme Distribution</h1>
      <p class="mt-1 text-sm text-slate-500">
        {% if user_house_scope %}{{ user_house_scope.name }}{% else %}School-wide{% endif %} · Current population snapshot
      </p>
    </div>
    <a href="{% url 'core:boarding_demographics' %}?print=1" target="_blank" rel="noopener"
       class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold border rounded-lg border-brand-300 bg-brand-50 text-brand-700 hover:bg-brand-100">
      Print / Save as PDF
    </a>
  </div>

  <section>
    <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
      {% for label, value in report.summary %}
        <div class="p-3 bg-white border rounded-lg shadow-sm border-slate-200">
          <p class="truncate text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ label }}</p>
          <p class="mt-0.5 text-lg font-bold leading-5 text-slate-900">{{ value }}</p>
        </div>
      {% endfor %}
    </div>
  </section>

  <div class="grid grid-cols-1 gap-6 lg:grid-cols-3">
    {% for group in report.distribution %}
      <section class="p-5 bg-white border shadow-sm rounded-xl border-slate-200">
        <h2 class="mb-3 text-sm font-bold text-slate-800">{{ group.title }}</h2>
        <div class="space-y-3">
          {% for label, value in group.rows %}
            <div>
              <div class="flex justify-between mb-1 text-xs">
                <span class="font-medium text-slate-700">{{ label }}</span>
                <span class="text-slate-500">{{ value }}</span>
              </div>
              <div class="w-full h-2 overflow-hidden rounded-full bg-slate-100">
                <div class="h-full bg-indigo-500 rounded-full"
                     style="width: {% widthratio value report.record_count 100 %}%; max-width: 100%;"></div>
              </div>
            </div>
          {% empty %}
            <p class="py-6 text-sm text-center text-slate-400">No boarders in scope.</p>
          {% endfor %}
        </div>
      </section>
    {% endfor %}
  </div>

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
              <tr><td colspan="{{ section.headers|length }}" class="px-3 py-6 text-center text-slate-500">No data.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </section>
  {% endfor %}

  <a href="{% url 'core:reports' %}" class="text-xs font-semibold text-brand-600 hover:text-brand-700">← Back to Reports &amp; Stats</a>
</div>
{% endblock %}
```

### 5b. `templates/core/reports/boarding_demographics_print.html`

Standalone print sheet (same inline-CSS conventions as the existing `print.html`).

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
    .report { max-width: 1100px; margin: 32px auto; padding: 32px; background: #fff; }
    .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid #cbd5e1; }
    .brand-mark { display: grid; width: 44px; height: 44px; place-items: center; border: 1px dashed #64748b; color: #475569; font-size: 9px; font-weight: 700; }
    .brand-logo { width: 44px; height: 44px; object-fit: contain; }
    .brand-name { margin: 0; font-size: 16px; font-weight: 800; letter-spacing: .04em; }
    .brand-details { margin: 3px 0 0; color: #64748b; font-size: 10px; }
    .toolbar { display: flex; justify-content: flex-end; margin-bottom: 24px; }
    button { border: 0; border-radius: 6px; background: #172033; color: #fff; padding: 10px 16px; font-weight: 700; cursor: pointer; }
    h1 { margin: 0; font-size: 22px; }
    .meta { margin: 8px 0 20px; color: #475569; font-size: 12px; }
    h2 { margin: 20px 0 8px; font-size: 14px; }
    table { width: 100%; border-collapse: collapse; font-size: 11px; }
    th, td { border: 1px solid #cbd5e1; padding: 6px; text-align: left; }
    th { background: #e2e8f0; font-size: 10px; text-transform: uppercase; }
    .bar { height: 8px; background: #e2e8f0; border-radius: 4px; }
    .bar > span { display: block; height: 8px; background: #6366f1; border-radius: 4px; }
    @page { size: portrait; margin: 12mm; }
    @media print { body { background: #fff; } .report { max-width: none; margin: 0; padding: 0; } .no-print { display: none !important; } }
  </style>
</head>
<body>
  <main class="report">
    <div class="toolbar no-print"><button type="button" onclick="window.print()">Print / Save as PDF</button></div>
    <header class="brand">
      {% if school_configuration.logo %}<img class="brand-logo" src="{{ school_configuration.logo.url }}" alt="logo">{% else %}<div class="brand-mark">LOGO</div>{% endif %}
      <div>
        <p class="brand-name">{{ school_configuration.name }}</p>
        {% if school_configuration.address %}<p class="brand-details">{{ school_configuration.address }}</p>{% endif %}
      </div>
    </header>
    <h1>{{ report.title }}</h1>
    <p class="meta">
      <strong>Scope:</strong> {% if user_house_scope %}{{ user_house_scope.name }}{% else %}School-wide{% endif %} ·
      <strong>Generated:</strong> {{ report_generated_at|date:"M d, Y H:i T" }}
    </p>

    {% for group in report.distribution %}
      <h2>{{ group.title }}</h2>
      <table>
        <thead><tr><th>Group</th><th>Boarders</th><th>Share</th></tr></thead>
        <tbody>
          {% for label, value in group.rows %}
            <tr>
              <td>{{ label }}</td>
              <td>{{ value }}</td>
              <td><div class="bar"><span style="width: {% widthratio value report.record_count 100 %}%;"></span></div></td>
            </tr>
          {% empty %}
            <tr><td colspan="3">No boarders in scope.</td></tr>
          {% endfor %}
        </tbody>
      </table>
    {% endfor %}

    {% for section in report.sections %}
      <h2>{{ section.title }}</h2>
      <table>
        <thead><tr>{% for heading in section.headers %}<th>{{ heading }}</th>{% endfor %}</tr></thead>
        <tbody>
          {% for row in section.rows %}
            <tr>{% for value in row %}<td>{{ value|default:"—" }}</td>{% endfor %}</tr>
          {% empty %}
            <tr><td colspan="{{ section.headers|length }}">No data.</td></tr>
          {% endfor %}
        </tbody>
      </table>
    {% endfor %}
  </main>
</body>
</html>
```

### 5c. `templates/core/reports/sms_usage.html`

```html
{% extends "base.html" %}

{% block title %}SMS Delivery &amp; Usage - {{ school_configuration.name }}{% endblock %}

{% block content %}
<div class="space-y-6">
  <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
    <div>
      <h1 class="text-2xl font-bold text-slate-900">SMS Delivery &amp; Usage</h1>
      <p class="mt-1 text-sm text-slate-500">
        {% if user_house_scope %}{{ user_house_scope.name }}{% else %}School-wide{% endif %} · Parent notification health
      </p>
    </div>
    <div class="flex flex-wrap items-end gap-2">
      <form method="get" action="{% url 'core:sms_usage' %}" class="flex flex-wrap items-end gap-2">
        <div>
          <label class="block mb-1 text-xs font-semibold text-slate-600">From</label>
          {{ report_filters.date_from }}
        </div>
        <div>
          <label class="block mb-1 text-xs font-semibold text-slate-600">To</label>
          {{ report_filters.date_to }}
        </div>
        <div>
          <label class="block mb-1 text-xs font-semibold text-slate-600">Term</label>
          {{ report_filters.term }}
        </div>
        <button type="submit" class="px-3 py-2 text-sm font-semibold text-white rounded-lg bg-slate-900 hover:bg-slate-700">Apply</button>
      </form>
      <a href="{% url 'core:sms_usage' %}?print=1" target="_blank" rel="noopener"
         class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold border rounded-lg border-brand-300 bg-brand-50 text-brand-700 hover:bg-brand-100">
        Print / Save as PDF
      </a>
    </div>
  </div>

  <!-- Monthly quota gauge -->
  <section class="p-5 bg-white border shadow-sm rounded-xl border-slate-200">
    <div class="flex items-center justify-between">
      <h2 class="text-sm font-bold text-slate-800">This month's SMS quota</h2>
      {% if report.gauge.limit %}
        <span class="text-xs font-semibold text-slate-600">{{ report.gauge.used }} / {{ report.gauge.limit }} · {{ report.gauge.percent }}%</span>
      {% else %}
        <span class="text-xs font-semibold text-slate-600">{{ report.gauge.used }} sent/queued · no monthly cap</span>
      {% endif %}
    </div>
    <div class="mt-3 h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
      {% if report.gauge.percent is not None %}
        <div class="h-full rounded-full {% if report.gauge.percent >= 90 %}bg-rose-500{% elif report.gauge.percent >= 80 %}bg-amber-500{% else %}bg-emerald-500{% endif %}"
             style="width: {% if report.gauge.percent > 100 %}100{% else %}{{ report.gauge.percent }}{% endif %}%;"></div>
      {% endif %}
    </div>
    <p class="mt-2 text-[11px] text-slate-500">Usage counts sent messages plus outstanding queued and processing items, matching the platform quota calculation.</p>
  </section>

  <section>
    <div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
      {% for label, value, tone in report.kpi_tiles %}
        <div class="p-3 bg-white border rounded-lg shadow-sm border-slate-200">
          <p class="truncate text-[10px] font-semibold uppercase tracking-wide text-slate-500">{{ label }}</p>
          <p class="mt-0.5 text-lg font-bold leading-5 {% if tone == 'rose' %}text-rose-600{% elif tone == 'emerald' %}text-emerald-600{% elif tone == 'amber' %}text-amber-600{% else %}text-slate-900{% endif %}">{{ value }}</p>
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
              <tr>{% for value in row %}<td class="px-3 py-2.5 text-slate-700">{{ value|default:"—" }}</td>{% endfor %}</tr>
            {% empty %}
              <tr><td colspan="{{ section.headers|length }}" class="px-3 py-6 text-center text-slate-500">No matching deliveries.</td></tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </section>
  {% endfor %}

  <a href="{% url 'core:reports' %}" class="text-xs font-semibold text-brand-600 hover:text-brand-700">← Back to Reports &amp; Stats</a>
</div>
{% endblock %}
```

### 5d. `templates/core/reports/sms_usage_print.html`

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
    .report { max-width: 1000px; margin: 32px auto; padding: 32px; background: #fff; }
    .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; padding-bottom: 14px; border-bottom: 1px solid #cbd5e1; }
    .brand-mark { display: grid; width: 44px; height: 44px; place-items: center; border: 1px dashed #64748b; color: #475569; font-size: 9px; font-weight: 700; }
    .brand-logo { width: 44px; height: 44px; object-fit: contain; }
    .brand-name { margin: 0; font-size: 16px; font-weight: 800; letter-spacing: .04em; }
    .toolbar { display: flex; justify-content: flex-end; margin-bottom: 24px; }
    button { border: 0; border-radius: 6px; background: #172033; color: #fff; padding: 10px 16px; font-weight: 700; cursor: pointer; }
    h1 { margin: 0; font-size: 22px; }
    .meta { margin: 8px 0 20px; color: #475569; font-size: 12px; }
    .kpi-grid { display: grid; grid-template-columns: repeat(3, minmax(0,1fr)); gap: 8px; margin: 0 0 16px; }
    .kpi-item { border: 1px solid #cbd5e1; padding: 8px; }
    .kpi-item span { display: block; font-size: 10px; text-transform: uppercase; color: #64748b; }
    .kpi-item strong { display: block; margin-top: 4px; font-size: 16px; }
    h2 { margin: 20px 0 8px; font-size: 14px; }
    table { width: 100%; border-collapse: collapse; font-size: 11px; }
    th, td { border: 1px solid #cbd5e1; padding: 6px; text-align: left; }
    th { background: #e2e8f0; font-size: 10px; text-transform: uppercase; }
    @page { size: portrait; margin: 12mm; }
    @media print { body { background: #fff; } .report { max-width: none; margin: 0; padding: 0; } .no-print { display: none !important; } }
  </style>
</head>
<body>
  <main class="report">
    <div class="toolbar no-print"><button type="button" onclick="window.print()">Print / Save as PDF</button></div>
    <header class="brand">
      {% if school_configuration.logo %}<img class="brand-logo" src="{{ school_configuration.logo.url }}" alt="logo">{% else %}<div class="brand-mark">LOGO</div>{% endif %}
      <div><p class="brand-name">{{ school_configuration.name }}</p></div>
    </header>
    <h1>{{ report.title }}</h1>
    <p class="meta">
      <strong>Scope:</strong> {% if user_house_scope %}{{ user_house_scope.name }}{% else %}School-wide{% endif %} ·
      <strong>Generated:</strong> {{ report_generated_at|date:"M d, Y H:i T" }}<br>
      <strong>This month:</strong> {{ report.gauge.used }}{% if report.gauge.limit %} / {{ report.gauge.limit }} ({{ report.gauge.percent }}%){% else %} (no monthly cap){% endif %}
    </p>
    <div class="kpi-grid">
      {% for label, value, tone in report.kpi_tiles %}<div class="kpi-item"><span>{{ label }}</span><strong>{{ value }}</strong></div>{% endfor %}
    </div>
    {% for section in report.sections %}
      <h2>{{ section.title }}</h2>
      <table>
        <thead><tr>{% for heading in section.headers %}<th>{{ heading }}</th>{% endfor %}</tr></thead>
        <tbody>
          {% for row in section.rows %}<tr>{% for value in row %}<td>{{ value|default:"—" }}</td>{% endfor %}</tr>
          {% empty %}<tr><td colspan="{{ section.headers|length }}">No data.</td></tr>{% endfor %}
        </tbody>
      </table>
    {% endfor %}
  </main>
</body>
</html>
```

### 5e. Optional links on `templates/core/reports/dashboard.html`

Next to the Tier-1 "Termly KPI Pack" button, add:

```html
<a href="{% url 'core:boarding_demographics' %}" class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold bg-white border rounded-lg shrink-0 border-slate-300 text-slate-700 hover:bg-slate-50">Demographics</a>
<a href="{% url 'core:sms_usage' %}" class="inline-flex items-center justify-center px-4 py-2 text-sm font-semibold bg-white border rounded-lg shrink-0 border-slate-300 text-slate-700 hover:bg-slate-50">SMS Usage</a>
```

The four table reports appear automatically in the dropdown — no dashboard or print template edits.



