# Mobile API v1

The staff mobile API is a Django-native JSON API mounted at **`/api/v1/`** on
each school's verified hostname. It is intentionally not built on Django REST
Framework: the URL map, request parsing, role checks, envelopes and pagination
live in `core/api/` so the web application and the mobile API share the same
models, services and permission constants.

> **Tenant rule:** every request must go to the school's own hostname. A bearer
> token issued by School A must never work on School B. The app stores the
> school's canonical origin during onboarding and must never accept a
> user-supplied base URL afterwards.

---

## 1. Tenant binding and URL pinning

1. During onboarding the app resolves the school through one of:
   - `GET https://<candidate-school-host>/api/v1/public/school/` — identity of
     the school served by that host.
   - `GET https://<marketing-host>/api/v1/public/schools/?q=<name>` — the
     central directory of active schools with verified primary domains. This
     endpoint lives on the public marketing host and touches no tenant data.
2. The response includes `canonical_origin` and `api_base_url`. The app stores
   **both**, then pins every subsequent request to that origin.
3. Login, refresh and `GET /meta/` also return the canonical origin. If the
   origin changes (for example, a school migrates from `school.example.org` to
   `portal.school.example.org`), the app must require a fresh onboarding rather
   than silently following a new host.

Hostnames are matched by `tenancy.VerifiedDomainRoutingMiddleware` against the
`Domain` table. Unknown hosts, inactive tenants and unverified domains receive a
JSON error for `/api/` paths instead of the web 404 page. The only
unauthenticated school-host path is `public/school/`; the public directory
`public/schools/` is served from the marketing host.

### Tokens

- Opaque bearer tokens; the raw value is shown **once** at login, refresh, or
  invite redemption.
- Stored in the school's schema as a SHA-256 hash plus a short display prefix.
  A database dump cannot recover a usable token.
- Default lifetime: **30 days** (`API_TOKEN_TTL_DAYS`).
- `POST /auth/refresh/` rotates the token and revokes the old one. The app must
  replace its stored token atomically.
- Maximum active tokens per user: **8** (`API_TOKEN_MAX_PER_USER`). Logging in
  on a ninth device revokes the oldest token.
- Session cookies are never accepted. A browser session alone gets
  `401 authentication_required`.
- Send `Authorization: Bearer <token>` and `Accept: application/json`.

### Biometric login

The server never receives fingerprint or face data. Biometric login is a local
unlock of the token already stored in the platform secure store:

- Android: Android Keystore-backed `EncryptedSharedPreferences` or equivalent.
- iOS: Keychain item with `kSecAccessControlBiometryCurrentSet` (or
  `BiometryAny` if the school accepts enrolment changes).
- Always keep the password/PIN fallback. If biometric enrolment changes and the
  secure item is invalidated, fall back to a normal login.
- Call `POST /auth/refresh/` on a schedule or before expiry. A revoked or
  expired token must send the user back to the login screen, not to a different
  school host.

---

## 2. Envelopes, errors and metadata

### Success

```json
{
  "data": { "...": "..." },
  "meta": {
    "request_id": "8f14e45fceea167a5a36dedd4bea2543",
    "server_time": "2026-10-23T09:15:00.123456+00:00",
    "school_timezone": "Africa/Accra",
    "api_version": "v1"
  }
}
```

Paginated collections add:

```json
"pagination": {
  "page": 1,
  "page_size": 25,
  "total": 132,
  "pages": 6,
  "has_next": true,
  "has_previous": false
}
```

### Errors

```json
{
  "error": {
    "code": "validation_error",
    "message": "The request could not be processed.",
    "request_id": "8f14e45fceea167a5a36dedd4bea2543",
    "fields": { "expected_return": ["Enter a valid ISO 8601 timestamp."] }
  }
}
```

- `X-Request-ID` is always returned. Supply `X-Request-ID` to correlate logs.
- `fields` is present only for field-level validation errors.
- Common codes: `authentication_required`, `role_forbidden`,
  `feature_unavailable`, `not_found`, `validation_error`, `conflict`,
  `rate_limited`, `upgrade_required`, `api_disabled`, `role_mismatch`,
  `account_not_ready`, `house_scope_denied`, `approval_forbidden`,
  `roll_call_state_error`, `gate_state_error`.
