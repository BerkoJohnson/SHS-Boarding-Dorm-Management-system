# Mobile API contract gap analysis

- **Status:** analysis and contract correction, not an implementation sign-off
- **Date:** 2026-10-27
- **Branch inspected:** `staging/render-preview`
- **Primary contract:** `docs/API.md`
- **Route inventory:** `core/api/urls.py`

This document separates what is verified in this checkout from what is still an
assumption or a client requirement. It supersedes the broad wording in the
earlier gap summary where that wording overstated or understated the code.

## 1. Method and evidence

“Verified” means directly observed in this checkout, including tests. “Not
implemented” means a repository-wide search found no implementation in the
Python code or migrations. “Assumption” means a client or product requirement
that cannot be settled from this repository.

The Android client is **not in this checkout**. Any statement about what the app
actually needs, renders, or retries is an assumption unless it is written in
`docs/API.md`.

## 2. Verified status

| Area | Verified position | Gap or correction |
| :--- | :--- | :--- |
| Roll-call corrections | Model, request/review services, evidence rule, reviewer separation, in-app notifications, audit events, and closed-session rejection exist. | No v1 correction routes. The `cancelled` status has no service or route. The service itself trusts the caller for role/house scope; the web view supplies that scope. |
| Report export | CSV, XLSX, and PDF are produced synchronously; `DataExportLog` and `AuditEvent` are written; house scope is applied. | No report-template metadata and no API asynchronous or signed download flow. Scheduled report email exists outside the mobile API. |
| Report and audit filters | Reports support `report`, `date_from`, `date_to`, and `term`; audit supports `action`, `date_from`, and `date_to`. Housemasters are scoped to their house. | No caller-selected house, form/class, or status report filters. No house/form/status audit filters. Point-in-time reports accept date/term filters but ignore them through the API. |
| Gate verification and pass tokens | Verify/depart/return exist; movements are idempotent and server-recorded. The QR identifier is a rotatable UUID `public_token` with optional expiry. | The token is not signed or short-lived; expiry defaults to never. Gate-duty enforcement is bypassed by the API. Two documented gate claims do not match current behavior. |
| Notifications and FCM | Notification feeds and device registration/deactivation exist. Logout deactivates push registrations. | No FCM/APNs sender, provider configuration, push outbox, retry task, or delivery audit exists in this checkout. |
| Authorization and audit | Tenant-bound bearer tokens, role checks, house scoping, and `AuditEvent` model methods exist. Gate-duty web routes and the model exist. | Audit immutability is application-level only. No mobile gate-duty routes. API gate endpoints do not call `can_log_gate_check`. |
| Student cards | The API returns the student summary, `public_token`, expiry, public URL, and layout choices. | No separate card number or issue-date field in the response or the student model. No issue/reissue lifecycle beyond QR rotation. |

## 3. Area detail

### 3.1 Roll-call corrections

**Verified**

- `RollCallCorrection` stores the previous and requested status, remarks, reason,
  evidence, requester, reviewer, timestamps, and a `requires_second_approval`
  flag (`core/models.py`).
- A partial unique constraint allows only one pending correction per entry.
- `request_roll_call_correction()` and `review_roll_call_correction()` implement
  the workflow (`core/services/roll_call_corrections.py`).
- Present/Absent changes and conduct-linked entries require evidence.
- The reviewer must be an administrator or Senior Housemaster and cannot be the
  requester.
- Requests and decisions create `AuditEvent` rows; request and decision also
  create in-app `StaffNotification` rows.
- Ordinary entry updates on completed or expired sessions raise a state error
  from `update_entry_status()` and are returned by the API as `409
  roll_call_state_error`.
- Web routes exist for request, list, and review under
  `core/views.py`/`core/urls.py`.

**Gaps**

- `core/api/urls.py` has no correction request, list, detail, or decision route.
- The model exposes `RollCallCorrection.Status.CANCELLED`, but no service or web
  route transitions a request to cancelled. Do not describe cancellation as
  implemented.
- The request service does not enforce the caller’s role or house scope; the
  review service checks the reviewer role but not the reviewer’s house. The web
  views filter on `entry__session__house_id` before calling either service. An
  API must do the same, or the services must be hardened for API use.
