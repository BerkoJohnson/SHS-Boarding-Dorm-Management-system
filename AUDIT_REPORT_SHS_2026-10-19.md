# SHS Boarding & Dorm Management System
## Repository Audit and Improvement Plan

**Prepared for:** Eden Creative Systems (Eden Sys) and Atebubu Senior High School (pilot school)
**Audit date:** 19 October 2026
**Baseline:** `HEAD 8c490e0` (`feat(students): add cohort promotion and SHS 3 phase-out`, which includes the previously uncommitted progression work and the prior audit file). The only working-tree modification is this report.
**Audit type:** Read-only repository audit and production-readiness review
**Status:** Initial audit complete. No source code, schema, data, or external configuration was changed.

> This document replaces the earlier draft of the same filename. It follows the current brief's required order: executive summary, repository map, findings, traceability, security assessment, roadmap, test plan, open decisions, and approval checkpoint.

> **Post-approval update (20 Oct 2026):** Batch 1 was implemented after the approval checkpoint. The audit baseline, findings, and audit-phase limitations below remain as recorded; current implementation and test evidence are in `BATCH1_IMPLEMENTATION.md` and Appendix A.2.

---

## How to read the status labels

| Label | Meaning |
|---|---|
| **Verified** | Confirmed by reading the implementation, and where relevant by a passing framework check or by tracing the full UI → view → service → model path. |
| **Partially verified** | Some layers were confirmed, but a complete end-to-end or device/network test was not possible in this environment. |
| **Unverified** | The code suggests a behaviour, but it was not executed or cannot be proven without staging infrastructure or school input. |
| **Not implemented** | No implementation was found in the repository. |
| **Policy-dependent** | The correct behaviour cannot be decided from the code; school management must approve a rule first. |

**Scope limitation:** The local environment did not have a provisioned PostgreSQL/Redis test stack. `python manage.py check --settings=schoolsystem.settings.development` passes, but the full test suite, live SMS, live backup/restore, and Android device testing were not run. No production data or credentials were accessed.

---

# 1. Executive summary

## 1.1 Overall readiness

The repository is a substantial, coherent Django application, not a prototype. It has a credible boarding-domain model, role-aware views, a tenancy layer, security-conscious production settings, and a broad test inventory. The strongest parts are the separation of staff roles, house-scoped access, the exeat/reconciliation model, roll-call follow-up records, configuration auditing, and the amount of automated test code.

It is **not ready for unattended production use**, and it is **not ready for multi-school commercial operation** in its current state. It is suitable for a **supervised, single-school staging/pilot only after the pilot blockers in this report are fixed**, and only after school management resolves the policy questions in section 8.

The most serious problems are not cosmetic:

1. The gate search can return a future, pending, or old pass instead of the student's current pass.
2. The database permits multiple overlapping active exeats for one student.
3. Two staff members can create duplicate roll-call sessions for the same house/dormitory/date.
4. A completed roll call can still be changed through the absence-follow-up service.
5. Unfinished roll calls are deleted after one hour, destroying attendance evidence.
6. The configured SMS sender ID is ignored; provisioning seeds the school name, so the approved sender is bypassed.
7. The documented Render deployment does not start Celery worker/Beat, so SMS, overdue reminders, expiry, and the SHS 3 phase-out silently do not run.
8. Backups are local, unencrypted, unscheduled, and have no offsite copy or retention policy.
9. The README's restore example cannot work against a registered live tenant.
10. Support-access tokens exist only as a CLI verification exercise; there is no supervised support session in the application.

## 1.2 Readiness by area

| Area | Readiness | Evidence summary |
|---|---|---|
| Code structure and maintainability | **Good** | Clear app split, services for reports/roll call/SMS, settings split, tenancy layer. Large `views.py` (3,759 lines) and `reports.py` (2,868 lines) are maintainability risks but not correctness defects by themselves. |
| Authentication and baseline security | **Good, with hardening gaps** | Production settings fail fast on weak secrets, force HTTPS/HSTS/secure cookies, use CSRF trusted origins, and serve private photos through authorised views. No MFA, CSP is report-only, no dependency scanning. |
| Role-based access and tenant isolation | **Good, with role-model gaps** | House scoping and gate-only roles are consistently applied. No headmaster/assistant-headmaster/medical roles; `ADMIN` means system administrator. |
| Exeat lifecycle and gate safety | **At risk** | Wrong-pass lookup, multiple active passes, no reschedule workflow, no emergency override, no gate roster, late returns create no incident. |
| Roll call and attendance integrity | **At risk** | Duplicate sessions, post-closure mutation, destructive expiry, no authorised correction workflow, hard-coded escalation. |
| Dormitory allocation | **Partial** | Room capacity is enforced; dormitory-level capacity and senior overcapacity exceptions are not. |
| SMS communication | **Not production-ready** | Sender-ID bug, stale quota rows, no low-credit alerts, no selected-group messaging, event-only messaging, worker not deployed. |
| Reports and exports | **Partial** | Broad report set and house scoping; several calculation defects; print-to-PDF only; one CSV export; no scheduled email reports. |
| Backup, restore, disaster recovery | **Not evidenced** | Manual local archives, no encryption, no retention/offsite, no scheduled test, live restore refused. |
| Remote support | **Not implemented** | CLI token generate/verify/revoke only; no approval, session, scope, or application path. |
| Accessibility and mobile use | **Partial** | Viewport and 44px mobile navigation exist; gate action buttons are ~30px, base text is small, no skip link, no device test. |
| Documentation and handover | **Partial** | Very detailed technical README; no role-based quick starts, plain-language manual, or videos. |
| CI and test execution | **Partial** | Roughly 390 test methods exist, but CI runs only the tenancy provisioning integration test; the core suite is not gated. |

## 1.3 Strongest aspects

- **Domain model depth.** `Student`, `Exeat`, `ExeatMovement`, `ExeatReconciliation`, `RollCallSession`, `RollCallEntry`, `RollCallAbsenceFollowUp`, `DisciplinaryRecord`, `SMSDelivery`, and `ConfigurationAuditLog` model real boarding operations rather than a generic CRUD demo.
- **Least-privilege intent.** Gate officers cannot create exeats; housemasters are house-scoped; reports are limited to administrators, the Senior Housemaster, and housemasters; private photos require a logged-in record role or a valid public token.
- **Safety-oriented state rules.** `Exeat.clean()` rejects day students and completed students, enforces date order, and requires an approver for an approved pass. `ExeatMovement.clean()` prevents a return before a departure. `Student.clean()` enforces gender-appropriate dormitories and room capacity with row locking.
- **Privacy-conscious design.** Student photos are stored under a private root and streamed through authorised views with `X-Content-Type-Options: nosniff` and `X-Robots-Tag: noindex, nofollow`; public photo access uses an expiring token. No `|safe` or `mark_safe` usage was found in the templates and core app.
- **Security baseline.** Production settings reject short or low-entropy `DJANGO_SECRET_KEY`, set `DEBUG=False`, require explicit hosts outside tenancy mode, enable SSL redirect, secure cookies, HSTS, `SECURE_CONTENT_TYPE_NOSNIFF`, and a report-only CSP with nonce-based scripts.
- **Tenancy architecture.** Schema-per-tenant with `django-pgschemas`, verified-domain routing, entitlement checks, audited provisioning, quota commands, backup/restore commands, and integration tests.
- **Reporting breadth.** Seventeen report types cover occupancy, attendance, exeats, discipline, placement exceptions, the attendance league, overdue returns, gate conflicts, approval turnaround, exeat analytics, room allocation, staff activity, welfare, data completeness, movement concerns, conduct normalisation, and intervention effectiveness.
- **Tests are not an afterthought.** The core app contains dedicated test modules for role authorisation, production settings, private storage, SMS, ID cards, room allocation, progression, registry, throttling, and house access; the tenancy app tests provisioning, isolation, plans, and platform commands.

## 1.4 Major risks

1. **Student safety at the gate.** The search view does not filter by status or active window and uses an unordered `.first()`. Because `Exeat.Meta.ordering` is `-expected_departure`, a future Pending pass can outrank the valid current pass. The verification card then correctly refuses to log movement, so a student with a valid pass can be turned away, or the officer may act on the wrong pass.
2. **Attendance records can be wrong or destroyed.** Duplicate sessions split the roster; a completed session can be changed through the follow-up service; expired sessions are deleted; there is no authorised post-closure correction record.
3. **SMS can silently fail.** The configured sender ID is ignored in favour of the school short name, and the documented Render blueprint does not run the worker or Beat. Both failures are silent in normal use.
4. **Recovery is not demonstrated.** Backups are manual, local, unencrypted, and not covered by retention or offsite controls. A live tenant cannot be restored in place. The 24-hour recovery target is an aspiration, not an evidenced capability.
5. **Policy gaps are being handled by admin edits or not at all.** Early departure, emergency override, late-return investigation, medical authorization, dormitory overcapacity, and retention have no approved workflow.
6. **Documentation overstates operational readiness.** The support-token and restore sections describe capabilities that the code does not provide end to end. This is a commercial and trust risk, not only a documentation issue.

## 1.5 What is not a defect

These items may look like gaps but are consistent with the stated requirements and should not be “fixed” without a policy change:

- **No guardian self-service portal.** Requirement C1 says staff create exeat requests; guardians do not submit them. SMS and telephone contact are the intended channels. A guardian portal is a product decision, not an audit finding.
- **No offline gate mode.** Requirement A9 says gate operations require internet connectivity and offline transactions are not assumed to be permitted. The system is online-only by design. The gap is graceful error handling and operational monitoring, not an offline queue.
- **No MFA.** The public website already states that two-factor authentication and single sign-on are not provided. MFA is a recommendation for production hardening, not a hidden capability.
- **No student registry CSV export.** This is privacy-positive. The only bulk exports are the boarding KPI CSV (report roles) and the admin-only student ID image ZIP. Export logging is still missing.

## 1.6 Staging and production recommendation

- **Staging:** Suitable for supervised workflow testing with synthetic data, provided SMS is disabled or pointed at a sandbox and the worker/Beat decision is made explicitly. Do not load real guardian or medical data until the backup and access decisions are settled.
- **Pilot:** Conditionally suitable after the pilot blockers (section 6) are fixed: gate lookup, one-active-exeat rule, duplicate roll call, closed-session mutation, SMS sender, worker/Beat, and a tested restore runbook.
- **Production / multi-school:** Not ready. Recovery, support access, retention, monitoring, accessibility, and policy workflows must be completed and drilled first.

---

# 2. Repository map

## 2.1 Top-level structure

| Path | Responsibility | Notes |
|---|---|---|
| `core/` | Main tenant-facing Django app: students, guardians, houses, dormitories, exeats, gate, roll call, conduct, SMS, reports, dashboards. | `models.py` 1,973 lines; `views.py` 3,759; `forms.py` 836; `admin.py` 558; `permissions.py` 167; `tasks.py` 538. |
| `core/services/` | Business logic: `reports.py` (2,868 lines), `roll_call.py` (489), `sms.py` (272), `student_import.py`, `student_progression.py`. | Reports and roll call are the highest-risk service modules. |
| `core/management/commands/` | `import_roster.py` and related operational commands. | Roster import supports CSV/XLSX with a 10 MB cap. |
| `core/tests.py` and `core/test_*.py` | Core behaviour, security, reports, registry, SMS, ID cards, progression, throttling, house access. | Roughly 300 test methods. |
| `tenancy/` | Platform layer: tenant registry, domains, plans, subscriptions, invoices, entitlements, support tokens, backups, restores, provisioning. | `models.py` 452 lines; commands are the operational surface. |
| `website/` | Public marketing site, demo requests, health check. | `/healthz` is served here and bypassed by tenancy middleware. |
| `schoolsystem/` | Django project: settings split, root URLs, Celery app, WSGI. | `base.py`, `development.py`, `production.py`, `build.py`, `tenancy_test.py`. |
| `templates/` | Server-rendered templates, including gate terminal, reports, students, exeats, roll call. | Tailwind-based, responsive classes, HTMX partials. |
| `static/`, `staticfiles/` | Committed CSS/JS and collected assets. | Tailwind output is committed; deployment does not require Node. |
| `.private/` | Private student photos, ID exports, backup output. | Git-ignored; local-filesystem fallback. |
| `.github/workflows/tenant-postgres-integration.yml` | CI for tenancy provisioning. | Does not run the core suite. |
| `render.yaml` | Render Blueprint for the staging web service and external Neon database. | Celery worker/Beat are documented as optional and are not declared. |
| `Procfile` | Web, worker, and Beat process definitions for platforms that read it. | Render does not use Procfile worker entries. |
| `requirements.txt`, `pyproject.toml` | Pinned Python dependencies and project metadata. | Django 6.0.9, Celery 5.6.3, django-pgschemas 1.3.2, boto3, openpyxl, qrcode. |
| `README.md` | Install, configuration, roles, deployment, backups, restore, maintenance. | Detailed and technical; not a plain-language user manual. |

