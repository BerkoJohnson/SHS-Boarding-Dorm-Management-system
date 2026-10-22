# Documentation and Handover Implementation Plan

**Status:** Proposed  
**Goal:** Make the system easier to adopt and hand over without losing the technical detail already in the root README.  
**Approach:** Keep `README.md` as the technical reference; add a small, task-oriented documentation set for school users and operators.

## 1. Outcome

A new user should be able to find the guide for their role, complete a common task without developer help, and know what to do when something goes wrong. A future maintainer should be able to operate and hand over the service without relying on undocumented personal knowledge.

The documentation should be accurate to the current application. Confirm every screen, permission, and workflow against the running system before publishing instructions. Where behavior is uncertain, record the question rather than inventing an answer.

## 2. Audiences and priorities

Implement in this order:

1. **Housemasters and house tutors** — roll call, absences, and house-level exeat work.
2. **Gate officers** — finding an approved exeat and recording departure and return.
3. **School administrators** — initial configuration, staff access, student records, and routine administration.
4. **Platform operators and maintainers** — school provisioning, service health, backups, restore checks, and escalation.

The first two roles are the best pilot because their work is frequent and can be demonstrated as concrete end-to-end tasks. Confirm role names and actual permissions in the product before finalizing the guides.

## 3. Proposed documentation structure

Add a `docs/` directory with a landing page and role/task-oriented guides. Keep the root README focused on development, deployment, commands, and architecture.

```text
docs/
├── README.md                         # Documentation index: choose your role or task
├── quick-starts/
│   ├── housemaster-and-tutor.md      # First roll call and follow-up
│   ├── gate-officer.md               # Verify, depart, and return
│   ├── school-administrator.md       # Initial setup and routine administration
│   └── platform-operator.md          # Provisioning and operational checks
├── user-manual/
│   ├── README.md                     # Task index
│   ├── roll-call-and-absences.md
│   ├── exeats.md
│   ├── student-records.md
│   └── reports-and-id-cards.md
├── operations/
│   ├── handover.md                   # Ownership, access, escalation, and runbooks
│   └── backup-and-restore.md         # Backup and restore verification checklist
└── videos/
    ├── README.md                     # Video links, duration, audience, and transcripts
    └── transcripts/                  # Searchable text matching each published video
```

This is a proposed layout, not a requirement to create every file at once. Start with the guides needed for the first pilot workflows, and add manual pages only when the corresponding workflow is verified.

### Role guide scope

Each quick start should state the role's purpose, prerequisites, common tasks, completion checks, and who to contact when the user cannot proceed. The following scopes make sure the later roles receive the same practical treatment as the pilot roles.

#### School administrator

The quick start should guide a school administrator through the routine school-level tasks they are authorized to perform:

- **Prepare the school:** confirm the active term or academic year and the required school structure, such as houses, dormitories, and rooms.
- **Set up access:** add or invite staff, assign the correct roles and house assignments, and explain how users sign in or request access help.
- **Maintain student records:** add or update a student, understand required fields and placement information, and safely import a roster when that feature is available to the role.
- **Check readiness:** confirm that staff can access their assigned work and that representative student records appear as expected.
- **Use routine administration tools:** find the relevant reports, configuration, or ID-card functions that are available to this role.
- **Handle common issues:** identify missing or duplicate data, incorrect role/house assignments, and failed imports; explain when to correct the data and when to escalate.

Do not instruct school administrators to provision tenants, change hosting settings, or run database commands unless the verified permission model explicitly assigns those duties to them.

#### Platform operator and maintainer

The quick start should focus on a safe, repeatable operating and support handover rather than ordinary school workflows:

- **Provision or onboard a school:** follow the approved process for creating the school, configuring its domain, and confirming the school can reach its login page.
- **Verify service health:** check the documented application health indicators and confirm the web service, database, background worker/scheduler, and integrations required by the deployment are operating.
- **Triage an incident:** collect the affected school, time, user-visible symptom, and safe diagnostic evidence; use the escalation path without requesting passwords or unnecessary student data.
- **Perform routine maintenance:** identify the approved deployment, migration, and maintenance runbooks and the person authorized to execute each action.
- **Protect and recover data:** locate the configured backup process, verify backup completion, and follow the approved restore-test procedure in a safe environment.
- **Transfer ownership:** confirm named owners and authorized access for hosting, database, domains, SMS/email, backups, and support; rotate credentials through the approved secret-management process.

The guide must distinguish read-only checks from actions that change production data or availability. It should link to the technical README or approved runbook for commands and environment-specific settings, not duplicate them or include secret values.

## 4. Implementation phases

### Phase 0 — Confirm scope and current behavior

- Inspect the current application and existing technical README for the supported roles and workflows.
- Walk through roll call, exeat approval, gate departure/return, student administration, and platform operations in a safe environment.
- Confirm role names, permissions, visible labels, error states, and any required background services.
- Use a synthetic school and fictional student data for all walkthroughs and captures.
- Record unresolved product or policy questions for the product owner; do not document assumptions as fact.

**Exit criteria:** A verified workflow outline exists for each planned quick start, including who can perform each step and any prerequisites.

### Phase 1 — Documentation index and first role guides

- Create `docs/README.md` with plain-language navigation by role and task.
- Write the housemaster/tutor and gate officer quick starts first.
- Then write the school administrator and platform operator quick starts using the role scopes above; do not leave those audiences with only links to technical deployment documentation.
- Keep each quick start focused on one successful outcome and short enough to use while doing the task.
- Add prerequisites, numbered steps, expected result, common mistakes, and a recovery/escalation route.
- Link to the relevant detailed technical README section only when a technical action is genuinely required.
- Add a concise documentation link in the root README without duplicating its technical content.

**Exit criteria:** A first-time user from each of the four audiences can complete at least one role-appropriate task using the applicable guide, and the school administrator and platform operator guides clearly state their boundaries and escalation routes.

### Phase 2 — Plain-language task manual

- Build a task index around “How do I…” questions rather than system components.
- Expand the pilot guides into standalone instructions for roll call and absences, exeats, student records, and reports/ID cards.
- Explain school-specific terms, including “exeat,” in context and use the exact labels shown in the UI.
- Include safe handling guidance wherever student, guardian, disciplinary, or contact information appears.
- Add troubleshooting entries only for observed or confirmed failure states. Include what the user can safely retry and when to escalate.
- Use screenshots only when they materially clarify a step; redact personal data and keep screenshots current with the UI.

**Exit criteria:** Each in-scope task has a clear starting point, prerequisites, expected completion state, and safe next step if it fails.

### Phase 3 — Operations and handover

- Document system ownership by responsibility: application, hosting, database, domain/DNS, SMS, email, backups, and user support.
- Describe where access is administered and who is authorized to grant it. Do not place passwords, tokens, private keys, recovery codes, or real student data in documentation.
- Provide a handover checklist for transferring ownership, rotating credentials through the approved secret-management process, and confirming access.
- Document health checks, routine maintenance, incident escalation, and the operator’s first checks for common service failures.
- Document backup frequency/location as configured in the deployed environment and add a restore-verification checklist. A backup is not considered proven until a restore has been tested safely.
- Link to the existing deployment and maintenance instructions in `README.md` rather than copying command recipes that may drift.

**Exit criteria:** A designated backup maintainer can identify the system owners, follow the operational checks, and explain how a restore is verified without exposing secrets.

### Phase 4 — Short videos and accessible alternatives

