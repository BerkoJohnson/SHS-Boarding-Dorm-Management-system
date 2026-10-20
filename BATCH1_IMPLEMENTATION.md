# Batch 1 implementation log

**Project:** SHS Boarding & Dorm Management System  
**Audit reference:** `AUDIT_REPORT_SHS_2026-10-19.md`  
**Implementation date:** 20 October 2026  
**Baseline:** `8c490e0`  
**Approval:** Batch 1 was approved after the 19 October 2026 audit checkpoint.  
**Schema changes:** None.

---

## 1. Scope delivered

| Finding | Status | Change | Files |
|---|---|---|---|
| **F-01** — gate lookup could show or act on the wrong pass | **Fixed** | An exact pass-number scan is still honoured so staff can see why a pending, cancelled, or rejected pass cannot be used. Admission/index/partial searches now return only approved, unreturned passes. Multiple matches are ranked and shown as a disambiguation list instead of being silently reduced to the first row. The card has an explicit “no approved pass” state and a two-character minimum. | `core/views.py`, `templates/core/operations/partials/gate_verification_card.html` |
| **F-09** — duplicate roll-call sessions | **Fixed at application level** | Session creation takes a PostgreSQL transaction, locks the house row, and reuses an existing non-expired in-progress session for the same house/dormitory/type/date. Expired sessions do not block a fresh muster. The view explains that the open muster was reused. A database partial unique constraint is deliberately deferred because Batch 1 has no schema changes. | `core/services/roll_call.py`, `core/views.py` |
| **F-10** — closed-session attendance could be changed | **Fixed (mutation blocked)** | `resolve_absence_follow_up()` now locks the session and rejects completed or expired sessions before any follow-up or entry update. The HTMX view returns a clear error and leaves the database unchanged. A separate audited post-closure correction workflow is still required and remains out of scope for Batch 1. | `core/services/roll_call.py`, `core/tests.py` |
| **F-16** — SMS sender ID bypass | **Fixed** | The SMS payload now uses `_sms_sender_id()`, which prefers the configured `SMS_SENDER_ID`. The school short name is only a fallback. Senders are normalised to the GSM 03.38 alphanumeric format, limited to 11 characters, and every normalisation is logged. | `core/services/sms.py` |
| **F-33** — no automated core regression gate | **Added** | A `core-regression` GitHub Actions job starts PostgreSQL 16 and Redis 7, runs the full `core` suite, runs `manage.py check`, and runs the production deployment check with warnings treated as non-fatal. The job has not been executed on GitHub from this environment. | `.github/workflows/tenant-postgres-integration.yml` |
| **F-27/F-28** — misleading support/restore documentation | **Corrected** | The README now states that `support_access` is a CLI-only approval/audit token, not remote access, and that `tenant_restore` restores into a new unregistered schema rather than repairing a live schema in place. | `README.md` |

## 2. Behaviour staff will notice

- Starting a second roll call for the same house/dormitory/type/date returns the open session and shows an “already open / reused” message instead of creating a duplicate roster.
- Starting a new muster after a session is completed still works normally. Expired in-progress sessions do not block a fresh muster.
- A pending absence must be resolved before the session is finalised. After completion or expiry, the follow-up action returns a lock error and does not change attendance. **There is not yet a post-closure correction workflow**, so staff must complete the absence review before finalising. This gap is documented for Batch 2/F-12.
- Gate search no longer silently chooses a future or pending pass. If more than one approved pass matches, staff must choose the correct pass from the disambiguation list.
- SMS sender IDs now come from `SMS_SENDER_ID`, not the school short name. If no sender is configured, the short name is used as a fallback. Invalid characters are removed and the result is limited to 11 characters.

## 3. Regression tests added

`core/test_audit_batch1.py` contains 16 tests:

| Area | Tests | What is protected |
|---|---|---|
| Gate lookup (F-01) | 5 | Exact pass scan; approved-only broad search; pending-pass exclusion; disambiguation; currently-out ranking; no-match state. |
| Duplicate roll call (F-09) | 5 | Reuse of an in-progress session; distinct house/dormitory sessions; new session after completion; concurrent starts create one session; view message. |
| Closed/expired follow-up (F-10) | 4 | Service rejection; HTMX rejection with no mutation; expired-session rejection; completed-session rejection. |
| SMS sender (F-16) | 2 | Configured sender wins; invalid configured sender is sanitised and truncated. |

