# SHS Boarding & Dormitory Management System — Independent Audit Report

**Prepared for:** Product owner / school leadership (pilot: Atebubu Senior High School)
**Audit date:** 19 October 2026
**Baseline audited:** commit `24afc5c` **plus uncommitted working-tree changes** (15 modified files, 5 untracked files — see Appendix D). All findings refer to this working tree, not to `HEAD`.
**Auditor scope:** static code, template, schema, and test inspection with targeted logic tracing. No production systems were touched; no SMS/email was sent; no migrations were run; no data was modified.
**Report status:** For review and approval. No fixes have been implemented.

---

## 0. Method, Evidence Rules, and Limits

### 0.1 What was done
- Read the domain models, all view functions, forms, permissions, admin registrations, report services, roll-call service, SMS service, import/progression/ID-card services, Celery tasks, settings, middleware, entitlements, templates, and deployment configuration.
- Traced each important feature from UI entry point → view → service → model/database constraint → external service (SMS gateway, QR page, backups).
- Audited the tenancy layer: provisioning, domain routing, backup, restore, support access, entitlements/quotas, platform audit logs.
- Ran `python manage.py check --settings=schoolsystem.settings.development` — **passes with no issues** (system check framework only; it does not exercise business logic).

### 0.2 What was NOT done (and why)
| Not done | Reason | Consequence |
|---|---|---|
| Full test suite execution | Local PostgreSQL/test-database setup was not verified in this environment | Findings based on static reasoning carry "Unverified runtime" risk; CI must close this gap |
| Live browser walkthrough of every screen | Audit focused on code truth over demo truth; runtime UI exercise still required | Rendering/layout issues may exist that code inspection cannot catch |
| Real SMS gateway call | Explicitly prohibited | Sender-ID and quota findings are code-verified but not gateway-verified |
| Backup/restore rehearsal | Explicitly prohibited (no external changes) | Restore gap is verified from command logic; an actual drill is required before go-live |
| Penetration test / load test | Out of static-audit scope | Security findings are configuration-level; formal security testing still needed |

### 0.3 Finding status legend
- **Verified** — traced in code with concrete evidence; behaviour is as described by the code path.
- **Partially verified** — core path traced, but a dependent path (runtime, gateway, or environment) was not exercised.
- **Unverified** — could not be established without running the system or obtaining school input.
- **Not implemented** — no code path exists for the claimed behaviour.

### 0.4 Severity legend
- **Critical** — risk of physical harm to a student, or irreversible loss of student records.
- **High** — breaks a core safety, gate, attendance, or recovery workflow, or produces materially wrong decisions.
- **Medium** — a workaround exists, but accuracy, efficiency, audit quality, or reliability is degraded.
- **Low** — cosmetic, documentation, or minor usability issue.

### 0.5 Evidence convention
Each finding gives: Finding ID, severity, status, location, evidence, expected vs actual, impact, action, and the test required to prove the fix. Findings appear in Deliverables A–H and are indexed in Deliverable D.

---

## Deliverable A — Executive Assessment

### A.1 One-paragraph verdict
This is a **substantially built, security-conscious, operations-focused boarding system** with a far-above-average feature depth for a Ghanaian SHS: 60 view functions, 18+ reports, QR-gated exeat verification, roll-call with gate reconciliation, SMS notifications with retries, tenant provisioning, and a genuine role model. It is **not yet a finished commercial product**. The audit found **no single catastrophic design flaw**, but it found a cluster of integrity and operational gaps concentrated in exactly the places a school feels first: gate pass lookup, roll-call records, leave-time changes, SMS sender identity, occupancy reporting, and disaster recovery. Several README claims also exceed what the code actually does. The system is **conditionally suitable for a supervised pilot** once the P0 items in Deliverable D are fixed and the recovery drill in Deliverable F passes. It is **not ready for unattended multi-school commercial operation** today.

### A.2 What is genuinely strong (verified)
1. **Role and scope model** — `core/permissions.py` implements ADMIN / SENIOR_HOUSEMASTER / HOUSEMASTER / HOUSE_TUTOR / GATE_OFFICER / UNASSIGNED with house scoping, explicit superuser bypass, and belt-and-suspenders checks inside workflow actions. This is rare in school software and prevents most cross-house data exposure.
2. **CSV import is production-grade** — atomic transaction, full-roster validation before commit, and formula-injection escaping for exports. This protects both data integrity and the school's spreadsheets.
3. **QR public pass page is privacy-preserving** — token-scoped, expiry-aware, and intentionally minimal; a scanned QR does not leak the student record.
4. **Exeat workflow is complete for the standard case** — request → approval with jurisdiction enforcement → gate departure → return, plus a reconciliation path for missed movements and automatic On-Exeat marking in roll call.
5. **Security baseline** — login rate limiting, 30-minute sessions, strong password validators, HSTS, CSP in report-only mode, CSRF on all mutating operations, no verbatim secret exposure in templates.
6. **Tenancy architecture** — schema-per-school with verified domain hostname routing, entitlements/quota hooks, provisioning audit trail, and platform-level logs. Backup and restore commands include SHA-256 checksums and a table-of-contents check before restore — uncommon care.
7. **Celery operations** — SMS retry with backoff, overdue-return reminders, roll-call session expiry, and SHS3 phase-out are all scheduled jobs rather than manual chores.

### A.3 What is genuinely risky (verified)
1. **Attendance records can be changed after a roll call is closed, silently** (AUD-001). The session lock does not check session status, and the entry is mutated with queryset `.update()`, bypassing model validation. A test even encodes the "no incident after completion" expectation. Any attendance report can therefore disagree with the roll call as conducted.
2. **Duplicate roll-call sessions are possible** (AUD-002). The service unconditionally creates a session despite a docstring claiming it finds an existing in-progress one. A double-submit produces two rosters and doubles or splits attendance data.
3. **The gate can show the wrong pass for a student** (AUD-003). A student with several exeats can return a future Pending or an old returned pass from the "active pass" lookup, depending on ordering. Gate staff may refuse a valid exit or log movement against the wrong pass.
4. **There is no controlled way to move a departure time** (AUD-004). Early departure is hard-blocked at both the exeat view and the gate terminal; the only edit path is Django admin, which bypasses the workflow. Emergencies will either be handled off-system (record and reality diverge) or by an administrator editing records without a state-machine guard.
5. **SMS sender identity is effectively hard-coded away from the configured sender ID** (AUD-006), and provisioning seeds a 50-character school-name-derived short name before truncation. Guardian messages may be rejected by the gateway or arrive under an unapproved ID — in a system that depends on SMS as the only guardian channel, this is a pilot-blocking risk.
6. **Disaster recovery of a live school has no in-place path** (AUD-015). The restore command is deliberately conservative (checksum + TOC verification, refuses registered/existing schemas, never drops) — good safety, but it means a live tenant cannot be restored in place without improvised schema surgery, and the README example does not match that constraint.
7. **Support access tokens are not wired into the application** (AUD-016). Tokens can be generated and verified only from the CLI. No login, middleware, or authorization path consumes them, so the README claim that engineers use them to troubleshoot a school is not implemented as described.
8. **Occupancy can be overstated** (AUD-009) because the house list counts boarders without an enrollment-status filter, while the equivalent report and dashboard do filter. Capacity decisions are made from the inconsistent number.

### A.4 What is missing for the product's own goals (verified absence)
- No guardian portal or student self-service; SMS is the only guardian channel, and it is one-way.
- No bulk SMS composition UI for announcements, no scheduled report delivery, and no offline/PWA mode despite HTMX forms requiring connectivity at the gate.
- No controlled correction workflow or comprehensive audit log for exeat/movement corrections; Django admin is the de-facto correction tool (AUD-018).

