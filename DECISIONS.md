# SHS Boarding & Dorm Management — Approved Policy Decisions

**Status:** Approved  
**Approval date:** 2026-10-20  
**Approved by:** School management / project owner  
**Scope:** Full P1 roadmap and the operational rules that depend on D1–D8.

These decisions are the authoritative policy inputs for the Full P1 implementation. If a future change is needed, record it here with a new date and identify the approving role. Code and user instructions must not override these decisions by implication.

---

## D1 — Retention and disposal

**Decision:** Retain all current records and backups. Do not delete student, attendance, medical, disciplinary, exeat, gate, audit, SMS, or backup records until a written retention schedule is approved.

- Start with a read-only archive if storage becomes a constraint.
- Include database backups and exported files in the retention schedule.
- Apply legal holds before any disposal.
- No automated disposal is permitted until the schedule and disposal procedure are approved.

**Implementation status:** Policy recorded. Retention automation is not enabled.

---

## D2 — Emergency authorization

**Decision:** The Headmaster and Senior Housemaster may authorize general emergency departures. A named medical/infirmary lead may authorize medical movements once a scoped medical permission exists. Housemasters may initiate but not authorize; gate officers never authorize.

Every emergency movement must record:

- the reason and destination;
- the authorizing person and time;
- the student and accompanying adult, where applicable;
- immediate guardian notification; and
- a post-review within 24 hours by the Headmaster or a named delegate.

Until a scoped medical permission is implemented, medical emergencies are authorized by the Headmaster or Senior Housemaster by telephone and recorded by the gate officer. Medical staff must not be given `ADMIN` access as a workaround.

**Implementation status:** Policy recorded; workflow implementation in progress.

---

## D3 — Late-return escalation

**Decision:** Escalation is graduated and configurable:

| Trigger | Action |
|---|---|
| Expected return time passes | Notify guardian and housemaster. |
| +2 hours | Notify Senior Housemaster and create a written incident. |
| +6 hours | Notify Headmaster. |
| After +6 hours | Headmaster decides whether to involve the police or guardian according to school policy. |

- Thresholds must be configurable rather than hard-coded.
- The incident record must include the pass, student, expected return time, escalation times, notifications, and decisions.
- No automatic police contact is permitted.

**Implementation status:** Policy recorded; workflow implementation in progress.

---

## D4 — Attendance corrections

**Decision:** Post-closure attendance corrections require a reason and an append-only history.

- **ABSENT ↔ PRESENT** and conduct-linked changes require supporting evidence and a second approval.
- The Senior Housemaster normally approves. If the Senior Housemaster made the change, the Headmaster approves.
- Only the Senior Housemaster or Headmaster may correct a completed roll-call session.
- Corrections must not delete the original entry or its audit trail.
- Expired roll-call sessions may be corrected only through this workflow.

**Implementation status:** Policy recorded; workflow implementation in progress.

---

## D5 — Dormitory overcapacity

**Decision:** Dormitory capacity is enforced by default.

- The Headmaster or Senior Housemaster may approve a short, time-bound exception with a written reason and review date.
- The initial maximum is seven days. Renewal beyond seven days requires the Headmaster.
- All exceptions appear in a weekly report.
- Permanent or implicit overcapacity is not permitted.

**Implementation status:** Policy recorded; workflow implementation in progress.

---

## D6 — SMS

**Decision:** SMS remains disabled during the pilot.

When SMS is enabled:

- Use transactional messages only: exeat approval/return and late-return escalation.
- Do not include medical or disciplinary details.
- Use one approved sender ID.
- The bursar sets and reviews a monthly cap.
- Group messages require Headmaster or bursar approval and a recipient/quota preview.
- Maintain a manual opt-out register until inbound opt-out handling is available.
- Do not enable SMS until a worker and scheduler exist and the delivery queue has been tested.

**Implementation status:** SMS is disabled in deployment. Workflow implementation is gated on worker/scheduler availability and D6 activation.

---

## D7 — Recovery objectives

**Decision:** The provisional internal objectives are:

- **RTO:** 24 hours.
- **RPO:** 24 hours or better; use point-in-time recovery where available.

These are internal targets, not a contractual service-level agreement, until a timed staging restore proves them.

- Take daily encrypted backups and store them offsite.
- Test a restore at least quarterly.
- A restore requires approval from the Headmaster and the ICT administrator.
- Eden Sys executes the restore.
- Planned downtime is scheduled outside academic hours where possible; emergency downtime is agreed case by case.
- Record the achieved RTO/RPO after every drill.

**Implementation status:** Policy recorded; backup encryption, offsite storage, scheduling, and staging restore drill remain open.

---

## D8 — Data ownership and remote support

**Decision:** The school owns its data. Eden Sys is a data processor.

- Use split key custody: the school and Eden Sys each hold a share; a restore requires both.
- Restores require written authorization from the school.
- Remote support requires approval from the Headmaster and ICT administrator.
- Support access must be time-limited, scoped to the approved task, logged, and revocable.
- Shared accounts are prohibited.
- Provide audit extracts monthly or on request.
- Do not use production data for demonstrations, documentation, or testing.

**Implementation status:** Policy recorded; support-access and audit controls are being implemented.

---

## Role model

The following role mapping is approved:

| Person / function | Pilot mapping | Target |
|---|---|---|
| Headmaster | Named `ADMIN` account | Dedicated `HEADMASTER` role before scale-up |
| Assistant head | `SENIOR_HOUSEMASTER` for boarding operations | Confirm whether school-wide `ADMIN` is also required |
| Medical / infirmary lead | No system access until a scoped medical permission exists | Scoped medical role; never `ADMIN` by default |
| Bursar | Finance and SMS reporting only | Dedicated finance/reporting permission |
| Housemaster | Existing `HOUSEMASTER` role | No approval of their own exeats |
| House tutor | Existing `HOUSE_TUTOR` role; create/view exeats and roll call | No approval rights |
| Gate officer | Existing `GATE_OFFICER` role; verify and record movements | No authorization rights |

No shared accounts are permitted. Named accounts are required for every approval and every support session.

---

## Change control

1. Any change to these decisions must be recorded in this file.
2. State the old decision, the new decision, the reason, the approving role, and the effective date.
3. Update dependent code, tests, and user documentation in the same change where practical.
4. Do not infer a policy change from an implementation shortcut.