One existing report test was updated because it encoded the old duplicate-session behaviour: it created two identical evening sessions to represent two absences. It now creates a morning and an evening session for the same day, which preserves the report assertion while respecting the new rule.

## 4. Verification evidence

### 4.1 Environment

- Disposable local PostgreSQL cluster on `127.0.0.1:55432` (localhost only, trust authentication). The cluster lives under the approved temporary directory and is removed after verification.
- The clean full-suite run temporarily moved `.env` out of the project so a local HSTS override could not mask the production-default test. `.env` was restored afterwards; `.env.*` backup files are now git-ignored (`.env.example` remains tracked).
- Redis was not installed locally. The Batch 1 tests do not dispatch live SMS or email; the SMS HTTP call is mocked.
- No live SMS, email, production database, or external production service was used for the final verification.

### 4.2 Commands and results

| Command | Result |
|---|---|
| `python manage.py test core.test_audit_batch1 --settings=schoolsystem.settings.test --noinput --keepdb` | **16 tests passed** in 56.319 s on the local PostgreSQL cluster. |
| `python manage.py test core.tests.HouseScopedAccessTests.test_housemaster_reports_and_printable_output_stay_in_assigned_house --settings=schoolsystem.settings.test --noinput --keepdb` | **1 test passed** in 5.311 s after the report test was updated. |
| `python manage.py check --settings=schoolsystem.settings.test` | Passed: no issues. |
| Full `core` suite with `--settings=schoolsystem.settings.test --noinput --keepdb` | First run: 323 tests, 2 failures. One failure was the old duplicate-session report test, which was corrected and passed on rerun. The other was the known local `.env` HSTS override; that test passes when `.env` is not loaded. |
| Clean full `core` suite with `.env` temporarily hidden and a freshly recreated local test database | **323 tests passed** in 739.023 s; `OK`. |
| `python manage.py check --deploy --settings=schoolsystem.settings.production --fail-level ERROR` with dummy Neon-style environment variables | Passed (exit 0) with W005/W021 warnings; no secrets were used. |
| `python -m compileall -q` on changed Python files | Passed. |
| `git diff --check` | No whitespace errors. |

> The first clean full-suite attempt was invalidated by an orphaned `core.test_audit_batch1` process from an earlier harness run. It held database locks and caused a flush deadlock plus a cascading duplicate-key error. The process was terminated, the test database was recreated, and the isolated rerun above passed.

### 4.3 What was not run

- The new GitHub Actions `core-regression` job has not been executed on GitHub. The local full-suite run is the available evidence.
- The multi-tenant `tenancy` suite was not re-run locally in this batch; the existing CI job still covers it on PostgreSQL with Redis.
- Live SMSOnlineGH sender-ID acceptance, delivery receipts, and balance API behaviour.
- Live backup/restore, offsite upload, or a production restore drill.
- Android/device accessibility testing, load testing, or penetration testing.
- A production deployment check with real production secrets. The deployment check used dummy values only.

## 5. Residual risks and deferred work

- Duplicate-session protection is application-level and depends on PostgreSQL row locking. Existing duplicate rows are not cleaned up or merged. A partial unique constraint and a data-cleanup migration belong in a later approved batch.
- The one-hour roll-call timeout still deletes unfinished sessions (F-11). Batch 1 does not change that timer.
- There is no post-closure correction workflow (F-12). Staff must resolve pending absences before finalising; after completion or expiry the records are intentionally locked.
- Multiple active exeats (F-02), late-return incidents, dormitory overcapacity, worker/Beat deployment, backup encryption/offsite/live restore, monitoring, retention, and support-session integration remain unresolved.
- The SMS sender is deployment-level, not tenant-configurable. A per-school approved sender registry remains future work.
- The full CI job is new and unproven on GitHub. Its first run should be watched before relying on it as a release gate.
- **Security incident:** a temporary `.env` backup created while the clean full-suite run was in progress was accidentally included in commit `0365c0a` and pushed to the remote branch `feature/student-registration-placement-workflow`. The file was removed from the local `fix/audit-pilot-remediation` commit and `.env.*` is now git-ignored, but the remote branch history still contains the secret. Rotate every credential in `.env` immediately and rewrite/remove the remote branch with owner approval. Do not push the old commit again.

## 6. Approval boundary

No Batch 2 work has started. The next batch should be selected only after this log, the clean full-suite evidence, and the remaining open decisions (D1–D8) are reviewed.