## 2.2 Domain model groups

| Group | Models | Purpose |
|---|---|---|
| People and roles | `User`, `UserProfile`, `HouseMaster`, `Guardian`, `Student` | Authentication, staff role, house assignment, guardian contact, student record. |
| Residence | `House`, `Dormitory`, `DormitoryRoom` | House oversight, dormitory block, room-level bed capacity. |
| Academic | `AcademicYear`, `AcademicTerm`, `Programme`, `ClassGroup`, `StudentProgressionRecord` | Terms, classes, promotion and SHS 3 phase-out. The progression model and migrations 0032/0033 are now committed in `8c490e0`, but were not executed during this audit. |
| Movement | `Exeat`, `ExeatMovement`, `ExeatReconciliation`, `MovementConcernReport` | Pass lifecycle, gate departure/return, missed-movement reconciliation, public QR concern. |
| Attendance | `RollCallSession`, `RollCallEntry`, `RollCallAbsenceFollowUp`, `RollCallNotification` | Session, per-student mark, follow-up, in-app expiry notification. |
| Conduct | `DisciplinaryRecord` | Incident, severity, sanction, linkage to roll call, correction audit fields. |
| Configuration and communication | `SchoolConfiguration`, `SMSConfiguration`, `SMSDelivery`, `ConfigurationAuditLog`, `StudentIDExport` | School identity, SMS switch, delivery attempts, config-change audit, ID card exports. |
| Tenancy | `Tenant`, `Domain`, `SubscriptionPlan`, `TenantSubscription`, `PlatformAuditLog` | School registry, host routing, plan/feature/quota, platform audit. |

## 2.3 Integrations and external services

| Integration | Implementation | Audit note |
|---|---|---|
| PostgreSQL | Single database, schema-per-tenant via `django-pgschemas`; Neon-compatible connection settings. | Schema isolation is tested. No user-controlled raw SQL found in core views; tenancy uses controlled cursors for schema operations. |
| Redis / Celery | Broker for SMS dispatch, overdue reminders, roll-call expiry, SHS 3 phase-out, ID export cleanup. | Worker and Beat are not deployed by `render.yaml`. |
| SMSOnlineGH | HTTP POST with API key in the request payload, sender ID, timeout, retries, quota guard. | Sender resolution is defective; no delivery receipts or low-credit alerts. |
| S3-compatible storage | `MEDIA_STORAGE_BACKEND=neon_s3` for durable media; filesystem fallback for local development. | Private photos are streamed through Django, not exposed as public media. |
| Render | Web service, `/healthz`, external Neon database, optional Redis. | No worker/Beat; no cron backup service; free-plan behaviour may spin down. |
| Email | Django email backend configured; console backend is the default. | No application code sends email; scheduled email reports are absent. |
| Whitenoise | Static asset serving. | Committed Tailwind output; no Node at deploy. |

## 2.4 Deployment and operational components

- **Web:** Gunicorn via `render.yaml`, 3 workers × 2 threads, 60-second timeout, health check `/healthz`.
- **Database:** External Neon PostgreSQL; schema-per-tenant. Migrations are not automatic in the blueprint; `preDeployCommand` is documented as requiring a paid instance.
- **Cache/rate limits:** `LocMemCache` by default; `CACHE_BACKEND`/`CACHE_LOCATION` can point at Redis. Per-process rate limits are not cluster-wide unless a shared cache is configured.
- **Media:** Neon S3 in the staging blueprint; filesystem fallback locally.
- **Background tasks:** `Procfile` defines worker and Beat, but `render.yaml` does not. The blueprint comments explicitly warn that SMS remains queued and cleanup/phase-out tasks do not run without them.
- **Backups:** CLI commands only; no scheduled job, no cloud upload, no encryption, no retention policy.
- **Monitoring:** `/healthz` only; no Sentry, no structured logging configuration, no queue-depth alert, no failed-SMS alert.

## 2.5 Baseline caveat

The repository moved during the audit: the previously uncommitted progression work and the earlier audit file were committed as `8c490e0` (`feat(students): add cohort promotion and SHS 3 phase-out`). The code content audited here is the same content that is now in `HEAD`; only this report is modified in the working tree.

That commit includes:

- `core/migrations/0032_academicyear_students_phased_out_at_and_more.py`
- `core/migrations/0033_alter_studentprogressionrecord_from_class_group_and_more.py`
- `core/services/student_progression.py`
- `core/test_student_progression.py`
- `templates/core/students/promotion.html`

The progression feature is therefore part of the release baseline, but its migrations and tests were still not executed during this audit. A deployment must apply migrations 0032/0033 deliberately and verify the phase-out/promotion path before pilot use. This is a release-verification risk independent of the functional findings.

---

# 3. Evidence-based findings

## 3.1 Severity summary

| Severity | Count | Finding IDs |
|---|---:|---|
| High | 13 | F-01, F-02, F-03, F-07, F-09, F-10, F-11, F-13, F-16, F-26, F-27, F-28, F-32 |
| Medium | 21 | F-04, F-05, F-06, F-08, F-12, F-14, F-15, F-17, F-18, F-20, F-21, F-22, F-23, F-25, F-29, F-30, F-31, F-33, F-34, F-35, F-38 |
| Low | 4 | F-24, F-36, F-37, F-39 |

High findings are release blockers for a real pilot or production use. Medium findings are operational-correctness or governance gaps that should be scheduled before scale-up. Low findings are documentation or polish issues.

**Verification correction:** Finding IDs are stable. F-19 (an earlier candidate finding that the SMS usage report undercounts quota) was removed after direct re-verification: the page's quota gauge calls `sms_monthly_usage()` tenant-wide and explicitly labels the delivery metrics as filtered. The real quota defect is F-17 (stale queue rows and segment accounting), not the report layout. No other finding was downgraded or removed without an explicit note.

---

## 3.2 Gate, exeat, and student-safety findings

### F-01 — Gate pass search can return the wrong pass

- **Severity:** High
- **Status:** Verified
- **Location:** `core/views.py:3461-3485`; `core/models.py:1012-1014`; `templates/core/operations/partials/gate_verification_card.html`
- **Evidence:** `gate_verify_pass_view` filters by `exeat_number`, admission number, index number, or a partial exeat number, then calls `.first()`. It does not filter by status, active date window, or movement state, and it does not order the queryset. `Exeat.Meta.ordering` is `["-expected_departure"]`, so the pass with the latest expected departure wins. The card template correctly disables actions for non-Approved passes, meaning a future Pending pass can be displayed while the valid current pass is ignored.
- **Expected vs actual:** Expected: the currently actionable pass, or a disambiguation list. Actual: an arbitrary newest pass, often Pending or future.
- **Impact:** A student with a valid pass can be denied exit or return; a gate officer can act on the wrong pass; records and SMS become attached to the wrong trip. This is a student-safety and audit-integrity defect.
- **Recommended action:** Filter to actionable statuses and an explicit active window, rank exact pass number first, then active Approved passes, then recent closed passes; show all matches with status, dates, and movement state; never auto-select when more than one pass matches. Add a query minimum length and a “no current pass” state.
- **Verification test:** Build a student with (a) an old returned pass, (b) a current Approved pass, and (c) a future Pending pass. Search by admission number and assert the current Approved pass is selected. Search by exact pass number and assert exact match wins over partial matches. Search for a cancelled pass and assert it is not actionable.

### F-02 — Multiple active or overlapping exeats are allowed for one student

- **Severity:** High
- **Status:** Verified
- **Location:** `core/models.py` `Exeat` model and `clean()`; `core/views.py:1646-1656`
- **Evidence:** `Exeat.clean()` validates day/completed status, date order, approval attribution, and movement constraints, but never checks for another active pass. There is no `UniqueConstraint` for active passes, and `exeat_create_view` does not perform a duplicate check. The public QR page and gate search each choose one pass from a set that can contain several.
- **Expected vs actual:** Expected: one active exeat per student, as required. Actual: multiple Approved passes with overlapping or adjacent windows are possible.
- **Impact:** Conflicting departure/return times, duplicate SMS, ambiguous gate decisions, and inconsistent “currently out” counts. It also makes the wrong-pass problem in F-01 more likely.
- **Recommended action:** School management must first define “active” (Pending + Approved? Approved only? includes overdue unreturned?). Then enforce it in the create and approve paths inside a transaction, and add a PostgreSQL partial unique constraint after cleaning existing data.
- **Verification test:** Creating a second overlapping Approved pass is rejected; a non-overlapping future pass is allowed; two concurrent create requests result in exactly one active pass.

### F-03 — Early departure and rescheduling have no workflow or history

- **Severity:** High
- **Status:** Verified
- **Location:** `core/views.py:2066-2071`, `core/views.py:3389-3393`; `core/models.py` `Exeat` fields; `core/admin.py:266-290`
- **Evidence:** Ordinary early departure is explicitly blocked. There is no reschedule action, no `rescheduled_from` link, no schedule-version model, and no reason/actor field for a schedule change. The only way to change an approved pass is a Django admin edit, which overwrites the existing dates without preserving the old schedule.
- **Expected vs actual:** Expected: a formal reschedule with reason, authorising role, old/new schedule, and audit history; gate screens prioritise the current schedule. Actual: blocked early departure, admin overwrite only.
- **Impact:** Emergencies are handled outside the system or by untraceable admin edits. The school cannot answer “who changed this pass, when, and why?” This directly conflicts with A1 and A4.
- **Recommended action:** Add a reschedule workflow and an immutable schedule-change record (or a versioned `ExeatSchedule` table). Keep the original schedule visible on the pass and in reports. Define early-departure authority and emergency handling with school management before implementing.
- **Verification test:** Rescheduling an Approved pass creates a history row with actor, reason, old and new times; the gate displays the current schedule; an unauthorised user cannot reschedule; the original schedule remains queryable.

### F-04 — Emergency overrides are neither explicit nor reviewed

- **Severity:** Medium
- **Status:** Not implemented
- **Location:** `core/models.py` `Exeat`; `core/services/roll_call.py` reconciliation; no override model or field
- **Evidence:** `ExeatReconciliation` handles missed gate movements retrospectively, but there is no way to authorise a departure outside the normal approval path with an immediate documented reason and a subsequent review.
- **Expected vs actual:** Expected: emergency override with reason, actor, approver, and post-hoc review. Actual: no override concept.
- **Impact:** Staff may use admin edits or informal phone approval; the record does not show that an exception occurred.
- **Recommended action:** Add an explicit override flag and review state, require a reason and an authorised role, and route the event to a review queue. The school must define which roles may override and the review deadline.
- **Verification test:** An override without a reason is rejected; an authorised override creates a review item; a reviewer can record the outcome; the audit history is immutable.

### F-05 — No gate-duty roster or temporary assignment validity

- **Severity:** Medium
- **Status:** Not implemented
- **Location:** `core/models.py:363-371` (`StaffRole.GATE_OFFICER`); no roster model
- **Evidence:** A user with the Gate Officer role can use gate actions at any time. There is no shift, gate, start time, end time, approving officer, or temporary-assignment record. `can_log_gate_movement()` checks role only.
- **Expected vs actual:** Expected: authorised duty roster with temporary assignments and start/end times. Actual: role-only access, any time.
- **Impact:** No shift accountability, no way to prove who was on duty, and no automatic expiry for temporary cover.
- **Recommended action:** Add a `GateDutyAssignment` model with staff, gate, start/end, approver, and status; enforce an active assignment for gate actions if the school approves that policy; keep a read-only history.
- **Verification test:** A gate action outside an active assignment is denied; an expired temporary assignment is denied; an approved assignment works and is recorded.

### F-06 — No medical or infirmary authorization path

- **Severity:** Medium (High if after-hours medical movements are in pilot scope)
- **Status:** Not implemented; policy-dependent
- **Location:** `core/models.py:363-371`; `core/permissions.py:53-59` (`EXEAT_APPROVAL_ROLES`)
- **Evidence:** Staff roles are `ADMIN`, `SENIOR_HOUSEMASTER`, `HOUSEMASTER`, `HOUSE_TUTOR`, and `GATE_OFFICER`. There is no medical/infirmary role. Approval is limited to Admin, Senior Housemaster, and Housemaster. A nurse or infirmary lead would need one of those roles, which grants broader access than necessary.
- **Expected vs actual:** Expected: designated medical staff can authorise medical emergencies, with after-hours review. Actual: no least-privilege role and no after-hours workflow.
- **Impact:** Least privilege is violated if medical staff are given admin/housemaster rights; otherwise medical emergencies cannot be approved in the system.
- **Recommended action:** Add a medical/infirmary permission (or role) scoped to medical exeats, with after-hours approval and a review queue. Do not grant broad admin access as a workaround.
- **Verification test:** Medical staff can approve a medical exeat but not an ordinary leave pass; after-hours approval creates a review item; an unauthorised role is denied.

### F-07 — Late returns do not create an incident and escalation is not configurable