- A duplicate pending request is protected by the database constraint, but the
  service does not translate `IntegrityError` into a domain error. The API must
  map it to a stable conflict response.
- The reviewer identity must come from the bearer token, never from the request
  body.

**Assumptions**

- The client needs list, detail, request, and decision operations. This is likely
  because the API already exposes every other roll-call action.
- Whether the client needs a requester-cancel operation is unknown; the model
  suggests it was intended, but no workflow exists.
- Whether housemasters should see the evidence text in list responses is a
  privacy decision; the current web review page shows it to authorized viewers.

### 3.2 Report export

**Verified**

- `core/api/views/reports.py` calls
  `core/services/report_export.export_report_response()`.
- The service builds CSV, XLSX, and PDF responses and sets an attachment
  `Content-Disposition`.
- Each export writes a `DataExportLog` row and an `audit` `AuditEvent` with row
  count and filters.
- Housemasters receive only their assigned house’s report data.
- CSV values are guarded against spreadsheet formula injection; XLSX uses the
  same guard.
- A scheduled report email service exists in `core/services/scheduled_reports.py`
  and a Celery task, but it is a web subscription feature, not an API download
  flow.

**Gaps**

- No report-template metadata (template id, version, owner, layout) is stored or
  returned.
- The API export is a direct authenticated HTTP response. There is no stored
  export job, signed URL, expiry, or download-history endpoint.
- The export endpoint is a `GET`; repeated calls create repeated export audit
  rows. That is defensible for an audit trail, but it is not idempotent.

**Assumptions**

- The client may only need the direct file response. Template metadata and
  asynchronous/signed downloads are unverified requirements.
- If asynchronous export is required, it needs a new export-job model, storage,
  a task, a status endpoint, and a short-lived download authorization model.
  That is a separate workstream, not a small change to the existing view.

### 3.3 Report and audit filters

**Verified**

- `ReportFilterForm` supports `report`, `date_from`, `date_to`, and `term`.
- It validates dates and rejects an end date before the start date.
- The report choice list contains 17 report types.
- `REPORTS_WITHOUT_PERIOD_FILTERS` defines the point-in-time reports:
  `housing`, `placement_exceptions`, `room_register`, and `data_completeness`.
- The web dashboard disables date/term controls for those reports.
- The API accepts date/term for every report and passes them to the service;
  point-in-time report functions simply do not consume them.
- Housemaster scope comes from `get_house_scope()`; administrators and Senior
  Housemasters receive school-wide data.
- The audit endpoint supports `action`, `date_from`, and `date_to` only.
- `AuditEvent` has `target_type`, `target_id`, and `metadata`, but no house,
  form, or status field.

**Gaps**

- No caller-selected house filter. A school-wide caller cannot narrow a report to
  one house, and a housemaster cannot widen beyond the assigned house.
- No form/class or status filter on report rows.
- No house/form/status audit filters.
- The API silently accepts date/term on point-in-time reports. This is a
  contract ambiguity: the web UI disables the controls, but the API does not
  reject or flag them.
- “Form” is ambiguous. It could mean `ClassGroup` (the student’s class) or a
  derived SHS form level. The client contract must choose one before a filter is
  added.

**Assumptions**

- The client may only need the existing filters. Adding a house selector changes
  the authorization model and should not be done speculatively.
- If a caller-selected house filter is added, the server must intersect it with
  the caller’s scope, never replace the scope.

### 3.4 Gate verification and pass tokens

**Verified**

- `POST /gate/verify/`, `POST /gate/depart/`, and `POST /gate/return/` exist.
- Depart and return are wrapped with `@idempotent` and call serialized movement
  services; a duplicate without a key returns `409 movement_recorded`.
- The student QR identifier is an opaque UUID `public_token`. It can be rotated
  through the API.
- `public_token_expires_at` is optional. `STUDENT_ID_PUBLIC_TOKEN_TTL_DAYS`
  defaults to `0`, meaning a newly rotated token never expires.
