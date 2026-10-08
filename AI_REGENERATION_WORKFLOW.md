# AI-Guided Project Regeneration Workflow

This runbook describes how to reconstruct the current Eden High School boarding and campus operations application, in small verified steps, with an AI coding assistant. It is grounded in the source currently in this repository. It is a workflow, not a claim that the project has been rebuilt.

## 1. Outcome and working rules

Regenerate a working Django application that preserves the project’s evidenced behavior, data relationships, authorization boundaries, interface patterns, and operational workflows. Build from the source repository and its tests rather than asking an AI to invent a replacement specification.

Use these rules throughout:

1. Work in a new branch or isolated worktree. Record `git status` and the base commit before changes; this checkout already contains modified files and an untracked printable report template. Preserve and review those changes; never overwrite or discard them as part of regeneration.
2. Keep a source-to-requirement map. Each feature should point to its model/migration, view/service, template, and relevant tests.
3. Use short, bounded AI tasks. Request one coherent component at a time, with explicit files, constraints, and acceptance checks. Review the diff after every task.
4. Treat `media/admissions_roster.xlsx` as sensitive student and guardian data. The workbook schema may guide import behavior, but never paste real rows, names, admission/index numbers, dates of birth, phone numbers, screenshots, or database extracts into an external AI service. Use synthetic fixtures for prompts and tests. Confirm that AI and logging tools will not retain private source data.
5. Do not commit `.env`, credentials, database dumps, virtual environments, or unreviewed personal data. Review generated static output and commit it only if it is an intentional project-owned build artifact. Keep secrets in local environment variables.
6. Reproduce intended behavior, not accidental implementation defects. If source code and tests conflict, identify the conflict and ask the project owner which behavior is authoritative before changing it.
7. Make each phase pass its gate before moving to the next. Keep migrations, tests, and documentation in sync with accepted behavior.

## 2. Repository evidence and current architecture

### Technology and entry points

- Python requirement: `>=3.12`; Django dependency currently declared as `>=6.1.1` in `pyproject.toml`.
- Backend: Django project `schoolsystem`, application `core`, PostgreSQL database configuration, UUID-keyed domain records, Django templates, and Django authentication.
- Interactive frontend: server-rendered HTML with HTMX partial updates and Alpine.js interactions; Tailwind CSS is built from `static/src/input.css`.
- Dependency manifests: `pyproject.toml` / `uv.lock` and `package.json` / `package-lock.json`; `requirements.txt` is also present and should be checked for consistency.
- Runtime settings: `schoolsystem/settings/base.py`, `development.py`, and `production.py`. Development and production must use different secrets and appropriate security settings.
- Main routes are in `schoolsystem/urls.py` and `core/urls.py`; business rules are primarily in `core/models.py`, `core/forms.py`, `core/permissions.py`, `core/views.py`, and `core/services/`.
- Schema history is represented by the migrations under `core/migrations/`.
- Existing functional regression evidence is concentrated in `core/tests.py`.
- UI references are the Django templates under `templates/` and Tailwind source in `static/src/input.css`; the README currently contains only basic development commands and is not a complete product specification.

### Source data

The workbook `media/admissions_roster.xlsx` has six sheets (`Agric`, `Bus`, `Gen Arts`, `Gen Sci`, `Hom Econ`, and `Vis Arts`). Each uses these 12 columns:

`Admission No.`, `Full Name`, `Index Number`, `Gender`, `Date of Birth`, `Programme`, `House`, `Class`, `Status`, `Residency`, `Parent Name`, `Parent Contact`.

The existing importer is `core/management/commands/import_roster.py`. It creates or finds programmes, classes, named houses, dormitory blocks, and guardians; assigns a student by admission number; and applies mappings for house name, dormitory gender, residency, status, and dates. House identity is based on the school's configured house name, not a fixed numeric house slot. Preserve intended source mappings, but validate assumptions and improve failures/atomicity only as explicit, tested decisions. Do not expose workbook records in prompts or documentation.

### Feature inventory to preserve