- **Severity:** High
- **Status:** Verified
- **Location:** `core/tasks.py:236-283`; `core/services/sms.py:139-155`; no `DisciplinaryRecord` creation on return
- **Evidence:** The overdue task sends SMS to the guardian and the student’s housemaster, deduplicated by an occurrence key. When a return is logged after `expected_return`, `gate_terminal_action_view` records `actual_return` and sends a return SMS. It does not create a `DisciplinaryRecord`, an investigation task, or a late-return event. The reminder cadence is a hard-coded 15-minute Beat schedule with daily deduplication.
- **Expected vs actual:** Expected: late return recorded, housemaster notified, incident requiring investigation created, escalation configurable. Actual: SMS notification only; no incident; no configurable escalation.
- **Impact:** There is no investigation trail, no accountable owner, and no escalation if the housemaster misses the SMS. This is a student-welfare and records gap.
- **Recommended action:** Create a late-return incident on return after the expected time (and optionally at an overdue threshold), with status, owner, and review outcome. Add configurable thresholds and recipient/escalation rules. Add an in-app notification for the housemaster.
- **Verification test:** A return after `expected_return` creates one incident linked to the pass; duplicate returns do not create duplicates; changing the threshold changes when the incident/escalation is created; the housemaster sees the notification.

### F-08 — Gate movement actor is stored as free text

- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/models.py:1178-1189` (`ExeatMovement.departure_recorded_by`, `return_recorded_by`)
- **Evidence:** Both fields are `CharField(max_length=100)` and are populated from a display name, not a foreign key to the authenticated user.
- **Expected vs actual:** Expected: authenticated actor reference plus a display-name snapshot. Actual: free text only.
- **Impact:** A gate record cannot be reliably attributed; names can be mistyped or spoofed; there is no link to the account for investigation.
- **Recommended action:** Add `departure_recorded_by_user` and `return_recorded_by_user` foreign keys (nullable for legacy rows), populate from the request, and keep the text snapshot for display. Backfill where possible.
- **Verification test:** Logging a departure records the logged-in user FK; the display name remains after the user is deactivated; legacy rows with only text still render.

### F-09 — Duplicate roll-call sessions can be created

- **Severity:** High
- **Status:** Verified
- **Location:** `core/services/roll_call.py:70`; `RollCallSession` model has no uniqueness constraint
- **Evidence:** `create_roll_call_session` calls `RollCallSession.objects.create(...)` unconditionally. Its docstring claims “newly created or existing in-progress session”, but the code does not use `get_or_create` or a lock. There is no `UniqueConstraint` on house, dormitory, session type, date, and status.
- **Expected vs actual:** Expected: one in-progress session per house/dormitory/type/date. Actual: two staff members, or two requests, create separate sessions and split the roster.
- **Impact:** Attendance is double-counted or split; one session may be completed while the other is missed; gate reconciliation may update only one; reports become inconsistent.
- **Recommended action:** Add an application-level guarded create inside a transaction and a PostgreSQL partial unique constraint for in-progress sessions. Decide how to merge or cancel duplicates already created.
- **Verification test:** Two sequential create calls return the same in-progress session; two concurrent calls produce one session; a new session is allowed after the first is completed.

### F-10 — Completed roll-call entries can be changed through follow-up resolution

- **Severity:** High
- **Status:** Verified
- **Location:** `core/services/roll_call.py:401-487`; `core/views.py:3154-3161`
- **Evidence:** `roll_call_update_entry_view` explicitly blocks edits when the session is not In Progress. However, `resolve_absence_follow_up` locks the session and follow-up but never checks `session.status` or `session.is_expired`. It then uses `RollCallEntry.objects.filter(pk=...).update(...)` to change the status, source, remarks, and timestamp, bypassing model validation and the view guard.
- **Expected vs actual:** Expected: completed and expired sessions are immutable except through an authorised correction workflow. Actual: a pending follow-up can mutate a completed session silently.
- **Impact:** Historical attendance and the linked conduct record can change after closure without a correction record. This undermines the audit trail and any disciplinary decision based on it.
- **Recommended action:** Reject follow-up mutation unless the session is In Progress and not expired. If post-closure correction is required, route it through a separate correction model with justification, evidence, approver, and immutable history. Stop using `.update()` for audited state changes.
- **Verification test:** A completed session’s follow-up resolution is rejected; an expired session’s follow-up is rejected; the authorised correction path records old/new status, actor, reason, and timestamp.

### F-11 — Expiry deletes unfinished roll calls and routine unresolved entries cannot remain visible

- **Severity:** High
- **Status:** Verified
- **Location:** `core/tasks.py:288-344`; `core/services/roll_call.py:238-265`; `core/models.py:21`
- **Evidence:** `_expire_unfinished_roll_calls_for_current_tenant` selects In Progress sessions older than `ROLL_CALL_SESSION_TIMEOUT` (one hour), creates a notification, and calls `session.delete()`. `complete_roll_call_session` refuses completion while any entry is Unmarked. There is no Expired/Abandoned status and no partial routine completion.
- **Expected vs actual:** Expected: routine unresolved entries remain visible for follow-up; emergency roll calls require full resolution; escalation periods are configurable. Actual: all sessions require full resolution, and unfinished sessions are destroyed after one hour.
- **Impact:** Attendance evidence is lost; unresolved students cannot be followed up from the record; staff are forced to mark everyone or lose the session. This is a records-retention and safeguarding risk.
- **Recommended action:** Replace deletion with an `EXPIRED`/`ABANDONED` status that preserves entries. Allow routine sessions to close with an explicit unresolved queue, while emergency/special sessions require full resolution. Make the timeout configurable per school.
- **Verification test:** An expired session remains queryable with its entries; the unresolved queue lists its students; an emergency session cannot close with unmarked students; changing the timeout changes the expiry time.

### F-12 — No authorised post-closure correction workflow

- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/views.py:3154-3161`; no correction model or reopen action
- **Evidence:** Completed sessions are blocked from entry updates. There is no correction request, reopen, approval, evidence field, or correction history. The only mutation path is the follow-up bypass in F-10, which is unintended and unaudited.
- **Expected vs actual:** Expected: authorised staff can correct attendance after closure with written justification and audit; sensitive corrections require extra evidence or approval. Actual: corrections are impossible through the UI, except via the bug.
- **Impact:** Staff cannot fix genuine errors through a controlled process, which encourages workarounds or falsified in-progress records.
- **Recommended action:** Add a correction request with reason, evidence, requested status, requester, approver, and decision. Apply the correction as an append-only event; never overwrite the original silently. Define which status changes need a second approver.
- **Verification test:** A housemaster requests a correction; a senior approver accepts; the original and corrected values are both visible; an unauthorised user cannot approve; a sensitive change without evidence is rejected.

---

## 3.3 Dormitory and occupancy findings

### F-13 — Dormitory-level capacity is not enforced and there is no senior exception

- **Severity:** High
- **Status:** Verified
- **Location:** `core/models.py:295-357` (`DormitoryRoom.clean`); `core/models.py:779-791` (`Student.clean`); `core/models.py:325-348` (`Dormitory.clean`)
- **Evidence:** Room capacity is enforced when a student is assigned to a room, with `select_for_update`. Dormitory capacity is checked only when the dormitory itself is edited: `Dormitory.clean()` raises if the current occupancy exceeds a newly lowered capacity. Student placement checks `self.room.capacity`, not `self.dormitory.capacity`. There is no overcapacity exception model.
- **Expected vs actual:** Expected: placements that exceed dormitory capacity are blocked unless an authorised senior administrator approves and records an exception. Actual: students can be assigned to a dormitory beyond its capacity as long as the room (if any) has space; there is no exception workflow.
- **Impact:** Overcrowding and inaccurate occupancy, with safety and safeguarding implications. House dashboards can show more boarders than beds.
- **Recommended action:** Add a shared capacity check for both rooms and dormitories inside the placement transaction, with row locking. Add a time-bound exception record with approver, reason, and review. Define the authorising role with the school.
- **Verification test:** Assigning beyond dormitory capacity is blocked; an authorised exception allows it and records the approver/reason; the exception expires; concurrent placements cannot exceed capacity.

### F-14 — Active-boarder and occupancy definitions are inconsistent

- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/views.py:641-651`; `core/services/reports.py:295-346`; `core/models.py:734-740`
- **Evidence:** `house_list_view` annotates `boarder_count=Count("students", filter=Q(students__residency=BOARDING))` with no status filter, so completed students are counted. The housing report filters to `ENROLLED`, `APPROVED`, and `IN_PROGRESS`. Completing a student does not clear the dormitory or room, and `DormitoryRoom.clean` excludes completed students only for its own capacity check. The result is that the house/dormitory overview and the report can disagree.
- **Expected vs actual:** Expected: one documented definition of an active boarder, applied consistently; inactive students excluded from current occupancy but available historically. Actual: the house list includes completed boarders; the report excludes them.
- **Impact:** Occupancy and capacity decisions are made on inconsistent numbers; completed students appear as current boarders; the dashboard loses credibility.
- **Recommended action:** Create one shared active-boarder queryset and use it in dashboards, reports, capacity checks, and roll-call rosters. School management must confirm whether `APPROVED` and `IN_PROGRESS` count as “currently enrolled” for occupancy. Add a separate historical view.
- **Verification test:** A completed student is excluded from house/dormitory occupancy but visible in the historical registry; the house list and housing report totals match for the same filters; approved/in-progress students follow the approved policy.

### F-15 — Escalation periods are hard-coded

- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/models.py:21` (`ROLL_CALL_SESSION_TIMEOUT = timedelta(hours=1)`); `core/tasks.py:264-281`; `core/tasks.py` Beat schedule
- **Evidence:** The roll-call timeout is a module constant. Overdue exeat reminders run every 15 minutes with a daily dedupe key. There is no per-school setting for roll-call expiry, late-return thresholds, absence follow-up deadlines, or escalation recipients.
- **Expected vs actual:** Expected: configurable escalation periods. Actual: one-hour roll-call expiry and fixed reminder cadence for every school.
- **Impact:** A pilot school cannot tune the system to its own timetable or escalation policy; changes require a code deployment.
- **Recommended action:** Add tenant-level operational settings with safe defaults, an audit trail for changes, and validation bounds. Use them in tasks and UI labels.
- **Verification test:** Changing the roll-call timeout changes expiry behaviour; changing the late-return threshold changes incident creation; settings changes are audited.

---

## 3.4 SMS and communication findings

### F-16 — SMS sender ID setting is ignored and provisioning bypasses the approved value

- **Severity:** High
- **Status:** Verified
- **Location:** `core/services/sms.py:22-25`, `core/services/sms.py:272`; `schoolsystem/settings/base.py:388`; `tenancy/management/commands/provision_school.py:80`
- **Evidence:** `_school_short_name()` reads the tenant's `SchoolConfiguration.short_name` first, then the school name, then `"ATSEC"`; it strips non-alphanumeric characters and truncates to 11 characters. The gateway payload uses `"sender": _school_short_name() or settings.SMS_SENDER_ID`. Provisioning seeds `short_name` with `school_name[:50].strip()`, so the tenant always has a short name and the configured `SMS_SENDER_ID` is never reached in normal tenant operation. The send path sanitizes the value, so the issue is the wrong sender, not an overlong payload.
- **Expected vs actual:** Expected: the approved sender ID is used, with validation and clear precedence. Actual: a school-name abbreviation is used; the configured sender is dead code whenever a short name exists.
- **Impact:** SMS is delivered under a derived abbreviation rather than the sender ID approved by the school and gateway; this can fail sender-ID whitelisting or branding review, and it makes troubleshooting confusing.
- **Recommended action:** Make the approved sender ID an explicit tenant setting with validation (GSM 03.38 alphanumeric, length limit), define precedence, and seed it from an approved value or a safe fallback. Log the resolved sender ID without logging the API key. Confirm the approved ID with SMSOnlineGH and the school.
- **Verification test:** With a short name and a configured sender ID, the payload uses the approved sender; a non-alphanumeric or overlong configured value is normalized or rejected at configuration time; the resolved sender is logged; the API key never appears in logs.

### F-17 — SMS quota counts stale queue rows and ignores message segments

- **Severity:** Medium
- **Status:** Verified
- **Location:** `tenancy/entitlements.py:153-165`
- **Evidence:** `sms_monthly_usage` counts `SENT` rows from the current month plus all `QUEUED` and `PROCESSING` rows regardless of age. A permanently stuck queue row consumes quota forever. The count is one row per `SMSDelivery`, not per SMS segment, so a long message can consume more gateway credit than the quota records.
- **Expected vs actual:** Expected: quota reflects billable segments and releases stale reservations. Actual: stale rows leak quota; long messages undercount.
- **Impact:** The school can be blocked from sending while the provider balance is fine, or can exceed its budget without warning.
- **Recommended action:** Expire stale Queued/Processing rows, count segments, and reconcile against provider delivery records. Add a repair command and an audit entry.
- **Verification test:** A week-old Queued row does not block a new send; a three-segment message counts as three; the usage report reconciles to delivery rows and provider data.