- Status codes: `200`, `201`, `204`, `400`, `401`, `403`, `404`, `405`, `409`,
  `426`, `429`, `503`.

### Common request rules

- All bodies are JSON objects. Empty bodies are accepted for action endpoints.
- Datetimes are ISO 8601 with an offset, for example
  `2026-10-24T14:30:00+00:00`. Dates are `YYYY-MM-DD`.
- IDs are UUID strings.
- Pagination: `?page=1&page_size=25` (max `API_MAX_PAGE_SIZE`, default 100).
- Ordering: `?ordering=full_name,-created_at`; only documented fields are
  accepted, and unknown fields return `400 invalid_ordering`.
- App version gate: send `X-App-Version: 1.4.0`. If
  `API_MIN_APP_VERSION` is configured and the supplied version is older, the
  API returns `426 upgrade_required`.

### Idempotency

State-changing POSTs that can be retried accept an `Idempotency-Key` header
(any opaque string, e.g. a UUID generated per user action):

```http
Idempotency-Key: 0f7e5c3a-8b1e-4f4f-9c3f-2b1a0d9e8c7b
```

Within the replay window (`API_IDEMPOTENCY_TTL`, default 24 hours) a replay
returns the original status and body with `Idempotent-Replayed: true`. A
concurrent duplicate gets `409 request_in_progress`. The key is scoped to the
token, path and key value, so reusing a key on a different endpoint is safe.

Idempotent endpoints include: exeat create/approve/reject/override/cancel/
reschedule/reconcile/review, gate depart/return, roll-call actions, student room
allocation, and SMS campaign creation/decisions.

### Feature gates

Roles are checked first, then subscription features. Feature keys:

`roll_call`, `exeat_gate`, `student_id_cards`, `sms_dispatch`,
`roster_import`, `advanced_reports`, `priority_support`.

A school without `exeat_gate` receives `403 feature_unavailable` from exeat and
gate endpoints even if the user's role is correct. When tenancy is disabled
(single-school deployment), every feature is treated as enabled.

---

## 3. Roles and scoping

| Role | Typical use |
| :--- | :--- |
| `ADMIN` | School-wide administration, invites, all records |
| `SENIOR_HOUSEMASTER` | School-wide student/exeat oversight |
| `HOUSEMASTER` | Own house's students, exeats, roll call |
| `HOUSE_TUTOR` | Own house's roll call; can request exeats, cannot approve |
| `GATE_OFFICER` | Gate verification and movements; no student registry |
| `MEDICAL` | Medical exeats only; can approve medical requests |

Scoping is enforced server-side by `core.permissions`:

- `filter_queryset_by_house()` scopes students, exeats and roll-call sessions.
- `get_approvable_pending_for_user()` scopes the approval queue: admins and
  senior housemasters see all; housemasters see their house; the medical lead
  sees medical passes only.
- Gate officers see school-wide gate data but cannot list the student registry
  or create/approve exeats.
- A user with `UNASSIGNED` role cannot log in (`403 account_not_ready`).
- The `role` sent during login is a UX hint. If it differs from the stored
  `UserProfile.role`, login fails with `403 role_mismatch`.

---

## 4. Endpoint reference

Role abbreviations: **A** = admin, **S** = senior housemaster, **H** =
housemaster, **T** = house tutor, **G** = gate officer, **M** = medical lead.

### 4.1 Onboarding and session lifecycle

| Method | Path | Auth | Roles | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/public/school/` | No | — | School identity for this host |
| `GET` | `/public/schools/` | No | — | Public school directory (marketing host) |
| `POST` | `/auth/invites/validate/` | No | — | Preview an invite code |
| `POST` | `/auth/invites/redeem/` | No | — | Create the staff account and sign in |
| `POST` | `/auth/login/` | No | — | Password login |
| `POST` | `/auth/refresh/` | Bearer | Any | Rotate the bearer token |
| `POST` | `/auth/logout/` | Bearer | Any | Revoke this device token |
| `POST` | `/auth/logout-all/` | Bearer | Any | Revoke every token for this user |
| `GET` | `/auth/me/` | Bearer | Any | Current user, school, features, unread counts |
| `POST` | `/auth/password/` | Bearer | Any | Change password; other tokens revoked |
| `GET` | `/auth/devices/` | Bearer | Any | List active device sessions |
| `DELETE` | `/auth/devices/<token_id>/` | Bearer | Any | Revoke one device session |
| `GET` | `/staff/invites/` | Bearer | A | List invites (`?status=open|accepted|revoked|expired`) |
| `POST` | `/staff/invites/` | Bearer | A | Create an invite; returns raw `code` once |
| `DELETE` | `/staff/invites/<invite_id>/` | Bearer | A | Revoke an unaccepted invite |

**Invite validation**

```http
POST /api/v1/auth/invites/validate/
Content-Type: application/json