- The approved `Exeat` is the pass record; depart/return use `exeat_id`.
- `can_log_gate_check()` enforces gate-duty assignment when
  `OperationalSettings.gate_duty_enforcement_enabled` is on. The web gate views
  call it.
- Gate-duty assignment models and web create/cancel routes exist.

**Gaps**

- The API gate endpoints check only the `A/G` role. They do **not** call
  `can_log_gate_check()`. A Gate Officer with no active assignment can call the
  API even when the school has enabled enforcement. This is a verified
  authorization gap.
- No mobile API routes expose gate-duty assignments. Whether the mobile client
  needs to list its own shifts or manage assignments is unconfirmed.
- `docs/API.md` says ambiguous admission/index matches return `409
  ambiguous_code` with candidates. The implementation uses `.first()` for
  admission/index and silently picks one; only a partial **exeat number** returns
  a `200` ambiguous list.
- `docs/API.md` says every scan is audited. `STUDENT_SCAN` is written only after
  a successful student QR or admission/index resolution. Exeat-number matches,
  ambiguous lists, no-match results, and expired QR scans do not currently write
  an audit event.
- Gate movement recording updates `ExeatMovement` or `ExeatReconciliation`; it
  does not write a separate `AuditEvent` row. The old documentation’s claim that
  gate movements are recorded in `AuditEvent` was inaccurate.
- The QR token is not a signed, short-lived pass token. It is a revocable opaque
  identifier, and its expiry can be unset. If the requirement truly demands
  signed, short-lived tokens, that is a new token model and migration.

**Assumptions**

- Whether a revocable UUID with optional expiry is sufficient is a product and
  security decision. The current code does not prove the requirement.
- If signed tokens are required, the design must cover key rotation, TTL,
  replay, revocation, printed-card migration, and gate error responses before
  implementation.
- Whether gate-duty enforcement should apply to `verify` (a read/scan action) or
  only to `depart`/`return` (state changes) needs a decision. Applying it to
  depart/return is the minimum consistent with the model help text.

### 3.5 Notifications and push

**Verified**

- `GET /notifications/`, `POST /notifications/read-all/`, and
  `POST /notifications/<id>/read/` work with namespaced ids.
- `GET/POST /devices/` and `PATCH/DELETE /devices/<id>/` register and manage
  `PushDevice` rows.
- Logout deactivates the device associated with the current token; logout-all
  deactivates all of the user’s push devices.
- There is no FCM/APNs SDK, provider client, credentials, sender, outbox, retry
  worker, or push delivery audit in the Python code or requirements.

**Gaps**

- Registering a token does not cause a push to be delivered. The only working
  delivery path is in-app notification rows plus client polling.
- There is no invalid-token handling, retry/backoff, delivery receipt, or
  per-tenant provider configuration.
- `docs/API.md` describes push registration and says push may be used as a
  prompt, but the backend does not yet implement the send side. The document
  should not imply a delivery guarantee.

**Assumptions**

- FCM is the likely Android provider; APNs is only relevant if an iOS client is
  in scope.
- A delivery SLA, retry budget, and whether an outbox is required are product
  decisions. Reliable delivery needs an outbox and a worker, not an inline send
  in the request path.

### 3.6 Authorization and audit

**Verified**

- Bearer tokens are tenant-bound and stored per tenant schema; browser sessions
  are not accepted by the API.
- Role checks, superuser bypass, feature gates, and house-scoped querysets are
  implemented in `core/api/base.py`, `core/api/auth.py`, and
  `core/permissions.py`.
- `AuditEvent.save()` rejects edits to an existing row and `delete()` rejects
  deletion. `record()` is the normal creation path.
- Gate-duty assignment is validated by the model and managed through web routes.

**Gaps**

- Audit immutability is application-level only. A queryset `.update()` or direct
  SQL can still modify rows; no database trigger, permission, or constraint
  proves immutability at the database layer.
- The API gate-duty bypass described above is the main authorization gap found in
  this pass.
- There are no mobile gate-duty routes, so mobile lifecycle coverage is
  incomplete if the client needs to view or manage shifts.

**Assumptions**