### F-18 — No low-credit or failed-message alerts; no selected-group messaging

- **Severity:** Medium
- **Status:** Not implemented (alerts); Partial (event messaging)
- **Location:** `core/tasks.py:171-178` (final failure logs only); `core/models.py:1456-1487` (`SMSConfiguration` has only `enabled`); no compose UI
- **Evidence:** On final failure, the task logs an error and marks the delivery failed. There is no notification to an administrator, no low-balance check, and no UI for composing an approved message to a selected group. SMS is limited to exeat event notifications to guardians and housemasters.
- **Expected vs actual:** Expected: low-credit and failed-message alerts; approved messages to selected groups. Actual: logs only; event-only messaging.
- **Impact:** The school discovers SMS failure only when a guardian does not receive a message; staff cannot use the system for approved group communication.
- **Recommended action:** Add admin alerts on repeated failures and quota thresholds, a provider balance check if the gateway supports it, and a permissioned group-message composer with templates, recipient preview, quota estimate, and approval. Keep message content out of general logs.
- **Verification test:** A failed delivery generates one alert; a low balance generates one alert; a group message is previewed, quota-checked, approved, and audited; unauthorised staff cannot send.

---

## 3.5 Reporting findings

### F-20 — Approval turnaround uses mutable `updated_at` and includes cancellations

- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/services/reports.py:1846-1871`
- **Evidence:** `decided = exeat["updated_at"]`; the turnaround is calculated for every row, including records later cancelled or edited. `updated_at` changes on any save, so an approval edited later appears slower than it was. The report counts approved, rejected, and cancelled but does not exclude cancelled records from turnaround statistics.
- **Expected vs actual:** Expected: decision timestamp at approval/rejection, with cancellations separated. Actual: last-modified timestamp for all records.
- **Impact:** Management decisions about approval performance are based on distorted data.
- **Recommended action:** Add `decided_at` (or use the approval action timestamp), set it once, and compute turnaround only for decided records. Report cancelled separately.
- **Verification test:** Approve then edit remarks; turnaround does not change. Cancel a request; it appears in cancellations but not in approval turnaround.

### F-21 — Staff activity report omits most staff and misdates approvals

- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/services/reports.py:2372-2429`
- **Evidence:** The report iterates `HouseMaster` records only. Administrators, gate officers, and house tutors who conduct roll calls, log incidents, or resolve follow-ups are not rows unless they also have a `HouseMaster` record. Approval filters use `updated_at__date`, with the same mutability problem as F-20.
- **Expected vs actual:** Expected: workload visibility for all staff who act in the system. Actual: only housemaster profiles, with approval dates based on last edit.
- **Impact:** The headmaster cannot see the real workload distribution; staff who do heavy operational work can appear inactive.
- **Recommended action:** Build the report from distinct actors in each activity table (roll call conductor, approver, incident logger, follow-up resolver, gate recorder), unioned with active staff, and use decision timestamps.
- **Verification test:** An admin who approves passes and a tutor who logs incidents appear in the report; a later edit does not change the approval date.

### F-22 — Overdue register date filters can hide currently overdue students

- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/services/reports.py:1626-1664`
- **Evidence:** The report filters `expected_return__date__gte=date_from` and `__lte=date_to`. A student who was expected back before the selected range but is still out is excluded, even though they are currently overdue. The “currently overdue” count is therefore range-dependent.
- **Expected vs actual:** Expected: “currently outside/overdue now” independent of the period, plus a period view for late returns. Actual: a single period filter can hide the most serious cases.
- **Impact:** A daily operational report can show zero overdue students while a student is still missing.
- **Recommended action:** Split the report into “overdue now” (no period restriction) and “late returns in period” (return date in range). Label both clearly.
- **Verification test:** A pass expected back last month and still out appears in “overdue now” for a this-week filter; a returned-late pass appears only in the period section when its return is in range.

### F-23 — Conduct-normalized report compares incidents against a different population

- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/services/reports.py:2730-2790`
- **Evidence:** The denominator counts boarding students in `ENROLLED`, `APPROVED`, and `IN_PROGRESS`. The numerator counts all active `DisciplinaryRecord` rows for the house and period, without filtering by residency or enrollment status. Day students and completed students can therefore contribute incidents to a rate per boarding student.
- **Expected vs actual:** Expected: numerator and denominator use the same population. Actual: unmatched populations.
- **Impact:** House comparisons are unfair and can mislead pastoral decisions.
- **Recommended action:** Apply the same residency/status filter to the numerator, or report day and boarding rates separately. State the population in the report header.
- **Verification test:** A day student’s incident does not change the boarding rate; a completed student’s historical incident is excluded from the current rate; totals reconcile.

### F-24 — Welfare report includes completed students

- **Severity:** Low
- **Status:** Verified
- **Location:** `core/services/reports.py:2518-2528`
- **Evidence:** The report collects student IDs from infirmary and medical exeat rows, then loads `Student.objects.filter(pk__in=student_ids)` without a status filter. Completed students with historical records appear in a current operational welfare report.
- **Expected vs actual:** Expected: current welfare list, with historical access separate. Actual: completed students included.
- **Impact:** The current caseload looks larger than it is; staff may follow up on students who have left.
- **Recommended action:** Filter to active statuses by default, with an explicit “include historical” option and a label.
- **Verification test:** A completed student with an old infirmary visit is excluded by default and appears when history is requested.

### F-25 — No server-side PDF/XLSX exports and no scheduled email reports

- **Severity:** Medium
- **Status:** Partial (print and one CSV); Not implemented (scheduled email)
- **Location:** `core/views.py:3514-3521` (print view); `core/views.py:3671-3698` (KPI CSV); `core/tasks.py` (no report-email task); `core/urls.py:136-150`
- **Evidence:** Detailed reports render an HTML print view; the browser can “Save as PDF”, but the server does not generate a PDF. Only the boarding KPI pack has a CSV export. There is no XLSX export and no scheduled report email task. `openpyxl` is used for roster import and tests, not report export.
- **Expected vs actual:** Expected: downloadable PDF and spreadsheet reports, plus scheduled email reports. Actual: browser print and one CSV; no schedule.
- **Impact:** Office staff cannot reliably produce the required daily/weekly packs without manual browser steps; there is no automated delivery.
- **Recommended action:** Add CSV/XLSX export endpoints for every report with the same filters and permissions, add a server-side PDF renderer if the school needs consistent output, and add an opt-in scheduled email task with recipient management and audit.
- **Verification test:** Every report exports CSV/XLSX with matching totals; a scheduled daily/weekly report is delivered to an authorised recipient; an unauthorised user cannot export.

---

## 3.6 Recovery, support, and platform findings

### F-26 — Backups are local, unencrypted, unscheduled, and lack retention/offsite controls

- **Severity:** High
- **Status:** Verified
- **Location:** `tenancy/management/commands/tenant_backup.py:115-120`, `251-254`, `277`, `298-320`; `render.yaml` (no backup service)
- **Evidence:** `tenant_backup` writes `pg_dump` output and optional media archives to a local output directory (default under `.private/backups`). It computes a SHA-256 checksum and writes an audit row, but does not encrypt the dump, upload it to cloud/offsite storage, apply a retention schedule, or run on a schedule. On Render's ephemeral filesystem, a local backup can be lost on redeploy. The audit metadata stores the local path.
- **Expected vs actual:** Expected: encrypted, access-controlled backups in cloud storage and a separate location, with retention and monitoring. Actual: manual local plaintext archives.
- **Impact:** A server loss can destroy both the database and its backups; student/guardian data at rest is unprotected; there is no evidence for the 24-hour recovery target.
- **Recommended action:** Encrypt archives with keys under documented custody, upload to an offsite bucket with object lock/versioning, define retention and deletion rules, schedule and monitor the job, and record restore drills. Do not store encryption keys beside the archives.
- **Verification test:** A scheduled backup lands offsite and is encrypted; a restore from the offsite copy succeeds in staging; retention deletes only expired archives; the key is not present in the backup bucket; a failed job alerts.

### F-27 — A live tenant cannot be restored in place; the README example contradicts the command

- **Severity:** High
- **Status:** Verified
- **Location:** `tenancy/management/commands/tenant_restore.py` (registered-tenant and nonempty-schema refusals); `README.md:1177-1179`
- **Evidence:** The restore command refuses if `Tenant.objects.filter(schema_name=schema_name).exists()`, and refuses if the target schema exists and is nonempty. The README tells operators to run `tenant_restore --schema=atebubu_shs --dry-run` and then restore from the newest archive for that same registered schema. That path cannot succeed.
- **Expected vs actual:** Expected: a documented, tested restore procedure for a live tenant within the 24-hour target. Actual: restore is only possible into a new/unregistered empty schema; the README example is invalid.
- **Impact:** In an incident, staff may discover the documented procedure does not work. Recovery time is unpredictable, and ad hoc manual intervention increases data-loss risk.
- **Recommended action:** Choose and document a real procedure: restore into a spare schema, validate, then switch the tenant mapping (or a controlled decommission/reprovision flow). Update the README, add a runbook with roles and approvals, and drill it quarterly.
- **Verification test:** A staging tenant is restored from backup into a spare schema, validated, and switched with minimal downtime; the runbook is followed by someone who did not write it; the old schema is retained for rollback.

### F-28 — Support-access tokens have no application wiring

- **Severity:** High
- **Status:** Verified
- **Location:** `tenancy/management/commands/support_access.py`; `tenancy/models.py:251-292`; no URL or view consumes the token
- **Evidence:** The command generates, verifies, revokes, and reports bcrypt-hashed time-bound tokens and writes platform audit rows. No view, middleware, or login path accepts a support token or creates a supervised support session. The README describes support tokens as enabling engineer access, but the application has no such access path.
- **Expected vs actual:** Expected: supervised, time-limited, approved support sessions with scope and action logging, or no such claim. Actual: a CLI token utility with no session integration.
- **Impact:** The documented remote-support control is not real. If engineers rely on ordinary admin accounts instead, there is no approval, scope, or session record.
- **Recommended action:** For the near term, remove the claim from the README and define support as a supervised screen-share/on-site process with the school. If remote access is later required, implement headmaster + ICT approval, a scoped session, action logging, and automatic expiry.
- **Verification test:** If implemented: an unapproved token cannot access a tenant; an approved session expires; every action is logged; revocation is immediate. If not implemented: the documentation no longer claims it.

### F-29 — Django admin can bypass exeat and movement workflow controls

- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/admin.py:266-290`
- **Evidence:** `ExeatAdmin` and `ExeatMovementAdmin` register the models without `readonly_fields`, custom form validation, or audit hooks. A staff user with admin model permission can change status, dates, approver, and movement times directly. `Model.save()` runs `full_clean()`, but there is no requirement for a reason, no state-transition guard, and no configuration audit entry.
- **Expected vs actual:** Expected: administrative corrections are controlled and audited. Actual: direct edits are possible with ordinary model validation only.
- **Impact:** An insider can rewrite gate/attendance history without a trace; the admin becomes a bypass around the workflow.
- **Recommended action:** Limit admin edit fields, require a reason for sensitive changes, route corrections through the same audited service as the UI, and log before/after values. Restrict admin access to a named platform administrator.
- **Verification test:** A status/date change in admin creates an audit record with actor, reason, and old/new values; an invalid transition is rejected; a read-only admin cannot edit.

### F-30 — No export or sensitive-access audit log; no dispute-review workflow

- **Severity:** Medium
- **Status:** Partial (configuration audit only); Not implemented (dispute review)
- **Location:** `core/models.py:1526+` (`ConfigurationAuditLog`); `core/views.py:3671-3698`; no dispute model
- **Evidence:** `ConfigurationAuditLog` covers staff role, housemaster assignment, academic term, school identity, and SMS enablement changes. The KPI CSV export is not logged; student ID exports record `requested_by` but there is no general export log. There is no model or UI for a complaint/dispute, reviewer, decision, or resulting correction.
- **Expected vs actual:** Expected: log bulk exports and sensitive access; support a formal dispute process for exeats, gate records, and attendance. Actual: partial config audit; no export log; no dispute workflow.
- **Impact:** The school cannot reconstruct who exported sensitive data or resolve a disagreement with a documented decision.
- **Recommended action:** Add an append-only access/export audit event (actor, time, report, filters, row count, purpose) and a dispute case model with complainant, record under dispute, reviewer, decision, and linked corrections. Restrict access to the case.
- **Verification test:** Exporting a report creates an audit row; a dispute is opened, reviewed, decided, and linked to a correction; unauthorised staff cannot see the case.

### F-31 — No retention or secure-disposal policy or code

- **Severity:** Medium
- **Status:** Policy-dependent; Not implemented
- **Location:** No retention fields, archival job, or disposal command; backups and records are retained indefinitely by default
- **Evidence:** There is no retention schedule, archive flag, anonymisation job, or secure-deletion command. The prompt contains conflicting proposed requirements (archive indefinitely vs approved retention and disposal). The code does not resolve that conflict, which is correct, but it also has no mechanism to apply a decision once made.
- **Expected vs actual:** Expected: school-approved retention schedule with archival access and secure disposal. Actual: no policy and no implementation.
- **Impact:** Data is kept longer than necessary, increasing privacy and storage risk; a future disposal request cannot be executed consistently.
- **Recommended action:** Do not implement disposal until the school approves a written schedule. Then add retention categories, archive/read-only access, export-before-disposal, dual approval for destruction, and audit logs.
- **Verification test:** A dry-run retention job lists eligible records without deleting; disposal requires two approvers; archived records remain readable; audit rows survive disposal.

---

## 3.7 Deployment, testing, accessibility, and documentation findings

### F-32 — The Render blueprint does not deploy Celery worker or Beat

- **Severity:** High
- **Status:** Verified
- **Location:** `render.yaml:200-211`; `Procfile`
- **Evidence:** The blueprint declares only the web service. Worker and Beat definitions are in comments, and the comment warns that without them SMS deliveries remain queued and beat-scheduled cleanup tasks do not run. Render does not use the `Procfile` worker/beat entries. Beat tasks include SMS dispatch, overdue reminders, roll-call expiry, SHS 3 phase-out, and ID export cleanup.
- **Expected vs actual:** Expected: background jobs run, or the deployment explicitly disables dependent features. Actual: the documented staging deployment silently leaves them unexecuted.
- **Impact:** SMS stays Queued; guardians and housemasters receive nothing; overdue escalations do not run; roll calls never expire; the SHS 3 phase-out does not complete. These are silent failures, not visible errors.
- **Recommended action:** Deploy worker and Beat with the same environment as the web service (requires Redis and a paid worker), or explicitly disable SMS and document manual runs for phase-out/cleanup. Add queue-depth and last-run monitoring.
- **Verification test:** In staging, a queued SMS moves to Sent; an overdue exeat produces a reminder; an expired roll call is processed; a Beat heartbeat is visible; stopping the worker raises an alert.

### F-33 — CI runs only tenancy tests; the core suite is not gated

- **Severity:** Medium
- **Status:** Verified
- **Location:** `.github/workflows/tenant-postgres-integration.yml:48-49`
- **Evidence:** The workflow provisions PostgreSQL and runs a single tenancy provisioning integration test. It does not run `core.tests`, the security/report/SMS/roll-call test modules, or the progression tests. `manage.py check` passes, but that does not exercise business logic. The full suite was not run in this audit because PostgreSQL/Redis was not provisioned locally.
- **Expected vs actual:** Expected: the full suite and security checks gate changes. Actual: one integration test gates CI.
- **Impact:** Regressions in gate, roll call, reports, and permissions can merge unnoticed. The large test inventory gives less protection than it appears to.
- **Recommended action:** Add a CI job with PostgreSQL and Redis that runs the full Django test suite, coverage, `manage.py check --deploy`, and a dependency/security scan. Keep the tenancy integration job.
- **Verification test:** CI runs all test modules on a clean database; a deliberately broken gate/roll-call test fails the build; the deploy check has no high-severity warnings.

### F-34 — Accessibility and mobile operability are partial

- **Severity:** Medium
- **Status:** Partially verified
- **Location:** `templates/base.html:6` (viewport); `templates/base.html:513-522` (44px mobile quick actions); `templates/core/operations/partials/gate_verification_card.html` (small action buttons); `templates/base.html` (17 `text-xs`/10px labels)
- **Evidence:** The viewport meta tag is present and the mobile navigation uses `min-h-11` (44px) for quick actions. Gate verification/action buttons use `px-3 py-1.5 text-xs` (roughly 30px high), and status labels use 10px text. No skip link was found. Focus styles exist on some custom controls but not consistently. No Android phone/tablet test was run.
- **Expected vs actual:** Expected: large touch targets, clear status labels, larger text, high contrast, keyboard navigation. Actual: partial responsive support with undersized gate controls and small labels.
- **Impact:** Gate staff using phones in sunlight or at a busy gate can mis-tap or misread status; keyboard and screen-reader users have a harder path.
- **Recommended action:** Raise gate action targets to at least 44×44px, increase base operational text to 14px+, use larger status badges, add a skip link and visible focus styles, verify contrast, and test on representative Android devices.
- **Verification test:** Automated accessibility scan (axe or equivalent) has no critical issues on gate, roll call, and exeat pages; a keyboard-only user can complete gate departure/return; a phone test records tap-target sizes.

### F-35 — Training and handover materials are not present

- **Severity:** Medium
- **Status:** Not implemented
- **Location:** `README.md` only; no `docs/` directory, quick-start guides, user manual, or videos
- **Evidence:** The README is a detailed technical installation/deployment/operations document. It is not a role-based user guide, and there are no short quick-start guides or demonstration videos for housemasters, gate officers, or administrators.
- **Expected vs actual:** Expected: joint Eden Sys/school training with quick starts, plain-language manual, and videos. Actual: technical README only.
- **Impact:** Training depends on the vendor walking staff through the system; handover is weak; staff may use unsafe workarounds.
- **Recommended action:** Produce one-page quick starts per role, a plain-language manual with screenshots, and short videos for gate, roll call, exeat approval, and reports. Run a joint training session and record attendance/questions.
- **Verification test:** A new gate officer can complete a departure/return using only the quick start; a housemaster can complete roll call and resolve an absence; staff feedback is recorded.

### F-36 — Production hardening and monitoring are incomplete

- **Severity:** Low
- **Status:** Partial
- **Location:** `schoolsystem/settings/production.py` (CSP report-only); no `LOGGING` configuration; no Sentry/monitoring; no dependency scan; no MFA
- **Evidence:** CSP is enforced only in report-only mode. There is no structured logging configuration, error-reporting integration, queue-depth alert, or failed-SMS alert. `pip-audit` is not installed, and no SAST/dependency scan is configured. MFA is absent (and the public site acknowledges it).
- **Expected vs actual:** Expected: enforced CSP after a report-only period, alerting, dependency scanning, and stronger admin authentication. Actual: good baseline settings, but no operational feedback loop.
- **Impact:** Security regressions and silent failures are discovered by users rather than operators.
- **Recommended action:** Run CSP report-only in staging, fix violations, then enforce. Add Sentry or equivalent, structured logs with sensitive-data redaction, uptime/queue alerts, `pip-audit`/Dependabot, and TOTP MFA for admin/superuser accounts.
- **Verification test:** CSP violations are reviewed and the policy is enforced; a failed SMS/queue backlog triggers an alert; a dependency scan runs in CI; admin login requires a second factor.

### F-37 — Rejection reason is optional

- **Severity:** Low
- **Status:** Verified
- **Location:** `core/views.py:2161-2173`
- **Evidence:** If `approval_remarks` is empty on rejection, the view only shows a warning (“no rejection reason was captured”) and proceeds. The form documentation says remarks are required for rejection, but the quick action path does not enforce it.
- **Expected vs actual:** Expected: a written reason for rejection to support the student/guardian record. Actual: optional warning.
- **Impact:** Rejected requests can lack an explanation, weakening the audit trail and communication.
- **Recommended action:** Require a reason on rejection, or use a structured reason code plus optional notes. Enforce it in the form and the view.
- **Verification test:** A rejection without a reason is rejected; a reason is stored and visible; the SMS/record includes the reason where appropriate.

### F-38 — Newly committed progression/phase-out feature is unverified

- **Severity:** Medium
- **Status:** Unverified
- **Location:** `core/services/student_progression.py`; `core/migrations/0032...`, `0033...`; `core/test_student_progression.py`; `templates/core/students/promotion.html`
- **Evidence:** The progression work was committed as `8c490e0` during the audit. Migration 0032 includes a `RunPython` backfill. The feature's tests and migrations were not executed in this environment, and the phase-out path changes student completion records.
- **Expected vs actual:** Expected: a reviewed migration and test run before release. Actual: committed feature with unexecuted migration/tests.
- **Impact:** A deployment could apply an unverified data backfill or phase-out flow to student records.
- **Recommended action:** Treat the progression work as its own release gate: review the migration, run the full suite against PostgreSQL, test phase-out and promotion on synthetic data, and confirm rollback/backup before the pilot.
- **Verification test:** Migration applies cleanly on a copy of staging; phase-out creates progression records and completion fields; promotion preserves history; the full suite passes.

### F-39 — Permission documentation diverges from enforced roles

- **Severity:** Low
- **Status:** Verified
- **Location:** `core/models.py:465-470` (`can_log_gate_movement` docstring); `core/permissions.py` role sets
- **Evidence:** The `can_log_gate_movement` docstring says “Only system administrators and assigned Gate Officers can log gate movements.” The enforced check also requires the gate feature entitlement and uses the `GATE_ROLES` tuple. The UI and enforcement are consistent, but the phrase “assigned Gate Officers” suggests a shift assignment that does not exist (see F-05).
- **Expected vs actual:** Expected: documentation matches the role model. Actual: “assigned” overstates the control.
- **Impact:** Readers may believe a roster exists when it does not.
- **Recommended action:** Correct the docstring now; implement the roster if the school requires shift control.
- **Verification test:** Documentation review; no functional test needed.

---

# 4. Requirements traceability matrix

Legend: **Implemented** = the requirement is met by the current code path; **Partial** = some layers exist but a required behaviour is missing or unverified; **Missing** = no implementation; **Defective** = implemented but demonstrably wrong; **Policy-dependent** = cannot be completed without a school decision; **Unverified** = code exists but was not executed.

## A. Exeat, departures, returns, and gate operations

| ID | Requirement | Status | Evidence and gap | Proposed test |
|---|---|---|---|---|
| A1 | Ordinary early departure requires formal rescheduling | **Missing** | No reschedule model/action; early departure blocked (`views.py:2066-2071`); admin edit only. F-03. | Reschedule creates history; early departure requires approved exception. |
| A2 | Approval roles: headmaster/admin, assistant head/senior admin, housemaster, medical staff | **Partial** | `EXEAT_APPROVAL_ROLES` = Admin, Senior HM, HM (`permissions.py:53-59`). No assistant-head or medical role. F-06, F-38. | Role matrix: each role can approve only its scope. |
| A3 | Emergency override allowed with immediate documentation | **Missing** | No override field/flow; reconciliation is retrospective. F-04. | Override requires reason; review item created. |
| A4 | Preserve reschedule history; gate prioritises current schedule | **Missing / Defective** | No history; gate search unordered and unfiltered. F-01, F-03. | Gate returns current pass; old schedules remain queryable. |
| A5 | One active exeat per student | **Missing** | No constraint or create check. F-02. | Second active pass rejected; sequential allowed. |
| A6 | Late returns recorded, notify housemaster, create incident, configurable escalation | **Partial** | Return recorded; SMS to guardian + HM; no incident; fixed cadence. F-07, F-15. | Late return creates incident; threshold changes behaviour. |
| A7 | After-hours medical emergencies approved by designated staff, followed by review | **Missing** | No medical role or after-hours review. F-06. | Medical role approves medical pass; review recorded. |
| A8 | Gate duty roster; temporary assignments with start/end | **Missing** | No roster model. F-05. | Outside-shift/expired assignment denied. |
| A9 | Gate requires internet; no offline transactions assumed | **Aligned (policy)** | Online-only implementation; no offline queue. | Connectivity-loss state is clear; no offline write path. |
| A10 | Support Android phones/tablets | **Partial / Unverified** | Responsive viewport and breakpoints; no device test. F-34. | Gate and roll call tasks on representative Android devices. |
| A11 | Large touch targets, labels, larger text, contrast, keyboard | **Partial** | Mobile nav 44px; gate buttons ~30px; 10-12px labels; no skip link. F-34. | Axe scan; keyboard-only task completion; tap-size measurement. |
| A12 | Prevent unauthorised gate/approval/record access | **Implemented, minor divergence** | Role decorators, house scoping, gate check; free-text actor and admin bypass remain. F-08, F-29, F-39. | Role matrix and IDOR tests. |

## B. Attendance, roll call, and dormitory allocation

| ID | Requirement | Status | Evidence and gap | Proposed test |
|---|---|---|---|---|
| B1 | Authorised staff may correct attendance after closure with justification and audit | **Missing** | Completed sessions blocked; no correction model. F-12. | Correction requires reason/approver; history retained. |
| B2 | Sensitive corrections require additional evidence or approval | **Missing** | No evidence/approval rule. F-12; open decision D4. | High-risk change without evidence rejected. |
| B3 | Emergency roll calls require full resolution; routine unresolved entries remain visible | **Defective** | All sessions require full resolution; expiry deletes. F-11. | Emergency cannot close unmarked; routine unresolved queue visible. |
| B4 | Escalation periods configurable | **Missing** | One-hour constant; fixed reminder cadence. F-15. | Settings change expiry/escalation. |
| B5 | Block dormitory placements over capacity unless senior approval recorded | **Partial** | Room capacity enforced; dormitory capacity not; no exception. F-13. | Over-capacity blocked; exception audited. |
| B6 | Exclude inactive students from current lists; allow historical access | **Partial** | Most lists exclude COMPLETED; house list does not. F-14. | Completed student excluded from current occupancy, visible historically. |
| B7 | Boarders on approved exeat still count as active | **Implemented** | Roll-call roster includes them as ON_EXEAT; occupancy counts them. | Exeat student remains in active boarder count. |
| B8 | Active-boarder definition = currently enrolled + boarding | **Ambiguous / Defective** | Code uses “not COMPLETED”, includes APPROVED/IN_PROGRESS; definitions differ by view. F-14; open decision D5. | Approved definition matches every report. |
| B9 | Preserve historical records subject to retention policy | **Partial** | Records retained, but expiry deletes sessions; no retention policy. F-11, F-31. | Expired session retained; retention dry-run. |

## C. Guardian communication and SMS

| ID | Requirement | Status | Evidence and gap | Proposed test |
|---|---|---|---|---|
| C1 | Staff create exeats; guardians do not submit directly | **Implemented** | Exeat creation is role-gated to staff; no guardian submission path. | Guardian cannot create; staff can. |
| C2 | SMS and telephone calls; SMS replies handled elsewhere | **Partial** | SMS implemented; no call log; replies are outside the system by design. | Call contact can be recorded (if school requires). |
| C3 | Approved SMS to selected groups | **Missing** | Event-only guardian/HM messages; no composer. F-18. | Group message preview/approval/audit. |
| C4 | Eden Sys maintains sender ID; ID not yet decided | **Partial / Policy-dependent** | Env setting exists but is ignored; provisioning seeds school name. F-16; decision D6. | Approved sender used and validated. |
| C5 | SMS expenditure approval by headmaster/bursar/ICT within budget | **Missing** | Quota exists; no approval/budget workflow. | Budget request/approval before bulk send. |
| C6 | Low-credit and failed-message alerts | **Missing** | Final failure logs only; no balance check. F-18. | Failure/low-balance alert. |
| C7 | Audit status, failures, retries without unnecessary content exposure | **Partial** | `SMSDelivery` stores status and content; retries tracked; no separate access audit. | Audit report without guardian content in logs. |
| C8 | Provider credentials, API security, rate limits, error handling, recipient validation, cost controls | **Partial** | Env key, timeout, retries, phone validation, quota; no delivery receipts/rate-per-recipient/low-credit. F-16, F-17, F-18. | Credential rotation; invalid recipient; provider timeout; cost cap. |

## D. Reports and operational visibility

| ID | Requirement | Status | Evidence and gap | Proposed test |
|---|---|---|---|---|
| D1 | Students currently outside school | **Implemented** | Gate dashboard active-out list. | Count matches open movements. |
| D2 | Overdue exeats and outstanding returns | **Implemented, range defect** | Overdue register exists; date filter can hide current cases. F-22. | Overdue-now independent of period. |
| D3 | Departures and returns over a selected period | **Implemented** | Exeat/gate compliance report. | Period totals reconcile to movement rows. |
| D4 | Medical and emergency movements | **Partial** | Welfare report and exeat analytics; no dedicated emergency filter. | Medical/emergency report filters correctly. |
| D5 | Attendance and movement incidents | **Implemented** | Attendance, gate conflicts, movement concerns. | Incident totals match source records. |
| D6 | Downloadable PDF and spreadsheet reports | **Partial** | Browser print; KPI CSV only. F-25. | Every report exports CSV/XLSX; PDF consistent. |
| D7 | Dashboard summaries | **Implemented** | Admin and gate dashboards. | Dashboard totals match reports. |
| D8 | Selected scheduled reports by email | **Missing** | No report-email task. F-25. | Daily/weekly email delivered to authorised recipients. |
| D9 | Daily operational summary and weekly report cadence | **Missing** | No scheduler/report subscription. | Schedule runs and is audited. |
| D10 | Report permissions, filters, dates, totals, exports correct and tested | **Partial / Defective** | Report roles and filters exist; F-20–F-24 show calculation defects. | Report-by-report reconciliation suite. |

## E. Recovery, backups, and continuity

| ID | Requirement | Status | Evidence and gap | Proposed test |
|---|---|---|---|---|
| E1 | Target recovery within 24 hours | **Unverified** | No drill evidence; manual local backups. F-26, F-27. | Timed restore drill. |
| E2 | No data loss where technically achievable; document RPO | **Unverified** | No documented RPO; Neon PITR not evidenced. | RPO stated and measured. |
| E3 | Backups in cloud and a separate location | **Missing** | Local output only. F-26. | Offsite object exists and restores. |
| E4 | Eden Sys controls encryption keys under documented controls | **Missing** | Archives are unencrypted; no key custody. F-26. | Encrypted archive; key access audited. |
| E5 | School and Eden Sys can restore, subject to permissions/audit | **Partial** | CLI restore is audited; no role-based app workflow; live restore refused. F-27. | Authorised operator restores; audit row. |
| E6 | Test restoration quarterly and document results | **Missing** | No schedule or evidence. | Quarterly drill record. |
| E7 | Encryption, access, retention, monitoring, procedures, deletion protection | **Partial** | Checksums and audit; no encryption/retention/offsite/monitoring. F-26. | Control checklist and evidence. |
| E8 | Verify the 24-hour target against actual architecture | **Unverified** | No measurement; Render/Neon plan not validated. F-26, F-27. | Drill measures actual RTO. |

## F. Remote support

| ID | Requirement | Status | Evidence and gap | Proposed test |
|---|---|---|---|---|
| F1 | Supervised, time-limited support | **Missing** | CLI token only; no session. F-28. | Session expires; supervisor sees actions. |
| F2 | Headmaster and ICT administrator approval per session | **Missing** | No approval workflow. | Unapproved session denied. |
| F3 | Record requester, approvers, personnel, times, scope, actions | **Partial** | CLI audit has operator/reason; no approvers/scope/actions. | Session record is complete. |
| F4 | Least privilege, revocable, securely authenticated, logged | **Partial** | bcrypt token, TTL, revoke; no application path/scope. | Token scope and revocation tested. |
| F5 | No hidden support accounts or unrestricted vendor access | **Verified** | No hidden accounts found. | Access review of all staff/superusers. |

## G. Access control, privacy, and record governance

| ID | Requirement | Status | Evidence and gap | Proposed test |
|---|---|---|---|---|
| G1 | Role-based access and least privilege | **Implemented, role gaps** | Role decorators and house scoping; no medical/assistant-head role. F-06. | Role matrix across all views. |
| G2 | Restrict sensitive student/medical/guardian/attendance/movement records | **Implemented, minor exposure** | Role gates and house scoping; gate sees destination/type; free-text actor. F-08. | IDOR and field-level access tests. |
| G3 | Exports only to authorised users; log bulk exports | **Partial** | Admin ID ZIP, report-role CSV; no export audit. F-30. | Export audit row and denial test. |
| G4 | Audit approvals, corrections, overrides, admin changes, sensitive actions | **Partial** | Config audit and model fields; no correction/override log; admin bypass. F-08, F-29, F-30. | Immutable audit for each action. |
| G5 | Formal dispute review for exeats, gate records, attendance | **Missing** | No dispute model/UI. F-30. | Open/review/decide/link correction. |
| G6 | School confirms data ownership, access, retention | **Policy-dependent** | No written schedule; school/vendor roles undocumented. Decision D1/D8. | Signed data schedule. |
| G7 | Apply retention periods and secure disposal after policy | **Missing** | No retention/disposal code. F-31. | Dry-run, dual approval, audit. |

## H. Training and handover

| ID | Requirement | Status | Evidence and gap | Proposed test |
|---|---|---|---|---|
| H1 | Eden Sys and school ICT jointly support training | **Missing** | No training plan/record. F-35. | Training session and attendance record. |
| H2 | Short quick-start guides | **Missing** | No role-based guides. F-35. | New user completes task from guide. |
| H3 | Plain-language user manual | **Partial** | Technical README only. F-35. | Manual reviewed by non-technical staff. |
| H4 | Short demonstration videos | **Missing** | None. F-35. | Video covers gate/roll call/exeat. |
| H5 | Documentation covers install, config, roles, operations, backup, restore, troubleshooting, security, deployment, maintenance | **Partial** | Strong technical coverage; gaps in role guides, security ops, and troubleshooting. F-35, F-36. | Documentation checklist review. |

---

# 5. Security and production-readiness assessment

## 5.1 Confirmed vulnerabilities and integrity defects

These are confirmed from the code path. “Vulnerability” is used only where an actor can exploit the behaviour; otherwise the item is an integrity or safety defect.

| ID | Issue | Type | Why it matters |
|---|---|---|---|
| F-01 | Gate lookup returns wrong pass | Safety / integrity | A valid student can be denied movement; wrong record can be actioned. |
| F-02 | Multiple active exeats | Integrity / policy bypass | Conflicting passes and duplicate notifications. |
| F-03 | Admin-only reschedule overwrites history | Audit / integrity | No answer to “who changed the schedule and why.” |
| F-07 | Late return creates no incident | Safeguarding / records | No investigation trail or escalation. |
| F-08 | Free-text gate actor | Repudiation | Cannot reliably attribute a movement. |
| F-09 | Duplicate roll calls | Integrity | Split/double attendance. |
| F-10 | Completed roll call mutation | Authorization bypass / integrity | Historical attendance can change silently. |
| F-11 | Destructive roll-call expiry | Data loss | Attendance evidence deleted. |
| F-13 | Dorm overcapacity | Safety / integrity | Overcrowding; inaccurate occupancy. |
| F-16 | SMS sender misconfiguration | Delivery / compliance | Messages may be sent under an unapproved sender or fail branding/whitelist review. |
| F-26 | Plaintext local backups | Data protection | Sensitive data at rest; loss on redeploy. |
| F-27 | No live restore path | Availability | Documented recovery procedure fails. |
| F-28 | Support token unwired | Access-control claim | No supervised vendor access control in the app. |
| F-29 | Admin bypass | Insider integrity | Direct edits without reason/audit. |
| F-32 | Worker/Beat absent | Availability / silent failure | SMS and scheduled jobs do not run. |

## 5.2 Security controls verified as present

These are strengths and should be preserved:

- **Production fail-fast settings:** `DJANGO_SECRET_KEY` length/entropy check, `DEBUG=False`, explicit `ALLOWED_HOSTS` outside tenancy, `SECURE_SSL_REDIRECT`, secure session/CSRF cookies, HSTS with validation, `SECURE_CONTENT_TYPE_NOSNIFF`, `SECURE_REFERRER_POLICY`, and `SECURE_PROXY_SSL_HEADER`.
- **Tenant host validation:** `VerifiedDomainRoutingMiddleware` validates hosts against the `Domain` table; `ALLOWED_HOSTS=["*"]` in tenancy mode is intentional and gated by that middleware.
- **CSRF:** Django CSRF middleware (or the dynamic tenant-aware replacement), with trusted origins from the environment. No `@csrf_exempt` was found in the audited core paths.
- **Authorization:** Role decorators, house-scoped querysets (`filter_queryset_by_house`), gate-specific checks, and admin-only ID exports. `download_student_id_export_view` filters by `requested_by`.
- **IDOR resistance:** Student records, exeats, roll calls, follow-ups, and photos use scoped querysets. The gate role is intentionally school-wide.
- **Private media:** Student photos are served through authenticated views or an expiring public token; `X-Content-Type-Options` and `X-Robots-Tag` are set.
- **Upload validation:** Student photo type/size validators; roster import accepts only CSV/XLSX and caps at 10 MB.
- **No obvious XSS sink:** No `|safe`, `mark_safe`, or autoescape-disabling usage was found in the audited templates/core app. Django autoescaping is the default.
- **No obvious SQL injection sink:** Core views use the ORM; no user-controlled raw SQL was found. Tenancy schema operations use controlled cursors and quoting.
- **Safe redirect:** The login flow uses `url_has_allowed_host_and_scheme` for the `next`/referer redirect.
- **Rate limiting:** Login, public report, and demo-request throttles exist; a shared cache is needed for multi-worker correctness.
- **Session hygiene:** 30-minute session age, refresh on activity, expire at browser close.
- **Audit:** `ConfigurationAuditLog` for role, housemaster, term, school identity, and SMS changes; `PlatformAuditLog` for provisioning, backup, restore, and support-token actions.
- **Health check:** `/healthz` exists and is exempted from tenant resolution.

## 5.3 Recommendations (not confirmed vulnerabilities)

1. **MFA for admin and superuser accounts.** TOTP is sufficient; enforce it before production. The public site already discloses its absence.
2. **Enforce CSP after a report-only period.** The nonce-based policy is well-formed; collect reports in staging, fix violations, then switch from `Content-Security-Policy-Report-Only` to the enforcing header.
3. **Add structured logging and error reporting.** Configure `LOGGING` with redaction for phone numbers, message bodies, tokens, and passwords. Add Sentry or an equivalent, plus uptime and queue-depth alerts.
4. **Run dependency and static analysis in CI.** `pip-audit`, `bandit`/Semgrep, and Dependabot/Renovate. `pip-audit` is not installed locally; no vulnerability scan was performed in this audit.
5. **Use a shared cache for rate limits.** `LocMemCache` is per-process; configure Redis so limits hold across Gunicorn workers.
6. **Restrict Django admin.** Limit admin access to named platform administrators; add readonly fields and audited correction paths for exeat/movement.
7. **Protect backups.** Encrypt, offsite, version, object-lock, and alert on failure; keep keys separate from archives.
8. **Review superuser and staff accounts.** Remove unused accounts; require named accounts; prohibit shared logins; review quarterly.
9. **Add a data-processing/ownership schedule.** Define school ownership, vendor access, retention, and deletion in writing (see D1/D8).
10. **Test the production security settings.** The existing production-settings tests are a good start; add a deployment check in CI.

## 5.4 Policy questions separated from technical defects

The following cannot be fixed by code alone and must not be silently decided:

- Which roles may authorise early departure, emergency override, and after-hours medical movement, and with what limits?
- What is the escalation sequence and timing for late returns and unresolved roll calls?
- Which attendance corrections need evidence or a second approver?
- Who may approve dormitory overcapacity, and what justification is required?
- What is the approved SMS sender ID, recipient groups, templates, budget, and approval chain?
- What retention schedule applies to student, medical, disciplinary, attendance, gate, audit, SMS, and backup records?
- What RPO/RTO is realistic and contractually promised?
- Who controls encryption keys, who can restore, and how is remote support approved and revoked?

---

# 6. Prioritized improvement roadmap

Effort is expressed as engineering effort, not calendar time: **S** (small, local change and tests), **M** (multi-file change, migration, or workflow), **L** (schema/workflow/operational change with rollout).

## 6.1 P0 — Pilot blockers (fix before real student data or SMS)

| Order | Item | Findings | Effort | Dependency |
|---:|---|---|---|---|
| 1 | Add failing regression tests for gate lookup, duplicate roll call, closed-session mutation | F-01, F-09, F-10 | S | Test DB with PostgreSQL |
| 2 | Gate lookup filtering, ranking, and disambiguation | F-01 | S | Test 1 |
| 3 | Duplicate roll-call guard and unique constraint | F-09 | S–M | Test 1; migration review |
| 4 | Block follow-up mutation of completed/expired sessions | F-10 | S | Test 1 |
| 5 | Resolve and validate SMS sender ID; stop deriving it from the school name | F-16 | S | School confirms sender ID |
| 6 | Deploy worker + Beat or explicitly disable dependent jobs | F-32 | M | Redis + Render plan decision |
| 7 | Correct README restore/support claims or implement the claims | F-27, F-28 | S (docs) / L (implementation) | School/vendor decision |
| 8 | Add a tested restore runbook and one timed drill | F-26, F-27 | M | Staging + backup target |

## 6.2 P1 — Operational correctness and governance (before scale-up)

| Item | Findings | Effort | Dependency |
|---|---|---|---|
| One-active-exeat rule (app + DB constraint) | F-02 | M | Policy definition of “active” |
| Reschedule/early-departure workflow with history | F-03 | M | D2 decision |
| Emergency override with review | F-04 | M | D2 decision |
| Late-return incident and configurable escalation | F-07, F-15 | M | D3 decision |
| Gate-duty roster and temporary assignments | F-05 | M | D2/D8 decision |
| Medical/infirmary authorization | F-06 | M | D2 decision |
| Dormitory capacity + senior exception | F-13 | M | D5 decision |
| Post-closure attendance correction with audit | F-12 | M | D4 decision |
| Preserve expired roll calls / unresolved queue | F-11 | M | D4 decision |
| Active-boarder definition consistency | F-14 | S–M | D5 decision |
| Audit log for exports, corrections, overrides, admin edits | F-08, F-29, F-30 | M | Audit schema |
| Dispute-review workflow | F-30 | M | D8 decision |
| Report calculation fixes (turnaround, staff activity, overdue, conduct, welfare) | F-20–F-24 | S–M | None |
| PDF/XLSX exports and scheduled email reports | F-25 | M | Report infrastructure |
| Full-suite CI with PostgreSQL/Redis | F-33 | M | CI budget |
| Accessibility fixes and Android device test | F-34 | S–M | Device access |
| Retention/disposal policy and implementation | F-31 | L | D1 decision |

## 6.3 P2 — Hardening, usability, and longer-term improvements

| Item | Findings | Effort | Dependency |
|---|---|---|---|
| SMS group messaging, templates, cost approval | F-18 | M | D6 decision |
| Low-credit/failure alerts | F-18 | S–M | Provider API |
| Encrypted offsite backups with retention | F-26 | M–L | Key custody, cloud budget |
| Monitoring/alerting and enforced CSP | F-36 | M | Ops access |
| MFA for admins | F-36 | S | User enrolment |
| Role model: headmaster, assistant head, medical, bursar/finance | A2, F-06 | M | D2/D8 |
| Training materials and joint training | F-35 | M | School staff time |
| Admin correction controls | F-29 | S–M | Audit schema |
| Rejection reason required | F-37 | S | None |
| Permission documentation cleanup | F-39 | S | None |

## 6.4 Dependencies and sequencing

1. **Policy first for schema-changing work.** One-active-exeat, reschedule, dormitory exception, late-return incident, correction approval, and retention all need school decisions before migration design.
2. **Test environment first for verification.** Provision PostgreSQL and Redis in staging; run the full suite and the new regression tests before claiming a fix.
3. **Worker/Beat before SMS pilot.** Otherwise the SMS fixes cannot be observed end to end.
4. **Backup/restore before real data.** A pilot with real guardians and medical notes requires a tested offsite restore.
5. **Training before gate rollout.** Gate staff need the quick start and a supervised drill; the system’s current touch targets and labels should be fixed first.

---

# 7. Test plan

## 7.1 Test levels and environment

| Level | Scope | Environment |
|---|---|---|
| Unit | Model validation, report calculations, SMS payload/sender, permission helpers, roll-call service | Local/staging PostgreSQL |
| Integration | UI → view → service → model, gate/exeat/roll-call workflows, tenant scoping | Staging PostgreSQL + Redis |
| Authorization | Role × object × action matrix, house scoping, IDOR | Staging with seeded users |
| Concurrency | Duplicate roll calls, overlapping exeats, room/dorm capacity, SMS quota | PostgreSQL with transactions |
| Accessibility | Gate, roll call, exeat, reports | Browser + Android device/emulator |
| Recovery | Backup, offsite restore, live-tenant switch, timed drill | Staging copy, no production data |
| Deployment | Migrations, worker/Beat, health check, static/media, security headers | Render staging + Neon |
| Regression | Full suite in CI on every change | CI PostgreSQL + Redis |

**Test data:** synthetic students, guardians, staff, and phone numbers only. Never use real guardian numbers or production data. SMS tests must mock the HTTP client; no real SMS is sent.

## 7.2 Required test cases

| ID | Test | Expected result | Covers |
|---|---|---|---|
| T-01 | Gate search with old, current, future passes | Current Approved pass selected; disambiguation shown if multiple | F-01 |
| T-02 | Gate search by exact pass number vs partial number | Exact match wins; partial matches are listed, not auto-selected | F-01 |
| T-03 | Create second overlapping active exeat | Rejected; sequential pass allowed | F-02 |
| T-04 | Reschedule an Approved pass | History row with actor, reason, old/new times; gate shows current | F-03 |
| T-05 | Emergency override without reason | Rejected; with reason creates review item | F-04 |
| T-06 | Gate action outside duty roster / expired temporary assignment | Denied; valid assignment allowed | F-05 |
| T-07 | Medical staff approve medical exeat; ordinary pass denied | Scoped permission works | F-06 |
| T-08 | Return after expected time | One incident, HM notification, no duplicate on repeat save | F-07 |
| T-09 | Departure/return actor | User FK recorded; display name preserved | F-08 |
| T-10 | Two sequential/concurrent roll-call creates | One in-progress session | F-09 |
| T-11 | Resolve follow-up on completed/expired session | Rejected; no entry mutation | F-10 |
| T-12 | Expire unfinished session | Status retained; entries queryable; unresolved queue visible | F-11 |
| T-13 | Post-closure correction request and approval | Original + corrected values; audit; unauthorised denied | F-12 |
| T-14 | Dormitory capacity placement and exception | Blocked without exception; allowed and audited with approval | F-13 |
| T-15 | Completed student in house list and housing report | Both exclude; registry history retains | F-14 |
| T-16 | Change roll-call timeout and late-return threshold | Behaviour follows setting; change audited | F-15 |
| T-17 | SMS sender resolution and validation | Approved sender used; invalid value normalized or rejected; key not logged | F-16 |
| T-18 | Stale queue row and long message | Stale row released; segments counted | F-17 |
| T-19 | Failed SMS / low balance | Admin alert generated once | F-18 |
| T-20 | SMS report reconciliation | Tenant-wide gauge uses quota; filtered deliveries are labelled | F-17 |
| T-21 | Approval turnaround after later edit | Decision time unchanged; cancellations separated | F-20 |
| T-22 | Staff activity with admin/tutor actions | All actors appear; decision date used | F-21 |
| T-23 | Overdue-now vs period filter | Currently overdue student remains visible | F-22 |
| T-24 | Conduct rate with day/completed student incidents | Same population; rate unchanged | F-23 |
| T-25 | Welfare report with completed student | Excluded by default; included on history toggle | F-24 |
| T-26 | Every report CSV/XLSX and scheduled email | Totals match; recipient authorised; audit row | F-25 |
| T-27 | Backup encryption/offsite/retention | Encrypted object offsite; restore works; expired archives deleted | F-26 |
| T-28 | Restore live tenant via runbook | Spare schema restore, validation, switch, rollback | F-27 |
| T-29 | Support access claim | Either no claim or approved/expiring/logged session | F-28 |
| T-30 | Admin edit of exeat/movement | Audit row with actor, reason, old/new; invalid transition rejected | F-29 |
| T-31 | Export and dispute audit | Export logged; dispute decision linked to correction | F-30 |
| T-32 | Retention dry-run and disposal | No deletion in dry-run; dual approval for disposal; audit | F-31 |
| T-33 | Worker/Beat staging test | SMS drains; reminders run; expiry runs; heartbeat visible | F-32 |
| T-34 | Full-suite CI and deploy check | All tests pass; no high-severity deploy warnings | F-33 |
| T-35 | Accessibility and Android tasks | Axe criticals zero; 44px targets; keyboard-only completion | F-34 |
| T-36 | Training quick start usability | New staff complete tasks without vendor help | F-35 |
| T-37 | CSP/monitoring/MFA | Enforced header; alert on failure; second factor required | F-36 |
| T-38 | Rejection without reason | Rejected by form/view | F-37 |
| T-39 | Progression migration and phase-out | Clean migration; history preserved; full suite passes | F-38 |

## 7.3 Authorization matrix tests

For every role (Admin, Senior Housemaster, Housemaster, House Tutor, Gate Officer, unauthenticated), test:

- View, create, edit, approve, reject, cancel, delete on `Exeat`.
- Departure/return logging and pass search.
- Roll-call create, mark, complete, correct, resolve follow-up.
- Student record, photo, ID card, export.
- Reports and exports.
- SMS configuration and send.
- Platform commands (not exposed via HTTP).

Assertions: allowed actions succeed; denied actions return 403/redirect without leaking existence; house-scoped users cannot access another house's object by ID; a gate officer cannot approve an exeat; a tutor cannot view a student record outside their permitted scope.

## 7.4 Concurrency tests

- Two simultaneous roll-call creates for the same house/dorm/date/type.
- Two simultaneous exeat approvals for the same student.
- Two simultaneous room/dorm placements at the capacity boundary.
- Two simultaneous SMS sends that would exceed the monthly quota.
- Backup running while a restore/dry-run is attempted (lock/refusal).

## 7.5 Recovery and deployment drills

1. Take a backup of a staging tenant; encrypt and upload offsite.
2. Restore into a spare schema; run row-count and checksum validation.
3. Switch a staging tenant to the restored schema; verify login, gate, roll call, and reports.
4. Measure elapsed time and data loss window; record against RTO/RPO.
5. Repeat quarterly; keep the signed drill record.
6. Deploy to Render staging with worker/Beat; verify `/healthz`, SMS queue drain, overdue reminder, roll-call expiry, and static/media.
7. Run `manage.py check --deploy` and the full suite against the staging database.

## 7.6 Test execution status

| Check | Result |
|---|---|
| `python manage.py check --settings=schoolsystem.settings.development` | Passed: no issues |
| Full Django test suite | Not run: PostgreSQL/Redis not provisioned locally |
| Live SMS | Not run: no real messages authorised |
| Live backup/restore | Not run: no staging backup target |
| Android device test | Not run: no device access |
| Dependency vulnerability scan | Not run: `pip-audit` not installed |

---

# 8. Open decisions for school management

Each decision must be recorded in writing with the approving role. Until then, the related feature must not be treated as approved policy.

## D1. Historical retention vs secure disposal

- **Conflict:** One proposal says archive historical student records indefinitely; another says adopt approved retention periods and secure disposal. These cannot both be the default.
- **Implications:** Indefinite retention increases privacy, storage, and breach-impact risk. Aggressive disposal can destroy records needed for transcripts, disputes, or legal requests. Backups complicate deletion because old copies persist.
- **Decision needed:** A written retention schedule by record class (student registry, medical, disciplinary, attendance, gate, exeat, audit, SMS metadata, backups), with retention periods, archive access rules, legal/privacy review, and disposal method. Address backup expiry and legal holds.
- **Responsible:** Headmaster and school management, with legal/privacy advice; Eden Sys implements.
- **Recommendation:** Do not dispose of anything until the schedule is approved. Start with a conservative schedule and a read-only archive.

## D2. Emergency authorization

- **Decision needed:** Which roles may authorise emergency departures and after-hours medical movements? What are their limits? Who reviews afterward, and by when?
- **Options:** (a) Headmaster only; (b) Headmaster, assistant head, and delegated senior housemaster; (c) add medical/infirmary lead for medical cases; (d) any admin with post-hoc review.
- **Implications:** Broader authority is faster but increases misuse risk; narrower authority may delay urgent medical care.
- **Responsible:** Headmaster, with input from the Senior Housemaster and medical staff.
- **Recommendation:** Named roles with scope limits, a required reason, immediate SMS/record, and review within 24 hours. Add a medical-only permission rather than granting full admin rights.

## D3. Late-return escalation

- **Decision needed:** Who is notified, in what order, at what thresholds, and what counts as an incident requiring investigation?
- **Options:** Guardian + housemaster at expected return; Senior Housemaster after 2 hours; Headmaster after 6–24 hours; police/guardian escalation per school policy.
- **Implications:** Too many alerts cause fatigue; too few leave a student unaccounted for. Escalation must work without internet at the gate? (The system is online-only, so the process must define a phone fallback.)
- **Responsible:** Headmaster and Senior Housemaster.
- **Recommendation:** Start with guardian + housemaster at return time, Senior Housemaster at 2 hours, Headmaster at 6 hours, and a written incident at the first threshold. Make thresholds configurable.

## D4. Sensitive attendance corrections

- **Decision needed:** Which changes require evidence, a second approval, or escalation? Examples: ABSENT↔PRESENT after closure, changing ON_EXEAT to ABSENT, correcting a conduct-linked entry.
- **Options:** All post-closure changes need a reason; status reversals need a second approver; only the Senior Housemaster can correct a completed session.
- **Implications:** Strong controls protect records but slow genuine fixes; weak controls invite tampering.
- **Responsible:** Headmaster and Senior Housemaster.
- **Recommendation:** Require a reason for every post-closure change; require evidence + Senior Housemaster approval for ABSENT↔PRESENT and conduct-linked changes; keep an append-only correction history.

## D5. Dormitory overcapacity

- **Decision needed:** Which senior role may approve an exception, for how long, and what evidence/justification is required?
- **Options:** No exceptions; Senior Housemaster; Headmaster; a named crisis team.
- **Implications:** Strict blocking can leave a student without a bed during an emergency; exceptions can become routine overcrowding.
- **Responsible:** Headmaster and Senior Housemaster, with health/safety input.
- **Recommendation:** Block by default; allow a time-bound exception approved by the Headmaster or Senior Housemaster with a written reason and review date; report all exceptions weekly.

## D6. SMS configuration and budget

- **Decision needed:** Approved sender ID, recipient groups, message templates, monthly budget, approval chain, and opt-out/reply handling.
- **Implications:** An unapproved sender ID may be rejected or spoofed; uncontrolled group messaging can exhaust the budget; message content may include sensitive information.
- **Responsible:** Headmaster, bursar/finance officer, ICT administrator, and Eden Sys for sender-ID maintenance.
- **Recommendation:** For the pilot, restrict SMS to guardian/housemaster exeat and late-return events, use a short approved sender ID, set a monthly cap, and require headmaster/bursar approval for group sends. Do not send medical details in SMS.

## D7. Recovery expectations

- **Decision needed:** Confirm the 24-hour RTO, the achievable RPO, who may request a restore, and what downtime is acceptable.
- **Implications:** A 24-hour RTO with manual local backups is not credible. Neon point-in-time recovery may provide a low RPO, but it must be tested and included in the contract.
- **Responsible:** Eden Sys and Headmaster/ICT administrator.
- **Recommendation:** Document the actual RPO/RTO by drill; do not promise 24 hours until a timed restore succeeds. Aim for encrypted offsite backups and a spare-schema restore with a tested switch.

## D8. School/vendor responsibilities and remote support

- **Decision needed:** Who owns the data, who holds encryption keys, who can restore, how remote support is approved and revoked, and what audit records are shared.
- **Implications:** Unclear ownership creates privacy and accountability gaps; unsupervised vendor access is unacceptable for student data.
- **Responsible:** Headmaster, ICT administrator, and Eden Sys management.
- **Recommendation:** A written data-processing/operations schedule: school owns the data; Eden Sys holds keys under split custody; restores require school authorisation; support sessions require headmaster + ICT approval, are time-limited, scoped, logged, and revocable; no shared or hidden accounts.

## Related role-model decision

Confirm how the school maps real roles to the system:

- Is the Headmaster an `ADMIN`, or does the school need a separate Headmaster role?
- Who is the “assistant headmaster/designated senior administrator”?
- Which medical/infirmary staff need access, and to what?
- Who is the bursar/finance approver for SMS spend?
- Are house tutors expected to view student records, or only conduct roll call and create exeats?

The current role set cannot express all of these without granting broader access than least privilege allows.

---

# 9. Approval checkpoint

## 9.1 Statement of no changes

No source code, model, migration, template, setting, database record, or external service was changed during the audit phase. The only file written during the audit was this report. `manage.py check` was run read-only and passed. No SMS or email was sent. No production data or credentials were accessed.

> **Post-approval update (20 Oct 2026):** Batch 1 was implemented after the approval checkpoint. The statement above describes the audit phase only. See `BATCH1_IMPLEMENTATION.md` for the change log, verification evidence, and residual limitations.

## 9.2 Proposed first safe implementation batch

I propose **Batch 1** as a small, test-first, low-risk change set that does not require a school policy decision and does not change the database schema:

1. **Add regression tests only** for F-01, F-09, and F-10. Run them against a provisioned PostgreSQL test database and confirm they fail for the documented reasons.
2. **Fix the gate lookup** (F-01): filter actionable statuses, rank exact matches, show a disambiguation list, and add a clear “no current pass” state. No schema change.
3. **Add the duplicate roll-call guard** (F-09): transactional check and a friendly conflict response. The database unique constraint is deferred to Batch 2 because it needs a migration and a review of any existing duplicates.
4. **Block follow-up mutation of completed/expired sessions** (F-10): status check and a clear error; no silent `.update()` on closed records.
5. **Fix SMS sender resolution** (F-16): use the approved tenant sender, validate it, and log the resolved sender without the API key. Unit tests mock the HTTP call; no live SMS.
6. **Add the core suite to CI** (F-33): PostgreSQL + Redis service, full test run, `manage.py check --deploy`.
7. **Correct the README** for restore and support access (F-27, F-28), or mark those sections as “not yet available” until the workflows are implemented. This is documentation-only and does not change behaviour.

**Why this batch is safe:** It changes no schema, no student data, no SMS behaviour in production (tests only), and no policy. It fixes the highest-risk gate/attendance defects and makes the test gap visible. Each item has a failing test first.

**Explicitly excluded from Batch 1:** one-active-exeat constraint, reschedule/emergency/medical workflows, late-return incidents, dormitory exceptions, correction approval, audit/dispute schema, retention, group SMS, scheduled reports, backup encryption/offsite, and support-session implementation. These need decisions D1–D8 and larger migrations.

## 9.3 Prerequisites for Batch 1

- A staging PostgreSQL database and Redis instance (not production).
- Synthetic fixtures for gate, roll call, and SMS tests.
- Agreement that no real SMS will be sent; the provider call is mocked.
- A short confirmation that the proposed sender ID is the one Eden Sys will maintain (or that tests should use a placeholder).

## 9.4 Questions for approval

1. Do you approve Batch 1 as scoped above?
2. Should the README restore/support claims be corrected now, or after the school chooses a support model?
3. Who will answer D1–D8, and by when? In particular, I need the “active exeat” definition (D2), late-return escalation (D3), and attendance-correction rules (D4) before Batch 2.
4. Is the pilot expected to send real SMS, or should SMS remain disabled until the sender ID and worker/Beat are confirmed?
5. Will the pilot use real student/guardian data? If yes, the backup/restore drill (F-26/F-27) must precede it.

I will not modify source code until this checkpoint is approved.

---

# Appendix A — Verification log

## A.1 Inspected

- `core/models.py`, `core/views.py`, `core/forms.py`, `core/admin.py`, `core/permissions.py`, `core/tasks.py`, `core/urls.py`.
- `core/services/reports.py`, `core/services/roll_call.py`, `core/services/sms.py`, `core/services/student_import.py`, `core/services/student_progression.py`.
- `tenancy/models.py`, `tenancy/entitlements.py`, `tenancy/middleware.py`, `tenancy/management/commands/tenant_backup.py`, `tenant_restore.py`, `support_access.py`, `provision_school.py`.
- `schoolsystem/settings/base.py`, `development.py`, `production.py`; `schoolsystem/urls.py`.
- `templates/base.html`, gate verification/terminal templates, report templates, student registry/record templates.
- `.github/workflows/tenant-postgres-integration.yml`, `render.yaml`, `Procfile`, `requirements.txt`, `README.md`.
- Test modules in `core/` and `tenancy/` (inventory and targeted reads; Batch 1 modules executed as recorded in A.2).

## A.2 Executed

| Command | Result |
|---|---|
| `python manage.py check --settings=schoolsystem.settings.development` | Passed: no issues |
| `git status --short` | Only the audit report modified at audit baseline; `HEAD 8c490e0` |
| `pip-audit --version` | Not installed; no dependency scan run |
| `python manage.py test core.test_audit_batch1 --settings=schoolsystem.settings.test --noinput --keepdb` | 16 Batch 1 tests passed in 56.319 s on a disposable local PostgreSQL 18.3 cluster; SMS HTTP mocked |
| `python manage.py test core.tests.HouseScopedAccessTests.test_housemaster_reports_and_printable_output_stay_in_assigned_house --settings=schoolsystem.settings.test --noinput --keepdb` | 1 test passed in 5.311 s after the duplicate-session test fixture was corrected |
| Clean `python manage.py test core --settings=schoolsystem.settings.test --noinput --keepdb` with `.env` hidden and a recreated local test database | **323 tests passed** in 739.023 s; `OK` |
| `python manage.py check --settings=schoolsystem.settings.test` | Passed: no issues |
| `python manage.py check --deploy --settings=schoolsystem.settings.production --fail-level ERROR` with dummy Neon-style environment variables | Exit 0; only `security.W005` and `security.W021` warnings |
| `python -m compileall -q` on changed Python files | Passed |
| `git diff --check` | No whitespace errors |

## A.3 Not executed

- Full multi-tenant `tenancy` suite locally (the existing CI job covers it; Redis was not available locally).
- Live SMS or email.
- Live backup/restore or offsite upload.
- Android/phone accessibility testing.
- Load or penetration testing beyond the targeted roll-call concurrency test.
- `manage.py check --deploy` with real production secrets or against the real deployment environment (a dummy-value run is recorded in A.2).

## A.4 Unverified items

- Android rendering and touch behaviour on the school's actual devices.
- Real SMSOnlineGH sender-ID acceptance, delivery receipts, and balance API behaviour.
- Neon point-in-time recovery window and actual restore time.
- Render free/paid plan behaviour for workers, disk, and sleep.
- Whether the newly committed progression feature and migrations 0032/0033 are correct.
- Whether existing production data contains duplicate active exeats, duplicate roll calls, or stale queued SMS rows.

---

# Appendix B — Document history

| Version | Date | Change |
|---|---|---|
| 1.0 | 19 Oct 2026 | Initial audit report (earlier structure). |
| 2.0 | 19 Oct 2026 | Restructured to the current brief; added full A–H traceability, expanded gate/roll-call/SMS/recovery findings, and the approval checkpoint. |
| 2.1 | 20 Oct 2026 | Added the post-approval Batch 1 implementation note and verification evidence; see `BATCH1_IMPLEMENTATION.md`. |

---

*End of report.*