1. **Academic structure and student directory** — academic years/terms and track levels; programmes, classes, configurable named houses, gendered dormitories, students, guardians, and staff assignments.
2. **Roles and jurisdiction** — Administrator, Senior Housemaster, Housemaster, House Tutor, and Gate Officer; school-wide access for privileged roles and house-scoped data/actions for assigned staff.
3. **Dashboard and shared shell** — student/boarding/capacity indicators, exeat activity, alerts, a collapsible navigation sidebar, profile menu, notifications, toast messages, and global search.
4. **Exeat workflow** — create a pending request; authorized house/senior staff review and approve or reject; approval establishes the gate log; authorized gate staff record departure and return; staff reconcile approved passes with missing movement records; cancel only when business rules permit.
5. **House and dormitory operations** — occupancy, capacity, staffing coverage, placement gaps, near-capacity and over-capacity warnings.
6. **Roll call** — start a session by house and optionally dormitory; populate boarding students; mark active gate departures as on exeat; update attendance; convert selected absences to conduct records; complete and lock the session.
7. **Conduct register** — create and search/filter disciplinary records with severity, action taken, student and house scope.
8. **Reports and configuration** — housing, roll-call attendance/follow-up, exeat/gate compliance, and discipline reports; date/term filters; print view; active academic-term selection.
9. **Interaction details** — HTMX table/roster/terminal fragments, search autocomplete, pagination preserving filters, CSRF protection, loading/error/empty states, and Alpine.js menus/modals.

## 3. Ordered regeneration phases

### Phase 0 — Establish a safe, reproducible baseline

**AI task:** Ask the assistant to inspect repository source, including pre-existing untracked user work, and summarize the architecture, dependency sources, existing changes, and available validation commands. Do not ask it to edit files.

**Actions:**

- Record the starting branch, base commit, and `git status --short`; save this in the task log outside the repository if needed.
- Identify ignored/private files and generated directories. Confirm the roster workbook’s availability and handling restrictions without printing records.
- Compare `pyproject.toml`, `uv.lock`, `requirements.txt`, `package.json`, and `package-lock.json`; determine which manifests are authoritative before installation.
- Inventory source templates, migration history, tests, static assets, settings, and management commands.
- Identify database and environment prerequisites. Do not assume PostgreSQL, environment variables, or deployment services are running.

**Prompt template**

> Inspect this repository read-only, including pre-existing untracked user files. Report the entry points, dependencies, domain areas, migrations, source-data files and their schema only, tests, settings, generated files, and current worktree changes. Do not read or reproduce any student/guardian data rows. Do not modify files. Cite the repository paths that support each conclusion and list uncertainties separately.

**Gate:** There is a verified inventory, a preserved-worktree plan, and a clear list of unknowns. No code has changed.

### Phase 1 — Derive and approve the behavioral specification

**AI task:** Turn observed behavior into a testable requirements ledger. Source facts before guesses:

1. Existing tests (`core/tests.py`) and migration history.
2. Domain validation and relationships (`core/models.py`).
3. Forms and permission helpers.
4. Views and service functions.
5. Templates and UI interaction patterns.
6. Owner-approved answers for unresolved conflicts.

Record each requirement with: identifier, user/role, trigger, preconditions, result/state change, visible outcome, scope boundary, source evidence, and a test case. Capture state transitions explicitly, especially exeat approval/movement/reconciliation and roll-call finalization. Mark any behavior inferred from code but not covered by tests as “needs confirmation.”

**Prompt template**

> Using the repository evidence, create a requirements ledger for [one feature]. Do not invent behavior. For each rule, cite its source file and distinguish tested behavior, code-only behavior, and unresolved ambiguity. Include roles, house scope, state transitions, validation, error/empty states, and a proposed test. Ask me before resolving any conflict that changes user-visible behavior or authorization.

**Gate:** The owner approves unresolved business choices; each feature has traceable acceptance criteria.

### Phase 2 — Set up the project skeleton and development environment

Regenerate project configuration before application behavior:

1. Create `pyproject.toml` with the agreed Python/Django bounds and runtime/dev dependencies; keep the lockfile reproducible.
2. Create the Django project/settings split, environment loading, PostgreSQL configuration, static/template paths, authentication redirects, and WSGI/ASGI entry points.
3. Add `core` app registration, root URL inclusion, `manage.py`, and safe local development settings.
4. Recreate npm manifests for Tailwind and build/watch commands. Use the lockfile for deterministic installs.
5. Establish `.gitignore`, an example environment file containing variable names only, and setup instructions. Do not put real secrets in tracked files.

**Prompt template**

> Implement only the project/bootstrap layer described in the approved ledger. Match the existing Django/PostgreSQL and Tailwind toolchain. Keep secrets out of source control, preserve separate development/production settings, and do not create domain models or UI yet. Show the diff and exact setup/check commands.

**Gate:** Dependency installation, `manage.py check`, configuration loading with test-only environment values, and Tailwind build succeed. No production secret is required for local development.

**Typical Windows PowerShell commands** (after resolving dependency-manifest authority and configuring a local PostgreSQL test database):

```powershell
uv sync
npm ci
npm run build
uv run python manage.py check --settings=schoolsystem.settings.development
uv run python manage.py migrate --settings=schoolsystem.settings.development
uv run python manage.py test --settings=schoolsystem.settings.development
```