### A.5 Assessment by audience
| Audience | Assessment |
|---|---|
| **Administrator / Headmaster** | Decision-ready dashboards and broad reporting exist, but the two numbers most likely to be quoted — occupancy and approval turnaround — have accuracy defects (AUD-009, AUD-010, AUD-012). Use only after fixes, or with the report caveats stated in Deliverable B. |
| **Housemasters / House Tutors** | Day-to-day exeat work is well designed. Roll-call integrity gaps (AUD-001, AUD-002) undermine the records they are accountable for. Reasonless quick rejects weaken the audit trail (AUD-005). |
| **Gate staff** | The gate terminal is fast and the reconciliation feature is valuable. Pass lookup ambiguity (AUD-003) and the early-departure block (AUD-004) are the two operational failure modes to fix before a gate drill. |
| **Guardians** | Receive event SMS but cannot ask for or verify anything. Entirely dependent on gateway deliverability and corrected sender ID (AUD-006). |
| **Students** | Depend on staff to create passes; no view of their own status. Acceptable for the era of the school, but a stated product goal (guardian/student access) is absent. |
| **System administrator** | Tenancy tooling is a strength, but backup encryption/retention (AUD-017), in-place restore (AUD-015), and the unwired support tokens (AUD-016) are the operational gaps to close before scale. |

---

## Deliverable B — Functionality Matrix

Statuses are per section 0.3. "Evidence" is the primary code location audited.

| # | Capability | Status | Evidence | Notes |
|---|---|---|---|---|
| B1 | Authentication, sessions, login throttling | Verified | `schoolsystem/settings/base.py`, `core/middleware.py` | 30-min sessions, validators, throttling present |
| B2 | Roles and house scoping | Verified | `core/permissions.py`, `core/models.py` | Superuser bypass explicit; jurisdiction checks on exeat approvals |
| B3 | Student registry and lifecycle | Verified (core) | `core/models.py`, `core/views.py` | Enrollment statuses enforced in queries; progression feature is new/uncommitted (see D) |
| B4 | Student promotion / SHS3 phase-out | Partially verified | `core/services/student_progression.py` (untracked), `core/tasks.py`, migrations 0032–0033 | Code and tests exist; never executed in this audit; migration files untracked |
| B5 | CSV import / export | Verified | `core/services/student_import.py` | Atomic; full-roster validation; formula escaping |
| B6 | Student ID cards and QR token lifecycle | Verified | `core/views.py`, `core/services/` ID/QR code | Rotation and optional TTL present |
| B7 | Houses, dormitories, capacity | Partially verified | `core/views.py` `house_list_view` | Occupancy counting defect AUD-009 |
| B8 | Roll call: session creation | Partially verified | `core/services/roll_call.py:33-130` | No duplicate guard AUD-002; docstring claim of "existing session" is false |
| B9 | Roll call: marking, completion | Verified | `core/services/roll_call.py`, views | Completion path traced |
| B10 | Roll call: absence follow-up | Partially verified | `core/services/roll_call.py:401-487` | Closed-session mutation and validation bypass AUD-001 |
| B11 | Roll call: gate reconciliation | Verified | `core/services/roll_call.py:490+` | Auto-marks unmarked entries On Exeat at gate departure |
| B12 | Exeat request and approval workflow | Verified | `core/views.py:375+`, `1618+`, `1745+` | Jurisdiction enforced; two-step state machine |
| B13 | Exeat rejection audit quality | Partially verified | `core/views.py:2162-2177` | Reasonless quick reject allowed AUD-005 |
| B14 | Exeat cancellation | Verified | `core/views.py:2134+`, templates | Approved-only cancel path present |
| B15 | Gate verification (QR / number lookup) | Partially verified | `core/views.py:1206-1260` and gate verify view | Wrong-pass risk AUD-003 |
| B16 | Gate departure / return logging | Verified | `core/views.py:3360-3460` | Admin + Gate Officer only, consistent with UI |
| B17 | Early departure / leave rescheduling | Not implemented | n/a | No UI path AUD-004 |
| B18 | Movement reconciliation for missed scans | Verified | `core/views.py:1962+`, `exeat_reconcile_view` | Retrospective; requires reason and outcome |
| B19 | SMS notifications (submit/approve/reject/depart/return/overdue) | Partially verified | `core/services/sms.py`, `core/tasks.py` | Sender identity AUD-006; quota AUD-007; retry logic present |
| B20 | Email notifications | Partially verified | settings/backends | Configured but not exercised; no audit evidence of delivery guarantees |
| B21 | Dashboards (admin, gate, housemaster) | Verified | `core/views.py` | House-list occupancy inconsistency AUD-009 |
| B22 | Reports (18 types) | Partially verified | `core/services/reports.py` | Breadth strong; accuracy defects AUD-008/010/011/012/013/014 |
| B23 | Conduct and discipline | Verified | `core/models.py`, `core/views.py` | Normalised-rate report caveat AUD-013 |
| B24 | Infirmary / welfare watchlist | Partially verified | `core/services/reports.py:2488-2546` | Completed students can appear AUD-014 |
| B25 | Configuration management + config audit log | Verified | `core/models.py` `ConfigurationAuditLog`, `core/admin.py` | Audit log exists for configuration changes |
| B26 | Django admin as correction tool | Partially verified | `core/admin.py` | No state-machine guard or movement audit AUD-018 |
| B27 | Tenant provisioning and domain routing | Verified | `tenancy/` models, provisioning command | Domain hostname routing verified in code |
| B28 | Entitlements and SMS quota | Partially verified | `tenancy/entitlements.py:59-175` | Stale queue items consume quota AUD-007 |
| B29 | Backup | Partially verified | `tenancy/management/commands/tenant_backup.py` | No encryption/retention; second-precision names AUD-017 |
| B30 | Restore | Partially verified | `tenancy/management/commands/tenant_restore.py` | Cannot restore live tenant in place AUD-015 |
| B31 | Support access | Not implemented as claimed | `tenancy/management/commands/support_access.py` | CLI-only verification AUD-016 |
| B32 | Scheduled tasks (retries, reminders, expiry, phase-out) | Verified (config) | `core/tasks.py` | Beat schedule present; runtime not exercised |
| B33 | Guardian portal / student self-service | Not implemented | n/a | AUD-020 (product gap) |
| B34 | Bulk SMS / announcements / scheduled reports | Not implemented | n/a | AUD-021 |
| B35 | Offline / PWA operation | Not implemented | n/a | AUD-021; gate depends on connectivity |
| B36 | Security hardening (HSTS, CSP, CSRF, validators) | Verified (config) | settings, middleware | CSP is report-only; formal pen test not done |
| B37 | Accessibility / usability | Partially verified | `templates/base.html` | 12px default text, many 10px labels, no skip link AUD-022 |
| B38 | Automated tests | Partially verified | `core/tests.py`, `core/test_advanced_reports.py`, `core/test_student_*.py` | Suite exists and covers key flows; not executed AUD-023; coverage gaps listed in Deliverable F |

---

## Deliverable B.5 — Detailed Findings Register

Each finding lists location, evidence, expected vs actual, impact, action, and the test that must prove the fix.

### AUD-001 — Attendance follow-ups can mutate completed roll calls
- **Severity:** High
- **Status:** Verified
- **Location:** `core/services/roll_call.py:401-487` (`resolve_absence_follow_up`)
- **Evidence:** The session is locked at line 437 (`select_for_update().get(...)`) but its `status` is never checked. If the resolution is not CONFIRMED, the entry is changed via `RollCallEntry.objects.filter(pk=entry.pk).update(...)` at lines 469-474, bypassing `RollCallEntry.clean()`/`save()`. `core/tests.py::test_authorized_absence_and_recording_error_resolutions_do_not_create_incidents` explicitly encodes resolution after session completion.
- **Expected:** Follow-ups for sessions that are `COMPLETED` (or auto-expired) cannot be resolved; any exception must be recorded with an audit entry.
- **Actual:** Closed sessions accept resolutions and the roll-call entry is silently rewritten.
- **Impact:** Historical attendance is mutable; conduct records, attendance-league and welfare reports can change after the fact; undermines the credibility of the school's attendance evidence.
- **Action:** Reject resolutions when session status is not `IN_PROGRESS` (or introduce a formal "correction" entity carrying reason, actor, timestamp, and old/new values). Replace `.update()` with a save path that runs validation, or document why validation is intentionally skipped and assert the new invariants.
- **Test needed:** Integration test resolving before/after completion and after expiry; verify closed sessions reject or audit-log; verify no time-of-check/time-of-use window under concurrency.

