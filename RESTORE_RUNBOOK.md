# Restore Runbook — Tenant Backup and Recovery

**System:** SHS Boarding & Dorm Management System (Eden Sys)
**Applies to:** `tenant_backup`, `tenant_restore`, and full-database PostgreSQL recovery
**Last drill:** 2026-10-20 (local PostgreSQL 18.3; see section 9)
**Owner:** Platform operator on duty; recovery switch requires the school's authorised contact.

> This runbook deliberately does **not** claim an in-place live-tenant restore. The
> `tenant_restore` command restores only into an **empty, unregistered schema whose name matches
> the dump**. Read section 1 before promising a recovery time to a school.

---

## 1. What is supported, and what is not

### Supported

- **Per-school backup:** `tenant_backup --schema=<schema> --include-media --verify` creates:
  - a `pg_dump` custom archive of one tenant schema;
  - a SHA-256 checksum file;
  - an optional media `.tar.gz` archive;
  - a `PlatformAuditLog` row in the `public` schema.
- **Per-school restore into an empty schema:** `tenant_restore` verifies the checksum and the
  archive's table of contents, refuses reserved/registered/existing/nonempty schemas and
  schema-name mismatches, and never issues `DROP`, `--clean`, or `--if-exists`.
- **Full-database recovery:** use `pg_dump` / `pg_restore` (README section 10.2). This is the
  recommended path for a site-wide loss because it includes the `public` registry and every
  tenant schema together.

### Not supported

- **In-place overwrite of a live tenant schema.** `tenant_restore` will refuse an existing or
  registered schema. There is no "restore over the old data" flag.
- **Renaming a dump to a different schema.** The schema name embedded in the dump must equal the
  target name. A backup of `atebubu_shs` cannot be restored directly as `atebubu_shs_spare`.
- **Restoring the `public` registry automatically.** `tenant_restore` restores one tenant schema;
  `Tenant` and `Domain` rows are separate. Registering the restored schema is a deliberate step.
- **Encrypted offsite upload (opt-in).** `tenant_backup --offsite` uploads the dump, checksum,
  and optional media archive to `BACKUP_OFFSITE_BUCKET` using S3 server-side KMS encryption and
  verifies the returned encryption state, object size, and checksum metadata. Configure a dedicated
  bucket, KMS key, access pair, versioning/Object Lock, and key policy first. The local archive is
  retained and remains sensitive data. Split-key custody, automated scheduling, retention controls,
  and an offsite restore drill remain open under **F-26/D7/D8**.
- **Point-in-time recovery (PITR).** Neon PITR may be available on the database plan, but this
  runbook has not tested it. Confirm the plan's retention window before relying on it.

---

## 2. Preconditions

1. **Same PostgreSQL major version** on the source and target (this runbook was drilled on 18.3).
2. `pg_dump` and `pg_restore` on `PATH`, matching the server version.
3. `TENANCY_ENABLED=True`.
4. The target database exists. It may contain the migrated `public` schema, but **must not**
   contain:
   - a schema with the target name, or
   - a `tenancy_tenant` row with the target `schema_name`.
5. The dump and its `.sha256` file are in an approved backup directory. The default is
   `.private/backups/tenants/<schema>/`.
6. The backup was taken before the incident and the checksum was verified at backup time.
7. For a live recovery, put the school into maintenance mode or stop writes first. A backup taken
   during active writes can be internally inconsistent.
8. Record the operator, time, source database, target database, dump filename, and SHA-256 in the
   incident log.

---

## 3. Taking a backup

Use the application environment, with PostgreSQL client tools on `PATH`.

```powershell
$env:TENANCY_ENABLED = "true"
.\.venv\Scripts\python.exe .\manage.py tenant_backup `
  --schema=<schema> `
  --include-media `
  --verify `
  --offsite `
  --operator="<name>" `
  --json
```

- If the school has no media files, add `--allow-empty-media`; otherwise the command refuses
  rather than silently omitting files.
- The JSON output must show `"status": "success"` and `"verified": true`. Record the `sha256`.
- Configure the dedicated `BACKUP_*` variables in the runtime secret store; never place values in
  `.env.example` or source control.
- `--offsite` fails closed unless bucket, KMS key, region, and dedicated access credentials are set.
- A KMS upload is not a restore drill and does not prove split-key custody or object retention.

