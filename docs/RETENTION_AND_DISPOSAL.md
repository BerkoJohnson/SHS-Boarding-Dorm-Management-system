# Retention and disposal policy position

**Status:** Approved policy D1, 20 October 2026  
**Applies to:** student, boarding, attendance, conduct, exeat, gate, SMS, audit,
backup, and export records in the SHS Boarding & Dorm Management System.

## Current rule: retain everything

The school has **not** approved a retention schedule or a secure-disposal
procedure. Until that happens:

1. **Do not delete records** from the application database, Django admin, or
   backups.
2. **Do not anonymise or redact records** in place.
3. **Do not destroy backup media** or remove backup copies.
4. **Do not add or run a purge, truncate, archive-and-delete, or retention
   cleanup job.** No such job is enabled in this codebase.
5. Preserve the append-only `AuditEvent` and export logs. They are evidence of
   who accessed or changed sensitive records.

This position includes records that look obsolete, such as cancelled exeats,
expired roll calls, corrected attendance, closed disputes, and failed SMS
deliveries. They remain part of the audit record.

## Legal holds and investigations

If the school, its lawyers, or an investigator places a hold on a student,
staff member, incident, or date range, that hold overrides any future retention
schedule. Preservation must include:

- the application database record and its audit history;
- related exports and `DataExportLog` rows;
- SMS delivery metadata (message bodies only where already stored);
- database backups and any restored copies.

The Headmaster and ICT lead must confirm a hold in writing and record its
scope, start date, and release date.

## Backups and copies

D1 also applies to backups. Backups are retained under the current backup
practice; they are not deleted because a record has been corrected in the live
database. A restored copy is subject to the same access, confidentiality, and
retention rules as production. Do not use a restored copy as a new live dataset
without the approval required by D7 and D8.

## What a future disposal policy must contain

Before any disposal code is written or enabled, the school must approve a
written schedule that defines:

- retention periods by record category and the trigger date (for example,
  end of academic year, closure of a case, or end of employment);
- a read-only archive period and who may access it;
- an export-before-disposal step and its verification;
- legal-hold handling;
- dual approval by the Headmaster and ICT lead, with Eden Sys executing the
  approved action;
- audit events that survive disposal;
- backup and restore implications;
- a dry-run mode and a tested restore path.

Until those items exist, the correct implementation is **retention only**.

## Review and evidence

Review this position at least annually and whenever the school publishes a
retention schedule. Record the review decision in `DECISIONS.md`.

Evidence that this control is working:

- `manage.py makemigrations --check` and code review show no purge command;
- database backups are still present and restorable;
- a sample corrected record still has its original value in the audit trail;
- `DataExportLog` and `AuditEvent` rows remain append-only.

## Related decisions

- D1: retain all records and backups; no deletion until an approved schedule,
  legal holds, and read-only archive approach exist.
- D7: provisional 24-hour RTO / 24-hour RPO; no contractual promise until a
  timed staging restore passes.
- D8: school owns the data; Eden Sys is the processor; split keys, written
  authorization, and logged support access.
