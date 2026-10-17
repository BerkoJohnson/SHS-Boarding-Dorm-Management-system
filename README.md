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
6. [Multi-School Tenancy Rollout & Platform CLI Management](#6-multi-school-tenancy-rollout--platform-cli-management)
   * [6.1 Tenancy Architecture & Directory Isolation](#61-tenancy-architecture--directory-isolation)
   * [6.2 Multi-School Tenant Provisioning Runbook](#62-multi-school-tenant-provisioning-runbook)
   * [6.3 Existing-School Cutover (`migrate_legacy_school`)](#63-existing-school-cutover-migrate_legacy_school)
   * [6.4 Platform CLI Management Suite](#64-platform-cli-management-suite-tenant-administration--billing)
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
   * **Public Schema**: Contains global platform tables (`tenancy_tenant`, `tenancy_domain`, `django_celery_beat`) and the public `django_content_type` registry.
   * **Tenant Schemas**: Each school receives a dedicated PostgreSQL schema (`achimota_shs`, `atebubu_shs`) containing school operational data (`auth_user`, `core_*`, and `django_session`).
   * **Shared Content Types**: Keep `django.contrib.contenttypes` in `PUBLIC_APPS` only; `django-pgschemas` emits a warning if it is also assigned to tenant schemas.
   * **Connection Hygiene**: Requests and Celery tasks reset the search path to `public` in `finally` blocks, preventing schema contamination across persistent connections (`CONN_MAX_AGE`).
   * **Dynamic CSRF**: Inspects `Origin` headers against verified tenant domains with signal-based cache invalidation and protocol downgrade protection.
   * **Platform operations**: `PLATFORM_ADMIN_DOMAIN` exposes a status-only JSON endpoint; it is not a browser-based admin interface. Manage tenants with the documented Django management commands.

---

## 2. Environment Configuration Reference (`.env`)

Create a `.env` file in the project root (`c:\RebuildEden\project 1\.env`).

### 2.1 Complete Environment Variable Catalog

| Variable | Description | Default | Recommended Value |
| :--- | :--- | :--- | :--- |
| `DJANGO_SECRET_KEY` | Cryptographic signing key (min. 50 characters, 5+ unique characters) | *None (Required)* | 64+ random characters |
| `DEBUG` | Enable Django debug mode. Defaults to `True` only under `schoolsystem.settings.development`; `schoolsystem.settings.production` forces `False` | `True` (development only) | `False` (Production) / `True` (Dev) |
| `DJANGO_SETTINGS_MODULE` | Settings module to load. `manage.py` auto-selects `schoolsystem.settings.production` when `RENDER=true` or `PORT` is set, otherwise `schoolsystem.settings.development` | *Auto-detected* | Set explicitly only to override |
| `ALLOWED_HOSTS` | Comma-separated accepted hostnames. Development defaults to `127.0.0.1,localhost`. **In multi-tenant production (`TENANCY_ENABLED=true`) this is ignored and forced to `*`** — tenant hostnames are validated against the `Domain` table by the routing middleware. In single-tenant production it is required and must be an explicit list | `127.0.0.1,localhost` (development) | Single-tenant: production domain list. Multi-tenant: leave empty |
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
| **Email** | | | |
| `EMAIL_BACKEND` | Django mail transport. Defaults to the console backend, which prints messages to the logs and never sends real mail | `django.core.mail.backends.console.EmailBackend` | A real SMTP/API backend for production |
| `DEFAULT_FROM_EMAIL` | `From:` address for outgoing mail | `no-reply@localhost` | `no-reply@your-domain` |
| **Rate Limiting & Cache** | | | |
| `CACHE_BACKEND` | Cache backend used for rate-limit counters. `LocMemCache` is per-process, so multiple workers each keep their own counters | `django.core.cache.backends.locmem.LocMemCache` | Shared backend (e.g. `django.core.cache.backends.redis.RedisCache`) |
| `CACHE_LOCATION` | Cache location/URL consumed by `CACHE_BACKEND` | `schoolsystem-cache` | Redis URL when a shared cache is used |
| `TRUSTED_PROXY_COUNT` | Trusted reverse-proxy hops used to pick the client IP from `X-Forwarded-For` for rate limiting. Render adds one hop, so `1` is correct | `1` | `1` |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW` | Max login attempts per client IP per window (seconds) | `10` / `300` | As required |
| `PUBLIC_REPORT_RATE_LIMIT` / `PUBLIC_REPORT_RATE_WINDOW` | Max public "report a concern" submissions per IP per window (seconds) | `5` / `3600` | As required |
| `STUDENT_ID_PUBLIC_TOKEN_TTL_DAYS` | Lifetime of public student-ID QR links in days (`0` = never expire) | `0` | As required |
| **Security Hardening** | | | |
| `CSP_REPORT_URI` | Endpoint appended as `report-uri` to the CSP report-only header. No collector is built in; point it at your own endpoint | *Empty* | Safe relative path or HTTPS collector URL |
| `SECURE_HSTS_SECONDS` | HTTP Strict Transport Security duration (seconds). Production defaults to one year; use `0` (or a short value) for a staging host you may still need to reach over plain HTTP | `31536000` (production settings) | `0` (staging) / `31536000` (production) |
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

> A complete, commented template covering every supported variable is provided in `.env.example` — copy it to `.env` and fill in the values.

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

### 3.4 Running the Test Suite

Tests run against PostgreSQL (SQLite cannot exercise schema isolation).

* **Core / single-tenant suites** (default test settings, tenancy forced off):
  ```powershell
  .\.venv\Scripts\python.exe .\manage.py test --settings=schoolsystem.settings.test
  ```
* **Multi-tenant / platform suites** (tenancy enabled; these are skipped under the settings above):
  ```powershell
  .\.venv\Scripts\python.exe .\manage.py test tenancy --settings=schoolsystem.settings.tenancy_test
  ```

`schoolsystem.settings.test` uses the `test_schoolsystem_core` database and `schoolsystem.settings.tenancy_test` uses `test_schoolsystem_tenancy`, so the two runs do not collide. The schema-isolation integration test and the SQL-backed platform/billing tests only execute under the tenancy-enabled settings.

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

> **Use the direct (non-pooler) `PGHOST`.** Multi-tenancy keeps each school in its own PostgreSQL schema and depends on a session-level `search_path`. Neon's pooled host (the one containing `-pooler`) runs PgBouncer in transaction mode and does **not** preserve that session state between statements, so a request could resolve against another school's schema. Never point `PGHOST` at a `-pooler` host — not for the web service, and not for migrations.

3. **Configure S3 Object Storage (Neon Storage)**:
   * Navigate to **Storage** in your Neon project dashboard.
   * Create a private bucket (e.g., `uploads`).
   * Generate S3 credentials with `storage:read` and `storage:write` scopes.
   * Note the endpoint URL, region, Access Key ID, and Secret Key.

### 4.2 Render Services Architecture

A complete deployment consists of three Render services and one Redis instance:

> The repository ships a `render.yaml` Blueprint that encodes the web service, its build and start commands, the health-check path and the environment-variable names (secrets are marked `sync: false`). Apply it via **Dashboard → New → Blueprint**, or configure the services manually as described below.

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
# ALLOWED_HOSTS is ignored while TENANCY_ENABLED=True; tenant hosts come from the Domain table
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
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
CACHE_LOCATION=redis://red-xxxxxxxx:6379/1
TRUSTED_PROXY_COUNT=1
# Optional security hardening (see section 2.1)
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=true
SECURE_HSTS_PRELOAD=true
CSP_REPORT_URI=https://your-csp-collector.example/report
```

> **Staging note:** a staging host may still need plain-HTTP access, so set `SECURE_HSTS_SECONDS=0` (leaving `SECURE_HSTS_INCLUDE_SUBDOMAINS` and `SECURE_HSTS_PRELOAD` at their `false` defaults) until HTTPS is confirmed, then raise it for production. `EMAIL_BACKEND` defaults to the console backend, which never sends real mail; set a real transport only when outgoing mail is required. `CACHE_BACKEND` defaults to a per-process `LocMemCache`; point it at a shared backend (as above) so rate limits apply across all workers.

> **Tenant hostnames are not configured here.** Under `TENANCY_ENABLED=True`, `ALLOWED_HOSTS` is ignored (forced to `*`) and each school's hostname is attached to the `Domain` table with `add_school_domain` (see section 6.2, Step 4). See section 2.1 for the `ALLOWED_HOSTS` reference row.

---

## 5. Database Management & Migrations

### 5.1 Single-Tenant Mode Migrations (`TENANCY_ENABLED=false`)

When multi-tenancy is disabled, migrations operate against the `public` schema using Django's standard migration commands:

```powershell
# Check migration status
.\.venv\Scripts\python.exe .\manage.py showmigrations --settings=schoolsystem.settings.development

# Apply all pending migrations
.\.venv\Scripts\python.exe .\manage.py migrate --settings=schoolsystem.settings.development
```

> [!NOTE]
> `schoolsystem.settings.production` requires `DATABASE_PROFILE=neon` plus the full Neon connection set, so local single-tenant work should use `schoolsystem.settings.development` (or omit `--settings` entirely, which auto-selects development on a workstation).

### 5.2 Multi-Tenant Mode Migrations (`TENANCY_ENABLED=true`)

In multi-tenant mode, migrations are divided into two categories:

#### 1. Public Schema Migrations (Shared Platform Tables)
Applies migrations for `tenancy`, `django_celery_beat`, and the public `django_content_type` registry:
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

## 6. Multi-School Tenancy Rollout & Platform CLI Management

### 6.1 Architecture & Isolation Guarantees

* **PostgreSQL Schema Isolation**: Each school's data is physically separated inside its own PostgreSQL schema. Queries within a school cannot see or join data from another school.
* **Validated Schema Identifiers**: Schema names are limited to 63 characters, must be PostgreSQL-safe identifiers (`^[_a-zA-Z][_a-zA-Z0-9]{,62}$`), and cannot collide with a reserved schema name. `provision_school` lower-cases the value you supply, so operational schemas follow `^[a-z][a-z0-9_]*$` in practice.
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
[ Step 6: Create School Admin ] ──> python manage.py runschema createsuperuser --schema=<name>
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
Creates the PostgreSQL schema, runs tenant migrations, seeds the default `SchoolConfiguration` singleton (`name` and `short_name` — the short name is used as the GSM-safe SMS sender identity), and leaves the school **inactive**:
```powershell
.\.venv\Scripts\python.exe .\manage.py provision_school `
  --name "Achimota Senior High" `
  --schema "achimota_shs" `
  --domain "achimota.yoursystem.com" `
  --confirm-domain-verified `
  --settings=schoolsystem.settings.production
```

#### Step 4: Attach Secondary Domains or Aliases (Optional)
Attach additional hostnames to an already-provisioned school. `add_school_domain` registers each hostname on the `Domain` table as verified; under tenancy that table is the **only** host gate (Django's `ALLOWED_HOSTS` is forced to `*`), so a hostname resolves only if it has its own verified row here. There are **no wildcards** — every hostname needs its own entry.

```powershell
# Attach a second domain to an existing school
.\.venv\Scripts\python.exe .\manage.py add_school_domain `
  --schema "achimota_shs" `
  --domain "portal.achimota.edu.gh" `
  --confirm-domain-verified `
  --settings=schoolsystem.settings.production

# Attach several schools' domains across multiple apex domains
.\.venv\Scripts\python.exe .\manage.py add_school_domain --schema atebubu_shs  --domain atebubushs.ecreativesys.com  --confirm-domain-verified
.\.venv\Scripts\python.exe .\manage.py add_school_domain --schema amanten_shs  --domain amantenshs.ecreativesys.com  --confirm-domain-verified
.\.venv\Scripts\python.exe .\manage.py add_school_domain --schema achimota_shs --domain dorm.achimotashs.com          --confirm-domain-verified
# ecreativesys.com -> whichever tenant owns the apex (or leave it out)
```
*(Add `--primary` if switching this domain to be the canonical primary address.)*

> **Host routing reminder:** while `TENANCY_ENABLED=true`, `ALLOWED_HOSTS` is ignored (always `*`) — see section 4.3. Routing is decided solely by the `Domain` table, so an apex like `ecreativesys.com` and each subdomain like `atebubushs.ecreativesys.com` must each have their own verified row. A hostname with no row returns `404 "No active school is configured for this hostname."`

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
.\.venv\Scripts\python.exe .\manage.py runschema createsuperuser `
  --schema achimota_shs `
  --settings=schoolsystem.settings.production
```

#### Step 7: Validate and Activate the School
The `set_school_status` command confirms a verified domain, the tenant schema and configuration table exist, and all tenant-app migrations are applied before activating:
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
   * **Self-Migrating**: Creates the new schema and applies the tenant app migrations to it before copying any rows, so a fresh cutover does not require a separate `migrate --schema <name>` step first.
   * Uses Kahn's topological sort algorithm to copy tables in foreign-key dependency order.
   * **Superuser-Free**: Does not execute `DISABLE TRIGGER ALL`, ensuring full compatibility with cloud databases like Neon, AWS RDS, and Supabase.
   * Namespaces referenced uploads from `students/photos/...` to `tenants/atebubu_shs/students/photos/...`.
   * Copies uploads through the configured media backend. When `MEDIA_STORAGE_BACKEND=neon_s3`, upload the legacy `media/` and `.private/` trees to the shared S3 bucket **before** running the command, because the copy reads the source objects from the configured backend (the same rule that already applies to school crests).
   * Leaves the source `public` data completely untouched for a safe fallback.
5. Validate data inside `atebubu_shs`, then activate the school and restart services.

### 6.4 Platform CLI Management Suite (Tenant Administration & Billing)

The Eden Platform provides 8 centralized management commands for day-to-day operations across schools, resource capacity auditing, termly billing calculation, feature flag control, backup/restore, and emergency support token generation.

All eight commands support human-friendly terminal tables (with ANSI color codes and Windows-safe ASCII indicators) and a `--json` flag for automated pipelines. Every administrative change is permanently recorded in the immutable `PlatformAuditLog` table.

```powershell
# Quick Reference of All 8 Platform Management Commands
.\.venv\Scripts\python.exe .\manage.py tenant_list --all
.\.venv\Scripts\python.exe .\manage.py tenant_backup --schema=<schema_name> --verify
.\.venv\Scripts\python.exe .\manage.py tenant_usage --schema=<schema_name>
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=<schema_name> --generate-pdf
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=<schema_name> --list
.\.venv\Scripts\python.exe .\manage.py support_access --schema=<schema_name> --status
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=<schema_name> --show
.\.venv\Scripts\python.exe .\manage.py tenant_restore --schema=<schema_name> --dry-run
```

---

#### 1. `tenant_list` — School Inventory & Routing Inspection

Inspect all schools registered on the platform, view their active status, assigned subscription plan tier, enrolled student headcounts, and primary domain routing addresses.

**Command Syntax**:
```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_list [OPTIONS]
```

**Options & Plain Explanations**:

| Option | Type | Default | Plain Explanation |
| :--- | :--- | :--- | :--- |
| `--all` | Flag | `True` | Lists every school registered on the platform, whether active or inactive. |
| `--active` | Flag | — | Filters the list to display **only active** schools currently serving web traffic. |
| `--inactive` | Flag | — | Filters the list to display **only inactive** schools (e.g. newly provisioned schools not yet launched, or schools suspended for vacation). |
| `--plan` | String | — | Filters schools by subscription tier. Choices: `free`, `starter`, `standard`, `premium`, `pilot`, `enterprise`. |
| `--json` | Flag | — | Suppresses terminal tables and outputs raw, machine-readable JSON data with student totals and tenant details. |
| `--operator` | String | `"cli"` | Sets the administrator username or identifier recorded in the permanent audit trail. |

**Practical Examples**:
```powershell
# View all registered schools in a formatted terminal table
.\.venv\Scripts\python.exe .\manage.py tenant_list --all

# View only currently active schools
.\.venv\Scripts\python.exe .\manage.py tenant_list --active

# Export active schools and student counts as JSON
.\.venv\Scripts\python.exe .\manage.py tenant_list --active --json
```

---

#### 2. `tenant_backup` — Schema-Isolated Database & Media Backup

Exports an isolated school database schema using PostgreSQL's native `pg_dump`, calculates a cryptographic SHA-256 integrity hash, optionally compresses all media uploads (photos and crests), and tests the dump file header.

**Command Syntax**:
```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_backup (--schema=<name> | --all) [OPTIONS]
```

**Options & Plain Explanations**:

| Option | Type | Default | Plain Explanation |
| :--- | :--- | :--- | :--- |
| `--schema` | String | *Required unless `--all`* | The exact schema name of the school to back up (e.g. `atebubu_test`). |
| `--all` | Flag | — | Automatically loops through and backs up every active school schema on the platform sequentially. |
| `--output-dir` | Path | `.private/backups/tenants/` | Custom folder on disk where backup `.dump` and `.sha256` files will be stored. |
| `--format` | String | `custom` | Dump format: `custom` (compressed binary archive for `pg_restore`, recommended) or `sql` (plain SQL text). |
| `--include-media` | Flag | — | Packages **every** tenant media root into a compressed `.tar.gz` companion file: public uploads (`MEDIA_ROOT/tenants/<schema>`), private student photos (`PRIVATE_STUDENT_PHOTO_ROOT/tenants/<schema>`), and generated student-ID exports (`.private/student_id_exports/<schema>`). Works against the local filesystem or the configured S3 backend. Fails if no files are found unless `--allow-empty-media` is also passed. |
| `--allow-empty-media` | Flag | — | Explicitly allow `--include-media` to produce an empty archive when the tenant is expected to have no files. |
| `--verify` | Flag | — | Immediately executes `pg_restore -l` against the generated dump file to prove the file is uncorrupted and readable. |
| `--json` | Flag | — | Outputs the backup metadata (file paths, sizes, SHA-256 hashes, timestamps) as JSON. |
| `--operator` | String | `"cli"` | Records who triggered the backup in the audit log. |

**Practical Examples**:
```powershell
# Create an isolated backup of Atebubu SHS with header verification
.\.venv\Scripts\python.exe .\manage.py tenant_backup --schema=atebubu_test --verify

# Create a complete backup of Atebubu SHS including student photos and crest uploads
.\.venv\Scripts\python.exe .\manage.py tenant_backup --schema=atebubu_test --include-media --verify

# Sequentially back up all active schools across the entire platform
.\.venv\Scripts\python.exe .\manage.py tenant_backup --all
```

---

#### 3. `tenant_usage` — Resource Consumption & Capacity Tracking

Aggregates operational activity inside a school's schema (enrolled learners, staff user accounts, dormitories, sent/failed SMS notifications, exeat permits, and ID card exports) and checks them against plan quotas.

**Command Syntax**:
```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_usage (--schema=<name> | --all) [OPTIONS]
```

**Options & Plain Explanations**:

| Option | Type | Default | Plain Explanation |
| :--- | :--- | :--- | :--- |
| `--schema` | String | *Required unless `--all`* | Target school schema name to inspect. |
| `--all` | Flag | — | Generates a high-level capacity summary table across all active schools. |
| `--start-date` | String (`YYYY-MM-DD`) | 30 days ago | Beginning of the usage window for counting SMS dispatches, exeat requests, and roll calls. |
| `--end-date` | String (`YYYY-MM-DD`) | Today | End of the usage window for counting operational events. |
| `--json` | Flag | — | Outputs all capacity metrics, progress percentages, and cost calculations as JSON. |
| `--operator` | String | `"cli"` | Administrator identifier recorded in the audit log. |

**Capacity Status Badges**:
* `[OK]`: Usage is healthy (under 80% of quota).
* `[WARNING]`: Approaching limit (between 80% and 99% of quota).
* `[EXCEEDED]`: Quota exceeded (100% or above); school should upgrade plan tier.

**Practical Examples**:
```powershell
# View 30-day operational usage and capacity for Atebubu SHS
.\.venv\Scripts\python.exe .\manage.py tenant_usage --schema=atebubu_test

# Inspect usage over a specific academic term window
.\.venv\Scripts\python.exe .\manage.py tenant_usage --schema=atebubu_test --start-date=2026-09-01 --end-date=2026-12-15

# View platform-wide capacity across all active schools
.\.venv\Scripts\python.exe .\manage.py tenant_usage --all
```

---

#### 4. `tenant_invoice` — 3-Pillar Termly Billing & PDF Generation

Calculates and manages commercial billing invoices according to the Eden 3-pillar formula:
1. **Software Subscription**: Base platform fee + (Students × Monthly rate × 3 months), or flat pilot agreement fee.
2. **Cloud Infrastructure**: Render compute/workers/Redis + Neon database hosting allocation with service markup margin.
3. **SMS Deliverability**: Actual delivered parent/guardian SMS messages charged at SMSOnlineGH cost basis with service markup margin.

**Command Syntax**:
```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=<name> [OPTIONS]
```

**Options & Plain Explanations**:

| Option | Type | Default | Plain Explanation |
| :--- | :--- | :--- | :--- |
| `--schema` | String | *Required* | Target school schema name to invoice or manage. |
| `--billing-start` | String (`YYYY-MM-DD`) | 1st of current month | Start date of the invoiced term or billing cycle. |
| `--billing-end` | String (`YYYY-MM-DD`) | End of current month | End date of the invoiced term or billing cycle. |
| `--generate-pdf` | Flag | — | Renders a formal, printable PDF invoice file saved to `.private/invoices/`. |
| `--output` | Path | `.private/invoices/` | Custom folder on disk where PDF invoices will be written. |
| `--list` | Flag | — | Lists all historical invoices generated for this school, showing payment status and outstanding balances. |
| `--show` | String | — | Displays the full itemized breakdown of a specific invoice number (e.g. `--show=INV-ATEBUBU_TEST-202610-001`). |
| `--mark-paid` | String | — | Marks a specific invoice as paid (e.g. `--mark-paid=INV-ATEBUBU_TEST-202610-001`). |
| `--payment-ref` | String | `""` | Payment transaction ID (e.g. Mobile Money reference code, bank transfer slip) recorded when marking as paid. |
| `--cancel` | String | — | Cancels an unpaid draft or issued invoice. |
| `--json` | Flag | — | Outputs invoice data and itemized calculations as JSON. |
| `--operator` | String | `"cli"` | Administrator recorded in the audit log. |

**Practical Examples**:
```powershell
# Generate a new termly invoice and create a PDF file
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=atebubu_test --generate-pdf

# List all invoices and payment balances for a school
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=atebubu_test --list

# View line items for a specific invoice
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=atebubu_test --show=INV-ATEBUBU_TEST-202610-001

# Mark an invoice as paid with a Mobile Money transaction reference
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=atebubu_test --mark-paid=INV-ATEBUBU_TEST-202610-001 --payment-ref="MTN-MOMO-9482019"
```

---

#### 5. `tenant_features` — Subsystem Feature Flags & Capacity Quotas

Inspects, enables, disables, and adjusts resource boundaries on individual school subscriptions.

**Command Syntax**:
```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=<name> [OPTIONS]
```

**Options & Plain Explanations**:

| Option | Type | Default | Plain Explanation |
| :--- | :--- | :--- | :--- |
| `--schema` | String | *Required* | Target school schema name. |
| `--list` | Flag | *Default action* | Displays a table of all boolean features and numeric capacity limits with current states. |
| `--enable` | String | — | Turns ON a feature by name (`roll_call`, `exeat_gate`, `student_id_cards`, `sms_dispatch`, `roster_import`, `advanced_reports`, `priority_support`). |
| `--disable` | String | — | Turns OFF a feature by name. |
| `--set-limit` | String (`key=value`) | — | Adjusts a numeric quota boundary. Keys: `max_students`, `max_staff`, `max_dormitories`, `max_sms_monthly`. |
| `--reset-to-plan` | Flag | — | Resets all features and limits back to the standard defaults of the school's assigned plan tier. |
| `--json` | Flag | — | Outputs features and limits as JSON. |
| `--operator` | String | `"cli"` | Administrator recorded in the audit log. |

**Practical Examples**:
```powershell
# View all feature entitlements and limits for Atebubu SHS
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=atebubu_test --list

# Enable SMS dispatch and student ID card printing
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=atebubu_test --enable=sms_dispatch
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=atebubu_test --enable=student_id_cards

# Increase learner capacity limit to 2,000 students
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=atebubu_test --set-limit=max_students=2000

# Reset all features back to plan tier defaults
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=atebubu_test --reset-to-plan
```

Feature flags are enforced by tenant application views and service entry points.
SMS requires both the platform `sms_dispatch` entitlement and the school's local
SMS configuration switch to be enabled; monthly SMS quotas include sent messages
and outstanding queued/processing deliveries. Student, staff, and dormitory
quotas are checked when records are created. `priority_support` records the
contractual support tier and does not change school application permissions.

---

#### 6. `support_access` — Cryptographic Time-Bound Support Tokens

Generates, verifies, and revokes temporary access tokens when platform engineers need to troubleshoot a school's schema. Tokens are 256-bit URL-safe random secrets, hashed with `bcrypt` (cost=12), and automatically expire.

**Command Syntax**:
```powershell
.\.venv\Scripts\python.exe .\manage.py support_access --schema=<name> [OPTIONS]
```

**Options & Plain Explanations**:

| Option | Type | Default | Plain Explanation |
| :--- | :--- | :--- | :--- |
| `--schema` | String | *Required* | Target school schema name requiring support access. |
| `--generate` | Flag | — | Generates a new 256-bit temporary token and prints it once. The plaintext is never stored in the database. |
| `--ttl-hours` | Integer | `4` | How many hours the token will remain valid before automatically expiring (valid range: 1 to 168 hours / 7 days). |
| `--verify` | String | — | Verifies whether a candidate plaintext token is currently valid and active for this school schema. |
| `--revoke` | Flag | — | Immediately revokes and invalidates the active support token, blocking any further use. |
| `--status` | Flag | *Default action* | Shows whether an active token exists, its expiration time, remaining minutes, and recent support audit history. |
| `--reason` | String | `""` | Compliance description explaining why access was granted or revoked (permanently logged). |
| `--json` | Flag | — | Outputs token status and audit trail as JSON. |
| `--operator` | String | `"cli"` | Administrator username recorded in the audit log. |

**Practical Examples**:
```powershell
# Check if an active support token exists for Atebubu SHS
.\.venv\Scripts\python.exe .\manage.py support_access --schema=atebubu_test --status

# Generate a 4-hour support token for troubleshooting
.\.venv\Scripts\python.exe .\manage.py support_access --schema=atebubu_test --generate --ttl-hours=4 --reason="Investigating SMS sync failures"

# Verify a token provided by an operator
.\.venv\Scripts\python.exe .\manage.py support_access --schema=atebubu_test --verify="TOKEN_STRING_HERE"

# Immediately revoke active support access
.\.venv\Scripts\python.exe .\manage.py support_access --schema=atebubu_test --revoke --reason="Troubleshooting completed"
```

---

#### 7. `tenant_subscription` — Contract Pricing, Plan Tiers & Margins

Configures commercial contract parameters for each school, including plan tier, termly base fees, per-student monthly rates, SMSOnlineGH packaging presets, and Render/Neon cloud infrastructure cost-recovery margins.

**Command Syntax**:
```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=<name> [OPTIONS]
```

**Options & Plain Explanations**:

| Option | Type | Default | Plain Explanation |
| :--- | :--- | :--- | :--- |
| `--schema` | String | *Required* | Target school schema name. |
| `--show` | Flag | *Default action* | Displays current contract terms, plan tier, pricing rates, cloud hosting allocation, and SMS packages. |
| `--set-plan` | String | — | Sets the commercial tier: `free`, `starter`, `standard`, `premium`, `pilot`, `enterprise`. |
| `--set-base-fee` | Decimal | — | Sets the base platform fee in Ghana Cedis charged at the beginning of each academic term (e.g. `1000.00`). |
| `--set-rate-per-student` | Decimal | — | Sets the monthly rate charged per enrolled student (e.g. `0.65`). |
| `--set-infra-cost` | Decimal | — | Sets the monthly cloud hosting cost basis in GHS (default: `150.00`, representing the school's share of Render + Neon). |
| `--set-infra-margin` | Decimal | — | Sets the service markup percentage applied to hosting costs (default: `15.0` for a 15% margin). |
| `--include-hosting` | Flag | — | Ensures the cloud hosting fee is itemized as a separate charge line on termly invoices. |
| `--exclude-hosting` | Flag | — | Omit cloud hosting fees from invoices (useful if hosting is absorbed into the base subscription). |
| `--set-sms-package` | Integer | — | Selects a standard SMSOnlineGH package preset: `10`, `20`, `50`, `100`, `200`, `500`, `1000`, `2000`, `3000`, `5000`. Auto-calculates unit cost basis. |
| `--set-sms-cost-basis` | Decimal | — | Sets a custom unit cost basis per SMS message (e.g. `0.02620`). |
| `--set-sms-margin` | Decimal | — | Sets the service markup percentage applied to SMS messages (default: `15.0` for 15% margin). |
| `--set-rate-per-sms` | Decimal | — | Directly overrides the final rate charged to the school per SMS message (e.g. `0.0301`). |
| `--set-currency` | String | — | Sets the 3-letter ISO 4217 currency code (default: `GHS`). |
| `--apply-plan-defaults` | Flag | — | When changing `--set-plan`, also resets feature flags and quotas to match the new tier's defaults. |
| `--json` | Flag | — | Outputs contract settings as JSON. |
| `--operator` | String | `"cli"` | Administrator recorded in the audit log. |

**Standard Plan Tiers & Termly Rates**:
* **Free / Trial**: GH₵0 base fee, GH₵0/student (up to 50 students).
* **Starter (Essential)**: GH₵500 base/term, GH₵0.50/student/mo (up to 500 students, 10 staff, 10 dorms).
* **Standard (Recommended)**: GH₵1,000 base/term, GH₵0.65/student/mo (up to 1,500 students, 40 staff, 40 dorms).
* **Premium (Advanced)**: GH₵2,000 base/term, GH₵0.80/student/mo (up to 2,500 students, 75 staff, 60 dorms).
* **Pilot (Atebubu SHS)**: Flat GH₵2,500/term agreement (includes up to 2,000 students, full exeat & QR IDs).
* **Enterprise (Custom)**: GH₵3,000 base/term, GH₵0.60/student/mo (unlimited students, staff, and dormitories; full feature set).

**Practical Examples**:
```powershell
# View contract and billing rates for Atebubu SHS
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=atebubu_test --show

# Configure Atebubu SHS under the official Pilot agreement
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=atebubu_test --set-plan=pilot --apply-plan-defaults

# Configure a school on the Standard tier with plan feature defaults
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=amanten_shs --set-plan=standard --apply-plan-defaults

# Apply the GH₵500 SMS package preset (19,083 credits @ GH₵0.02620) with a 15% service markup
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=amanten_shs --set-sms-package=500 --set-sms-margin=15.0

# Adjust Render & Neon cloud hosting allocation to GH₵150/mo with 15% markup
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=amanten_shs --set-infra-cost=150.00 --set-infra-margin=15.0
```

---

#### 8. `tenant_restore` — Verified Tenant Schema Restore

Restores a tenant PostgreSQL schema from a `tenant_backup` archive. The restore is strictly non-destructive: it verifies the dump's SHA-256 checksum and its `pg_restore` table of contents before touching the database, refuses reserved schemas, schemas already registered to a tenant, and schemas that already exist in PostgreSQL, rejects dumps whose embedded schema name does not match the target, and never issues `DROP`, `--clean`, or `--if-exists`.

**Command Syntax**:
```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_restore --schema=<name> [OPTIONS]
```

**Options & Plain Explanations**:

| Option | Type | Default | Plain Explanation |
| :--- | :--- | :--- | :--- |
| `--schema` | String | *Required* | Target tenant schema name to restore into. |
| `--dump` | Path | Newest archive under the backup directory | Path to the `pg_dump` custom-format archive to restore. |
| `--checksum` | Path | `<dump>.sha256` | Path to the `.sha256` checksum file produced by `tenant_backup`. |
| `--media-archive` | Path | — | Optional companion `.tar.gz` from `tenant_backup --include-media` with the same timestamp as the database dump; validates every entry, refuses path traversal and existing-file overwrites, and restores into the configured file or S3 storages. |
| `--backup-dir` | Path | `.private/backups/tenants/` | Approved backup directory searched when `--dump` is omitted. |
| `--operator` | String | `"cli"` | Administrator recorded in the audit log. |
| `--dry-run` | Flag | — | Runs every integrity and safety check but does not restore anything. |
| `--json` | Flag | — | Outputs the restore summary as JSON. |

**Practical Examples**:
```powershell
# Validate a backup without restoring it (recommended first step)
.\.venv\Scripts\python.exe .\manage.py tenant_restore --schema=atebubu_shs --dump=.private/backups/tenants/atebubu_shs/atebubu_shs.dump --dry-run

# Restore from the newest archive in the approved backup directory
.\.venv\Scripts\python.exe .\manage.py tenant_restore --schema=atebubu_shs

# Restore the database and companion media archive from a complete backup
.\.venv\Scripts\python.exe .\manage.py tenant_restore --schema=atebubu_shs `
  --dump=.private/backups/tenants/atebubu_shs/atebubu_shs_20261017_091800.dump `
  --media-archive=.private/backups/tenants/atebubu_shs/atebubu_shs_media_20261017_091800.tar.gz
```

Media archives are fully validated before database restoration and are restored
through the configured storage backends. Existing media files are never
overwritten. If storage fails while restoring media, the command reports the
failure and removes files already written by that media restore where possible;
the database schema may already have been restored.

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

| Scheduler Key | Task Path | Interval | Multi-Tenant Behavior | Description |
| :--- | :--- | :--- | :--- | :--- |
| `dispatch-queued-sms-deliveries` | `core.tasks.dispatch_queued_sms_deliveries` | 60 sec | Fans out to each active tenant | Recovers stale processing deliveries and dispatches up to 100 queued SMS notifications via SMSOnlineGH. |
| `check-overdue-exeats-for-sms` | `core.tasks.enqueue_overdue_exeat_reminders` | 15 min | Fans out to each active tenant | Identifies approved exeats where the student is currently out and expected return has passed; queues overdue SMS reminders. |
| `expire-unfinished-roll-calls` | `core.tasks.expire_unfinished_roll_calls` | 60 sec | Fans out to each active tenant | Automatically deletes roll-call sessions that have remained in progress for longer than 1 hour and notifies the session creator. |
| `cleanup-expired-student-id-exports` | `core.tasks.cleanup_expired_student_id_exports` | 24 hours | Fans out to each active tenant | Deletes generated student ID ZIP archives that have exceeded their expiration date and unlinks the export records. |

---

## 8. Daily Operations, Shell & Disaster Recovery

### 8.1 Tenant-Aware Django Shell

In multi-tenant mode, querying the database in a standard shell defaults to `public`, where `auth_user` and `core_*` tables do not exist. Use one of two tenant-aware patterns:

#### Method 1: Run the Shell in a Tenant Schema (Recommended)
```powershell
.\.venv\Scripts\python.exe .\manage.py runschema shell --schema achimota_shs --settings=schoolsystem.settings.production
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

For routine platform operations, prefer the managed `tenant_backup` and `tenant_restore` commands (see §6.4), which add SHA-256 integrity verification and refuse to drop or overwrite existing objects. The raw `pg_dump`/`pg_restore` recipes below are for manual or offline recovery.

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
* **Local Filesystem**: Back up `media/tenants/<schema_name>/`, `.private/student_photos/tenants/<schema_name>/`, and `.private/student_id_exports/<schema_name>/`.
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
.\.venv\Scripts\python.exe .\manage.py runschema shell --schema achimota_shs -c "from core.models import SchoolConfiguration; SchoolConfiguration.objects.get_or_create(id=1, defaults={'name': 'Achimota Senior High', 'short_name': 'ACHIMOTA'})"
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
| **House Tutor (`HOUSE_TUTOR`)** | Assigned House Only | Create and view exeat requests, record disciplinary incidents, and view house student records within the assigned house. Cannot approve exeats, run roll call, or access gate operations. |
| **Gate Officer (`GATE_OFFICER`)** | Main Gate | Dedicated gate scanner interface. Record actual departure and arrival times for students on approved exeat. Read-only exeat verification. |

### 9.2 Student Roster Import Specifications

Administrators can import student rosters via modern Excel (`.xlsx`) or UTF-8 CSV up to 10 MB and 2,000 rows.

Header names are matched case-insensitively and accept common aliases (for example `AdmissionNumber`, `IndexNo`, `Name`, `Form`, `Dorm`, `ParentPhone`).

* **Required Columns**: `Admission No`, `Index No`, `Full Name`, `Gender` (`Male`/`Female`), `Programme`.
* **Optional Columns**: `Date of Birth` (`YYYY-MM-DD` or `DD/MM/YYYY`), `Class`, `Residency` (`Boarder`/`Day`), `House`, `Dormitory`, `Room`, `Status` (enrollment), `Guardian Name`, `Guardian Phone`, `Guardian Alt Phone`.
* **Validation Rules**:
  * Duplicate admission or index numbers reject the entire import batch.
  * Boarding students must resolve to a valid house and gender-compatible dormitory block.
  * If room capacity is exceeded, the upload is rejected.
  * Importer never silently updates or overwrites existing students.

**Headless Import (CLI)**: `python manage.py import_roster <path/to/roster.csv|.xlsx>` performs the same validation and import without the web UI. In multi-tenant mode, select the target school with `--schema`:
```powershell
.\.venv\Scripts\python.exe .\manage.py import_roster roster.xlsx --schema achimota_shs
```

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

* **Request Acknowledgement**: Submitting an exeat request queues an "awaiting approval" SMS to the guardian (`SMSDelivery.EventType.REQUESTED`).
* **Overdue Alerts**: If an approved student fails to return by their `expected_return` timestamp, Celery Beat detects the overdue state and dispatches daily SMS alerts to guardians until the student is recorded returned.

### 9.4 Roll-Call Attendance Verification

* Sessions begin with students marked **Unmarked**.
* Active gate departures automatically set the student to **On Exeat**.
* In-progress roll calls expire automatically after 1 hour if left unfinished to prevent stale roster locks.
* Unexcused absences enter the Authorized Follow-Up queue for investigation.

### 9.5 Student ID Cards & Public QR Bearer Checks

* Print CR80-sized dual-sided student ID cards with high-resolution portraits.
* Back side features a unique QR code pointing to `/verify/student/<public_token>/`.
* **Privacy Controls**: Scanning the QR code shows whether an active approved movement is recorded and whether gate departure was logged. It does not display the student's name, photo, admission number, or exeat details.
* **Movement concerns**: The public scan page lets a person submit a concern for staff review. Reports are saved as open records in Django Admin under **Public Movement Concern Reports** (`/admin/core/movementconcernreport/`).
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
* **Cause**: In multi-tenant mode (`TENANCY_ENABLED=true`), `auth` and `core` tables are created in tenant schemas, not `public`. A fresh Django test database has no tenants yet, so ordinary `TestCase` tests run against `public` without those tables.
* **Fix for the legacy `core` test suite**: Use the dedicated test settings, which disable tenancy before loading the application settings and use a separate test database:
  ```powershell
  .\.venv\Scripts\python.exe .\manage.py test core --settings=schoolsystem.settings.test
  ```
  This runs `core` tests in single-school mode; it does not validate schema isolation. Run platform/tenancy tests using the normal development settings:
  ```powershell
  .\.venv\Scripts\python.exe .\manage.py test tenancy
  ```
  To test actual tenant-schema behavior, run the tenancy PostgreSQL integration tests with tenancy enabled and a PostgreSQL database:
  ```powershell
  $env:TENANCY_ENABLED = "true"
  .\.venv\Scripts\python.exe .\manage.py test tenancy.test_tenant_provisioning_integration
  .\.venv\Scripts\python.exe .\manage.py test tenancy.test_postgres_integration
  ```
  The `Tenant PostgreSQL integration` GitHub Actions workflow runs a focused fresh-tenant provisioning test against PostgreSQL. It creates a tenant through the normal provisioning command, verifies that `core_academicyear`, `core_house`, and `auth_user` exist in the tenant schema, and performs an ORM query there. Require this check in repository branch protection before merging or deploying tenancy-related changes.
* **For interactive shell queries in multi-tenant mode**: Activate a tenant schema before querying:
  ```powershell
  python manage.py runschema shell --schema achimota_shs
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

### 10.7 HTTP 403 on Exeat, Gate, Roll-Call or ID-Card Pages
* **Symptom**: An authenticated staff member receives `403 Forbidden` on pages such as `/exeats/`, `/operations/gate/`, `/operations/roll-call/`, or the student-ID export, even though their role should allow access.
* **Cause**: The school's subscription does not entitle the required feature. Those views are wrapped in `@tenant_feature_required(...)` for `exeat_gate`, `roll_call`, `student_id_cards`, `roster_import`, and `advanced_reports`. A school with no `TenantSubscription` row falls back to the **Free** plan defaults, which disable `exeat_gate`, `student_id_cards`, and `sms_dispatch`.
* **Fix**: Grant the missing feature (or move the school to a plan that includes it):
  ```powershell
  # Enable specific features
  .\.venv\Scripts\python.exe .\manage.py tenant_features --schema=<schema> --enable=exeat_gate
  .\.venv\Scripts\python.exe .\manage.py tenant_features --schema=<schema> --enable=student_id_cards

  # Or adopt a plan tier and apply its defaults
  .\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=<schema> --set-plan=standard --apply-plan-defaults
  ```
* **Note**: A 403 page explains when the feature is disabled for the school's plan; other permission denials show a general access explanation. The public QR verification page (`/verify/student/<token>/`) requires the `student_id_cards` feature to be enabled.