{"code": "EDEN-ABCD-EFGH"}
```

Returns role, name hints, house (if any), expiry and school identity.

**Invite redemption**

```http
POST /api/v1/auth/invites/redeem/
Content-Type: application/json

{
  "code": "EDEN-ABCD-EFGH",
  "username": "ama.mensah",
  "password": "…",
  "first_name": "Ama",
  "last_name": "Mensah",
  "phone_number": "+233240000000"
}
```

Returns the same session payload as login with `201`. Invite codes are hashed at
rest, single-use, and expire after `API_INVITE_TTL_DAYS` (default 7). House
roles require a house on the invite; house staff and senior housemasters require
a phone number.

**Login**

```http
POST /api/v1/auth/login/
Content-Type: application/json

{
  "username": "ama.mensah",
  "password": "…",
  "role": "HOUSEMASTER",
  "device": {
    "device_id": "android-installation-id",
    "device_name": "Pixel 8",
    "platform": "android",
    "app_version": "1.4.0"
  }
}
```

`role` is optional. `device_id` is strongly recommended: logging in again with
the same device id replaces that device's previous token.

**Session payload**

```json
{
  "token": "raw-opaque-token",
  "token_type": "Bearer",
  "expires_at": "2026-11-22T09:15:00+00:00",
  "canonical_origin": "https://school.example.org",
  "user": { "id": "…", "full_name": "…", "role": "HOUSEMASTER", "capabilities": { } },
  "school": { "name": "…", "short_name": "…", "logo_url": null },
  "features": { "roll_call": true, "exeat_gate": true }
}
```

**Change password**

```json
{"current_password": "…", "new_password": "…"}
```

Uses Django's configured password validators. All other tokens are revoked;
the current token stays valid.

### 4.2 Bootstrap, reference and home

| Method | Path | Auth | Roles | Purpose |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/meta/` | Bearer | Any | User, school, canonical origin, features, enums |
| `GET` | `/reference/` | Bearer | Any | Terms, years, houses, dormitories, rooms, programmes, classes, staff |
| `GET` | `/dashboard/` | Bearer | Any | Role-aware home summary |
| `GET` | `/notifications/` | Bearer | Any | Merged staff + roll-call notification feed |
| `POST` | `/notifications/read-all/` | Bearer | Any | Mark all (or one `kind`) read |
| `POST` | `/notifications/<id>/read/` | Bearer | Any | Mark one notification read |

`/dashboard/` returns only sections the role may see:

- `scope` — `school_wide` and the assigned `house` (if any).
- `students` — totals/enrolled/boarding/on-exeat for A/S/H (house-scoped for H).
- `exeats` — active, on-pass, overdue, today's departures/returns, upcoming;
  `pending_approvals` and `latest_pending` for approvers.
- `gate` — on-pass and overdue counts for A/G.
- `roll_call` — in-progress sessions, unmarked entries, latest session for
  A/S/H/T.
- `notifications` — unread counts and the five newest items.

`/reference/` limits houses, dormitories, rooms, staff, and student counts to
the assigned house for house-scoped staff. School-wide administrators and
senior housemasters receive school-wide structure. Other roles without a house
assignment receive no house structure; academic terms and class/programme
choices remain school-wide reference data.

Notification IDs are namespaced: `staff:<uuid>` and `roll_call:<uuid>`. A bare
UUID is also accepted for convenience. Each item has `source`, `kind`,
`kind_display`, `title`, `message`, `url`, `read_at`, `is_read`, `created_at`.
Feed filters: `?unread=true`, `?kind=late_return|exeat_review|roll_call|…`,
`?page=`, `?page_size=`.

