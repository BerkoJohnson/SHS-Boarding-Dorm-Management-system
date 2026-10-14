# SHS Boarding and Dorm Management System

## Local development startup (Windows PowerShell)

Run commands from the repository root (`C:\RebuildEden\project 1`). Keep the
server, Celery worker, and Celery Beat running in separate PowerShell terminals.

### First-time setup

1. Install Python 3.12 or newer, PostgreSQL, Node.js, and a Redis server version
   6 or newer. The Redis server must be reachable at the URL in `REDIS_URL`.
   Redis 5.0.14 is too old for the Redis client used by Celery.
2. Create the virtual environment and install the Python requirements:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

   If `.venv` already exists and is working, skip these commands.
3. Create a `.env` file in the repository root and configure the database
   connection (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`),
   `REDIS_URL`, and the SMS settings described under
   [Exeat SMS notifications](#exeat-sms-notifications). The local Redis URL is
   `redis://127.0.0.1:6379/0`.
   Keep the `DB_*` values for local PostgreSQL. To switch to Neon without
   replacing them, set `DATABASE_PROFILE=neon` and configure `PGHOST`,
   `PGDATABASE`, `PGUSER`, `PGPASSWORD`, and optionally `PGPORT` (defaults to
   `5432`). The Neon profile requires TLS by default; override it only with
   `PGSSLMODE` if needed. Set `DATABASE_PROFILE=local` to switch back.
4. Install the frontend dependencies if you want to run the Tailwind watcher:

   ```powershell
   npm ci
   ```

### Each time you start the project

1. Start PostgreSQL and Redis using the Windows Services app or the method used
   to install them. If they are Windows services, find their service names with:

   ```powershell
   Get-Service *postgres*
   Get-Service *redis*
   ```

   Start a stopped service in an elevated PowerShell with
   `Start-Service -Name "<service-name>"`. Verify the database is listening on
   the configured host and port (the defaults are `localhost:5432`), and verify
   Redis is reachable on the configured host and port (default `127.0.0.1:6379`):

   ```powershell
   Test-NetConnection -ComputerName localhost -Port 5432
   Test-NetConnection -ComputerName 127.0.0.1 -Port 6379
   ```

   If `redis-cli` is installed, confirm the server reports `redis_version` 6 or
   newer:

   ```powershell
   redis-cli -h 127.0.0.1 -p 6379 INFO server
   ```

   If the Redis version is older, upgrade that server or point `REDIS_URL` to a
   Redis 6+ instance before starting Celery.
2. Apply database migrations (also do this after pulling changes that add
   migrations):

   ```powershell
   .\.venv\Scripts\python.exe .\manage.py migrate --settings=schoolsystem.settings.development
   ```
3. In **Terminal 1**, start Django:

   ```powershell
   .\.venv\Scripts\python.exe .\manage.py runserver --settings=schoolsystem.settings.development
   ```

4. In **Terminal 2**, start the Celery worker:

   ```powershell
   .\.venv\Scripts\celery.exe -A schoolsystem worker --pool=solo --concurrency=1 --loglevel=INFO
   ```

   Keep `--pool=solo --concurrency=1` on Windows. The default prefork pool can
   fail on Windows with Billiard multiprocessing `PermissionError: [WinError 5]`.
5. In **Terminal 3**, start Celery Beat for scheduled tasks:

   ```powershell
   .\.venv\Scripts\celery.exe -A schoolsystem beat --loglevel=INFO
   ```

6. Optional, in **Terminal 4**, rebuild CSS automatically while editing
   templates/styles:

   ```powershell
   npm run dev
   ```

## Production deployment and school-trial checklist

The commands above are for local development, not a production deployment.
Before using real student or guardian data:

### Staff access and configuration

- Staff sessions end when the browser closes and expire after 30 minutes of
  inactivity. The login page does not offer a persistent sign-in option.
- Staff who forget their password must contact the system administrator for a
  reset.
- Only staff assigned the `ADMIN` role (or a Django superuser) can open system
  configuration. Django Admin also requires an active staff account and the
  relevant model permissions. Academic-term, SMS, school identity, staff-role,
  and housemaster-assignment changes are recorded in the configuration audit
  log.

1. Configure the production environment with a randomly generated
   `DJANGO_SECRET_KEY` of at least 50 characters, a comma-separated
   `ALLOWED_HOSTS` containing only the deployed hostnames, and the database and
   `REDIS_URL` values. Keep these values in the deployment secret store; do not
   commit them or reuse a development key. Production settings fail at startup
   if the key or allowed hosts are missing. When connecting to Neon, use the
   `DATABASE_PROFILE=neon` connection variables described above; TLS is enabled
   by default for that profile.
2. Run checks and migrations with production settings:

   ```powershell
   .\.venv\Scripts\python.exe .\manage.py check --deploy --settings=schoolsystem.settings.production
   .\.venv\Scripts\python.exe .\manage.py migrate --settings=schoolsystem.settings.production
   .\.venv\Scripts\python.exe .\manage.py collectstatic --noinput --settings=schoolsystem.settings.production
   ```

   Run Django behind a production-grade WSGI server and HTTPS-terminating
   reverse proxy. Configure that server or a trusted static-file service to
   serve `STATIC_ROOT`; do not expose `MEDIA_ROOT` or `.private`.
3. Keep the web process, Celery worker, and Celery Beat running under a process
   manager. Confirm Redis 6 or newer, task execution, scheduled overdue checks,
   expired-export cleanup, and SMS delivery using test recipients before
   enabling school-wide SMS. The `Procfile` defines separate `web`, `worker`,
   and `beat` processes; configure all three with the same production
   environment and Redis broker.
4. Back up PostgreSQL and required private files, including
   `.private/student_photos` and `.private/student_id_exports`, using encrypted,
   access-controlled storage. Test a restore before the trial and define
   retention and incident-response procedures. Keep real roster and export
   files out of Git, shared development machines, and public file storage.
5. Start with synthetic data. Before loading real records, verify staff roles
   and house scoping, account provisioning and recovery, gate and attendance
   workflows, and the school's approval of the public student-ID QR contents.
   A QR link is a bearer link: anyone who obtains it can view the data described
   in [Student ID cards and public exeat checks](#student-id-cards-and-public-exeat-checks).

### Render staging preview

The repository includes a `Procfile` web process for Render. Connect the
GitHub repository to a Render **Web Service**, select the branch containing the
deployment changes, and configure:

- Build command: `pip install -r requirements.txt && python manage.py collectstatic --noinput --settings=schoolsystem.settings.production`
- Start command: `gunicorn schoolsystem.wsgi:application --bind 0.0.0.0:$PORT`
- Environment: `DJANGO_SETTINGS_MODULE=schoolsystem.settings.production`,
  `DATABASE_PROFILE=neon`, a newly generated `DJANGO_SECRET_KEY`,
  `ALLOWED_HOSTS` containing the exact Render hostname, and
  `CSRF_TRUSTED_ORIGINS` containing its HTTPS origin. Add the separate
  staging database's `PGHOST`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`;
  `PGPORT=5432` and `PGSSLMODE=require`. For media, create a private Neon
  Object Storage bucket and set `MEDIA_STORAGE_BACKEND=neon_s3` and
  `NEON_STORAGE_BUCKET=uploads`. In Neon Console, use **Connect → Storage** for
  the endpoint, region, and branch credential; the credential must have
  `storage:read` and `storage:write` scopes. Set those values as
  `AWS_ENDPOINT_URL_S3`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`, and
  `AWS_SECRET_ACCESS_KEY`. Use the endpoint and region returned for the correct
  Neon branch, not the database hostname. The S3 client uses path-style
  addressing and SigV4. Neon buckets are private by default and do not support
  S3 ACL writes: leave the bucket private and do not configure object ACLs.
  The school logo is displayed using a temporary signed URL. Never put storage
  credentials in Git. Re-upload the logo in Django Admin after configuring the
  bucket. See Neon’s [Object Storage setup](https://neon.com/docs/storage/get-started)
  and [authentication guide](https://neon.com/docs/storage/authentication).

Do not point a staging service at a Neon database that contains real student
records. Create an isolated empty staging database and use synthetic data.
Render free web services use ephemeral storage, sleep when idle, and are
intended for testing rather than production. The initial web-only preview does
not start Celery worker/Beat services: create separate Render Background Worker
services using `celery -A schoolsystem worker --loglevel=INFO` and
`celery -A schoolsystem beat --loglevel=INFO`, and give them the same database,
Redis, and Django environment as the web service. Without these services,
queued exports and scheduled SMS, reminder, roll-call expiration, and expired
export cleanup tasks will not run. The preview also lacks durable private
student photo and export storage; student photos remain on protected local
storage and exports are stored locally. Do not use real student photos or
exports until worker/Beat services, durable private storage, and backup/restore
are set up and verified.

### Supervised synthetic-data pilot

Use an isolated, dedicated staging database for rehearsal. Never run the
synthetic seeder against the live school database or a Neon branch that contains
copied production records. The command refuses to write unless the configured
database name is explicitly confirmed, SMS is disabled, and relevant school
tables are empty. It does not delete or overwrite existing data.

The fixture contains 100 fictional learners (80 boarders and 20 day students),
four pilot houses, gender-specific dormitories and rooms, three programmes,
six class groups, and ten inactive test staff accounts covering housemasters,
tutors, a senior housemaster, and a gate officer. Names and identifiers are
synthetic; no guardian contacts, student photos, exeat transactions, or
attendance sessions are created. Test accounts have unusable passwords and
must be individually activated and given unique credentials by an authorized
administrator before use. Create an active staging superuser first so these
accounts can be configured. Do not enable SMS for this dataset.

From the repository root, first review the plan without modifying the database:

```powershell
.\.venv\Scripts\python.exe .\manage.py seed_synthetic_pilot --settings=schoolsystem.settings.production
```

Only after verifying that the selected configuration points to a newly created,
empty staging database, set `DATABASE_PROFILE=neon` and
`SYNTHETIC_PILOT_DATABASE` to the exact staging database name in the current
PowerShell process. Keep credentials in the existing environment/secret store;
do not put them in command arguments or paste them into logs. For example:

```powershell
$env:DATABASE_PROFILE = "neon"
$env:SYNTHETIC_PILOT_DATABASE = "<exact dedicated staging database name>"
```

Then apply:

```powershell
.\.venv\Scripts\python.exe .\manage.py seed_synthetic_pilot --apply --confirm-empty-staging --settings=schoolsystem.settings.production
```

The seeder refuses to mix its records with existing students, guardians,
houses, dormitories, staff profiles, exeats, attendance, or related operational
records. If it refuses, stop and verify the selected database and its contents;
do not work around the guard or delete records to make it pass. Ask the database
administrator to provision a genuinely empty staging database instead.

For the supervised session, use synthetic test accounts only; verify each
account's role and house scope before enabling it. Exercise registration/search,
house boundaries, roll call, exeat approval, gate departure/return, absence
follow-up, reports, and export access with a facilitator observing. Keep SMS
disabled and use the UI to exercise live workflows rather than seeding live
exeats or attendance actions. Record defects without including real student
information. This 100-record dataset is for workflow rehearsal, not a load or
capacity test.

## Multi-school tenancy rollout

Tenancy uses `django-pgschemas` with one PostgreSQL schema per school. The
legacy single-school mode remains the default:

```text
TENANCY_ENABLED=false
```

Do not enable tenancy on a live service until the existing school has been
copied into a tenant schema and that schema has been validated. The
`migrate_legacy_school` command leaves the original public-schema records and
source uploads intact, creates an inactive tenant, and registers the verified
hostname. It does not cut traffic over automatically.

### Domain setup and onboarding

- Configure school subdomains to resolve to the web service. Attach custom
  domains at the hosting provider and complete its DNS and TLS certificate
  verification before registering them. Keep the original Render hostname as
  a verified domain for the existing school.
- Domain routing accepts only exact, manually verified hostnames belonging to
  active tenants. Unknown, unverified, or inactive hostnames receive a 404.
  Do not enable header-based tenant selection.
- When `TENANCY_ENABLED=true`, production sets Django `ALLOWED_HOSTS` to `*`
  because new custom domains are registered in the tenant directory rather
  than a static environment list. The domain-routing middleware runs first and
  performs exact active/verified hostname matching before any school request
  is processed. Keep that middleware first and never bypass it at the proxy.
- Platform operators provision a new school schema only after verifying its
  domain. The schema is created and migrated automatically, but remains
  inactive until explicitly enabled:

  ```powershell
  .\.venv\Scripts\python.exe .\manage.py provision_school --name "<school name>" --schema "<schema_name>" --domain "<school hostname>" --confirm-domain-verified --settings=schoolsystem.settings.production
  .\.venv\Scripts\python.exe .\manage.py add_school_domain --schema "<schema_name>" --domain "<verified alias hostname>" --confirm-domain-verified --settings=schoolsystem.settings.production
  .\.venv\Scripts\python.exe .\manage.py set_school_status --schema "<schema_name>" --activate --settings=schoolsystem.settings.production
  ```

  Use `--primary` with `add_school_domain` when moving a school to a new
  canonical hostname; otherwise it adds an alias.

For a tenant-aware release, first apply the public-schema migrations and then
the dynamic-tenant migrations. `django-pgschemas` replaces the normal
`migrate` command when tenancy is enabled:

```powershell
$env:TENANCY_ENABLED = "true"
.\.venv\Scripts\python.exe .\manage.py migrate --schema public --settings=schoolsystem.settings.production
.\.venv\Scripts\python.exe .\manage.py migrate --include-dynamic-schemas --settings=schoolsystem.settings.production
```

Run schema migrations as a controlled release step. Review the package's
schema selection options with `manage.py migrate --help`; keep public and
tenant migration results in the deployment log.

### Existing-school cutover

Rehearse these steps against an isolated staging database first. On the
production database, take a verified backup of PostgreSQL, public media,
private student photos, and private ID exports; pause web writes and stop
Celery worker/Beat processes during the data copy. Set `TENANCY_ENABLED=true`
only in the one-off migration command environment until validation passes:

```powershell
$env:TENANCY_ENABLED = "true"
.\.venv\Scripts\python.exe .\manage.py migrate --schema public --settings=schoolsystem.settings.production
.\.venv\Scripts\python.exe .\manage.py migrate_legacy_school --name "<current school name>" --schema "<schema_name>" --domain "<current Render hostname>" --confirm-domain-verified --settings=schoolsystem.settings.production
```

The adoption command requires the source core/auth tables to match the newly
migrated tenant schema, preserves database IDs and relationships, copies
referenced uploads under the tenant namespace, and keeps the public source
untouched. Database rows are copied transactionally; file copies can leave
unreferenced destination files if an error interrupts the command. In that
case, inspect the inactive target schema and files before retrying; do not
delete the source.

Tenant migrations create a default SMS configuration row. The adoption command
replaces that row with the existing school's configuration, but refuses to
overwrite any other unexpected target data. If a copy stops after creating the
inactive tenant, rerun the same command with the same name and schema; it can
resume only when that tenant is still inactive and has no registered domains.
If the target contains other data or the tenant has already been activated,
stop and inspect it rather than deleting or reusing the schema.

Validate tenant data, staff login, student photos, school branding, public QR
checks, exeats, scheduled jobs, exports, and isolation before activating the
tenant. Then enable `TENANCY_ENABLED=true` on the web, worker, and Beat
processes, activate the tenant, and test the existing Render hostname and
every custom domain. Keep the public-schema copy and backup until cutover is
accepted.

Before tenant writes begin, disabling tenancy can return the service to the
unchanged public-schema copy. After tenant writes begin, this is not a
lossless rollback: restore from a coordinated backup or reconcile tenant
changes first. Do not drop tenant schemas as part of routine deactivation.

Celery tasks carry explicit tenant schema names; scheduled jobs fan out over
active tenant schemas. Tenant app migrations include the school's
authentication and operational data; `django_celery_beat` and the tenant
directory remain public. Private student photos, logos, and ID export archives
are isolated by tenant path even though they use shared storage infrastructure.
Back up and monitor database schemas and files together.

## Exeat SMS notifications

Configure `SMS_ENABLED`, `SMS_ONLINE_GH_API_KEY`, `SMS_SENDER_ID`, `SMS_TIME_ZONE`,
and `REDIS_URL` in `.env`. Use Redis 6 or newer as the Celery broker; the
legacy Redis 5.0.14 server currently installed on this Windows machine does not
support the `HELLO` handshake used by the Redis Python client. SMS dispatch is
disabled unless `SMS_ENABLED=true`. After migration, the `SMS Configuration`
record in Django Admin controls the live SMS switch and takes precedence over
the environment default. Disable it to stop new SMS queueing, queued-message
dispatch, and overdue reminders; re-enable it to resume processing.
The SMS outbox records each recipient's delivery state and retry history.
Guardian notifications use the configured school short name in the message body,
for example `ATSEC: Dear Berko J, Your ward 'Student One' ...`.

Exeats generate SMS notifications for request submission, approval or rejection,
gate departure and return, and overdue status. Guardians and the student's
assigned Housemaster receive the notices. Overdue reminders are sent at the
expected return time and then once every 24 hours until a return is recorded.

## Roll-call attendance verification

New roll-call rosters begin with students marked **Unmarked**; an active gate
exeat is automatically recorded as **On Exeat**. A gate departure recorded
during an in-progress roll call also marks an unmarked matching entry On Exeat.
It never overwrites a staff-verified status: a discrepancy is shown on the
roster and must be reviewed before the session can be finalized. A gate return
does not automatically mark a student present.

Staff-marked absences enter the authorized follow-up queue and are not conduct
incidents by default. A reviewer can confirm an unexcused absence, record an
authorized reason, or correct a recording error; only confirmation creates a
conduct incident. Follow-up decisions and corrections retain an audit trail.
Attendance status changes do not require a free-text reason; roll call does
not collect diagnoses or punishment details.

Staff must assign each remaining student a status before finalizing the
session. Unmarked students remain in the roster and attendance-rate denominator
but do not count as present. The attendance report also shows the number of
unmarked entries in sessions still in progress.

## Student registration and roster import

Staff accounts have no institutional access until an administrator assigns a
role. A Housemaster role is provisioned only when the account is linked to a
Housemaster record. Django Admin additionally requires an active staff account,
an Administrator or Senior Housemaster role, and the relevant Django model
permission.

Administrators and Senior Housemasters can register or edit students
individually, assign configured programme, class, house and dormitory records,
and upload an optional student portrait. Portraits must be JPEG or PNG, no
larger than 5 MB or 25 megapixels; the registry displays them as cropped
portraits. Portrait files are stored outside `MEDIA_ROOT` and delivered only
through an authenticated, role- and house-scoped endpoint. Bulk photo uploads
are not supported. The privacy migration moves existing portraits from the
public media directory into `.private/student_photos`; back up media files and
ensure this private directory is writable and not web-served before migrating.
In development, only school logos are served from `MEDIA_ROOT`; keep student
rosters and other sensitive files out of any web-served media directory.

The Student Registry search updates as you type (after a two-character
minimum), matching names, admission/index numbers, programme, class, house,
dormitory and room. Combine it with residency, gender, enrollment status,
house, dormitory, room, programme or class filters. The registry table shows
10 results per page and preserves the active search and filters when paging.
Django Admin student search covers the same student and placement details and
provides corresponding list filters with 50 records per page.

The same roles can import a roster from UTF-8 CSV or modern Excel `.xlsx`
files, up to 10 MB and 2,000 student rows. Download the CSV template from the
Student Registry to see the required headers. Admission number, index number,
full name, gender and programme are required. Placement and guardian columns
are optional in the file, but boarding students must resolve to an existing
house and a compatible dormitory. The importer can infer a dormitory only when
the selected house has exactly one suitable block for that student's gender.
Programmes, classes, houses and dormitories must already be configured; unknown
or ambiguous references are reported rather than created automatically.
Use an empty class cell or `Unallocated` when no class has been assigned.
Colored numbered house values such as `Blue House(M), TWO` resolve to a
configured house named `House Two (Blue)`; the compatible dormitory must also
be configured for the student's gender.

The importer validates every worksheet/row before writing any records.
Duplicate admission or index numbers (within the upload or already in the
system), invalid references, or invalid placements reject the entire upload.
It never updates an existing student. A roster import does not include photos;
add photos through an individual student form.

Dormitory blocks can be divided into named rooms with a positive room capacity.
Manage room inventory in Django Admin before assigning students. Once rooms are
configured for a block, individual boarding registration requires a room and
offers rooms with available capacity. A room cannot exceed its configured
capacity, and its capacity cannot be reduced below current occupancy. Existing
boarders without rooms remain valid during rollout and appear in the housing
report as awaiting room placement.

The roster template includes an optional `Room` column. When a room is given,
it must exist in the selected dormitory and have available capacity. If the
column is omitted, the importer assigns a room only when that dormitory has
exactly one configured room; ambiguous placements remain unassigned for later
review.

Student record viewing is a separate permission: Administrators and Senior
Housemasters can view school-wide records, while Housemasters can view records
for students in their assigned house. Other staff are redirected to the
dashboard when they try to access those pages.

## Student ID cards and public exeat checks

Administrators and Senior Housemasters can print a student's ID from the
Student Registry or student record. Choose one of three fixed, school-branded
layouts; the school's configured name and logo are used when available. Cards
are laid out at CR80 dimensions with front and back on separate print pages.
The front includes the student's portrait (or initials), name, admission and
index numbers, programme, class, house, and residency.

The back contains a permanent QR link for that student. Anyone scanning it can
open the public check without signing in. The QR is a bearer link: anyone with
it can see the student's identity and portrait, plus only the latest exeat
request, including its type, approval state, approved departure/return times,
and actual gate departure/return records. It never shows the request reason,
destination, guardian contact details, or older requests. When there is no
request, the check says so. Public check pages are not cached or indexed by
search engines; treat a lost ID as exposed. Administrators and Senior
Housemasters can replace the QR link from the ID card page; previously printed
cards then stop working.