### AUD-002 — Duplicate roll-call sessions are possible
- **Severity:** High
- **Status:** Verified
- **Location:** `core/services/roll_call.py:33-130`; callers in `core/views.py` (`start_roll_call` around line 2940)
- **Evidence:** Line 70 unconditionally calls `RollCallSession.objects.create(...)` inside the transaction; no lookup for an existing in-progress session for `(house, dormitory, date, session_type)`. The docstring at line 60 claims an existing session is reused.
- **Expected:** One in-progress session per house/dormitory/date/type; repeat submissions reopen it.
- **Actual:** Each submission creates a new session and a new roster.
- **Impact:** Double rosters split attendance; totals, absence follow-ups and "unmarked" counts become contradictory; teachers may mark the wrong session.
- **Action:** Add a transactional guard plus a database-level unique constraint (or unique partial index for in-progress sessions). Reopen instead of recreate.
- **Test needed:** Concurrency test with two parallel starts; repeated-post test; constraint test at DB level.

### AUD-003 — Gate lookup can select the wrong pass for a student
- **Severity:** High
- **Status:** Verified
- **Location:** `core/views.py:1206-1260` (public pass page) and the gate verification lookup (`gate_verify_pass_view`, around line 1550-1650)
- **Evidence:** Lookup filters to approved passes and then orders by `-expected_departure` and takes `.first()`. A student holding a future Pending/Approved pass and an active current pass can surface the wrong record; an old return-complete pass can also win when ordering ties.
- **Expected:** The system should resolve the pass that is active now (departed and not returned, or within its departure window today), and when several passes match, show all matches for the gate officer to choose.
- **Actual:** A single pass is silently chosen by date ordering.
- **Impact:** A legitimate exit can be refused, or movement logged against the wrong pass, corrupting both records; students are held at the gate.
- **Action:** Rank by an explicit "actionable now" predicate; never auto-pick when multiple actionable passes exist — present a disambiguation list; add a regression test with three passes (returned, current approved, future pending).
- **Test needed:** Gate lookup matrix test; UI test showing multiple matches; assertion that movement always attaches to the officer-selected pass.

### AUD-004 — No controlled workflow for early departure or leave-time change
- **Severity:** High
- **Status:** Verified
- **Location:** `core/views.py:2066-2071` (`exeat_action_view`) and `core/views.py:3389-3393` (`gate_terminal_action_view`)
- **Evidence:** Both reject departure when `now < exeat.expected_departure`, with "available at" messaging. No view, form, or route exists to edit or reschedule an approved exeat; the only edit path is Django admin.
- **Expected:** A defined, permissioned path to move a departure earlier or later, with reason and actor recorded.
- **Actual:** Hard block; admin edit bypasses the workflow.
- **Impact:** Real emergencies will be handled off-system, so records diverge from reality; or an admin edits records without state-machine validation.
- **Action:** Decide school policy first (see Appendix A), then implement a reschedule/early-leave action for approval roles with reason capture and audit trail; keep the gate block for anything not approved.
- **Test needed:** Reschedule action test; early departure still blocked without approval; audit entry created; SMS reflects the new time.

### AUD-005 — Quick reject accepts an empty reason
- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/views.py:2162-2177`
- **Evidence:** `rejection_reason` may be empty; the code emits a warning message and still rejects. The review page enforces a reason.
- **Expected:** One policy for both surfaces — either a required reason or a recorded "no reason given" marker.
- **Actual:** Divergent enforcement; silent audit gap.
- **Impact:** Guardian/student cannot be told why; appeals and oversight lose evidence.
- **Action:** Require a reason on both paths, or auto-record a structured "unspecified" reason with the actor and timestamp.
- **Test needed:** POST reject without reason asserts validation error on both surfaces.

### AUD-006 — SMS sender ID is ignored; provisioning seeds an over-long unapproved short name
- **Severity:** High
- **Status:** Verified
- **Location:** `core/services/sms.py:22-25` (`_school_short_name`), `core/services/sms.py:272` (sender assembly), `tenancy/management/commands/provision_school.py:80`
- **Evidence:** `_school_short_name()` returns `configuration.short_name or configuration.name or "ATSEC"`, so it is never empty; therefore the `settings.SMS_SENDER_ID` fallback at line 272 is unreachable. Provisioning assigns `school_name[:50]` as the short name before sanitising/truncating to 11 characters.
- **Expected:** Sender ID is the approved gateway ID, explicitly configured per tenant and validated at provisioning time.
- **Actual:** Sender is derived from school name; approved ID setting is dead code; seeded value can exceed the gateway's limit.
- **Impact:** Guardian messages may be rejected or branded incorrectly — in the only guardian communication channel. This is pilot-blocking.
- **Action:** Resolve sender as `configuration.sms_sender_id or settings.SMS_SENDER_ID`; validate against the gateway's approved pattern/length at save and provisioning time; add a startup warning if unset.
- **Test needed:** Unit test for sender resolution precedence; provisioning test asserting a valid sender; sandbox send with the real gateway account.

### AUD-007 — SMS quota counts stale queue items; no segment costing
- **Severity:** Medium
- **Status:** Verified
- **Location:** `tenancy/entitlements.py:153-165` (`sms_monthly_usage`, `quota_limit`)
- **Evidence:** Usage counts `SENT` this month plus **all** `QUEUED`/`PROCESSING` rows regardless of age. Long-lived stuck outbox items permanently consume quota. Messages are not counted per SMS segment.
- **Expected:** Usage counts delivered messages in the period, with bounded treatment of stuck items (expiry or reconciliation), and segment-aware cost reporting.
- **Actual:** Stale items block the tenant indefinitely; costs under-reported for long messages.
- **Impact:** A school can be unable to send urgent SMS because of dead queue rows; billing/spend data is wrong.
- **Action:** Age out or fail stale QUEUED/PROCESSING rows via the existing expiry/retry tasks; count segments; expose quota consumption in the SMS report.
- **Test needed:** Inject a month-old QUEUED row and assert quota is released after the new task; segment-count test with a 3-segment message.

### AUD-008 — SMS usage report excludes non-exeat deliveries when filters are applied
- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/services/reports.py:2282-2294`
- **Evidence:** With `term_id` or `house_id` filters, the queryset filters on `exeat__academic_term_id` / `exeat__student__house_id`, dropping every delivery not linked to an exeat.
- **Expected:** Report totals reconcile with quota usage for the same period, or clearly label the subset being shown.
- **Actual:** Filtered totals silently undercount; headmaster sees different numbers from quota usage.
- **Impact:** Trust in reporting degrades; SMS spend oversight is wrong.
- **Action:** Apply term/house scoping to the exeat-linked subset only and include non-exeat events (e.g., overdue reminders) in totals where mappable; label any residual subset explicitly.
- **Test needed:** Golden-data test asserting report total equals quota usage for the same month with filters applied.

### AUD-009 — House list overstates occupancy versus reports and dashboard
- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/views.py:641-651` compared with `core/services/reports.py:295-346` and `core/views.py:231`
- **Evidence:** `house_list_view` annotates boarders by residency only, without excluding `COMPLETED` (or withdrawn) students. The housing report and dashboard both filter enrollment status.
- **Expected:** One definition of "current boarder" across all occupancy surfaces.
- **Actual:** Completed students inflate house/dorm counts and can trigger false over-capacity alerts.
- **Impact:** Capacity, placement, and staffing decisions from wrong numbers; unplaced-boarder worklists inaccurate.
- **Action:** Extract a single `current_boarders()` queryset/manager and use it everywhere.
- **Test needed:** Seed a completed boarder and assert house list, housing report, and dashboard agree.

### AUD-010 — Approval turnaround uses `updated_at` and counts cancellations
- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/services/reports.py:1820-1871`, caveat text at line 1964
- **Evidence:** Decision time is `updated_at`; cancellations are counted among decided requests. Any later edit of an approved record shifts its "decision" time.
- **Expected:** A dedicated `decided_at` timestamp written once by the state transition.
- **Actual:** Approximation documented in the report but still surfaced as KPI.
- **Impact:** Headmaster performance metrics can be wrong after unrelated edits.
- **Action:** Add `decided_at` (and decide whether cancellations are excluded or reported separately); backfill carefully via migration.
- **Test needed:** Test that approval stamps `decided_at`; later edit does not change it; cancellation reported separately.