`POST /notifications/read-all/` accepts an optional body
`{"kind": "late_return"}` to mark only one kind.

Detailed reports are available at `GET /reports/` to administrators, senior
housemasters, and housemasters; housemasters receive only their assigned
house's data. Supported filters are `report` (default `housing`), `date_from`,
`date_to`, and `term`, plus `page` and `page_size` (maximum 100). There is no
caller-selected `house`, `class_group`/form, or `status` filter in v1. The
response contains report headers, the current rows, and the total
`record_count`.

The point-in-time reports `housing`, `placement_exceptions`, `room_register`,
and `data_completeness` do not consume `date_from`, `date_to`, or `term`. The
API currently accepts those parameters but they have no effect; the web
dashboard disables the controls. Invalid dates or an inverted range return
`400 validation_error`.

`GET /reports/export/?format=csv|xlsx|pdf` returns the file directly and records
both a `DataExportLog` row and an `export` `AuditEvent`. There is no
report-template metadata, asynchronous export job, or signed download URL in
v1.

`GET /audit/events/` is restricted to administrators and senior housemasters.
It supports `action`, `date_from`, and `date_to` filters only; it has no
house/form/status filter. It returns paginated event summaries and deliberately
omits raw before/after payloads and metadata.

Only administrators can use the guardian SMS campaign endpoints. Submit to
`POST /sms/campaigns/` with `{"scope":"all_boarders","message":"…"}` or
`{"scope":"house","house_id":"…","message":"…"}`. A different administrator
must approve a pending campaign through
`POST /sms/campaigns/<id>/decision/`; the existing server SMS configuration,
quota checks, durable delivery queue, and audit trail remain authoritative.
The mobile API never returns guardian phone numbers.

### 4.3 Students

