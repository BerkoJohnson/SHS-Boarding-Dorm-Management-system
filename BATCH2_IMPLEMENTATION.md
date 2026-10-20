# Batch 2 Implementation — Pilot Blockers

**Date:** 2026-10-20
**Branch:** `fix/batch2-pilot-blockers` (from Batch 1 commit `d7b2503`)
**Scope approved:** pilot blockers first, then the full P1 roadmap.
**F-02 policy chosen:** a student may hold only one **Approved and unreturned** pass. Pending
requests do not block by themselves; approval is refused while an active pass exists. Overdue
unreturned passes count.

---

## 1. Delivered in this batch

| Finding | Status | Summary |
| :--- | :--- | :--- |
| **F-02** — multiple active exeats | **Fixed** | `Exeat.is_active`, a per-student partial unique constraint, save-time computation, movement/reconciliation synchronisation, friendly approval errors, and a data migration that refuses to guess when historic duplicates exist. |
| **F-09** — duplicate in-progress roll calls | **Fixed** | Partial unique constraints for dormitory and house-wide sessions (PostgreSQL 14 compatible), plus the existing application lock/reuse path. |
| **F-11** — expiry deletes roll calls | **Fixed** | New `RollCallSession.Status.EXPIRED`; the expiry task marks sessions expired and preserves every entry; starting a new muster expires stale sessions instead of deleting them. Expired records are immutable. |
| **F-32** — no Render worker/Beat | **Explicitly disabled** | The web-only staging blueprint now sets `BACKGROUND_JOBS_ENABLED=False`, emits `core.W003`, uses a no-op Beat scheduler, and rejects `SMS_ENABLED=True` with `core.E004`. README and `render.yaml` list the dormant features and how to enable them. |
| **F-26/F-27** — backup/restore claims and runbook | **Runbook + local drill** | Added `RESTORE_RUNBOOK.md` and completed a real local `tenant_backup` → `tenant_restore` drill. F-26 (offsite/encrypted/scheduled backups) remains open. |

Not in this batch: full P1 items that depend on decisions D1–D8 (emergency authorization,
late-return escalation, attendance corrections, dormitory exceptions, audit/retention policy, etc.).
No policy was invented for those.

---

## 2. F-02 — one active exeat per student

### Model and database

- Added `Exeat.is_active` (indexed boolean).
- Added `UniqueConstraint(fields=["student"], condition=Q(is_active=True),
  name="unique_active_exeat_per_student")`.
- `Exeat.save()` recomputes `is_active` before `full_clean()`:
  - `status != Approved` → inactive;
  - a reconciliation with `unused` or `returned` → inactive;
  - a reconciliation with `student_out` and no return → active;
  - a movement with `actual_return` → inactive;
  - otherwise an Approved pass is active.
- `ExeatMovement.save()` and `ExeatReconciliation.save()` call
  `Exeat.refresh_active_state()` so gate returns, unused reconciliations, and returned
  reconciliations release the student immediately.
- `Exeat.clean()` raises a clear conflict error naming the existing pass and telling staff to
  record the return or cancel it.
- `exeat_action_view` and `exeat_approval_review_view` catch `ValidationError` and
  `IntegrityError`; a concurrent approval returns a friendly message instead of a 500.

### Migration `0034_exeat_active_state_and_roll_call_unique.py`

- Adds `is_active`.
- Backfills existing Approved passes using gate movement and reconciliation records.
- **Refuses to add the constraint if a student has more than one active pass.** The error lists
  the conflicting pass numbers and IDs so staff can record returns/cancel duplicates. It does not
  silently choose or merge records.
- Adds the partial unique constraint after the check passes.

### Tests (`core/test_audit_batch2.py`)

- First approved pass is active.
- Second approval is rejected while the first is unreturned; the second stays Pending.
- Pending requests do not block the first approval.
- Gate return releases the student; a later approval succeeds.
- Overdue unreturned pass still blocks.
- `unused` reconciliation releases; `student_out` keeps it active until a return is recorded.
- Cancelling an active pass releases the student.
- Direct database update to a second active row raises `IntegrityError`.
- The approve action view shows the friendly conflict message.
- Two concurrent approvals leave exactly one active pass (`TransactionTestCase`).

---

## 3. F-09/F-11 — roll-call uniqueness and preservation

### Model