For a full-database backup, follow README section 10.2:

```powershell
pg_dump -h <host> -U <user> -d <db_name> -F c -f "backup_full_<date>.dump"
```

---

## 4. Restore drill (staging or disposable local database)

Never perform a first drill against production. The commands below use a disposable local
PostgreSQL cluster; substitute staging hostnames for a staging drill.

### 4.1 Create empty source and target databases

```powershell
$env:PATH = "C:\Program Files\PostgreSQL\18\bin;" + $env:PATH

# Local non-SSL cluster: pg_dump/pg_restore inherit PGSSLMODE.
$env:PGSSLMODE = "disable"
$env:DATABASE_PROFILE = "local"
$env:DB_USER = "postgres"
$env:DB_PASSWORD = ""
$env:DB_HOST = "127.0.0.1"
$env:DB_PORT = "55432"
$env:DB_SSLMODE = "disable"
$env:TENANCY_ENABLED = "true"

dropdb  -h 127.0.0.1 -p 55432 -U postgres --if-exists restore_drill_source
dropdb  -h 127.0.0.1 -p 55432 -U postgres --if-exists restore_drill_target
createdb -h 127.0.0.1 -p 55432 -U postgres restore_drill_source
createdb -h 127.0.0.1 -p 55432 -U postgres restore_drill_target
```

> For Neon or another SSL-only target, omit `PGSSLMODE=disable`, use
> `DATABASE_PROFILE=neon`, and set `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`,
> `PGSSLMODE=require`. Never point a drill at the production database.

### 4.2 Prepare the public schema and a sample tenant in the source

```powershell
$env:DB_NAME = "restore_drill_source"
.\.venv\Scripts\python.exe .\manage.py migrate --schema public `
  --settings=schoolsystem.settings.development

.\.venv\Scripts\python.exe .\manage.py provision_school `
  --name "Restore Drill School" `
  --schema restore_drill `
  --domain restore-drill.example.test `
  --confirm-domain-verified `
  --settings=schoolsystem.settings.development
```

Add representative data (a student, a house, a dormitory) through the application or a shell:

```powershell
.\.venv\Scripts\python.exe .\manage.py shell `
  --settings=schoolsystem.settings.development -c "from tenancy.models import Tenant; from core.models import Programme, House, Dormitory, Student; tenant=Tenant.objects.get(schema_name='restore_drill'); ctx=tenant; ctx.__enter__(); p,_=Programme.objects.get_or_create(name='Restore Drill Science', defaults={'code':'RDS'}); h,_=House.objects.get_or_create(name='Restore Drill House'); d,_=Dormitory.objects.get_or_create(house=h, name='Restore Drill Block', defaults={'gender':Dormitory.GenderBlock.MALE,'capacity':20}); Student.objects.get_or_create(admission_no='RESTORE-DRILL-001', defaults={'index_number':'RESTORE-IDX-001','full_name':'Restore Drill Student','gender':Student.Gender.MALE,'programme':p,'house':h,'dormitory':d,'residency':Student.ResidencyStatus.BOARDING}); print('students=', Student.objects.count()); ctx.__exit__(None,None,None)"
```

### 4.3 Back up the source tenant

```powershell
$env:DB_NAME = "restore_drill_source"
.\.venv\Scripts\python.exe .\manage.py tenant_backup `
  --schema=restore_drill `
  --verify `
  --operator=batch2-drill `
  --settings=schoolsystem.settings.development `
  --json
```

Record the printed `dump_file`, `dump_size_bytes`, and `sha256`. Confirm `"verified": true`.

### 4.4 Prepare the target and dry-run the restore

```powershell
$env:DB_NAME = "restore_drill_target"
.\.venv\Scripts\python.exe .\manage.py migrate --schema public `
  --settings=schoolsystem.settings.development

.\.venv\Scripts\python.exe .\manage.py tenant_restore `
  --schema=restore_drill `
  --dump="<absolute path to the .dump file>" `
  --operator=batch2-drill `
  --dry-run `
  --settings=schoolsystem.settings.development `
  --json