| Method | Path | Roles | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/students/` | A/S/H | Paginated registry, house-scoped for H |
| `GET` | `/students/search/?q=` | A/S/H | Fast search (name/admission/index, max 20) |
| `GET` | `/students/lookup/?q=` | A/S/H | Resolve scanned QR/URL or admission/index number |
| `GET` | `/students/options/` | A/S | Filter choices: houses, dormitories, rooms, programmes, classes |
| `GET` | `/students/allocation-options/` | A/S | Dormitory rooms only for mobile placement |
| `GET` | `/students/<id>/` | A/S/H | Detail plus five recent exeats |
| `POST` | `/students/<id>/allocation/` | A/S | Assign a boarding student to a validated dormitory room |
| `GET` | `/students/<id>/photo/` | A/S/H | Private photo stream (`FileResponse`) |
| `GET` | `/students/<id>/id-card/` | A/S | ID-card metadata and QR URL |
| `POST` | `/students/<id>/rotate-public-token/` | A/S | Reissue the QR token |

`GET /students/<id>/id-card/` returns the nested student summary (including
`admission_no` and `index_number`), `public_token`, `public_token_expires_at`,
`public_qr_url`, and `layouts`. It does not return a separate card number or
issue date, and the student model stores neither. Card issuance and reissue
metadata beyond QR rotation are not implemented.

List filters: `q`, `house_id`, `dormitory_id`, `class_group_id`,
`programme_id`, `status`, `residency`, `gender`,
`has_active_exeat=true|false`. Ordering: `full_name`, `admission_no`,
`created_at`, `updated_at`, `house__name`.

Room allocation accepts both `dormitory_id` and `room_id`. The server validates
that the room belongs to that dormitory and that house, gender, dormitory
capacity, and room capacity constraints hold. Successful moves are recorded in
the audit trail.

**QR and barcode handling.** The QR on a student ID card encodes an opaque
student `public_token` UUID, normally inside a public verification URL. The app
must treat the payload as an identifier only and resolve it against the pinned
tenant origin:

```http
GET /api/v1/students/lookup/?q=<scanned-value>
```

The server extracts the first UUID, checks expiry, records a `STUDENT_SCAN`
audit event and returns the student summary. Admission/index numbers typed by
older staff follow the same endpoint. Never trust a name or URL embedded in a
QR code.

### 4.4 Gate operations

| Method | Path | Roles | Purpose |
| :--- | :--- | :--- | :--- |
| `POST` | `/gate/verify/` | A/G | Resolve a card/QR/exeat/admission/index code |
| `POST` | `/gate/depart/` | A/G | Record departure against an exeat |
| `POST` | `/gate/return/` | A/G | Record return against an exeat |

**Verify** accepts exactly one `code`:

```json
{"code": "SHS-EXEAT-2026-00042"}
```

It tries, in order: student QR UUID, exeat number, admission number, index
number. A partial pass number can return `match: "ambiguous"` with
`candidates` (HTTP `200`). Admission and index numbers are exact-match lookups;
if legacy data contains duplicates, the current implementation returns the
first match rather than `409 ambiguous_code`. This is a known contract
mismatch. A successful response includes `match`, `student`, `exeat`,
`movement_state`, `can_depart`, `can_return` and `warnings`.

`STUDENT_SCAN` is written only after a successful student QR or admission/index
resolution. Exeat-number matches, ambiguous lists, no-match results, and
expired QR scans do not currently write an audit event. Treat audit coverage as
scoped to successful student scans until this is corrected.

**Depart / return** accept `{"exeat_id": "<uuid>"}`. Departure requires an
approved pass whose scheduled departure has arrived. Return requires a recorded
departure (missed departures must go through reconciliation). Both are
idempotent and return the updated exeat plus movement timestamps. A second
attempt without an `Idempotency-Key` returns `409 movement_recorded`.

The student QR payload is an opaque UUID `public_token`, not a signed or
short-lived pass token. It can be rotated, and its expiry is optional:
`STUDENT_ID_PUBLIC_TOKEN_TTL_DAYS` defaults to `0` (never expires). The pass
itself is the approved `Exeat` record; depart/return use `exeat_id`.

When `OperationalSettings.gate_duty_enforcement_enabled` is on, the web gate
workflow requires an active `GateDutyAssignment` through
`can_log_gate_check()`. The v1 API gate endpoints currently check only the
`A/G` role and do not call that helper, so a Gate Officer without an active
assignment can still use `/gate/depart/` and `/gate/return/` through the API.
This is a known authorization gap, not a client assumption.

### 4.5 Exeats

| Method | Path | Roles | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/exeats/` | A/S/H/T/M | Scoped list with filters |
| `POST` | `/exeats/` | A/S/H/T | Create a pending request |
| `GET` | `/exeats/<id>/` | A/S/H/T/M | Detail |
| `POST` | `/exeats/<id>/approve/` | A/S/H/M | Approve a normal pass |
| `POST` | `/exeats/<id>/reject/` | A/S/H/M | Reject with a required reason |
| `POST` | `/exeats/<id>/override/` | A/S/H/M | Emergency approval + 24h review |
| `POST` | `/exeats/<id>/cancel/` | A/S/H/M | Cancel an approved/pending pass |
| `POST` | `/exeats/<id>/reschedule/` | A/S/H/M | Change departure/return times |
| `POST` | `/exeats/<id>/reconcile/` | A/S/H/M | Reconcile a missed gate scan |
| `POST` | `/exeats/<id>/review/` | A/S | Decide an exeat review case |

**Create**

```json
{
  "student_id": "…",
  "exeat_type": "Town",
  "expected_departure": "2026-10-24T14:30:00+00:00",
  "expected_return": "2026-10-24T20:00:00+00:00",
  "destination": "Accra Mall",
  "reason": "Family visit",
  "academic_term_id": "…"
}
```

`academic_term_id` is optional and defaults to the current term when omitted.
The server validates term rules, one active pass per student, and house scope.
A student outside the caller's house returns `404`.

List filters: `status`, `exeat_type`, `student_id`, `house_id`,
`academic_term_id`, `q`, `active=true|false`, `from`, `to`,
`bucket=pending_approvals|active|overdue`, plus pagination and ordering.
Approvers can use the API to see only what they may act on via
`get_approvable_pending_for_user`; the dashboard exposes that queue directly.

Approval of an `Emergency` pass is refused with `409 override_required`; use
`/override/` with a reason so the 24-hour review is created. `reject` requires
`approval_remarks` (or `reason`). `reschedule` requires both timestamps and
records a schedule change. `reconcile` requires `outcome` (`student_out` or
`returned`), a reason, and the actual return time when applicable; see the
endpoint's validation errors for the exact shape. `review` accepts `outcome`
and `notes` and is limited to administrators and senior housemasters.

