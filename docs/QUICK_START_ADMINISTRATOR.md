# Administrator / Headmaster (pilot ADMIN) — one-page quick start

**Goal:** configure safely, review exceptions, and avoid bypassing audited
workflows.

## Daily and weekly checks

1. Review **Exeat Reviews**, **Late Returns**, **Attendance Corrections**, and
   **Disputes** for items needing a decision.
2. Review **Capacity Exceptions** weekly. No exception may exceed seven days.
3. Review **Gate Duty Roster** for gaps and expired assignments.
4. Review export and audit records. Confirm exports were authorized and stored
   safely.
5. Check that SMS remains disabled until a paid worker/Beat and the D6 policy
   are in place.

## Configuration rules

- Operational timings and escalation thresholds are in **Escalation Settings**.
  Changes are configuration-audited.
- Do not add or edit exeats, movements, completed roll calls, corrections,
  reviews, or disputes in Django admin. Those screens are read-only; use the
  workflow pages.
- Do not delete records or backups. D1 requires retention until a written
  schedule and legal-hold process are approved.
- Do not enable background jobs or SMS on the web-only Render service. The
  current deployment has `BACKGROUND_JOBS_ENABLED=False` and `SMS_ENABLED=False`.
- Do not restore a tenant in place. Follow D7/D8 written authorization and Eden
  Sys execution.

## Headmaster decisions

- General emergency authorization: you or the Senior Housemaster may authorize.
- Late return at six hours: the system notifies you; decide further escalation
  and record the decision.
- Overcapacity: approve only a written, time-bound exception of seven days or
  less.
- Disputes: decide only cases you did not raise.

## Account and support safety

Use a named account, never a shared login. Report suspected credential exposure
to Eden Sys; the school owns credential rotation. Remote support must be
time-limited, scoped, logged, and revocable under D8.