```

The dry run must report `"status": "dry_run"` and a matching `source_schema`/`sha256`. If it
reports a schema mismatch, checksum mismatch, registered schema, or existing schema, stop and fix
the target; do not bypass the check.

### 4.5 Restore for real

Run the same command without `--dry-run`. Success prints `"status": "success"`.

### 4.6 Validate the restored data

```powershell
psql -h 127.0.0.1 -p 55432 -U postgres -d restore_drill_target -c `
  "SELECT count(*) AS students FROM restore_drill.core_student;"
psql -h 127.0.0.1 -p 55432 -U postgres -d restore_drill_target -c `
  "SELECT count(*) AS migrations FROM restore_drill.django_migrations;"
psql -h 127.0.0.1 -p 55432 -U postgres -d restore_drill_target -c `
  "SELECT name FROM restore_drill.core_schoolconfiguration LIMIT 1;"
```

Compare the counts with the source. Also check key operational tables (exeats, roll-call sessions,
SMS deliveries) and the `public.tenancy_platformauditlog` row for the restore action.

### 4.7 Register the restored schema (drill or new tenant)

`tenant_restore` intentionally does not create `Tenant`/`Domain` rows. After the restore, register
the schema so the application can route to it:

```powershell
$env:DB_NAME = "restore_drill_target"
.\.venv\Scripts\python.exe .\manage.py shell `
  --settings=schoolsystem.settings.development -c "from tenancy.models import Tenant, Domain; t,c=Tenant.objects.get_or_create(schema_name='restore_drill', defaults={'name':'Restore Drill School','is_active':True}); d,dc=Domain.objects.get_or_create(domain='restore-drill-restored.example.test', defaults={'tenant':t,'is_verified':True}); print('tenant_created=',c,'domain_created=',dc)"
.\.venv\Scripts\python.exe .\manage.py tenant_list --all `
  --settings=schoolsystem.settings.development
.\.venv\Scripts\python.exe .\manage.py tenant_usage --schema=restore_drill `
  --settings=schoolsystem.settings.development