- Added `RollCallSession.Status.EXPIRED` (`"Expired", "Expired - unfinished (records retained)"`).
- `is_expired` is true for `status=Expired` or an In Progress session past `expires_at`.
- Partial unique constraints:
  - `(house, dormitory, session_type, date)` where `status='In Progress'` and dormitory is not
    null;
  - `(house, session_type, date)` where `status='In Progress'` and dormitory is null.
- The two-constraint form deliberately avoids `NULLS NOT DISTINCT`, which would require
  PostgreSQL 15; the project documents PostgreSQL 14 as the minimum.
- `RollCallEntry.clean()` now blocks edits to entries in expired sessions, matching the existing
  completed-session lock.

### Service and task

- `create_roll_call_session`:
  - keeps the house row lock and `select_for_update` session query;
  - marks stale In Progress sessions `Expired` with an explanatory note instead of deleting them;
  - returns a genuinely active session if one exists;
  - creates a new session only when no active session exists.
- `_expire_unfinished_roll_calls_for_current_tenant`:
  - marks sessions `Expired` and preserves entries;
  - notification now says the records were **preserved for review**;
  - returns an `expired_count`, not a deletion count.
- Templates display an `Expired` badge and state that records are preserved and locked.
- Existing manual deletion remains available to the owner/admin as an explicit staff action; the
  scheduled task no longer destroys evidence.

### Migration

- Marks stale In Progress sessions `Expired` before adding the constraints.
- Refuses to add the constraints if duplicate non-stale In Progress sessions remain, listing the
  conflicting house/dormitory/type/date groups. It does not merge or delete them.

### Tests

- Retained full suite coverage for the existing duplicate-start guard.
- Database-level duplicate rejection for house-wide and dormitory sessions.
- A new session is allowed after completion.
- House-wide and dormitory sessions remain distinct.
- Expiry preserves the session, its entries, and the absence follow-up; notification says
  "preserved".
- Starting a new muster expires the stale session.
- Expired sessions reject entry updates, follow-up resolution, and completion.

---

## 4. F-32 — explicit web-only deployment

The user selected **“Keep web-only, document disabled jobs”** rather than enabling paid Render
worker/Beat services.

- `BACKGROUND_JOBS_ENABLED` (default `true`) is parsed in `schoolsystem/settings/base.py`.
- When false:
  - `CELERY_BEAT_SCHEDULE` is empty;
  - `CELERY_BEAT_SCHEDULER` points to `schoolsystem.beat.DisabledScheduler`, which loads no
    periodic rows from `django_celery_beat`;
  - `core/checks.py` emits warning `core.W003`;
  - if `SMS_ENABLED=True`, it emits error `core.E004` because deliveries would queue forever;
  - `CoreConfig.ready()` logs the disabled-feature warning.
- `render.yaml` now sets `BACKGROUND_JOBS_ENABLED=False` with a comment listing the dormant
  features, and the bottom comment explains exactly how to enable paid worker/Beat later.
- The Render build command now runs `manage.py check` before `collectstatic`, so `core.E004`
  aborts a deploy that enables SMS while background jobs are disabled.
- README sections 8.1, 8.3, 8.4, 8.6, 9.4, 9.5, and 10.3 were updated, including manual commands
  for roll-call expiry, ID-export cleanup, and SHS 3 phase-out while jobs are dormant.
- Verified:
  - `manage.py check` with `BACKGROUND_JOBS_ENABLED=False`, `SMS_ENABLED=False` → 1 warning
    (`core.W003`);
  - with `SMS_ENABLED=True` → warning + error `core.E004` and a non-zero exit;
  - normal settings (background jobs enabled) → no background-job checks.

**Operational consequence:** on the current staging blueprint, queued SMS dispatch, overdue
reminders, roll-call expiry, ID-export cleanup, and SHS 3 phase-out do not run automatically.
SMS must remain disabled. Do not enable SMS until paid worker + Beat services exist and
`BACKGROUND_JOBS_ENABLED=True` is set on all services.

---

## 5. F-26/F-27 — restore runbook and local drill

Added `RESTORE_RUNBOOK.md` with:

- supported vs unsupported restore paths;
- exact backup, dry-run, restore, validation, registration, refusal, and cleanup commands;
- live recovery options (full database restore preferred; per-tenant restore into a fresh
  database; shared-database limitations);
- media restore notes;
- rollback and security guidance;
- local drill evidence.

### Drill result (2026-10-20)

- PostgreSQL 18.3 disposable local cluster; source `restore_drill_source`, target
  `restore_drill_target`, schema `restore_drill`.
- `tenant_backup --verify` succeeded: 135,535-byte dump, SHA-256
  `7c64d33eec5556e9dcef6b00717b391365d0d27e4900d5db5745230a3d2093a3`, `verified=true`.