The repository does not include a PostgreSQL service definition, so provision and configure the database separately. If uv is not the chosen Python workflow, use the project’s selected environment manager consistently instead.

### Phase 3 — Build the domain schema and migration chain

Implement models in dependency order, using UUID/timestamp behavior consistently:

1. Academic years and terms.
2. Programme, house, dormitory, class group, guardian, and student.
3. Staff profile, roles, housemaster assignment and constraints.
4. Exeat request, movement, reconciliation, and audit links.
5. Disciplinary record.
6. Roll-call session and entries.

Specify keys, uniqueness, deletion behavior, indexes, choices, timestamps, validation, computed state labels, and invariants from source. Preserve migrations as reviewed source files; do not regenerate or squash migration history casually. Test constraints and validation at the model/service boundaries rather than relying only on template controls.

**Gate:** `makemigrations --check --dry-run`, migration application on a fresh test database, model validation tests, relationship tests, and state-machine boundary tests pass.

### Phase 4 — Recreate the admissions roster import pipeline

Treat the workbook as the initial operational data source, not as an AI prompt payload.

1. Define and validate the exact required headers on every supported sheet.
2. Decide and document mapping/normalization rules for programme names, house identifiers and gender blocks, class “unallocated” values, day/boarding residency, status, guardian contact, and dates.
3. Preserve identifiers such as admission/index numbers and phone values as text; do not silently coerce away leading zeroes or meaningful symbols.
4. Validate required and cross-field data before writing. Produce actionable row/sheet errors without echoing sensitive values into shared logs.
5. Make the operation atomic or explicitly define partial-import behavior; make reruns deterministic; report created/updated/skipped/rejected counts.
6. Test using a small synthetic workbook that covers all sheets/mappings and invalid/duplicate cases. Never use actual student records in a test fixture or prompt.
7. Run the actual import only with explicit authorization and into a controlled target database, after a backup/restore plan and dry-run review.

The current management command accepts a workbook path; after an approved dry-run/transaction strategy exists, a local invocation is of the form `uv run python manage.py import_roster media\admissions_roster.xlsx --settings=schoolsystem.settings.development`. Do not run it against production as part of routine regeneration.

**Prompt template**

> Implement the roster importer using the approved mapping ledger and a synthetic workbook fixture only. Do not open, print, upload, or add real roster rows to tests/logs. Validate sheet headers, identifiers, dates, residency/house/dormitory/gender consistency, and duplicate handling. State transaction and rerun behavior, report safe row references, and add tests for success and failure paths.

**Gate:** Synthetic import tests pass; repeated import behavior is confirmed; a dry run has no unexpected rejected or changed records. Real-data import remains a separately approved operation.

### Phase 5 — Implement roles, authorization, and house scoping

Build role definitions, profile provisioning, staff-to-house assignments, decorators, object-level approval checks, and queryset scoping before feature views. Cover both the permission decision and the data returned. UI visibility is not authorization.

Test:

- Anonymous access and login redirects.
- Each role’s allowed/denied pages and actions.
- Housemaster/gate staff cannot list, search, mutate, report on, or infer another house’s records.
- Senior Housemaster and Administrator scope matches approved requirements.
- Unassigned operational staff receive the intended explicit denial.
- Direct object URLs, forged form values, and HTMX endpoints obey the same scope.

**Gate:** A role/action/house test matrix passes, including direct POST and object-ID attempts.

### Phase 6 — Implement domain services and state transitions

Implement isolated services and transaction boundaries before wiring views:

- Academic active-term selection.
- Exeat submission, review, approval/rejection, cancellation, movement recording, and reconciliation.
- Roll-call roster creation, active-exeat prepopulation, attendance updates, bulk actions, completion lock, and absence follow-up.
- Dashboard metrics, house occupancy calculations, and report aggregation.

Keep transitions explicit and idempotent where appropriate. Check scheduled-vs-actual dates, return-after-departure, approvals/signatories, missing gate data, repeated actions, finalized sessions, and concurrent requests. Avoid duplicate business logic between views, services, and model methods.

**Gate:** Unit tests cover normal, invalid, duplicate, boundary-time, and unauthorized transitions; service operations preserve audit history.

### Phase 7 — Add forms, URLs, views, and HTML pages

Wire each approved use case through Django forms, named routes, views, and templates. Suggested order:

1. Login, base shell, sidebar, profile, notifications, and messages.
2. Dashboard and shared register partial/pagination patterns.
3. Houses/dormitories and student lookup.
4. Exeat creation, list, detail/pass, approval hub/review, gate terminal, and reconciliation.
5. Roll-call sessions and roster.
6. Conduct register and incident entry.
7. Reports, print output, and term configuration.
8. Global search and quick actions.