- Database-level immutability and retention enforcement are deployment/security
  requirements, not established by the current model methods.
- A database trigger may complicate fixture loading and tests; it needs an
  explicit migration and rollback plan.

### 3.7 Student cards

**Verified**

- `GET /students/<id>/id-card/` returns:
  - the student summary, including `admission_no` and `index_number`;
  - `public_token`;
  - `public_token_expires_at`;
  - `public_qr_url`;
  - `layouts` (`classic`, `crest`, `modern`).
- `POST /students/<id>/rotate-public-token/` rotates the token and returns the
  new value and URL.
- The `Student` model has no `card_number`, `card_issued_at`, or card-specific
  expiry field. The public-token expiry is a QR-link expiry, not necessarily a
  card expiry.

**Gaps**

- No separate card number or issue date is returned or stored.
- There is no card issue/reissue lifecycle beyond rotating the QR token.
- No print batch, card status, or replacement reason is recorded.

**Assumptions**

- The school may intend `admission_no` to be the card number. That is not stated
  in the API contract and should not be assumed by the client.
- Card ownership, reissue semantics, and whether a card expiry is distinct from
  the QR expiry must be decided before a schema change.

## 4. Corrections to the earlier summary

| Earlier statement | Corrected position |
| :--- | :--- |
| “Status transitions” exist for corrections. | Pending, approved, and rejected transitions exist. `cancelled` exists only as a model choice; no service or route uses it. |
| Requests and decisions send notifications. | They create in-app `StaffNotification` rows. There is no push delivery. |
| Report export records audit data. | It writes both `DataExportLog` and `AuditEvent`; the response is synchronous and direct. |
| Report date filters are missing. | They are implemented for reports that consume a period. The narrower true statement is that point-in-time reports ignore them and the API does not reject them. |
| The QR identifier is a UUID with optional expiry. | Correct. It is not signed or short-lived by default; the default TTL is `0` (never expires). |
| FCM delivery is unverified. | Stronger: no sender or delivery mechanism exists in this checkout. Only registration and in-app feeds exist. |
| Audit events are append-only. | Model-enforced append-only at the application layer; database-level immutability is not proven. |
| Gate movements are recorded in the audit trail. | Movement timestamps live on `ExeatMovement`/`ExeatReconciliation`; they are not separate `AuditEvent` rows. |
| Student cards have no card number or issue date. | Correct for both the API response and the model. |
| Gate duty has web routes/models but no mobile routes. | Correct, and the API also bypasses the enforcement helper. |
| Report filters lack house/form/status. | Correct, and audit filters also lack house/form/status. |

## 5. Decisions required before implementation

These are blocking for the affected workstream. They should be recorded in the
contract or an ADR, not inferred from the current code.

| ID | Decision | Why it blocks |
| :--- | :--- | :--- |
| D1 | Confirm the Android client’s remaining requirements and acceptance criteria, including exact payloads and roles. | The client repository is not available; endpoint shape and error codes cannot be validated against it. |
| D2 | Define report filter semantics: caller-selected house, form/class meaning, status filter, and whether point-in-time reports must reject or ignore period filters. | Adding filters changes authorization and report-service behavior. |
| D3 | Decide whether the gate token remains a revocable UUID with optional expiry or becomes a signed, short-lived token. | This is a token-model and printed-card migration, not a serializer change. |
| D4 | Define push provider(s), credentials, event payloads, retry budget, and delivery SLA. | No provider or outbox exists; reliable delivery needs infrastructure. |
| D5 | Define card number, issue date, card expiry, ownership, and reissue semantics. | A schema change without ownership rules creates duplicate identifiers and printing problems. |
| D6 | Decide whether audit immutability must be enforced at the database layer and what retention applies. | The current model methods do not stop queryset updates or direct SQL. |
| D7 | Decide whether correction cancellation is required and who may cancel. | The status exists but no service or route does. |
| D8 | Decide whether gate-duty enforcement applies to verification as well as departure/return. | The current API applies neither. |

## 6. Backend-first plan and dependencies

### Phase 0 - Freeze the contract (blocking)