- Select a small initial set across the audiences: take roll call, review/approve an exeat, record a gate departure and return, complete one school setup or roster task, and perform one safe operator health check or incident triage.
- Draft and verify the script against the corresponding guide before recording.
- Record in a non-production environment using synthetic data; avoid real names, phone numbers, IDs, and records.
- Keep each video to one task, use readable zoom and narration, and avoid displaying credentials or sensitive browser data.
- Publish captions and a matching text transcript. Include the video duration and target role in the video index.
- Treat the written guide as the maintained source of truth; update or retire a video when the UI changes.

**Exit criteria:** Every published video has captions, a transcript, a matching current written guide, and no real personal or secret data.

### Phase 5 — Review, release, and maintenance

- Ask at least one representative user from each pilot role to follow the guide without coaching.
- Record where they hesitate, use incorrect terminology, or cannot reach the expected outcome; revise and repeat the walkthrough.
- Check links, headings, spelling, image alternatives, and video transcripts.
- Review role and permission instructions against the current application before each significant release.
- Assign a documentation owner and require relevant guide updates when UI or workflow changes are merged.
- Re-review operational ownership and handover details at least annually and whenever a provider or owner changes.

**Exit criteria:** Guides pass an uncoached task walkthrough, have an identified owner, and have a review trigger.

## 5. Definition of done

- Documentation is discoverable from the root README and from a single docs landing page.
- Role-based quick starts are separate from the developer/deployment reference.
- Each instruction is tested against current application behavior and names the expected result.
- No real student, guardian, staff, or secret data appears in examples, screenshots, or videos.
- Videos include captions and transcripts; no user is required to watch a video to complete a task.
- Operations and backup instructions identify owners and safe escalation paths without embedding credentials.
- Documentation updates are included in the workflow for future product changes.

## 6. Suggested delivery sequence

| Order | Deliverable | Priority | Dependency |
|---|---|---:|---|
| 1 | Verify pilot workflows and role permissions | Must do | None |
| 2 | Docs landing page and housemaster/tutor quick start | Must do | 1 |
| 3 | Gate officer quick start | Must do | 1 |
| 4 | School administrator quick start: setup, staff access, and student records | High | 1 |
| 5 | Platform operator quick start: provisioning, health checks, incident triage, and handover | High | 1; confirmed operational owners |
| 6 | Task-based manual pages and troubleshooting | High | Verified workflows |
| 7 | Handover and backup/restore checklists | High | Confirmed deployment ownership/configuration |
| 8 | Role-specific videos, captions, and transcripts | Later | Stable guides and safe recording environment |
| 9 | User walkthrough, revisions, and maintenance ownership | Must do | Initial guides |

For a small team, deliver orders 1–3 as the first usable increment, then complete orders 4–5 before treating role-based documentation as complete. Avoid delaying useful written guides while waiting for video production.

## 7. Risks and safeguards

| Risk | Safeguard |
|---|---|
| Guides describe screens or permissions that have changed | Verify against the running app and assign a review owner |
| Technical README grows further and remains hard for non-technical readers | Keep role/task guides separate and link to technical detail only when needed |
| Videos become stale or are inaccessible to some users | Keep text authoritative; provide captions and transcripts; review videos with their guides |
| Sensitive information is exposed in examples or recordings | Use synthetic data and review captures before publication |
| Handover documentation contains secrets or personal credentials | Document access ownership and approved secret-management location, never secret values |
| Unclear product policy is presented as a rule | Flag unresolved questions and obtain owner approval before publication |

## 8. Decisions needed before publication

- Confirm the official role names and whether housemasters and tutors have the same workflows.
- Confirm which exeat transitions each role is permitted to perform.
- Confirm which setup, staff-management, roster-import, report, and ID-card actions school administrators can perform.
- Identify which platform operations are read-only, which require elevated approval, and who owns each production-changing action.
- Identify the owner/contact for school-user support and platform incidents.
- Confirm the actual backup schedule, storage location, and approved restore-test environment.
- Choose the approved location and accessibility requirements for published videos.

Until these are confirmed, keep the affected instructions marked as draft and do not imply a policy or service commitment.