### 4.6 Roll call

| Method | Path | Roles | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/roll-call/sessions/` | A/S/H/T | List sessions, house-scoped |
| `POST` | `/roll-call/sessions/` | A/S/H/T | Start (or reuse) a session |
| `GET` | `/roll-call/sessions/<id>/` | A/S/H/T | Session plus every roster entry |
| `POST` | `/roll-call/sessions/<id>/entries/<entry_id>/` | A/S/H/T | Set one entry's status |
| `POST` | `/roll-call/sessions/<id>/mark-all-present/` | A/S/H/T | Correct all `Absent` to `Present` |
| `POST` | `/roll-call/sessions/<id>/complete/` | A/S/H/T | Finalize the muster |

Start:

```json
{
  "house_id": "…",
  "session_type": "Evening",
  "inspection_date": "2026-10-23",
  "dormitory_id": null,
  "notes": ""
}
```

`house_id` is required for school-wide roles and rejected if it differs from a
housemaster's or tutor's assigned house. Starting the same house/type/date while
an in-progress session exists returns that session with `200` and
`meta.reused_existing: true` instead of creating a duplicate.

Entry update:

```json
{"status": "Present", "remarks": "Seen at muster."}
```

Valid statuses: `Unmarked`, `Present`, `Absent`, `On Exeat`, `Infirmary`,
`Activity`. The API wraps `core.services.roll_call.update_entry_status`, so gate
conflicts and absence follow-ups keep their existing semantics. A completed or
expired session returns `409 roll_call_state_error`.

`mark-all-present` only corrects entries currently marked `Absent`; it does not
guess at unmarked entries. `complete` refuses while any entry is `Unmarked` or
has an unreviewed gate conflict (`409`). House tutors can run their house's
muster but cannot approve exeats or use gate controls.

**Post-closure corrections.** The API exposes the existing correction workflow
(`RollCallCorrection`, `core/services/roll_call_corrections.py`):

| Method | Path | Roles | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/roll-call/corrections/` | A/S/H | List corrections; house-scoped for H; filters `status`, `session_id`, `student_id` |
| `GET` | `/roll-call/corrections/<id>/` | A/S/H | Detail, including evidence, remarks, entry, and session |
| `POST` | `/roll-call/sessions/<session_id>/entries/<entry_id>/corrections/` | A/S/H/T | Request a correction for a completed/expired entry |
| `POST` | `/roll-call/corrections/<id>/decision/` | A/S | Approve or reject a pending correction |

Request body:

```json
{
  "requested_status": "Present",
  "requested_remarks": "Seen at the dormitory.",
  "reason": "The student was present during the muster.",
  "evidence": "Dormitory register signed by the tutor."
}
```

Only completed or expired sessions accept corrections; an open session returns
`409 roll_call_state_error`. Direct entry updates on a completed or expired
session remain blocked with the same code. Present/Absent and conduct-linked
changes require evidence (`400 validation_error` with an `evidence` field
error). A second pending request for the same entry returns
`409 correction_pending`. The requester comes from the bearer token.

Decision body:

```json
{"decision": "approve", "review_note": "Evidence checked."}
```

`decision` must be `approve` or `reject`. The reviewer comes from the token and
cannot be the requester; self-review returns `403 approval_forbidden`. A stale
entry returns `409 correction_stale`; a correction that is already decided
returns `409 correction_decided`. Both request and decision accept an
`Idempotency-Key`. Cancellation is not in v1: the model's `cancelled` status
remains unused.

### 4.7 Push devices

| Method | Path | Roles | Purpose |
| :--- | :--- | :--- | :--- |
| `GET` | `/devices/` | Any | List active push registrations |
| `POST` | `/devices/` | Any | Register/update an FCM/APNs token |
| `PATCH` | `/devices/<id>/` | Any | Update `app_version` / `is_active` |
| `DELETE` | `/devices/<id>/` | Any | Deactivate a push registration |

Register:

```json
{
  "token": "fcm-or-apns-token",
  "platform": "android",
  "device_id": "android-installation-id",
  "app_version": "1.4.0"
}
```

Registering a new token for the same `device_id` deactivates the old token.
Logout deactivates the push device associated with the current API token;
logout-all deactivates all of the user's push devices.