### AUD-011 — Staff activity report omits non-housemaster staff and misdates approvals
- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/services/reports.py:2372-2429`
- **Evidence:** Rows iterate `HouseMaster.objects`; gate officers and tutors with recorded actions are absent. Approval dates filter on `updated_at`.
- **Expected:** Activity attributed to every actor, or the report clearly scoped to housemasters only.
- **Actual:** Misleading "staff activity" title; invisible work.
- **Impact:** Oversight and fairness problems; duplicated/masked accounts cannot be spotted.
- **Action:** Base rows on distinct actors across roll calls/approvals/incidents/resolutions; label scope; use `decided_at` after AUD-010.
- **Test needed:** Seed a tutor and gate officer action and assert they appear.

### AUD-012 — "Currently overdue" is constrained by the selected date range
- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/services/reports.py:1629-1664, 1703`
- **Evidence:** The register filters `expected_return__date` by `date_from`/`date_to`, then counts `OVERDUE - still out` within that set and labels it "Currently overdue".
- **Expected:** "Currently overdue" is a point-in-time count independent of the reporting window; the window applies to "returned late in period".
- **Actual:** A range ending yesterday can report zero currently overdue.
- **Impact:** Safety-relevant number can read falsely clean.
- **Action:** Compute overdue-now from an unfiltered (scoped by house/term only) queryset; keep the range for late returns.
- **Test needed:** Test with a currently-overdue student outside the range; assert count is 1.