Use Django URL reversing, CSRF protection, escaped template output, accessible form labels, clear validation messages, loading/empty states, and POST for mutations. Apply the same authorization and house scope to full-page and HTMX responses. Keep public/private data out of error messages.

**UI prompt template**

> Implement [one named page/interaction] from the approved requirement and existing template conventions. Use Django templates with Tailwind, HTMX for the specified partial update, and Alpine.js only for local interaction. Preserve named routes, CSRF, keyboard/accessibility behavior, loading/empty/error states, query filters and house scope. Do not change adjacent workflows. Add or update focused view/template tests and report the changed files.

**Gate:** Route tests and template rendering checks pass. Manual browser checks cover full-page and HTMX states, mobile/narrow layout, forms, messages, and role-specific navigation.

### Phase 8 — Reports, printing, and project polish

Recreate only the report types and filters supported by approved requirements: housing placement/capacity, roll-call attendance/follow-up, exeat/gate compliance, and disciplinary trends. Match interactive and print data, role/house scope, date/term filtering, invalid-filter handling, and data caveats. Confirm that printed output is usable without the interactive sidebar and does not disclose another house’s information.

Complete admin registrations, static assets, project documentation, and operational setup. Resolve manifest drift, document migration/import commands, and keep generated Tailwind output separate from source CSS.

**Gate:** Each report’s totals reconcile to test fixtures; print and screen output use the same scope and filters; invalid filters fail visibly and safely.

### Phase 9 — Verification, release, and handoff

Run in increasing scope:

1. Static/configuration checks: `uv run python manage.py check --settings=schoolsystem.settings.development`, `uv run python manage.py makemigrations --check --dry-run --settings=schoolsystem.settings.development`, formatting/lint/type checks if configured, and `npm run build`.
2. Focused tests for each changed service/view/template.
3. Full test suite against a clean test database.
4. Fresh-database migration smoke test and synthetic roster import.
5. Browser walkthrough for each role and complete operational workflow. For local UI review, the existing README uses `uv run python manage.py runserver --settings=schoolsystem.settings.development`.
6. Production-settings checks with non-production test secrets and deployment-like static configuration; confirm `DEBUG=False`, allowed hosts, secure cookies/HTTPS settings, and no secret values in logs.
7. Review `git diff --check`, test output, generated/untracked files, privacy exposure, and the final source-to-requirement coverage matrix.

Do not claim completion if a gate is skipped. Record exact commands, environment prerequisites, results, known limitations, migrations, and any owner decisions. Commit only explicitly approved source/docs and intended generated artifacts; never include private roster exports, secrets, or unrelated worktree changes.

**Final acceptance gate**

- Every ledger requirement is implemented or explicitly marked deferred.
- Every critical role/state transition has automated coverage.
- Fresh setup, migrations, synthetic import, tests, and asset build are reproducible.
- Cross-house access is tested at list, search, report, object, and mutation boundaries.
- No real student/guardian data or credentials were sent to AI or added unintentionally.
- The final diff contains no unrelated work and preserves the pre-existing changes identified at baseline.

## 4. Reusable AI task contract

Include this contract in each implementation prompt:

> Before editing, inspect the named files and tests. Work only on the requested bounded scope. Follow the approved requirement and existing conventions; do not make unrelated cleanup. Preserve uncommitted user changes. Do not expose real roster data or secrets. Add/update focused tests for behavior changes, run the smallest relevant checks, inspect the final diff, and report changed files, commands/results, unresolved questions, and any migration or operational consequence. Stop and ask before changing permissions, data mappings, state transitions, or another user-visible behavior not settled in the ledger.

## 5. Source map for the AI assistant

| Concern | Primary repository evidence |
|---|---|
| Project/dependencies and setup | `pyproject.toml`, `uv.lock`, `requirements.txt`, `README.md`, `manage.py` |
| Django settings and entry points | `schoolsystem/settings/`, `schoolsystem/urls.py`, `schoolsystem/asgi.py`, `schoolsystem/wsgi.py` |
| Domain and invariants | `core/models.py`, `core/migrations/` |
| Roster mapping | `media/admissions_roster.xlsx` (headers only for AI), `core/management/commands/import_roster.py` |
| Forms and access policy | `core/forms.py`, `core/permissions.py`, `core/context_processors.py` |
| Requests/routes and page assembly | `core/views.py`, `core/urls.py` |
| Operational/report services | `core/services/configuration.py`, `core/services/roll_call.py`, `core/services/reports.py` |
| Admin and regression tests | `core/admin.py`, `core/tests.py` |
| UI and assets | `templates/`, `static/src/input.css`, `tailwind.config.js`, `package.json` |

When sources disagree, cite the exact evidence and pause for a decision rather than letting the AI silently choose.
