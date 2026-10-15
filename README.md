# SHS Boarding and Dorm Management System
## System Administrator & Operations Manual

An enterprise school management system designed for Senior High Schools, featuring boarding and dormitory management, staff jurisdictional access, exeat life cycle tracking with automated SMS notifications, roll-call attendance verification, student ID card printing with bearer QR verification, and schema-isolated multi-school tenancy.

---

## Table of Contents

1. [System Overview & Architecture](#1-system-overview--architecture)
2. [Environment Configuration Reference (`.env`)](#2-environment-configuration-reference-env)
3. [Local Development Setup (Zero to Running)](#3-local-development-setup-zero-to-running)
4. [Cloud Production Deployment (Render + Neon)](#4-cloud-production-deployment-render--neon)
5. [Database Management & Migrations](#5-database-management--migrations)
6. [Multi-School Tenancy Rollout & Pilot Onboarding](#6-multi-school-tenancy-rollout--pilot-onboarding)
7. [Background Workers & Scheduled Tasks (Celery & Celery Beat)](#7-background-workers--scheduled-tasks-celery--celery-beat)
8. [Daily Operations, Shell & Disaster Recovery](#8-daily-operations-shell--disaster-recovery)
9. [Core System Workflows & Access Control](#9-core-system-workflows--access-control)
10. [Operational Troubleshooting & FAQs](#10-operational-troubleshooting--faqs)

---

## 1. System Overview & Architecture

### 1.1 Technical Stack

* **Framework**: Django 6.x (Python 3.12+)
* **Database**: PostgreSQL 14+ (Local PostgreSQL or Serverless Neon PostgreSQL)
* **Multi-Tenancy**: `django-pgschemas` (Schema-per-tenant isolation)
* **Message Broker & Cache**: Redis 6.0+
* **Asynchronous Workers**: Celery 5.x & Celery Beat (Database-backed periodic scheduler)
* **Frontend**: Django Templates + Tailwind CSS + HTMX
* **Static Assets**: WhiteNoise (`CompressedManifestStaticFilesStorage`)
* **Media & File Storage**: Local Protected Filesystem or Neon Object Storage (AWS S3-compatible, SigV4)
* **External Integrations**: SMSOnlineGH (REST API for Ghana Telecom SMS dispatch)

### 1.2 Dual-Mode Architecture

The platform operates in one of two architectural modes controlled by the `TENANCY_ENABLED` flag:

```
                            ┌────────────────────────────────────────┐
                            │           Incoming HTTP Request        │
                            └────────────────────┬───────────────────┘
                                                 │
                                       [ TENANCY_ENABLED? ]
                                        /                \
                                  (False)                (True)
                                    /                        \
                    ┌──────────────────────┐    ┌─────────────────────────────────┐
                    │  Single-School Mode  │    │  VerifiedDomainRoutingMiddleware │
                    │                      │    └────────────────┬────────────────┘
                    │  All tables reside   │                     │ (Resolves Host to Domain)
                    │  in public schema    │            ┌────────┴────────┐
                    └──────────────────────┘            │                 │
                                                   (Matched)         (Unmatched)
                                                       │                 │
                                              ┌────────┴───────┐     [ 404 Not Found ]
                                              │ Activate Schema│     (Fail-Closed)
                                              │ 'tenant_name'  │
                                              └────────┬───────┘
                                                       │
                                              ┌────────┴────────┐
                                              │ Execute View in │
                                              │ Tenant Context  │
                                              └────────┬────────┘
                                                       │
                                              ┌────────┴────────┐
                                              │ activate_public │
                                              │ in finally block│
                                              └─────────────────┘
```

1. **Single-School Legacy Mode (`TENANCY_ENABLED=false`)**:
   * All models and operational tables live directly in the PostgreSQL `public` schema.
   * Host routing uses standard Django `ALLOWED_HOSTS`.
   * Ideal for standalone single-school installations or local feature development.

2. **Multi-School Tenancy Mode (`TENANCY_ENABLED=true`)**:
   * **Public Schema**: Contains global platform tables (`tenancy_tenant`, `tenancy_domain`, `django_celery_beat`, and `django_content_type`).
   * **Tenant Schemas**: Each school receives a dedicated PostgreSQL schema (`achimota_shs`, `atebubu_shs`) containing all school operational data (`auth_user`, `core_*`, and `django_session`).
   * **Connection Hygiene**: Requests and Celery tasks reset the search path to `public` in `finally` blocks, preventing schema contamination across persistent connections (`CONN_MAX_AGE`).
   * **Dynamic CSRF**: Inspects `Origin` headers against verified tenant domains with signal-based cache invalidation and protocol downgrade protection.

---

## 2. Environment Configuration Reference (`.env`)

Create a `.env` file in the project root (`c:\RebuildEden\project 1\.env`).

### 2.1 Complete Environment Variable Catalog

| Variable | Description | Default | Recommended Value |
| :--- | :--- | :--- | :--- |
| `DJANGO_SECRET_KEY` | Cryptographic signing key (min. 50 characters, 5+ unique characters) | *None (Required)* | 64+ random characters |
| `DEBUG` | Enable Django debug mode (must be `False` in production) | `True` | `False` (Production) / `True` (Dev) |
| `ALLOWED_HOSTS` | Comma-separated list of accepted hostnames (auto-overridden to `*` if tenancy is enabled) | `localhost,127.0.0.1` | Production domain list |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated HTTPS origins trusted for form submissions | *Empty* | `https://your-domain.onrender.com` |
| `DATABASE_PROFILE` | Database connection backend profile: `local` or `neon` | `local` | `local` (Dev) / `neon` (Prod) |
| `TENANCY_ENABLED` | Toggle multi-school schema isolation (`true`/`false`) | `false` | `true` (Multi-tenant) |
| `PLATFORM_ADMIN_DOMAIN` | Optional host that returns platform status without activating a school | *None* | `admin.yoursystem.com` |
| **Local PostgreSQL** | | | |
| `DB_NAME` | Local database name | `schoolsystem_db` | `schoolsystem_db` |
| `DB_USER` | Local database user | `postgres` | `postgres` |
| `DB_PASSWORD` | Local database password | `postgres` | Your PostgreSQL password |
| `DB_HOST` | Local database host | `localhost` | `localhost` / `127.0.0.1` |
| `DB_PORT` | Local database port | `5432` | `5432` |
| `DB_SSLMODE` | Local database SSL requirement | *Empty* | Leave empty for local dev |
| **Neon PostgreSQL** | | | |
| `PGHOST` | Neon compute endpoint host | *None* | `ep-xxxx.region.aws.neon.tech` |
| `PGDATABASE` | Neon database name | *None* | `neondb` / your database name |
| `PGUSER` | Neon database username | *None* | `neondb_owner` |
| `PGPASSWORD` | Neon database password | *None* | Provisioned Neon password |
| `PGPORT` | Neon database port | `5432` | `5432` |
| `PGSSLMODE` | Neon TLS mode (`require`, `verify-ca`, `verify-full`) | `require` | `require` |
| **Celery & Redis** | | | |
| `REDIS_URL` | Redis broker connection URL (Requires Redis 6.0+) | `redis://127.0.0.1:6379/0` | `redis://...` / `rediss://...` |
| **SMS Gateway** | | | |
| `SMS_ENABLED` | Global master toggle for SMS notifications | `false` | `true` (if live notifications desired) |
| `SMS_ONLINE_GH_API_KEY` | API key from SMSOnlineGH portal | *Empty* | Provisioned 64-char API key |
| `SMS_SENDER_ID` | Fallback SMS sender ID if school configuration is unset | `ATSEC` | Alphanumeric (max 11 chars) |
| `SMS_TIME_ZONE` | Time zone used for dispatch windows | `UTC` | `Africa/Accra` or `UTC` |
| `SMS_HTTP_TIMEOUT` | Gateway HTTP request timeout in seconds | `10.0` | `10.0` |
| `SMS_MAX_ATTEMPTS` | Maximum retry attempts for failed deliveries | `5` | `5` |
| **Media & S3 Storage** | | | |
| `MEDIA_STORAGE_BACKEND` | Media backend: `filesystem` or `neon_s3` | `filesystem` | `filesystem` (Dev) / `neon_s3` (Prod) |
| `MEDIA_ROOT` | Local directory for uploaded files | `BASE_DIR / media` | Resolved absolute path |
| `NEON_STORAGE_BUCKET` | Neon S3 bucket name | *Empty* | Bucket name (e.g. `uploads`) |
| `AWS_ENDPOINT_URL_S3` | Neon S3 endpoint URL | *Empty* | `https://<bucket>.storage.<region>.neon.tech` |
| `AWS_ACCESS_KEY_ID` | Neon S3 Access Key ID | *Empty* | Provisioned access key |
| `AWS_SECRET_ACCESS_KEY` | Neon S3 Secret Key | *Empty* | Provisioned secret key |
| `AWS_REGION` | Neon S3 Region | *Empty* | E.g. `us-east-2` |
| **Security Hardening** | | | |
| `CSP_REPORT_URI` | Endpoint to collect browser CSP violation reports | *Empty* | `/csp-report/` or HTTPS collector |
| `SECURE_HSTS_SECONDS` | HTTP Strict Transport Security duration (seconds) | `0` | `31536000` (1 year in production) |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | Enforce HSTS on all subdomains | `false` | `true` (when all domains use HTTPS) |
| `SECURE_HSTS_PRELOAD` | Submit domain to browser HSTS preload list | `false` | `true` (only for verified HTTPS apex) |
| **Staging Safety** | | | |
| `SYNTHETIC_PILOT_DATABASE` | Guard variable for `seed_synthetic_pilot` seeder | *Empty* | Exact staging DB name |

---

## 3. Local Development Setup (Zero to Running)

Run all commands from the repository root (`C:\RebuildEden\project 1`).

### 3.1 Prerequisites

1. **Python 3.12+**: Ensure Python is in your system PATH (`py --version` or `python --version`).
2. **PostgreSQL 14+**: Installed and running locally on port 5432.
3. **Redis 6.0+**: Installed and running on `localhost:6379`.
   > [!IMPORTANT]
   > Redis 5.0.14 is obsolete and incompatible with modern Celery Redis clients because it does not support the `HELLO` handshake. You must run Redis 6.0 or newer (via Docker, Memurai Developer, or WSL2).
4. **Node.js 18+ & npm**: For compiling Tailwind CSS.
5. **`uv` Package Manager**: For fast, deterministic Python environment syncing.

### 3.2 Step-by-Step Installation (Windows PowerShell)

#### Step 1: Create Virtual Environment and Install Dependencies
```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv
.\.venv\Scripts\uv.exe sync --locked
```

#### Step 2: Install Frontend Dependencies
```powershell
npm ci
```

#### Step 3: Configure `.env`
Create `.env` in the project root:
```env
DJANGO_SECRET_KEY="local-development-secret-key-replace-with-random-chars-in-prod"
DEBUG=True
DATABASE_PROFILE=local
DB_NAME=schoolsystem_db
DB_USER=postgres
DB_PASSWORD=your_postgres_password
DB_HOST=localhost
DB_PORT=5432
REDIS_URL=redis://127.0.0.1:6379/0
TENANCY_ENABLED=false
```

#### Step 4: Create the Local PostgreSQL Database
Open PostgreSQL CLI (`psql`) or pgAdmin and run:
```sql
CREATE DATABASE schoolsystem_db;
```

#### Step 5: Apply Migrations & Create Superuser
```powershell
.\.venv\Scripts\python.exe .\manage.py migrate --settings=schoolsystem.settings.development
.\.venv\Scripts\python.exe .\manage.py createsuperuser --settings=schoolsystem.settings.development
```

### 3.3 Running the Development Environment

Keep four separate PowerShell terminals open:

* **Terminal 1 (Web Server)**:
  ```powershell
  .\.venv\Scripts\python.exe .\manage.py runserver 127.0.0.1:8000 --settings=schoolsystem.settings.development
  ```
* **Terminal 2 (Celery Worker)**:
  ```powershell
  .\.venv\Scripts\celery.exe -A schoolsystem worker --pool=solo --concurrency=1 --loglevel=INFO
  ```
  > [!TIP]
  > On Windows, always use `--pool=solo --concurrency=1`. The default Celery prefork pool raises `PermissionError: [WinError 5]` on Windows systems.
* **Terminal 3 (Celery Beat Scheduler)**:
  ```powershell
  .\.venv\Scripts\celery.exe -A schoolsystem beat --loglevel=INFO
  ```
* **Terminal 4 (Tailwind CSS Watcher - Optional)**:
  ```powershell
  npm run dev
  ```

Access the application in your browser at `http://127.0.0.1:8000/`.

---

## 4. Cloud Production Deployment (Render + Neon)

### 4.1 Neon Serverless PostgreSQL Setup

1. **Create a Neon Project**:
   * Sign in to [Neon Console](https://console.neon.tech/).
   * Create a new project (e.g., `schoolsystem-prod`) and select your nearest AWS region (e.g., `us-east-2`).
2. **Collect Connection Details**:
   * Under **Dashboard → Connection Details**, select **Parameters Only**:
     * Host (`PGHOST`)
     * Database (`PGDATABASE`)
     * Username (`PGUSER`)
     * Password (`PGPASSWORD`)
     * Port (`5432`)
3. **Configure S3 Object Storage (Neon Storage)**:
   * Navigate to **Storage** in your Neon project dashboard.
   * Create a private bucket (e.g., `uploads`).
   * Generate S3 credentials with `storage:read` and `storage:write` scopes.
   * Note the endpoint URL, region, Access Key ID, and Secret Key.

### 4.2 Render Services Architecture

A complete deployment consists of three Render services and one Redis instance:

```
┌────────────────────────────────────────────────────────┐
│                      Render Cloud                      │
│                                                        │
│   ┌────────────────────┐      ┌────────────────────┐   │
│   │ Render Web Service │      │ Render Redis       │   │
│   │ (Gunicorn + Django)│◄────►│ (Broker & Cache)   │   │
│   └─────────┬──────────┘      └─────────▲──────────┘   │
│             │                           │              │
│             │                 ┌─────────┴──────────┐   │
│             │                 │ Celery Worker      │   │
│             │                 │ (Background Tasks) │   │
│             │                 └────────────────────┘   │
│             │                           ▲              │
│             │                 ┌─────────┴──────────┐   │
│             │                 │ Celery Beat        │   │
│             │                 │ (Periodic Cron)    │   │
│             │                 └────────────────────┘   │
└─────────────┼──────────────────────────────────────────┘
              ▼
   ┌──────────────────────┐
   │ Neon PostgreSQL & S3 │
   └──────────────────────┘
```

#### Service 1: Render Web Service
* **Type**: Web Service (Python)
* **Environment**: Python 3.12+
* **Build Command**:
  ```bash
  pip install -r requirements.txt && python manage.py collectstatic --noinput --settings=schoolsystem.settings.production
  ```
* **Start Command**:
  ```bash
  gunicorn schoolsystem.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --threads 2 --timeout 60
  ```
* **Health Check Path**: `/healthz`

#### Service 2: Render Background Worker
* **Type**: Background Worker
* **Build Command**:
  ```bash
  pip install -r requirements.txt
  ```
* **Start Command**:
  ```bash
  celery -A schoolsystem worker --loglevel=INFO --concurrency=4
  ```

#### Service 3: Render Celery Beat Worker
* **Type**: Background Worker
* **Build Command**:
  ```bash
  pip install -r requirements.txt
  ```
* **Start Command**:
  ```bash
  celery -A schoolsystem beat --loglevel=INFO
  ```

#### Service 4: Redis Instance
* Provision a **Render Managed Redis** instance or an external Redis 6+ instance.
* Copy the Internal Redis URL into the `REDIS_URL` environment variable for all three services.

### 4.3 Required Production Environment Variables (Render Dashboard)

Apply these variables across the Web Service, Worker, and Beat services:

```env
DJANGO_SETTINGS_MODULE=schoolsystem.settings.production
DATABASE_PROFILE=neon
PGHOST=ep-xxxx.us-east-2.aws.neon.tech
PGDATABASE=neondb
PGUSER=neondb_owner
PGPASSWORD=npg_xxxxxxxxxxxx
PGPORT=5432
PGSSLMODE=require
REDIS_URL=redis://red-xxxxxxxx:6379
DJANGO_SECRET_KEY=at-least-50-random-characters-kept-strictly-secret
ALLOWED_HOSTS=your-app.onrender.com,portal.yourschool.com
CSRF_TRUSTED_ORIGINS=https://your-app.onrender.com,https://portal.yourschool.com
MEDIA_STORAGE_BACKEND=neon_s3
NEON_STORAGE_BUCKET=uploads
AWS_ENDPOINT_URL_S3=https://br-xxxx.storage.us-east-2.aws.neon.tech
AWS_REGION=us-east-2
AWS_ACCESS_KEY_ID=nak_live_xxxxxxxx
AWS_SECRET_ACCESS_KEY=nsk_live_xxxxxxxx
SMS_ENABLED=True
SMS_ONLINE_GH_API_KEY=your_smsonlinegh_key
SMS_SENDER_ID=ATSEC
TENANCY_ENABLED=True
```

---

## 5. Database Management & Migrations

### 5.1 Single-Tenant Mode Migrations (`TENANCY_ENABLED=false`)

When multi-tenancy is disabled, migrations operate against the `public` schema using Django's standard migration commands:

```powershell
# Check migration status
.\.venv\Scripts\python.exe .\manage.py showmigrations

# Apply all pending migrations
.\.venv\Scripts\python.exe .\manage.py migrate --settings=schoolsystem.settings.production
```

### 5.2 Multi-Tenant Mode Migrations (`TENANCY_ENABLED=true`)

In multi-tenant mode, migrations are divided into two categories:

#### 1. Public Schema Migrations (Shared Platform Tables)
Applies migrations for `tenancy`, `django_celery_beat`, and `django_content_type`:
```powershell
.\.venv\Scripts\python.exe .\manage.py migrate --schema public --settings=schoolsystem.settings.production
```

#### 2. Tenant Schemas Migrations (School Operational Data)
Applies migrations for `auth`, `core`, and `sessions` across all active tenant schemas:
```powershell
# Migrate all tenant schemas at once
.\.venv\Scripts\python.exe .\manage.py migrate --include-dynamic-schemas --settings=schoolsystem.settings.production

# Or migrate a single specific tenant schema
.\.venv\Scripts\python.exe .\manage.py migrate --schema achimota_shs --settings=schoolsystem.settings.production
```

> [!NOTE]
> When provisioning a new school via `manage.py provision_school`, tenant migrations run automatically for that schema.

---

## 6. Multi-School Tenancy Rollout & Pilot Onboarding

### 6.1 Architecture & Isolation Guarantees

* **PostgreSQL Schema Isolation**: Each school's data is physically separated inside its own PostgreSQL schema. Queries within a school cannot see or join data from another school.
* **Regex-Validated Schema Identifiers**: Schemas are constrained at both the model and PostgreSQL database level to `^[a-z][a-z0-9_]*$`, preventing SQL injection or broken search paths.
* **Unique Primary Domain**: A database-level `UniqueConstraint` ensures that at most one primary domain exists per school.
* **Fail-Closed Host Routing**: Requests to unverified, inactive, or unknown domains receive an immediate `404 Not Found`.

### 6.2 Step-by-Step Pilot Onboarding Runbook

Follow this runbook to onboard and verify a new school:

```
[ Step 1: Set TENANCY_ENABLED=true ]
                  │
[ Step 2: Migrate Public Schema ] ──> python manage.py migrate --schema public
                  │
[ Step 3: Provision School Tenant ] ──> python manage.py provision_school
                  │                     (Creates schema, migrates apps, seeds config)
[ Step 4: Attach Domain Aliases ] ──> python manage.py add_school_domain (Optional)
                  │
[ Step 5: Seed Synthetic Rehearsal ] ──> python manage.py seed_synthetic_pilot (Optional)
                  │
[ Step 6: Create School Admin ] ──> python manage.py createsuperuser --schema=<name>
                  │
[ Step 7: Verify & Activate ] ──> python manage.py set_school_status --activate
                  │
        [ School is Live! ]
```

#### Step 1: Ensure Environment Configuration
Verify in `.env` or environment secrets:
```env
TENANCY_ENABLED=true
DATABASE_PROFILE=neon  # or local
```

#### Step 2: Ensure Public Platform Tables Are Migrated
```powershell
.\.venv\Scripts\python.exe .\manage.py migrate --schema public --settings=schoolsystem.settings.production
```

#### Step 3: Provision the School Tenant
Creates the PostgreSQL schema, runs tenant migrations, seeds the default `SchoolConfiguration` singleton (school name & GSM-safe SMS sender ID), and leaves the school **inactive**:
```powershell
.\.venv\Scripts\python.exe .\manage.py provision_school `
  --name "Achimota Senior High" `
  --schema "achimota_shs" `
  --domain "achimota.yoursystem.com" `
  --confirm-domain-verified `
  --settings=schoolsystem.settings.production
```

#### Step 4: Attach Secondary Domains or Aliases (Optional)
```powershell
.\.venv\Scripts\python.exe .\manage.py add_school_domain `
  --schema "achimota_shs" `
  --domain "portal.achimota.edu.gh" `
  --confirm-domain-verified `
  --settings=schoolsystem.settings.production
```
*(Add `--primary` if switching this domain to be the canonical primary address.)*

#### Step 5: Seed Synthetic Pilot Rehearsal Data (Optional)
To test workflows without real student records, populate 100 fictional learners, houses, dormitories, and test staff accounts:
```powershell
$env:SYNTHETIC_PILOT_DATABASE = "<exact database name>"
.\.venv\Scripts\python.exe .\manage.py seed_synthetic_pilot `
  --schema "achimota_shs" `
  --apply `
  --confirm-empty-staging `
  --settings=schoolsystem.settings.production
```

#### Step 6: Create the Institutional Superuser
Provision the school's head administrator account inside its tenant schema:
```powershell
.\.venv\Scripts\python.exe .\manage.py createsuperuser `
  --schema achimota_shs `
  --settings=schoolsystem.settings.production
```

#### Step 7: Validate and Activate the School
The `set_school_status` command inspects PostgreSQL `information_schema` to confirm tables exist and a domain is attached before activating:
```powershell
.\.venv\Scripts\python.exe .\manage.py set_school_status `
  --schema "achimota_shs" `
  --activate `
  --settings=schoolsystem.settings.production
```

The school is now live at `https://achimota.yoursystem.com/`.

To suspend access during term breaks or maintenance:
```powershell
.\.venv\Scripts\python.exe .\manage.py set_school_status `
  --schema "achimota_shs" `
  --deactivate `
  --settings=schoolsystem.settings.production
```

### 6.3 Existing-School Cutover (`migrate_legacy_school`)

To migrate an existing single-school installation from the `public` schema into a new tenant schema without data loss:

1. Pause web traffic and stop Celery worker and Beat processes.
2. Back up the PostgreSQL database and storage directory.
3. Execute the adoption migration command:
   ```powershell
   $env:TENANCY_ENABLED = "true"
   .\.venv\Scripts\python.exe .\manage.py migrate --schema public --settings=schoolsystem.settings.production
   .\.venv\Scripts\python.exe .\manage.py migrate_legacy_school `
     --name "Atebubu Senior High" `
     --schema "atebubu_shs" `
     --domain "atebubu.yoursystem.com" `
     --confirm-domain-verified `
     --settings=schoolsystem.settings.production
   ```
4. **How It Works**:
   * Uses Kahn's topological sort algorithm to copy tables in foreign-key dependency order.
   * **Superuser-Free**: Does not execute `DISABLE TRIGGER ALL`, ensuring full compatibility with cloud databases like Neon, AWS RDS, and Supabase.
   * Namespaces referenced uploads from `students/photos/...` to `tenants/atebubu_shs/students/photos/...`.
   * Leaves the source `public` data completely untouched for a safe fallback.
5. Validate data inside `atebubu_shs`, then activate the school and restart services.

---

## 7. Background Workers & Scheduled Tasks (Celery & Celery Beat)

### 7.1 Multi-Tenant Task Execution Model

Celery workers process asynchronous jobs across all schools while enforcing connection hygiene:

1. **Socket Isolation (`worker_process_init`)**: Pre-fork worker child processes close all inherited parent database sockets upon spawning, preventing socket-descriptor sharing.
2. **Schema & Connection Recycling (`task_prerun` / `task_postrun`)**:
   * Stale/idle database connections are closed.
   * PostgreSQL search path is reset to `public` before and after every task execution.
3. **Tenant Context Decoration (`@_with_tenant_schema`)**: Direct tasks (such as `send_sms_delivery_task` and `generate_student_id_export`) accept `tenant_schema` and switch the connection schema context before executing.

### 7.2 Scheduled Periodic Tasks Catalog (Celery Beat)

All scheduled periodic tasks are defined in `CELERY_BEAT_SCHEDULE` and automatically fan out across active tenants using `_run_for_each_tenant`:

| Task Name | Interval | Multi-Tenant Behavior | Description |
| :--- | :--- | :--- | :--- |
| `dispatch_queued_sms_deliveries` | 60 sec | Fans out to each active tenant | Recovers stale processing deliveries and dispatches up to 100 queued SMS notifications via SMSOnlineGH. |
| `check-overdue-exeats-for-sms` | 15 min | Fans out to each active tenant | Identifies approved exeats where the student is currently out and expected return has passed; queues overdue SMS reminders. |
| `expire-unfinished-roll-calls` | 60 sec | Fans out to each active tenant | Automatically deletes roll-call sessions that have remained in progress for longer than 1 hour and notifies the session creator. |
| `cleanup-expired-student-id-exports` | 24 hours | Fans out to each active tenant | Deletes generated student ID ZIP archives that have exceeded their expiration date and unlinks the export records. |

---

## 8. Daily Operations, Shell & Disaster Recovery

### 8.1 Tenant-Aware Django Shell

In multi-tenant mode, querying the database in a standard shell defaults to `public`, where `auth_user` and `core_*` tables do not exist. Use one of two tenant-aware patterns:

#### Method 1: Using the `--schema` CLI Flag (Recommended)
```powershell
.\.venv\Scripts\python.exe .\manage.py shell --schema achimota_shs --settings=schoolsystem.settings.production
```

#### Method 2: Dynamic Schema Switching Inside the Shell
```python
from tenancy.models import Tenant
from django_pgschemas.schema import activate, activate_public

# Look up and activate the target school
tenant = Tenant.objects.get(schema_name="achimota_shs")
activate(tenant)

# All ORM queries now operate strictly inside 'achimota_shs'
from core.models import Student, SchoolConfiguration, Exeat
print("School Name:", SchoolConfiguration.get_solo().name)
print("Enrolled Students:", Student.objects.filter(status=Student.EnrollmentStatus.ENROLLED).count())

# Return to public schema when finished
activate_public()
```

### 8.2 Static Assets Collection (`collectstatic`)

Static assets (JavaScript, CSS, fonts, admin assets, and HTMX libraries) are compiled once globally and served via WhiteNoise:

```powershell
.\.venv\Scripts\python.exe .\manage.py collectstatic --noinput --settings=schoolsystem.settings.production
```

* **Static Files (`/static/`)**: Shared, immutable frontend code bundles.
* **Media Uploads (`/media/` or S3)**: Tenant-specific dynamic files (school crests, student portraits). These are stored under `tenants/<schema_name>/...` and never mixed with static code.

### 8.3 Tenant Database Backup & Restoration

To back up an individual school tenant without dumping the entire PostgreSQL database:

#### 1. Dump a Tenant Schema
```powershell
# Binary archive format (recommended for pg_restore):
pg_dump -h <host> -U <user> -d <db_name> -n achimota_shs -F c -f "backup_achimota_shs_$(Get-Date -Format 'yyyyMMdd_HHmmss').dump"

# Or plain SQL format:
pg_dump -h <host> -U <user> -d <db_name> --schema=achimota_shs -f "backup_achimota_shs.sql"
```

#### 2. Dump the Platform Directory (Public Schema)
Preserves tenant registry, verified domains, and Celery beat schedules:
```powershell
pg_dump -h <host> -U <user> -d <db_name> -n public -F c -f "backup_public_$(Get-Date -Format 'yyyyMMdd_HHmmss').dump"
```

#### 3. Back Up Tenant Files
* **Local Filesystem**: Back up `media/tenants/<schema_name>/` and `.private/student_photos/`.
* **Neon S3 Object Storage**: Copy the tenant prefix:
  ```powershell
  aws s3 sync s3://<bucket_name>/tenants/achimota_shs/ ./backup_files_achimota/ --endpoint-url <endpoint>
  ```

#### 4. Restore a Tenant Schema
```powershell
pg_restore -h <host> -U <user> -d <db_name> -n achimota_shs "backup_achimota_shs.dump"
```

### 8.4 Resetting or Decommissioning a Tenant Schema

#### Scenario A: Resetting School Data (Truncating Without Dropping Schema)
To wipe all student, attendance, exeat, and staff records for a fresh cycle while keeping migrations, schema structure, and domain configurations intact:

```sql
-- Connect to the database and run within the target schema:
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT tablename 
        FROM pg_tables 
        WHERE schemaname = 'achimota_shs' 
          AND tablename NOT IN ('django_migrations')
    ) LOOP
        EXECUTE 'TRUNCATE TABLE achimota_shs.' || quote_ident(r.tablename) || ' CASCADE';
    END LOOP;
END $$;
```

After truncating, re-seed the required `SchoolConfiguration` singleton:
```powershell
.\.venv\Scripts\python.exe .\manage.py shell --schema achimota_shs -c "from core.models import SchoolConfiguration; SchoolConfiguration.objects.get_or_create(id=1, defaults={'name': 'Achimota Senior High', 'short_name': 'ACHIMOTA'})"
```

#### Scenario B: Complete Decommissioning (Dropping a Tenant Schema)
To permanently delete a school:

1. **Deactivate the school**:
   ```powershell
   .\.venv\Scripts\python.exe .\manage.py set_school_status --schema "achimota_shs" --deactivate --settings=schoolsystem.settings.production
   ```
2. **Drop the PostgreSQL schema**:
   ```sql
   DROP SCHEMA IF EXISTS achimota_shs CASCADE;
   ```
3. **Delete the Tenant record** (cascades and deletes registered `Domain` records in `public`):
   ```powershell
   .\.venv\Scripts\python.exe .\manage.py shell -c "from tenancy.models import Tenant; Tenant.objects.filter(schema_name='achimota_shs').delete()"
   ```
4. **Remove isolated tenant files**:
   ```powershell
   Remove-Item -Recurse -Force "media/tenants/achimota_shs"
   ```

---

## 9. Core System Workflows & Access Control

### 9.1 Staff Roles & Jurisdictional Matrix

| Role | System Scope | Permissions |
| :--- | :--- | :--- |
| **Administrator (`ADMIN`)** | Entire School | Full system configuration, academic terms, SMS configuration, staff role assignment, student registry, housemaster assignments, audit logs. |
| **Senior Housemaster (`SENIOR_HOUSEMASTER`)** | All Houses & Dorms | School-wide student registry viewing, exeat approval across all houses, roll-call reconciliation, disciplinary records, ID card generation. |
| **Housemaster (`HOUSEMASTER`)** | Assigned House Only | Scoped to assigned house. Approve exeats for house students, conduct evening roll calls, follow up on absences, view house student records. |
| **Tutor (`TUTOR`)** | Assigned House Only | Assist housemasters in roll-call verification and dorm inspection within assigned house. |
| **Gate Officer (`GATE_OFFICER`)** | Main Gate | Dedicated gate scanner interface. Record actual departure and arrival times for students on approved exeat. Read-only exeat verification. |

### 9.2 Student Roster Import Specifications

Administrators can import student rosters via modern Excel (`.xlsx`) or UTF-8 CSV up to 10 MB and 2,000 rows.

* **Required Columns**: `Admission No`, `Index No`, `Full Name`, `Gender` (`M`/`F`), `Programme`.
* **Optional Columns**: `Residency` (`Boarder`/`Day`), `House`, `Dormitory`, `Room`, `Guardian Name`, `Guardian Phone`.
* **Validation Rules**:
  * Duplicate admission or index numbers reject the entire import batch.
  * Boarding students must resolve to a valid house and gender-compatible dormitory block.
  * If room capacity is exceeded, the upload is rejected.
  * Importer never silently updates or overwrites existing students.

### 9.3 Exeat Life Cycle & SMS Notifications

```
[ Student Exeat Request ] ──> [ Housemaster Review ] ──> (Approved)
                                        │                      │
                                   (Rejected)                  ▼
                                        │             [ SMS: Approved ]
                                        ▼                      │
                                 [ SMS: Rejected ]             ▼
                                                      [ Main Gate Departure ]
                                                               │
                                                               ▼
                                                      [ SMS: Left Campus ]
                                                               │
                                                               ▼
                                                      [ Main Gate Return ]
                                                               │
                                                               ▼
                                                      [ SMS: Safe Return ]
```

* **Overdue Alerts**: If an approved student fails to return by their `expected_return` timestamp, Celery Beat detects the overdue state and dispatches daily SMS alerts to guardians until the student is recorded returned.

### 9.4 Roll-Call Attendance Verification

* Sessions begin with students marked **Unmarked**.
* Active gate departures automatically set the student to **On Exeat**.
* In-progress roll calls expire automatically after 1 hour if left unfinished to prevent stale roster locks.
* Unexcused absences enter the Authorized Follow-Up queue for investigation.

### 9.5 Student ID Cards & Public QR Bearer Checks

* Print CR80-sized dual-sided student ID cards with high-resolution portraits.
* Back side features a unique QR code pointing to `/verify/student/<public_token>/`.
* **Privacy Controls**: Scanning the QR code displays the student's name, photo, and latest exeat approval status without disclosing phone numbers, home addresses, disciplinary records, or historical requests.
* Administrators can invalidate and reissue public tokens instantly if an ID card is lost.

---

## 10. Operational Troubleshooting & FAQs

### 10.1 Celery Multiprocessing Error on Windows
* **Symptom**: `PermissionError: [WinError 5] Access is denied` when starting Celery worker.
* **Cause**: Python's `billiard` multiprocessing library cannot fork on Windows.
* **Fix**: Always start workers on Windows with `--pool=solo --concurrency=1`:
  ```powershell
  .\.venv\Scripts\celery.exe -A schoolsystem worker --pool=solo --concurrency=1 --loglevel=INFO
  ```

### 10.2 Redis Handshake Error (`HELLO` command)
* **Symptom**: `redis.exceptions.ResponseError: unknown command 'HELLO'`.
* **Cause**: Redis server version is older than 6.0 (e.g., Redis 5.0.14).
* **Fix**: Upgrade Redis to version 6.2 or 7.x, or use a Docker/cloud Redis instance.

### 10.3 `UndefinedTable` Error in Django Shell or Tests
* **Symptom**: `django.db.utils.ProgrammingError: relation "auth_user" does not exist`.
* **Cause**: Running database queries while connected to the `public` schema in multi-tenant mode (`TENANCY_ENABLED=true`).
* **Fix**: Activate a tenant schema before querying:
  ```powershell
  python manage.py shell --schema achimota_shs
  ```

### 10.4 Dynamic CSRF Origin Rejection
* **Symptom**: HTTP 403 Forbidden: `Origin checking failed - https://school.customdomain.com does not match any trusted origins`.
* **Cause**: The domain has not been registered in `Domain` table, is unverified, or an HTTP origin was submitted to an HTTPS endpoint.
* **Fix**:
  1. Confirm the domain is registered and verified:
     ```powershell
     python manage.py add_school_domain --schema <schema> --domain school.customdomain.com --confirm-domain-verified
     ```
  2. Verify cache invalidation signal fired, or restart the web service to refresh the in-memory domain cache.

### 10.5 Render Health Check Fails (`/healthz`)
* **Symptom**: Render web service deployment times out or reports unhealthy.
* **Cause**: Health check path `/healthz` is blocked by tenant resolution middleware.
* **Fix**: `VerifiedDomainRoutingMiddleware` contains built-in bypasses for `/healthz`, `/healthz/`, `/live`, and `/live/`. Ensure Render's Health Check Path setting is set to `/healthz`.

### 10.6 Neon Statement Timeout (`30000ms`)
* **Symptom**: `django.db.utils.OperationalError: canceling statement due to statement timeout`.
* **Cause**: A bulk migration, heavy report, or large student import exceeded the 30-second statement timeout configured in `DATABASES["default"]["OPTIONS"]`.
* **Fix**: Management commands like `migrate_legacy_school` set `SET LOCAL statement_timeout = '0'`. For one-off large queries in the shell, execute:
  ```python
  from django.db import connection
  with connection.cursor() as cursor:
      cursor.execute("SET statement_timeout = 0;")
  ```