- `tenant_restore --dry-run` passed; real restore returned `status=success` in 1.57 s.
- Source and target both: 1 student, 81 migrations, `SchoolConfiguration.name` =
  `Restore Drill School`.
- After registering `Tenant`/`Domain`, `tenant_list` showed 1 student and
  `tenant_usage --schema=restore_drill` succeeded.
- A second restore was refused: `Schema 'restore_drill' already exists and is not empty.
  Refusing to restore into a nonempty schema.`
- Media was not exercised because the drill tenant had no media files. A staging drill with a
  real school and media archive is still required.

F-26 remains open: backups are local, plaintext, unscheduled, and have no retention/offsite
policy. The drill proves the restore command, not the backup durability controls.

---

## 6. Verification

Commands used (local disposable PostgreSQL, port 55432; `.env` temporarily isolated for the
production-settings tests because the restored local `.env` sets `SECURE_HSTS_SECONDS=0`):

```powershell
$env:DATABASE_PROFILE='local'
$env:DB_NAME='schoolsystem_test_local'
$env:DB_USER='postgres'
$env:DB_PASSWORD=''
$env:DB_HOST='127.0.0.1'
$env:DB_PORT='55432'
$env:DB_SSLMODE='disable'
$env:TENANCY_ENABLED='false'

.\\.venv\\Scripts\\python.exe manage.py test core \
  --settings=schoolsystem.settings.test
```

Results:

- Batch 2 suite: **22 tests OK**.
- Batch 1 + active-exeat + reports subset: **56 tests OK**.
- Full `core` suite: **345 tests OK** in 883.225 s (final run after all Batch 2 code changes;
  executed 2026-10-20 with the local disposable PostgreSQL test database).
- `makemigrations --check --dry-run`: **No changes detected**.
- `manage.py check`: no issues with normal settings; expected `core.W003`/`core.E004` behavior
  verified with background jobs disabled.

---

## 7. Residual risks and blocked work

1. **Historic duplicate cleanup is manual.** Migration `0034` aborts with a report if existing
   active passes or non-stale in-progress roll calls conflict. This is intentional; no records are
   merged or deleted silently. A platform operator must resolve them and rerun the migration.
2. **`is_active` is denormalised.** Model saves and movement/reconciliation saves keep it in sync.
   Direct `QuerySet.update()` calls that bypass those hooks can drift the flag; the migration
   backfill and constraint catch most cases, but an audit/repair command would be a useful
   follow-up.
3. **Post-closure attendance corrections remain deferred to F-12.** Expired sessions are
   immutable and preserved; there is no authorised correction workflow yet.
4. **F-32 is deliberately not deployed.** The staging blueprint is web-only. SMS and all scheduled
   jobs are dormant until paid worker + Beat services are added and `BACKGROUND_JOBS_ENABLED=True`
   is set. Queue-depth and last-run/heartbeat monitoring are still absent; the checks and manual-run
   commands make the state visible but do not alert on a stopped worker.
5. **F-26 backups remain local/plaintext/unscheduled.** Encryption, offsite storage, retention, and
   scheduling are still open. The local drill is not a staging drill and did not test media.
6. **Full P1 work is decision-blocked.** D1–D8 (emergency authorization, late-return escalation,
   attendance correction, dormitory exceptions, audit/retention rules, etc.) still need answers.
7. **Credential rotation and remote branch cleanup are user-owned.** The exposed
   `origin/feature/student-registration-placement-workflow` branch and the credentials from
   `.env.batch1_full_backup` must still be rotated/removed by the user; Batch 2 does not touch them.
8. **`core-regression` CI has not run on GitHub yet.** The workflow includes the full `core`
   suite, so the new tests will run there once pushed.

---

## 8. Next steps

1. Push `fix/batch2-pilot-blockers` and let the `core-regression` job run on GitHub.
2. Run migration `0034` on staging **after** resolving any duplicate active exeats/roll calls it
   reports; verify the new constraints exist.
3. Run the staging restore drill in `RESTORE_RUNBOOK.md` with a real school backup and media
   archive; record the evidence.
4. Decide D1–D8, then start the remaining Full P1 work (no-policy correctness fixes first).
5. If the school approves paid Render resources, add worker + Beat, set
   `BACKGROUND_JOBS_ENABLED=True`, and enable SMS only after an end-to-end queue test.
6. Rotate the exposed credentials and finish the remote branch cleanup (user-owned).