### AUD-013 — Conduct-normalised rates divide historical incidents by current boarders
- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/services/reports.py:2726+` (`_conduct_normalized_report`)
- **Evidence:** Incident counts come from records in the period; denominators use current boarder counts.
- **Expected:** Denominators use the population of the same period (or the report states the approximation).
- **Actual:** Rates rise/fall with roster changes unrelated to conduct.
- **Impact:** Term-over-term comparisons and interventions are misleading.
- **Action:** Store or derive per-period population; or relabel with an explicit "per current boarder" note.
- **Test needed:** Golden test with roster change mid-period.

### AUD-014 — Welfare/infirmary watchlist includes completed students
- **Severity:** Low
- **Status:** Verified
- **Location:** `core/services/reports.py:2520`
- **Evidence:** Student rows are fetched by ID with no enrollment-status filter.
- **Expected:** Active students only, unless explicitly including alumni.
- **Actual:** Completed students can appear on a welfare watchlist.
- **Impact:** Noise; possible follow-up on departed students.
- **Action:** Filter to active statuses, aligned with AUD-009's single queryset.
- **Test needed:** Seed completed student with infirmary mark; assert exclusion.

### AUD-015 — Live-tenant restore cannot be done in place
- **Severity:** High
- **Status:** Verified
- **Location:** `tenancy/management/commands/tenant_restore.py` (safety checks in header lines; refuses registered/existing schemas; never drops/cleans); `README.md` example
- **Evidence:** The command verifies checksum and TOC, refuses reserved/registered/existing schemas, and avoids `--clean`/`--if-exists` by design. A failed live tenant therefore cannot be recovered into its own schema by the tool; the README example conflicts with this constraint.
- **Expected:** A documented, rehearsed recovery path for an active school (e.g., restore into a spare schema, verify, then swap; or a guarded in-place mode).
- **Actual:** Manual/improvised recovery only.
- **Impact:** Extended downtime during the worst incident; the moment a school most needs the tool is the moment it cannot be used safely.
- **Action:** Write the runbook now (spare-schema restore + verification + swap), add a `--target-schema` override guarded by typed confirmation, and rehearse quarterly. Fix the README.
- **Test needed:** Scheduled disaster-recovery drill in Deliverable F: backup a seeded tenant, restore into a scratch schema, verify row counts and spot-check records, then document elapsed time.

### AUD-016 — Support access tokens are CLI-only
- **Severity:** High
- **Status:** Verified
- **Location:** `tenancy/management/commands/support_access.py:114-115, 211+`; `README.md`
- **Evidence:** Generate/verify/revoke operate on stored bcrypt hashes and expiry, but no login, middleware, session, or request authorization path consumes a token. Verification is available only via the CLI.
- **Expected:** Either a real, auditable support-access mechanism, or the README truthfully states tokens are a CLI verification aid only.
- **Actual:** Claimed capability does not exist in the application.
- **Impact:** False confidence about how remote support is performed and monitored; a future hasty wiring could create an unaudited backdoor.
- **Action:** Decide: (a) remove the claim and keep the CLI as a bookkeeping tool, or (b) implement scoped support sessions with expiry, IP binding, read/limited-write permissions, and full platform audit logging. Option (a) is the safer near-term choice.
- **Test needed:** Documentation test (claim matches code) or, if implemented, an authorization test suite for expiry, scope, revocation, and audit logging.

### AUD-017 — Backups are unencrypted, retained without policy, and second-precision named
- **Severity:** Medium
- **Status:** Verified
- **Location:** `tenancy/management/commands/tenant_backup.py:181, 251-254, 277`
- **Evidence:** `pg_dump` output and media tar archives are written without encryption; no retention/pruning policy exists in the command; filenames use `%Y%m%d_%H%M%S`, so a second backup within the same second collides.
- **Expected:** Encrypted backups (or encrypted storage), a retention policy with pruning, and collision-safe naming.
- **Actual:** Student PII and media sit in plaintext archives indefinitely.
- **Impact:** Data-protection exposure; disk exhaustion; silent overwrite risk.
- **Action:** Encrypt at rest or require an encrypted destination; implement retention (e.g., keep N daily + M weekly) with an audit log; add a random suffix to filenames; document the key custody procedure.
- **Test needed:** Restore-from-encrypted-archive test; retention pruning test with dry-run; collision test.

### AUD-018 — Django admin is an unguarded correction path for exeats and movements
- **Severity:** Medium
- **Status:** Verified
- **Location:** `core/admin.py` (`Exeat`, `ExeatMovement` registrations)
- **Evidence:** Records are editable through admin without the state-machine checks used by application views, and without a comprehensive movement-correction audit entry.
- **Expected:** Admin edits restricted to break-glass with a recorded reason and immutable audit trail, or admin made read-only for state fields.
- **Actual:** Silent edits possible; QR pass state and reconciliation can desync.
- **Impact:** Audit integrity; disputes cannot be resolved from logs.
- **Action:** Make status/movement fields read-only in admin; add a controlled correction action (reuse reconciliation); log all overrides to the platform audit log.
- **Test needed:** Admin test asserting protected fields cannot be saved directly; correction workflow test.

### AUD-019 — Gate-action permission wording diverges from enforcement
- **Severity:** Low
- **Status:** Verified
- **Location:** `core/views.py:2028-2055` vs `core/permissions.py:187-200` and `core/models.py:465-469`
- **Evidence:** `exeat_action_view` is decorated with `EXEAT_ROLES` and its docstring claims departure/return handling, but `can_log_gate_check` allows only Admin/Gate Officer. Templates consistently show gate actions only when `can_access_gate`.
- **Expected:** One documented permission table.
- **Actual:** Housemaster POSTs to departure/return are denied with a generic message; dead branches in the view.
- **Impact:** Maintenance risk and occasional confusing denials; not a security hole.
- **Action:** Split gate actions into a gate-only endpoint or align decorators; update the docstring; add a permissions-matrix test.
- **Test needed:** Permission matrix test across all six roles and all actions.

### AUD-020 — No guardian portal or student self-service
- **Severity:** Medium (product gap)
- **Status:** Not implemented
- **Evidence:** No guardian/student authentication paths exist; SMS is the only guardian channel; `Exeat` creation is staff-only (`EXEAT_CREATE_ROLES`).
- **Expected:** Per product goals: guardian status visibility and possibly request submission; student pass visibility.
- **Actual:** Absent.
- **Impact:** Office staff absorb all status calls; guardians remain uncertain; adoption depends on SMS quality.
- **Action:** Validate demand with the school before building (Appendix A). If built, start read-only status + receipt pages rather than full submission.
- **Test needed:** Product discovery before any engineering test.

### AUD-021 — No bulk SMS, scheduled reports, or offline gate mode
- **Severity:** Medium (product gap)
- **Status:** Not implemented
- **Evidence:** SMS is event-driven only; reports are on-demand HTML/print; templates are HTMX server-rendered with no service worker.
- **Expected:** At minimum, a resilience plan for connectivity loss at the gate (printed active-pass list / cached last-known list).
- **Actual:** Gate verification stops when connectivity stops.
- **Impact:** Gate backlog during network outages — a routine risk in the pilot environment.
- **Action:** Near-term: printable "currently out" and "approved today" lists from the gate terminal. Later: queue-and-sync offline mode only if drills show real outages.
- **Test needed:** Offline drill as part of UAT; printed-list accuracy test against live data.

### AUD-022 — Accessibility and legibility risks
- **Severity:** Low
- **Status:** Partially verified
- **Location:** `templates/base.html` and report/gate templates
- **Evidence:** Global `text-xs` (12px) base and numerous 10px labels/uppercase micro-text; no skip link found; touch targets in gate controls are adequate (44px). Contrast was not formally measured.
- **Expected:** WCAG 2.1 AA-aligned legibility for office, gate, and older staff; keyboard navigability.
- **Actual:** Dense micro-typography increases error rates, especially at the gate at night.
- **Impact:** Slower data entry; more mis-taps in a safety workflow.
- **Action:** Accessibility pass: minimum 14px body at gate surfaces, larger critical values, focus states, skip link, contrast audit; verify on the actual tablet used at the gate.
- **Test needed:** Axe/Lighthouse plus a task-based usability test with 5 staff users.

### AUD-023 — The full test suite was not executed during this audit
- **Severity:** Medium
- **Status:** Unverified
- **Location:** `core/tests.py`, `core/test_advanced_reports.py`, `core/test_student_registry.py`, `core/test_student_progression.py`, `core/test_student_import.py`-style modules
- **Evidence:** `manage.py check` passes. Test execution requires PostgreSQL/Redis; the environment was not provisioned, and the working tree contains untracked migrations.
- **Expected:** CI runs the suite on every change with a seeded Postgres.
- **Actual:** Unknown pass rate; the new progression feature is entirely unexercised.
- **Impact:** Regression risk exactly where the working tree changed most.
- **Action:** Make the suite a required CI gate; run the Deliverable F tests; record results in the repository.
- **Test needed:** The entire Deliverable F plan.

### AUD-024 — README claims exceed code behaviour
- **Severity:** Low
- **Status:** Verified
- **Location:** `README.md` (backup/restore example; support-access description)
- **Evidence:** Restore example conflicts with restore command constraints; support tokens described as a troubleshooting path not present in application code.
- **Expected:** Operator documentation that a tired engineer can follow during an incident.
- **Actual:** Documentation leads to failed or improvised procedures.
- **Impact:** Longer outages; risky ad-hoc recovery.
- **Action:** Rewrite README sections after the AUD-015/AUD-016 decisions; add runbook links; date-stamp operational docs.
- **Test needed:** Dry-run the documented runbook on staging and correct every step that fails.

---

## Deliverable C — User Workflow Assessment

Legend: **[OK]** verified end-to-end, **[Friction]** works but with identified issues, **[Broken/absent]** not functional as intended.

### C.1 Administrator / Headmaster
- **Workflow traced:** login → dashboard (students, boarders, day students, occupancy, overdue exeats) → students registry → reports hub → configuration → audit log.
- **Assessment:** The dashboard and 18-report suite give a real command picture. Approval hub, exeat register and conduct tools are decision-ready. **[Friction]**: two headline numbers are unreliable (occupancy AUD-009, current-overdue AUD-012), and approval turnaround can shift after edits (AUD-010). If the headmaster quotes these in assembly or to the board, the system can be wrong.
- **Efficiency:** High — most daily questions are answerable without exporting to Excel.
- **Trust requirement:** fix the reporting defects before presenting numbers officially.

### C.2 Senior Housemaster / Housemaster
- **Workflow traced:** login → house roll call (start, mark, complete) → absence follow-ups → exeat approvals (approvals hub / exeat register) → reconcile missed movement → conduct records → house reports.
- **Assessment:** This is the best-designed part of the system. Jurisdiction rules are enforced (`can_approve_exeat_check`), the two-step approve/reject state machine is coherent, and reconciliation rescues the common "student left without a gate scan" case. **[Friction]**: roll-call duplicates (AUD-002) and closed-session mutations (AUD-001) put the records they are accountable for at risk. Quick reject without a reason (AUD-005) weakens the audit trail a housemaster will need in a dispute. Early-leave emergencies force them toward admin (AUD-004).
- **Efficiency:** High for the normal case; the gaps above are trust issues, not speed issues.

### C.3 House Tutor
- **Workflow traced:** login → conduct recording → exeat request creation → view exeats in assigned house.
- **Assessment:** **[OK]** for intended duties. Correctly excluded from roll call and gate logging (permission model is deliberate and consistent here). No report access — appropriate.
- **Friction:** cannot see approval outcome beyond what housemasters tell them; SMS/notification coverage for tutors is unverified.

### C.4 Gate staff (Gate Officer)
- **Workflow traced:** login → gate security terminal → QR/number verification → one-click departure/return → automatic roll-call reconciliation → active-out list.
- **Assessment:** The fastest and most safety-relevant surface in the system; one-click actions and the active-out list are good design. **[Friction]**: wrong-pass selection risk (AUD-003) can cause a valid student to be stopped or a record to attach to the wrong pass; early-departure block (AUD-004) has no approved workaround; connectivity loss halts verification (AUD-021). The terminal depends on the officer correctly interpreting status labels — AUD-022's legibility concerns matter most here, at night, under time pressure.
- **Reliability:** Depends on network and gateway; needs an offline fallback before unsupervised night shifts.

### C.5 Guardians
- **Workflow traced:** receive SMS on request/approval/rejection/departure/return/overdue. That is the whole experience.
- **Assessment:** **[Broken/absent]** as a product channel until AUD-006 is fixed: sender identity can be wrong or messages rejected. Even when working, guardians cannot confirm receipt, request a pass, or check status; every question routes through the school office or through the student. In Ghanaian SHS practice, guardians often live away from campus and rely entirely on the telephone — SMS quality is therefore adoption-critical.
- **Efficiency:** zero self-service.

### C.6 Students
- **Workflow traced:** receive approved pass (printed slip with QR) → present at gate → return logged. No login exists.
- **Assessment:** **[OK]** for the pilot's scope. Sensible privacy: the public QR page exposes only pass-relevant facts. Students cannot check their own exeat status, so they depend on housemasters saying "it is approved".
- **Friction:** if SMS to guardians fails, the student may still depart with a valid pass — acceptable — but any dispute over timing is settled by staff records only.

### C.7 System Administrator
- **Workflow traced:** provision tenant → configure school → manage users/roles → run backup → monitor tasks → generate support token → inspect platform audit log.
- **Assessment:** Provisioning, domain routing, entitlements and platform logs are a genuine strength. **[Friction/Broken]**: restore of a live tenant is not feasible in place (AUD-015) and the README misleads (AUD-024); backups are unencrypted and unreleased (AUD-017); support tokens verify only in the CLI (AUD-016). Day-to-day administration is solid; incident-day administration is where the risk sits.
- **Maintainability:** Python/Django + Celery + Postgres + Redis stack is affordable and mainstream for Ghana; no exotic dependencies. Deployment configuration exists for Render with Celery workers.

### C.8 Cross-cutting workflow observations
1. **Notification dependency:** exeat submission, approval and gate events all trigger SMS; with AUD-006/AUD-007 outstanding, the whole guardian communication layer is fragile.
2. **Correction workflows are the weakest seam:** workarounds for real-world exceptions (early leave, missed scan) are split between a well-built reconciliation feature and an unguarded admin.
3. **Connectivity assumption:** every workflow except printing assumes the server is reachable. Gate and roll call are the two places where an outage has immediate safety impact.
4. **Consistency of definitions:** "boarder", "decided", and "currently overdue" mean slightly different things on different screens — a systemic fix (shared querysets, explicit timestamps) would prevent the entire class of AUD-009/010/011/012/014 findings.

---

## Deliverable D — Prioritized Backlog

Priority key: **P0** pilot-blocking (fix before any live gate/roll-call/SMS use), **P1** fix before scaling beyond the pilot, **P2** product growth, **P3** cleanup.
Effort key: S (≤1 day), M (2-5 days), L (1-3 weeks).

| Pri | Item | Findings | Effort | Owner | Exit criteria |
|---|---|---|---|---|---|
| P0 | Gate pass selection: rank actionable pass and present disambiguation on multiple matches | AUD-003 | M | Backend | Regression suite with returned/current/future passes passes; gate drill shows correct pass 20/20 |
| P0 | Roll-call duplicate prevention: transactional guard + DB constraint, reopen instead of recreate | AUD-002 | M | Backend | Concurrency test passes; repeated-post shows one session |
| P0 | Roll-call integrity: block/audit resolutions on non-in-progress sessions; stop raw `.update()` bypass | AUD-001 | M | Backend | New tests pass; closed-session resolution rejected or audited; existing assertion updated with product sign-off |
| P0 | SMS sender identity: approved-ID resolution precedence, provisioning validation, gateway sandbox test | AUD-006 | S-M | Backend/DevOps | Real sandbox SMS received from the approved sender ID; unit precedence test |
| P0 | Recovery: documented spare-schema restore runbook + staged drill; fix README | AUD-015, AUD-024 | M | DevOps | Drill restores seeded tenant to a scratch schema with verified row counts, within an agreed RTO |
| P0 | Support access decision: remove claim or implement scoped, audited sessions | AUD-016 | S (remove) or L (implement) | Product/Backend | README matches code; if implemented, expiry/scope/revocation tests pass |
| P1 | Early-leave / reschedule workflow with reason + audit, or documented approved exception policy | AUD-004 | M | Product/Backend | Approved reschedule reflected in gate, SMS and audit log; unapproved early exit still blocked |
| P1 | SMS quota lifecycle: expire/fail stale queue rows, segment-aware counting | AUD-007 | M | Backend | Month-old QUEUED row no longer blocks; segment test passes |
| P1 | SMS report reconciliation with quota; include non-exeat events | AUD-008 | S-M | Backend | Golden test: report total = quota usage for same month |
| P1 | Shared "current boarder" queryset across views and reports | AUD-009, AUD-014 | S-M | Backend | Seeded completed student excluded everywhere; three surfaces agree |
| P1 | Add `decided_at`; exclude/report cancellations separately; update staff activity to all actors | AUD-010, AUD-011 | M | Backend/Data | Migration + backfill test; later edits do not shift decision time; tutor/gate-officer actions appear |
| P1 | Overdue register: point-in-time "currently overdue" independent of range | AUD-012 | S | Backend | Test with overdue student outside selected range |
| P1 | Conduct-normalised denominators: period population or explicit relabel | AUD-013 | M | Backend | Golden test with mid-period roster change |
| P1 | Backup encryption at rest, retention/pruning with dry-run, collision-safe filenames | AUD-017 | M | DevOps | Restore from encrypted archive test; pruning dry-run; collision test |
| P1 | Admin hardening: read-only state fields, controlled correction action, audit logging | AUD-018 | M | Backend | Admin protected-field test; correction action writes audit entry |
| P1 | Require rejection reason on all surfaces | AUD-005 | S | Backend/UX | Validation tests on both surfaces |
| P1 | CI gate: Postgres + Redis suite on every push; publish results | AUD-023 | M | DevOps | Green pipeline required to merge; coverage report archived |
| P2 | Connectivity resilience: printable "approved today" and "currently out" lists; outage drill | AUD-021 | M | Product | Drill: gate continues on paper and reconciles after restoration with no lost movements |
| P2 | Guardian/student read-only status and receipts (after school validation) | AUD-020 | L | Product | Discovery sign-off; if built, privacy/security review passes |
| P2 | Bulk SMS / announcements and scheduled report delivery | AUD-021 | L | Product | Pilot school confirms need; quota/cost controls in place |
| P2 | Accessibility pass on gate/office surfaces with real devices | AUD-022 | M | UX/Frontend | Axe/Lighthouse clean on target screens; task test with 5 staff |
| P3 | Permission docstring/dedup cleanup and permissions-matrix test | AUD-019 | S | Backend | Matrix test across six roles |
| P3 | README/runbook overhaul and date-stamping | AUD-024 | S | Docs | Runbook dry-run passes step by step |

**Sequencing rule:** P0 items block the pilot; P1 items block end-of-pilot sign-off and scaling; P2 requires product validation; P3 rides along with related changes.

---

## Deliverable E — Risk Register

Likelihood (L) and Impact (I) scored 1-5; Score = L × I. Residual risk assumes the existing controls only (no new fixes).

| Risk ID | Risk | L | I | Score | Existing controls | Residual exposure | Treatment |
|---|---|---|---|---|---|---|---|
| R-01 | Student exits with an invalid/unverified pass because gate lookup surfaced the wrong pass | 3 | 5 | 15 | QR token, approval state machine, reconciliation | High — safety and trust | AUD-003 P0 |
| R-02 | Attendance records (and derived conduct/attendance reports) proved unreliable because closed sessions were edited | 3 | 4 | 12 | Session locks, follow-up state, some tests | High — disciplinary fairness and audits | AUD-001 P0 |
| R-03 | Guardian never learns about approval/departure because SMS was rejected under the wrong sender | 4 | 4 | 16 | Retry with backoff, delivery status | High — the only guardian channel | AUD-006 P0 |
| R-04 | School cannot send SMS mid-term because stale queue rows consumed quota | 3 | 3 | 9 | Quota limits, entitlements | Medium | AUD-007 P1 |
| R-05 | Prolonged outage after data loss because live-tenant restore cannot be performed in place | 2 | 5 | 10 | Checksums, TOC verification, conservative restore | High tail risk | AUD-015 P0 (runbook + drill) |
| R-06 | Student PII exposed via unencrypted backup archives or retained indefinitely | 2 | 4 | 8 | Filesystem permissions on server/object store | Medium-high | AUD-017 P1 |
| R-07 | Staff or admin silently alters exeat/movement records, undermining disputes | 3 | 3 | 9 | Role checks, reconciliation, admin audit log (incomplete) | Medium | AUD-018 P1 |
| R-08 | Roll-call duplicate sessions split attendance and hide absentees | 3 | 4 | 12 | House scoping, completion flow | High — student supervision | AUD-002 P0 |
| R-09 | Emergency early departure is handled off-system, so records diverge from reality | 4 | 3 | 12 | Reconciliation captures retrospective movement | Medium-high | AUD-004 P1 (plus school policy) |
| R-10 | Occupancy/capacity decisions made on inflated counts | 3 | 3 | 9 | Reports filtered correctly, dashboard filtered | Medium | AUD-009 P1 |
| R-11 | Night gate operations degrade due to dense micro-typography or connectivity loss | 3 | 3 | 9 | 44px targets, offline nothing | Medium | AUD-021/022 P2 |
| R-12 | False confidence in remote support controls because tokens are unwired | 2 | 3 | 6 | CLI verification + platform logs | Low-medium | AUD-016 P0 |
| R-13 | Regression shipped in the uncommitted progression feature because it was never executed | 3 | 4 | 12 | Unit tests exist but unrun | High for the changed area | AUD-023 P1 (run suite + review migration files) |
| R-14 | Headmaster quotes wrong approval/overdue KPIs | 3 | 2 | 6 | Report caveats in text | Low-medium | AUD-010/012 P1 |
| R-15 | Tenant operator follows README and performs an unsupported restore | 2 | 4 | 8 | Restore refusals prevent the worst case | Medium | AUD-024 P0 doc fix |

---

## Deliverable F — Testing Plan

### F.1 Environment and data prerequisites
| Requirement | Detail |
|---|---|
| Database | PostgreSQL 15+ with a dedicated test database; no production connection strings |
| Services | Redis available; Celery worker + beat running for task tests |
| External | SMS pointed at a sandbox/fake gateway for automated runs; one controlled real-sender test in UAT |
| Fixtures | "Atebubu golden dataset": 2 houses, 3 dormitories, ~60 students across all enrollment statuses, 6 staff accounts (one per role), ≥3 exeat scenarios per student type, one completed student |
| Version control | Test against a feature branch that first commits the five untracked files (see Appendix D) so results are reproducible |

### F.2 Test levels
1. **Unit** — services (`roll_call.py`, `sms.py`, `reports.py`, `student_import.py`, `student_progression.py`).
2. **Integration** — view → service → database, including permissions and state transitions.
3. **Regression** — full suite as a required CI gate (closes AUD-023).
4. **UAT** — task-based scripts for each role at the school (Deliverable C workflows).
5. **Operational drills** — gate, roll call, SMS, backup/restore (below).
6. **Non-functional** — security checks, performance smoke, accessibility.

### F.3 Core test cases (mapped to findings)
| Test ID | Objective | Steps sketch | Expected result | Finding |
|---|---|---|---|---|
| T-01 | Closed roll call cannot be silently changed | Complete a session; attempt follow-up resolution | Rejected with error, or an audited correction record is created; original entry history preserved | AUD-001 |
| T-02 | Expired session behaviour | Let expiry task expire a session; attempt resolution via UI and service | Same as T-01; no conduct record created incorrectly | AUD-001 |
| T-03 | Duplicate session prevention | Submit start twice simultaneously (two threads) and sequentially | Exactly one in-progress session; entries not duplicated; second request reopens | AUD-002 |
| T-04 | Gate pass disambiguation | Student with returned pass, active pass, future pending pass; run lookup | Active pass prioritized; multiple actionable matches listed, none auto-selected | AUD-003 |
| T-05 | Departure logged against selected pass | From lookup with multiple matches, log departure | Movement attaches to selected pass only | AUD-003 |
| T-06 | Early departure policy | Attempt gate departure before expected time without approval | Blocked with clear message; no record change | AUD-004 |
| T-07 | Approved reschedule | Approve, reschedule, then depart | Departure allowed at new time; SMS reflects new time; audit entry records actor/reason | AUD-004 |
| T-08 | Reject without reason | POST quick reject with empty reason | Validation error, or structured "unspecified" record with actor | AUD-005 |
| T-09 | Sender resolution precedence | Configure tenant sender; override setting; send sandbox message | Tenant-approved sender used; never a 50-char name; fallback only when configured | AUD-006 |
| T-10 | Provisioning validation | Provision a school with a long name | Stored short name valid for gateway; provisioning fails loudly if invalid | AUD-006 |
| T-11 | Stale queue quota release | Insert month-old QUEUED row; run lifecycle task; attempt send | Quota reflects only legitimate in-flight items; send succeeds | AUD-007 |
| T-12 | Segment counting | Send a 3-segment message | Usage counts 3 segments; report and quota agree | AUD-007 |
| T-13 | SMS report reconciliation | Mixed exeat and non-exeat deliveries in a month; run report with/without filters | Filtered and unfiltered totals reconcile with quota usage | AUD-008 |
| T-14 | Boarder definition consistency | Completed student with dormitory assigned | Excluded from house list, reports, dashboard, welfare watchlist consistently | AUD-009, AUD-014 |
| T-15 | Decision timestamp | Approve; edit the record a week later; run turnaround report | Duration unchanged; cancellation reported separately | AUD-010 |
| T-16 | Staff activity completeness | Tutor records incident; gate officer logs movement; run report | Both actors appear, or the report is explicitly titled as housemaster-only | AUD-011 |
| T-17 | Overdue point-in-time count | Currently-overdue student outside selected date range | "Currently overdue" count includes them; range only affects late-returns-in-period | AUD-012 |
| T-18 | Conduct denominator | Roster change mid-term; run normalised report | Denominator matches period population (or explicit relabel present) | AUD-013 |
| T-19 | Restore contract | Restore dump into existing/registered schema | Refused with safe error unless `--target-schema` break-glass used with confirmation | AUD-015 |
| T-20 | Support claim audit | Grep README claims against code paths | Every claim has an implementation or is removed | AUD-016 |
| T-21 | Backup lifecycle | Back up twice in the same second; apply retention dry-run | Distinct files, no overwrite; pruning plan lists correct archives | AUD-017 |
| T-22 | Admin protection | Attempt to edit status/movement fields in admin | Blocked or routed through audited correction workflow | AUD-018 |
| T-23 | Permission matrix | For each of 6 roles × 12 key actions, assert allow/deny | Matches the documented matrix; no UI link leads to a denied POST | AUD-019 |
| T-24 | Offline gate drill | Disconnect network at the gate after printing lists | Movements continue on paper; reconciliation after reconnection loses nothing | AUD-021 |
| T-25 | Progression feature suite | Run `test_student_progression.py` and migration forward/backward on a copy | Tests pass; migration reversible; no orphaned records | AUD-023 |

### F.4 Operational drills (must be scheduled, not optional)
1. **Gate drill (half day, UAT phase):** 20 scripted students, including one with multiple passes, one early-departure request, one unapproved exit attempt, one QR-scan failure. Success = every movement attributed correctly, zero wrong-pass selections, all exceptions handled by policy.
2. **Roll-call drill (half day):** two simultaneous sessions, one interrupted completion, one absence follow-up on a completed session (must be rejected/audited). Success = counts reconcile with the physical roster.
3. **Disaster-recovery drill (one day):** back up the seeded tenant, destroy the scratch schema, restore via the new runbook, verify row counts, latest exeat movement, attendance entries, and media sample. Success = documented RTO met and signed off by the school's data owner.
4. **SMS pilot batch (controlled):** 10 messages to opt-in staff numbers through the real sender ID; confirm delivery and sender display, then release guardian traffic.
5. **Connectivity drill:** simulate a 2-hour outage; run gate on paper; reconcile; verify no orphan movements.

### F.5 Security and non-functional checks
- Verify every mutating endpoint has `@require_POST`/CSRF where applicable (spot-check shows this is generally true).
- Confirm CSP report-only is promoted to enforce after a clean staging week; collect violation reports first.
- Session expiry (30 minutes) tested idle during roll call and gate flows.
- Rate limiting tested on login and public report page.
- Performance smoke: dashboard and gate terminal on the golden dataset with 2,000 generated movements; target < 2s server render.
- Accessibility: axe-core on gate terminal, roll-call marking, dashboard, reports; task test with five staff including one over 50.

### F.6 Entry / exit criteria for the pilot
- **Entry:** P0 backlog items closed with passing tests; suite green in CI; staging seeded; school policy questions in Appendix A answered; staff trained on the drill scripts.
- **Exit (pilot success):** zero unresolved safety-severity findings; drill results archived; attendance and gate records reconcile with physical counts for four consecutive weeks; SMS delivery rate ≥95% with correct sender; headmaster confirms report numbers match manual counts.

---

## Deliverable G — Implementation Roadmap

No dates are promised; durations are indicative for one experienced full-stack developer with school-staff availability for decisions.

### Phase 0 — Verification and decisions (1-2 weeks)
- Commit or shelve the untracked progression/profession work so the baseline is reproducible (Appendix D).
- Stand up staging with Postgres/Redis; run the full suite; publish the first CI result (AUD-023).
- Answer Appendix A with school leadership; write down the early-departure, late-return, and reconciliation policies.
- **Exit:** green (or explicitly triaged) test suite; signed policy answers; approved P0 scope.

### Phase 1 — Safety and integrity (2-3 weeks)
- AUD-003, AUD-002, AUD-001, AUD-006; plus the AUD-015 runbook and first DR drill.
- Add regression tests T-01–T-05, T-09, T-10, T-19 as part of each fix.
- **Exit:** P0 tests green; gate and roll-call drills pass on staging; approved sender ID demonstrated.

### Phase 2 — Reporting, operations, and control (2-3 weeks)
- AUD-004, AUD-005, AUD-007–AUD-014, AUD-017, AUD-018, AUD-023.
- Golden-dataset report tests; backup encryption/retention; admin hardening.
- **Exit:** P1 tests green; reporting reconciliation signed off by the headmaster on paper vs system.

### Phase 3 — Supervised pilot at Atebubu SHS (3-4 weeks)
- Deploy to the pilot tenant; train roles using Deliverable C; run all five drills.
- Weekly review of gate conflicts, roll-call discrepancies, SMS delivery, and support tickets.
- **Exit:** Deliverable F.6 exit criteria met; go/no-go decision documented.

### Phase 4 — Productisation (6-8 weeks, only after pilot sign-off)
- Guardian/student read-only access decision and implementation (AUD-020), bulk SMS and scheduled reports (AUD-021), accessibility pass (AUD-022).
- Commercial readiness: pricing/entitlement review, onboarding documentation, multi-tenant operational runbooks, quarterly DR drill cadence.
- **Exit:** two-school onboarding rehearsed end-to-end on staging with a named support owner.

**Effort guardrail:** none of the P0/P1 fixes require new services or paid tiers; they are code, configuration, tests, and documentation. This preserves the affordability goal for a Ghanaian SHS.

---

## Deliverable H — Final Product Verdict

### H.1 Ratings (out of 5)
| Dimension | Rating | Reasoning |
|---|---|---|
| Functional coverage | 4.0 | Genuinely broad: lifecycle, exeats, gate, roll call, reports, SMS, tenancy. Missing guardian access and bulk comms are acknowledged gaps, not oversights. |
| Student safety and gate integrity | 3.0 | Strong concepts (one-click terminal, reconciliation) undermined by wrong-pass lookup (AUD-003) and no approved early-leave path (AUD-004). |
| Records and reporting accuracy | 3.0 | Broadest in class for this market, but six report defects and mutable roll-call history mean numbers cannot yet be trusted unconditionally. |
| Reliability and recoverability | 2.5 | Celery retries, checksums and conservative restore are good; no in-place live restore, unencrypted backups, no offline mode. |
| Security and auditability | 3.5 | Solid baseline and role model; admin bypass, CLI-only support claims, and quiet attendance mutation pull it down. |
| Usability | 3.5 | Fast workflows; dense 12px/10px typography and no offline gate experience are real operational risks. |
| Ghanaian SHS suitability | 4.0 | House/dorm model, exeat-with-guardian-SMS, and SMS-first comms match reality; affordability of stack is a strong fit. Only SMS sender correctness and connectivity resilience stand in the way. |
| Commercial readiness | 2.5 | Multi-tenant plumbing exists and is good; recovery, support process, documentation, and product gaps prevent unattended scale. |

### H.2 Verdict statement
**Conditionally approved for a supervised single-school pilot at Atebubu SHS, subject to closing all P0 items and passing the gate, roll-call, SMS and disaster-recovery drills.** The system should **not** be sold or operated as an unattended multi-school commercial service until the P1 items are closed and the Phase 4 readiness work is done. Nothing found in this audit requires abandoning the architecture; every problem identified is fixable within the existing Django/Celery/Postgres stack by one competent developer with school-side policy decisions.

### H.3 Conditions for a green light
1. All P0 backlog items closed with the specified tests passing in CI.
2. School policy questions in Appendix A answered and written down.
3. Gate, roll-call, SMS-sandbox and disaster-recovery drills passed and archived.
4. Reporting reconciliation (headmaster manual count vs system) passed for one full week on staging data.
5. The untracked working-tree work either committed with passing tests or removed from the deployment baseline.

### H.4 What would change the verdict to "not suitable"
- Evidence that the school cannot obtain an approved SMS sender ID; guardian communication is the product's backbone.
- Discovery during drills that gate staff cannot reliably operate the terminal under real night/network conditions even after accessibility and offline-list fixes.
- A decision to operate multiple schools before per-tenant restore, backup encryption, and support-access controls exist.

---

## Appendix A — Questions Requiring School Staff Input

These are not code questions; the answers change what the correct implementation is. Each should be answered in writing by the headmaster or delegated owner before Phase 1 completes.

1. Who may authorize a student leaving before the scheduled departure time, and by what channel (phone, written note, in person)? Should the system allow it at all, or always require a formal reschedule?
2. When a pass is rescheduled, should the old time be preserved in history and shown to the gate? For how long?
3. What is the school's rule when a student returns after the expected time: automatic incident, housemaster discretion, or both? At what delay does escalation occur?
4. Are gate officers permitted to refuse a departure if the pass exists but the schedule has not started? What is the current unwritten practice?
5. Should students with multiple active passes exist at all, or is one active pass per student the intended policy?
6. Who approves medical exeats outside working hours, and how is that captured today?
7. Does the school use SMS alone to contact guardians, or are phone calls/letters also expected? Should guardians be able to reply?
8. Is there demand for guardians to submit exeat requests themselves, or would that create more work for staff?
9. Which staff roles actually man the gate at night and on weekends? Is it always a designated Gate Officer?
10. What is the expected reporting cadence for the headmaster (daily, weekly, termly) and which five numbers matter most?
11. Should attendance corrections be allowed after a roll call closes? If yes, who may do it and what evidence must be recorded?
12. What are the real student supervision expectations during roll call — must every unmarked entry be resolved before the next session starts?
13. What is the school's capacity rule when dormitories exceed capacity: alert only, or block placement?
14. Are completed/withdrawn students ever expected to appear on welfare or infirmary lists (e.g., residual medical follow-up)?
15. How much network downtime does the gate realistically experience in a term, and what do staff do today during outages?
16. What device will the gate use (tablet, phone, desktop)? Screen size, brightness, and whether staff use reading glasses materially affect the accessibility fix.
17. What is the acceptable recovery time after data loss (hours), and who owns the data at the school?
18. Where should backups physically live (on-premises, cloud bucket), and who holds the encryption keys — school or vendor?
19. What is the school's policy on vendor remote support access: forbidden, supervised, or time-boxed? Who approves and who reviews logs?
20. What SMS sender ID is approved by the gateway, and who at the school/vendor owns keeping it valid?
21. Which report definitions does the school already use in its paper records (e.g., "boarder" excludes which statuses)?
22. Is there an existing conduct/attendance appeal process the system must support with evidence?
23. Who trains new staff, and how often does staff turnover occur? (Affects usability and documentation priorities.)
24. Does the school want to send whole-school SMS announcements through this system, and who approves the cost?
25. What are the data-protection expectations for guardian phone numbers and student records (who may export, who may view)?

## Appendix B — Technical Findings vs Assumptions About School Operations

**Technically verified (code is the evidence):** AUD-001 to AUD-019, AUD-023, AUD-024 and all strengths in A.2.

**Assumptions that need school confirmation:** that early departures occur and matter (AUD-004 impact); that guardians depend on SMS and will notice sender identity (AUD-006 severity); that gate connectivity fails often enough to justify offline capability (AUD-021); that the three report definitions the school actually uses match the code (AUD-010 to AUD-014); that admin-only correction is not an accepted practice (AUD-018); that staff will accept a 12px interface (AUD-022); that guardians want a portal (AUD-020).

**Partially verified:** SMS delivery behaviour (code verified, gateway not called); Celery task execution (configuration verified, runtime unobserved); email notifications; the uncommitted progression feature; migration files 0032/0033.

## Appendix C — Verification Log (what the audit actually did)

| Activity | Result |
|---|---|
| Repository mapping and file inventory | Complete |
| Models, views, forms, permissions, admin, services, tasks, settings, middleware review | Complete |
| Template and UI review (base, gate, exeat, roll-call, reports, registry) | Complete |
| Tenancy layer review (provisioning, domains, backup, restore, support access, entitlements, platform audit) | Complete |
| Test suite structure and gap review | Complete; suite not executed |
| `python manage.py check --settings=schoolsystem.settings.development` | Passed, no issues |
| External calls, migrations, production data changes, SMS/email sends | None (prohibited) |
| Source modifications during audit | None; this report is the only file created |

**Test coverage gaps identified for Phase 0:** no test resolves a follow-up against an expired session; no concurrency test for session creation; no gate test with multiple passes for one student; no sender-ID precedence test; no quota staleness test; no report-vs-quota reconciliation test; no admin field-protection test; no performance test; the progression tests are new and unexecuted.

## Appendix D — Baseline Warning: Uncommitted Working Tree

The working tree contains substantial user work beyond `HEAD` (`24afc5c`):

- **Modified (15):** `README.md`, `core/admin.py`, `core/forms.py`, `core/models.py`, `core/services/reports.py`, `core/services/roll_call.py`, `core/services/student_import.py`, `core/tasks.py`, `core/test_student_registry.py`, `core/urls.py`, `core/views.py`, `render.yaml`, `schoolsystem/settings/base.py`, `templates/core/students/record.html`, `templates/core/students/registry.html`
- **Untracked (5):** `core/migrations/0032_academicyear_students_phased_out_at_and_more.py`, `core/migrations/0033_alter_studentprogressionrecord_from_class_group_and_more.py`, `core/services/student_progression.py`, `core/test_student_progression.py`, `templates/core/students/promotion.html`

Consequences: findings describe this working tree, not a released revision; the progression feature is unexercised; deployment of this tree would carry uncommitted migrations. Phase 0 must commit (with passing tests) or set aside this work before pilot fixes begin, so every finding and fix can be tied to an immutable revision.

---

*End of report. Awaiting review and approval before any implementation begins.*