Registration and logout cleanup are implemented. This checkout contains no
FCM/APNs sender, provider credentials, push outbox, retry task, or delivery
audit. A registered token does not cause a push to be delivered. Until the
send side is implemented, clients must poll `/notifications/` and `/dashboard/`;
push is a planned backend workstream, not a delivery guarantee.

### 4.8 Public onboarding endpoints

`GET /public/school/` (no token) returns the school identity, canonical origin,
API base URL, feature flags and API version for the host that served the
request. Use it to confirm a candidate school URL before login.

`GET /public/schools/?q=` (no token) returns up to
`API_SCHOOL_DIRECTORY_LIMIT` active schools with verified primary domains:

```json
[
  {
    "name": "Example Senior High School",
    "short_name": "",
    "domain": "school.example.org",
    "api_base_url": "https://school.example.org/api/v1"
  }
]
```

The directory is served from the marketing host and deliberately exposes no
per-tenant data. In single-school mode it returns the current host as the sole
entry.

---

## 5. Background tracking and push

The app should treat the API as the source of truth. When push is available, use
it only as a prompt to refresh, never as the only copy of an alert.

- Poll `/dashboard/` when the app returns to the foreground and after a push.
- Poll `/notifications/?unread=true` for the review queue and roll-call expiry
  alerts.
- Use `/devices/` to register the FCM/APNs token after login and after a token
  refresh. Tokens can rotate at any time; re-register on app start.
- Gate movements are online-only in v1. Do not queue a departure/return
  offline: retry with the same `Idempotency-Key` once connectivity returns.
- Exeat and roll-call state transitions are idempotent where documented, so a
  timed-out request can be retried safely with the same key.
- The server does not yet send push notifications. Polling `/notifications/`
  and `/dashboard/` is the only guaranteed delivery path in v1.
- When push delivery is implemented, payloads should carry an opaque
  notification id (`staff:<uuid>` or `roll_call:<uuid>`) and a type; the app
  resolves details through the API on the pinned origin.

---

## 6. Operational notes

- API access is controlled by `API_ENABLED`. Disabling it returns
  `503 api_disabled` on every API endpoint while leaving the web application
  untouched.
- Rate limits use the shared cache. Login attempts are limited per client IP +
  username, and invite validation/redemption per client IP
  (`API_LOGIN_RATE_LIMIT` / `API_LOGIN_RATE_WINDOW`); public onboarding uses
  `API_PUBLIC_RATE_LIMIT` / `API_PUBLIC_RATE_WINDOW`.
- Uploads are capped by `API_MAX_UPLOAD_BYTES` (default 10 MiB).
- All timestamps are ISO 8601. The client should render in
  `meta.school_timezone`, which reports the server's configured `TIME_ZONE`.
- The API returns JSON for unknown `/api/v1/` routes and for tenant-resolution
  failures on `/api/` paths, so clients never have to parse HTML error pages.
- Student photos are private. Use the authenticated photo endpoint; do not
  cache or share the URL outside the app's secure storage.
- Successful student scans, invite actions, token revocations, correction
  requests and decisions, exports, and gate-duty assignment changes are recorded
  in `AuditEvent`. Gate movement timestamps are stored on `ExeatMovement` and
  `ExeatReconciliation`, not as separate `AuditEvent` rows. Exeat-number,
  ambiguous, no-match, and expired-QR scan outcomes are not currently audited.

---

## 7. Quick smoke test

```bash
# 1. Confirm the host and pin the origin
curl -s https://school.example.org/api/v1/public/school/

# 2. Sign in
curl -s -X POST https://school.example.org/api/v1/auth/login/ \
  -H 'Content-Type: application/json' \
  -H 'X-App-Version: 1.4.0' \
  -d '{"username":"ama.mensah","password":"…","role":"HOUSEMASTER",
       "device":{"device_id":"curl-test","platform":"android","app_version":"1.4.0"}}'

# 3. Use the returned token
curl -s https://school.example.org/api/v1/dashboard/ \
  -H "Authorization: Bearer $TOKEN"

# 4. Scan a student ID QR / type an admission number
curl -s "https://school.example.org/api/v1/students/lookup/?q=$SCANNED_VALUE" \
  -H "Authorization: Bearer $TOKEN"
```