```

`tenant_list` must show the restored student count. `tenant_usage` must query the restored
schema without error.

### 4.8 Prove the command is non-destructive

Run the restore again against the same target. It must refuse:

```text
CommandError: Schema 'restore_drill' already exists and is not empty. Refusing to restore into a nonempty schema.
```

This is a required safety property. Do not delete a schema just to make a restore command succeed.

### 4.9 Clean up the drill

```powershell
dropdb -h 127.0.0.1 -p 55432 -U postgres --if-exists restore_drill_target
dropdb -h 127.0.0.1 -p 55432 -U postgres --if-exists restore_drill_source
```

Keep the drill evidence (dump SHA-256, counts, refusal output) in the incident/runbook record.
Remove the plaintext drill dump and media archive from local disk once the evidence is recorded.

---

## 5. Live recovery procedures

### A. Full database restore (recommended for site-wide loss)

Use a full `pg_dump` archive that includes `public` and all tenant schemas.

1. Create a **fresh** database with the same PostgreSQL major version.
2. `pg_restore` the full archive into it (no `--clean`, no overwrite).
3. Run `manage.py migrate --schema public` only if the archive predates required public
   migrations; otherwise use the archived state.
4. Validate the `public` registry (`tenancy_tenant`, `tenancy_domain`) and at least one tenant
   schema with the SQL in section 4.6.
5. Put the application into maintenance, switch its database connection (`PG*` or
   `DATABASE_URL`) to the fresh database, restart web, and smoke-test login and tenant routing.
6. Keep the old database untouched and read-only for rollback until the school signs off.

This avoids the per-tenant registration problem because the public registry and the tenant
schemas come from the same archive.

### B. New tenant / migration into an existing database

1. Create the target database/tenant schema as empty and unregistered.
2. Run `tenant_restore` with the dump whose embedded schema name matches the target.
3. Register `Tenant` and `Domain` rows only **after** the restore (section 4.7).
4. Validate and activate the tenant.

### C. Single-tenant deployment (one school per database)

1. Restore into a fresh database as in section A.
2. Switch the application connection to it.
3. Keep the old database for rollback.

### D. Shared multi-tenant database, one school damaged

In-place restore of one schema is **not supported** by `tenant_restore`. Choose one:

- **Preferred:** restore the full database into a fresh database, then switch the whole deployment
  (section A). This protects the public registry and every school.
- **Maintenance-window decommission/reprovision:** after a verified backup and written approval,
  deactivate the tenant, drop only that schema, delete the `Tenant` row, restore the dump into the
  now-empty same-name schema, re-register the tenant/domain, validate, and reactivate. This is
  destructive and must be rehearsed on staging first.
- **Do not** create a second schema with a different name and expect `tenant_restore` to rename it.

---

## 6. Validation checklist

- [ ] Backup `status=success`, `verified=true`, SHA-256 recorded.
- [ ] Target schema name matches the dump's embedded schema name.
- [ ] `tenant_restore --dry-run` passed before the real restore.
- [ ] `tenant_restore` status `success`.
- [ ] Student, staff, exeat, roll-call, and SMS row counts match the source snapshot.
- [ ] `django_migrations` count matches; no pending migrations for the tenant schema.
- [ ] `SchoolConfiguration.name` matches the school.
- [ ] `tenant_list` shows the restored student count.
- [ ] `tenant_usage --schema=<schema>` succeeds.
- [ ] Application login, dashboard, student list, roll call, and exeat pages work.
- [ ] A sample photo/ID export opens (if the media archive was restored).
- [ ] A second restore is refused without deleting data.
- [ ] `PlatformAuditLog` contains the backup/restore actions and operator.
- [ ] Old database/backup retained for rollback; retention date recorded.

---

## 7. Media restore

- `tenant_backup --include-media` writes `<schema>_media_<timestamp>.tar.gz` beside the dump.
- `tenant_restore --media-archive=<archive>` restores the media after the database restore. Use
  `--allow-empty-media` at backup time only when the school genuinely has no media.
- Filesystem mode writes under the tenant media root; S3 mode writes to the tenant prefix. Verify
  the destination backend and free space/bucket permissions before restoring.
- The 2026-10-20 drill was **database-only**; the sample tenant had no media files. Run a separate
  media drill with at least one photo and one ID export before claiming media recovery is tested.

---

## 8. Rollback and cleanup

1. Do not delete the old database, schema, dump, or media archive until the school signs off.
2. If validation fails, switch the application connection back to the old database (full restore)
   or restore the old schema state through the approved decommission/reprovision process.
3. Record the failure, the exact command, and the checksum in the incident log. Do not retry by
   disabling the command's safety checks.
4. Remove temporary target databases and plaintext drill archives after the evidence is captured.
5. Rotate any credentials that were exposed during the incident.

---

## 9. Drill evidence — 2026-10-20 (local, disposable)

| Item | Result |
| :--- | :--- |
| PostgreSQL | 18.3 (local disposable cluster, port 55432) |
| Source database | `restore_drill_source` |
| Target database | `restore_drill_target` |
| Tenant schema | `restore_drill` |
| Backup command | `tenant_backup --schema=restore_drill --verify --operator=batch2-drill` |
| Dump | `restore_drill_20261020_071218.dump` (135,535 bytes) |
| SHA-256 | `7c64d33eec5556e9dcef6b00717b391365d0d27e4900d5db5745230a3d2093a3` |
| Restore dry run | `status: dry_run`, checksum and schema matched |
| Restore | `status: success` in 1.57 s |
| Source counts | students=1, migrations=81, configuration=`Restore Drill School` |
| Target counts | students=1, migrations=81, configuration=`Restore Drill School` |
| Registration | `Tenant` + `Domain` created after restore; `tenant_list` showed 1 student; `tenant_usage` succeeded |
| Duplicate restore | Refused: `Schema 'restore_drill' already exists and is not empty.` |
| Media | Not exercised (no media files in the drill tenant) |

The drill used the application's normal `tenant_backup`/`tenant_restore` commands and the local
PostgreSQL client tools. It did not touch production or remote data.

---

## 10. Open items

- **F-26:** backups are still local, unencrypted, unscheduled, and have no retention/offsite
  policy. The drill proves the command works; it does not close F-26.
- **Staging drill:** repeat sections 4–6 against staging with a real school backup and a media
  archive, then record the result here.
- **Switchover rehearsal:** rehearse the full-database switch in section A with the school's
  authorised contact and a rollback decision point.
- **PITR:** confirm whether the Neon plan provides point-in-time recovery and document its window;
  this runbook does not cover it.