Resolve D1-D8. Produce a short contract addendum with endpoint lists, roles,
payloads, status codes, scope rules, and explicit “not in v1” items. Do not start
schema or client work before D2-D6 are decided.

**Exit criteria:** signed-off contract addendum; the Android client confirms the
same behaviors.

### Phase 1 - Publish the corrected API contract (this pass)

- Correct `docs/API.md` where it overstates gate scan auditing and ambiguous
  admission/index handling.
- Document the actual correction, report-filter, push, and card surfaces.
- Mark missing capabilities as “not in v1” rather than implying they exist.
- Keep the route inventory in `core/api/urls.py` as the implementation reference.

**Dependencies:** none, but final wording for D2-D5 waits on those decisions.
**Exit criteria:** no documented claim contradicts a test or a code path.

### Phase 2 - Expose roll-call corrections through the API

Reuse `RollCallCorrection` and `core/services/roll_call_corrections.py`; do not
create a parallel workflow.

Suggested surface:

| Method | Path | Roles | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/roll-call/corrections/` | A/S/H | List corrections, house-scoped for H; filters `status`, `session_id`, `student_id`. |
| `GET` | `/roll-call/corrections/<id>/` | A/S/H | Detail, including evidence for authorized reviewers. |
| `POST` | `/roll-call/sessions/<session_id>/entries/<entry_id>/corrections/` | A/S/H/T | Request a correction for a closed/expired entry. |
| `POST` | `/roll-call/corrections/<id>/decision/` | A/S | Approve or reject; reviewer from the token. |
| `POST` | `/roll-call/corrections/<id>/cancel/` | Requester or A/S | Only if D7 requires it; add a service transition and audit event first. |

Implementation requirements:

- Scope every lookup with `filter_queryset_by_house(..., "entry__session__house_id")`.
- Reject a request on an open session with `409 roll_call_state_error`.
- Reject a duplicate pending request with `409 correction_pending`; also catch
  the database `IntegrityError` for the concurrent case.
- Reject self-review in the service and map it to `403 approval_forbidden`.
- Require evidence for Present/Absent and conduct-linked changes; return field
  errors for missing evidence.
- Re-check that the entry still matches `previous_status` at decision time.
- Apply `@idempotent` to create and decision; the idempotency key is scoped to the
  token and path.
- Record the requester and reviewer from `request.api_user`, never from JSON.
- Return `AuditEvent`-backed history or at least the correction status/timestamps;
  do not expose unrelated audit metadata.

**Dependencies:** D1, D7. No migration is needed unless cancellation or new
fields are added.
**Acceptance:** closed-session rules, open-session rejection, evidence,
self-review, duplicate pending, concurrent duplicate, stale-entry rejection,
house boundary, tenant boundary, idempotent replay, notifications, and audit
events are covered by API tests.

### Phase 3 - Complete the reporting contract

1. Document every report type and whether it consumes date/term.
2. Decide whether the API rejects or explicitly ignores period filters on
   point-in-time reports. Prefer a clear `400` or a documented `meta` flag over
   silent acceptance.
3. Add only the filters approved in D2. For a caller-selected house, intersect
   with `get_house_scope()`; never let a housemaster widen scope.
4. Keep CSV/XLSX/PDF behavior and both audit records. Add template metadata only
   if the client needs it.
5. If asynchronous or signed downloads are required, design a separate export
   job/artifact/download-token flow. Do not overload the existing synchronous
   endpoint.
6. Add audit filters only where the data model can support them; house/status
   filters likely need denormalized fields or a join and should not be inferred
   from free-text metadata.

**Dependencies:** D2, D6.
**Acceptance:** invalid dates and inverted ranges fail clearly; unknown report
types fail; point-in-time semantics are explicit; house/tenant boundaries cannot
be crossed; export audit rows contain the same filters the caller used.

### Phase 4 - Close gate authorization and token gaps

1. Call `can_log_gate_check()` in the API depart/return endpoints when the
   setting is enabled. Return a stable `403 gate_duty_required` (or the agreed
   code), and add tests for enforcement on/off and the administrator bypass.
2. Decide whether `verify` also requires an active assignment (D8).
3. Add mobile gate-duty read routes only if the client needs them. Reuse the
   existing assignment model and web validation; do not duplicate overlap or
   approver rules.
4. Fix the two contract mismatches:
   - detect duplicate admission/index matches and return a defined ambiguity
     response, or document the first-match behavior; and
   - either audit every verification outcome with safe metadata or narrow the
     documentation to successful student scans.
5. If signed tokens are required (D3), design and migrate them separately. The
   current UUID is a public identifier, not a pass token; do not describe it as
   signed or short-lived.

**Dependencies:** D3, D8.
**Acceptance:** API and web enforce the same gate-duty rule; failed scans have
defined audit behavior; ambiguity never silently selects the wrong student;
token rotation/revocation tests pass.

### Phase 5 - Implement push delivery

1. Add a provider abstraction and one concrete provider for the approved
   platform. FCM HTTP v1 is the usual Android choice; APNs is separate.
2. Add a durable outbox model with tenant, user, device, event key, payload,
   status, attempts, next attempt, provider message id, and last error.
3. Enqueue outbox rows when `StaffNotification`/`RollCallNotification` are
   created, preferably through one service hook rather than scattered signals.
4. Add a Celery task with retry/backoff, invalid-token deactivation, and
   per-tenant provider credentials.
5. Keep `/devices/` as the registration surface; add no client-visible push
   secrets.
6. Define the SLA as queued/best-effort, not guaranteed. The client must keep
   polling `/notifications/` and `/dashboard/`.

**Dependencies:** D4; FCM/APNs credentials; Celery worker and beat in the target
environment.
**Acceptance:** registration and rotation; logout deactivation; outbox dedupe;
retry and terminal failure; invalid-token removal; tenant isolation; no guardian
phone numbers or sensitive payloads in push bodies.

### Phase 6 - Card metadata (only if required)

If D5 confirms a separate card identity:

- Add card number, issue date, optional card expiry, issuing staff, and reissue
  reason/status to the student or a card-issue model.
- Define uniqueness per tenant and the relationship to `admission_no`.
- Add issue/reissue endpoints, audit events, and print-batch metadata.
- Decide whether card expiry is independent of `public_token_expires_at`.
- Migrate existing cards and keep QR rotation working.

**Dependencies:** D5, `student_id_cards` feature.
**Acceptance:** card number uniqueness, reissue history, printed-card migration,
tenant isolation, and role tests.

### Phase 7 - Client integration and end-to-end verification

- Publish the agreed contract and generate or hand-maintain client types.
- Run the Android client against a tenant test environment, not a shared host.
- Verify login/refresh, house scope, correction workflow, report filters and
  exports, gate enforcement, notification polling, and device logout.
- Run targeted API suites, then the full core suite.
- If D6 requires it, add database-level audit protection in a separate,
  reversible migration and test it against fixtures.

**Dependencies:** Phases 1-6 and the Android client.
**Exit criteria:** the client can perform every agreed flow using only documented
endpoints; no endpoint relies on undocumented behavior; audit and scope tests
pass on a tenant test environment.

## 7. Recommended sequence

```text
D1-D8 decisions
    |
    +--> Phase 1 contract correction (can start with current wording)
    |
    +--> Phase 2 corrections API (backend-ready; highest confidence)
    |
    +--> Phase 3 reporting contract (after D2)
    |
    +--> Phase 4 gate authorization/token (after D3, D8)
    |
    +--> Phase 5 push delivery (after D4 and credentials)
    |
    +--> Phase 6 card metadata (after D5)
    |
    +--> Phase 7 client E2E (after all in-scope phases)
```

## 8. What not to do

- Do not build a second correction workflow beside `RollCallCorrection`.
- Do not add a caller-selected house filter without intersecting it with the
  caller’s existing scope.
- Do not describe the UUID `public_token` as a signed or short-lived pass token.
- Do not add push registration and claim delivery until an outbox and sender
  exist.
- Do not add a card number that duplicates `admission_no` without an explicit
  ownership decision.
- Do not rely on `AuditEvent.save()` as proof of database immutability.
- Do not let the API and web diverge on gate-duty enforcement.
