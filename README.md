# SHS Boarding & Dorm Management System

**A complete setup, operations and deployment guide.**
Written for new developers, system administrators and school administrators.

The SHS Boarding & Dorm Management System is a web application for Ghanaian Senior High
Schools. It manages student boarding life: houses and dormitories, exeat (leave) permits with
automatic parent SMS notifications, evening roll-call attendance, disciplinary records, student
ID cards with QR codes for gate verification, and student roster imports. The same codebase can
run as a **single school** or as a **multi-school platform** where each school's data is kept in
its own PostgreSQL schema.

---

## Table of Contents

1. [What This System Is](#1-what-this-system-is)
2. [Project Structure and Important Files](#2-project-structure-and-important-files)
3. [Prerequisites](#3-prerequisites)
4. [Local Installation](#4-local-installation)
5. [First Successful Run](#5-first-successful-run)
6. [Environment Variables Reference](#6-environment-variables-reference)
7. [Platform Commands and Administrative Operations](#7-platform-commands-and-administrative-operations)
8. [Background Services and Scheduled Tasks](#8-background-services-and-scheduled-tasks)
9. [Deploying Online: Render + Neon](#9-deploying-online-render--neon)
10. [Database Updates, Backups and Maintenance](#10-database-updates-backups-and-maintenance)
11. [Multi-School Tenancy Rollout](#11-multi-school-tenancy-rollout)
12. [Core Workflows and Access Control](#12-core-workflows-and-access-control)
13. [Troubleshooting](#13-troubleshooting)
14. [What Was Verified in This Audit](#14-what-was-verified-in-this-audit)

---

## 1. What This System Is

### 1.1 What it does

| Area | What the system does |
| :--- | :--- |
| **Houses & dormitories** | Records houses, dormitory blocks (male/female), rooms and capacities. |
| **Student records** | Admission number, index number, programme, class group, house, dormitory, room, guardian, photo. Imports from Excel/CSV. |
| **Exeat life cycle** | Staff request leave for a student, a housemaster approves or rejects it, the main gate records departure and return, and guardians receive SMS updates at each stage. |
| **Roll call** | Housemasters start an attendance session, mark students present/absent/on-exeat, and follow up absences. Unfinished sessions expire automatically. |
| **Discipline** | Records disciplinary incidents against students. |
| **Student ID cards** | Generates printable CR80 ID cards with a portrait and a QR code. Scanning the QR code opens a privacy-safe public page that shows only whether the student is currently out and whether a gate departure was logged. |
| **Reports** | Boarding KPIs, demographics, SMS usage and configuration audit reports (screen and print). |
| **Public website** | A separate marketing/enquiry site for Eden Creative Systems (the vendor), plus a demonstration-request form. It is isolated from school data. |
| **Multi-school platform** | Optional: one deployment serving many schools, each isolated in its own database schema, with subscription plans, feature flags, quotas, billing invoices and backups. |

### 1.2 Who uses it

- **School administrators** configure terms, houses, staff roles, SMS and student records.
- **Senior housemasters** oversee all houses; **housemasters** and **house tutors** work within their assigned house.
- **Gate officers** use the gate scanner to verify exeats and log departures/returns.
- **Platform operators** (the vendor) provision schools, manage plans and quotas, take backups, and use support tokens.

### 1.3 Technology stack (verified in this repository)

| Component | What is used | Version |
| :--- | :--- | :--- |
| Language | Python | 3.12 (`.python-version` = `3.12`; `pyproject.toml` requires `>=3.12`) |
| Web framework | Django | 6.0.9 |
| Database | PostgreSQL only (no SQLite) | 14+ (CI runs PostgreSQL 16) |
| Database driver | `psycopg` (binary) | 3.3.6 |
| Multi-tenancy | `django-pgschemas` (schema per school) | 1.3.2 |
| Task queue | Celery + `django-celery-beat` | 5.6.3 / 2.9.0 |
| Broker / cache | Redis | 6.0+ (`redis` client 6.4.0) |
| Frontend | Django templates + Tailwind CSS + HTMX (`django-htmx` 1.29.0) | Tailwind 3.4.19 |
| Static files | WhiteNoise (`CompressedManifestStaticFilesStorage`) | 6.12.0 |
| Uploaded media | Local filesystem **or** S3-compatible storage (`django-storages` + `boto3`) | 1.14.6 |
| Spreadsheets | `pandas` + `openpyxl` | 3.0.6 / 3.1.5 |
| Images / QR / PDF | `pillow`, `qrcode`, `reportlab` | 12.3.0 / 8.2 / 4.x |
| SMS gateway | SMSOnlineGH (REST API, Ghana) | via `requests` |
| WSGI server | Gunicorn | 23.0.0 |
| Package manager | `uv` (with `uv.lock`) **or** `pip` (`requirements.txt`) | — |

> **Important:** The database backend is PostgreSQL in every mode. The default Django
> `sqlite3` backend is not configured anywhere, and `django-pgschemas` schema isolation
> cannot work on SQLite. Do not try to run this project on SQLite.

### 1.4 Two operating modes

The system is controlled by the `TENANCY_ENABLED` setting.

1. **Single-school mode (`TENANCY_ENABLED=false`, default).**
   All tables live in the PostgreSQL `public` schema. Standard Django host handling applies.
   This is the normal choice for local development and for a single standalone school.

2. **Multi-school mode (`TENANCY_ENABLED=true`).**
   The `public` schema holds platform tables (`tenancy_tenant`, `tenancy_domain`,
   `django_celery_beat`, `website_*`, shared `django_content_type`). Each school gets its own
   PostgreSQL schema (for example `achimota_shs`) containing that school's users, students,
   exeats, dormitories and sessions. Requests are routed to a school by hostname, which must
   exist as a **verified domain row** in the database.

The two modes use different Django database engines:
`django.db.backends.postgresql` (single-school) and `django_pgschemas.postgresql` (multi-school).

---

## 2. Project Structure and Important Files

The project root used throughout this guide is:

```
C:\RebuildEden\project 1
```

Substitute your own path if you cloned the project somewhere else.

```
project 1/
├── manage.py                     Django's command-line entry point (auto-selects settings)
├── pyproject.toml                Python project metadata + dependency list (uv)
├── uv.lock                       Locked dependency versions (uv)
├── requirements.txt              Pip-installable dependency list (used by Render)
├── package.json                  npm scripts for building Tailwind CSS
├── Procfile                      Optional process definitions (web / worker / beat)
├── render.yaml                   Render Blueprint (staging web service + env var names)
├── .env.example                  Template for your local .env (safe placeholders only)
├── .env                          YOUR local secrets — created by you, git-ignored, never commit
├── .python-version               Pins Python 3.12
│
├── schoolsystem/                 The Django project package (settings + entry points)
│   ├── __init__.py               Loads the Celery app on Django start-up
│   ├── settings/
│   │   ├── base.py               Shared settings: database, apps, SMS, Celery, tenancy
│   │   ├── development.py        Local development (DEBUG, relaxed hosts)
│   │   ├── production.py         Production (forces DEBUG=False, Neon, security headers)
│   │   ├── test.py               Tests with tenancy OFF  (DB name test_schoolsystem_core)
│   │   └── tenancy_test.py       Tests with tenancy ON   (DB name test_schoolsystem_tenancy)
│   ├── urls.py                   School application URLconf (admin + core), mounts /site/ locally
│   ├── urls_public.py            Public marketing website URLconf (public host only)
│   ├── celery.py                 Celery app named "schoolsystem" + worker cleanup signals
│   ├── security.py               Report-only Content-Security-Policy middleware
│   ├── wsgi.py / asgi.py         Server entry points
│
├── core/                         The school application (models, views, tasks, services)
│   ├── models.py                 Students, houses, dormitories, exeats, roll call, SMS, etc.
│   ├── urls.py                   School routes (see section 12)
│   ├── views.py                  School views
│   ├── tasks.py                  Celery tasks (SMS dispatch, roll-call expiry, ID exports)
│   ├── admin.py / admin_site.py  Django admin registrations and site class
│   ├── services/                 Business logic (SMS, imports, ID card images)
│   ├── management/commands/      Custom commands: import_roster, seed_synthetic_pilot
│   ├── migrations/               Database migrations for the school app
│   └── templates/core/           School application templates
│
├── tenancy/                      Multi-school platform app
│   ├── models.py                 Tenant and Domain models
│   ├── middleware.py             Host routing + dynamic CSRF middleware
│   ├── entitlements.py           Plan feature flags and quota checks
│   ├── management/commands/      The 8 platform CLI commands + provisioning commands
│   └── migrations/               Platform migrations (public schema)
│
├── website/                      Public Eden Creative Systems marketing site (public schema)
│   ├── urls.py / views.py        Home, features, pricing, contact, request-a-demo
│   ├── models.py                 DemoRequest (enquiry) model
│   ├── pricing.py                APPROVED_PRICE_LIST (empty until prices are approved)
│   ├── management/commands/      demo_requests, build_brand_assets
│   └── tests/                    Public website tests
│
├── templates/                    Project-level templates (403/404/500 pages, base layout)
├── static/                       Source static assets (CSS, JS, images)
│   ├── src/input.css             Tailwind source for the school application
│   ├── src/site.css              Tailwind source for the public website
│   └── css/output.css, css/site.css   Built CSS (committed — see note below)
├── staticfiles/                  collectstatic output (generated, git-ignored)
├── media/                        Uploaded files in filesystem mode (git-ignored)
├── .private/                     Private student photos, ID exports, backups (git-ignored)
├── .github/workflows/            CI: tenant PostgreSQL integration test
└── *.md                          Additional design/audit notes (see "Other documents")
```

### 2.1 Files a beginner usually edits

- **`.env`** — the only file most people edit. Copy `.env.example` to `.env` and fill it in.
- School content such as houses, students, terms and SMS settings are changed **through the web
  interface or the Django admin**, not by editing code.

### 2.2 Files you should not edit without understanding them

| File | Why |
| :--- | :--- |
| `schoolsystem/settings/*.py` | Changing these affects database, security and deployment behaviour for every environment. |
| `schoolsystem/settings/production.py` | Enforces production safety (DEBUG off, Neon required, HTTPS cookies). Changing it can make a deployment insecure or fail. |
| `tenancy/middleware.py` | This is the host router. A wrong change can send a request to the wrong school or expose school data. |
| `core/models.py` and `*/migrations/*` | Changing models requires new migrations. Never delete migration files. |
| `static/css/output.css` and `static/css/site.css` | These are **built artefacts**. They are committed on purpose so deployments need no Node toolchain. Rebuild them with npm (section 7.5), do not hand-edit. |

### 2.3 Other documents in the repository

These planning and audit notes exist alongside this README and remain useful reference material:
`AI_REGENERATION_WORKFLOW.md`, `multi-tenancy-audit.md`, `platform-cli-commands-plan.md`,
`ux-ui fixes.md`, and the `report_gen_*.md` files. This README is the single entry point; where
any note disagrees with the code, the code is authoritative.

---

## 3. Prerequisites

Install these before you begin. Nothing here is optional except where marked.

| Requirement | Version | Why it is needed | Notes |
| :--- | :--- | :--- | :--- |
| **Python** | 3.12 (3.12.12 verified) | Runs the application. `pyproject.toml` requires `>=3.12`. | Windows installer from python.org; tick "Add Python to PATH". |
| **PostgreSQL** | 14 or newer (16 tested in CI) | The only supported database. | Must include the command-line client tools `psql`, `pg_dump`, `pg_restore` (needed by backup/restore commands). |
| **Git** | Any recent version | To clone/update the repository. | Skip if you received a zip archive. |
| **Node.js + npm** | Node 18+ | Only to rebuild Tailwind CSS when you change styles. | **Not** needed to run or deploy the app, because the built CSS is committed. |
| **Redis** | 6.0 or newer | Message broker for Celery. Required for SMS dispatch and scheduled tasks. | Optional if you never run Celery (see section 8). Redis 5.x will **not** work. |
| **`uv`** (optional) | Latest | Fast, locked dependency install using `uv.lock`. | Optional; plain `pip` works too. |
| **A Neon account** (optional) | — | Needed only for the online deployment described in section 9. | Free tier is available. |

> **Windows note:** For Redis, common choices are Docker Desktop, Memurai Developer, or WSL2.
> Redis 5.0.14 (commonly bundled on Windows) does not support the `HELLO` handshake that modern
> Celery clients send, and will fail with `unknown command 'HELLO'`.

---

## 4. Local Installation

This section installs and runs the application on your own computer. Section 9 covers deploying
it online. Follow **A (Windows)** **or** **B (Linux/macOS)**, then read section 5.

### A. Windows (PowerShell) — the primary example

Open **PowerShell** and change into the project folder:

```powershell
cd "C:\RebuildEden\project 1"
```

#### Step 1 — Check Python

```powershell
py -3.12 --version
```

You should see `Python 3.12.x`. If `py` is not recognised, install Python 3.12 and tick
"Add Python to PATH", then reopen PowerShell.

#### Step 2 — Create and activate a virtual environment

A virtual environment ("venv") keeps this project's packages separate from everything else.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Your prompt now starts with `(.venv)`. If activation is blocked by execution policy, run this
once in the same window and retry:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

> Every command below assumes the virtual environment is active **and** the working directory is
> the project root (`C:\RebuildEden\project 1`). You can also call the interpreter directly as
> `.\.venv\Scripts\python.exe` without activating, which is what this repository's brief
> commands do.

#### Step 3 — Install dependencies

Choose **one** of these two options.

**Option 1 — pip (simplest, no extra tools):**

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**Option 2 — uv (faster, uses the lock file):**

```powershell
python -m pip install uv
uv sync --locked
```

Both install the same packages (Django, Celery, `psycopg`, Gunicorn, Pillow, pandas, qrcode,
reportlab, and so on).

#### Step 4 — Create your `.env` file

The repository ships a safe template. Copy it:

```powershell
Copy-Item .env.example .env
```

Open `.env` in a text editor and fill in at least the values shown in section 6. At minimum you
must set a `DJANGO_SECRET_KEY`, choose a `DATABASE_PROFILE`, and provide that profile's database
details.

> **Local PostgreSQL example** — set `DATABASE_PROFILE=local` and use the `DB_*` block
> (`DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`) with your own PostgreSQL password.

#### Step 5 — Create the local database

If you are using local PostgreSQL, create an empty database. Using `psql`:

```powershell
psql -U postgres -c "CREATE DATABASE schoolsystem_db;"
```

You will be prompted for the `postgres` password you set when installing PostgreSQL.

#### Step 6 — Run database migrations

```powershell
.\.venv\Scripts\python.exe .\manage.py migrate --settings=schoolsystem.settings.development
```

Migrations create all database tables. You should see a long list of `Applying ... OK` lines.

#### Step 7 — Create the first administrator

```powershell
.\.venv\Scripts\python.exe .\manage.py createsuperuser --settings=schoolsystem.settings.development
```

You will be asked for a username, email (optional) and password. **Choose your own credentials;
this project ships no default username or password.** This account is the Django superuser and is
also the first school administrator.

#### Step 8 — Run Django's system checks

```powershell
.\.venv\Scripts\python.exe .\manage.py check --settings=schoolsystem.settings.development
```

Expected output: `System check identified no issues (0 silenced).`

#### Step 9 — (Optional) Install frontend tooling

Only if you intend to change styling or the public website:

```powershell
npm ci
```

#### Step 10 — Start the development server

```powershell
.\.venv\Scripts\python.exe .\manage.py runserver 127.0.0.1:8000 --settings=schoolsystem.settings.development
```

Leave this window open. The application is now at <http://127.0.0.1:8000/>.

#### Step 11 — Start background services (separate windows)

SMS dispatch and scheduled tasks need Redis and Celery. Open a **new** PowerShell window, activate
the venv in it, then:

**Celery worker** (Windows must use the `solo` pool):

```powershell
.\.venv\Scripts\celery.exe -A schoolsystem worker --pool=solo --concurrency=1 --loglevel=INFO
```

**Celery Beat scheduler** (a third window):

```powershell
.\.venv\Scripts\celery.exe -A schoolsystem beat --loglevel=INFO
```

See section 8 for details and for how to check they are working. If you are not testing SMS or
scheduled jobs yet, you can skip these.

---

### B. Linux and macOS

These instructions use a POSIX shell (bash/zsh). Replace the project path with wherever you
cloned the repository.

#### Step 1 — Check Python

```bash
python3.12 --version
```

If `python3.12` is missing, install it (for example `sudo apt install python3.12 python3.12-venv`
on Debian/Ubuntu, or `brew install python@3.12` on macOS).

#### Step 2 — Create and activate a virtual environment

```bash
cd /path/to/project-1
python3.12 -m venv .venv
source .venv/bin/activate
```

#### Step 3 — Install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
# or, with uv:
# pip install uv && uv sync --locked
```

#### Step 4 — Create your `.env` file

```bash
cp .env.example .env
```

Edit `.env` and fill in the values from section 6. For local PostgreSQL set
`DATABASE_PROFILE=local`.

#### Step 5 — Create the local database

```bash
sudo -u postgres psql -c "CREATE DATABASE schoolsystem_db;"
```

On macOS with Homebrew PostgreSQL, `psql -U $(whoami) -c "CREATE DATABASE schoolsystem_db;"` is
usually enough.

#### Step 6 — Run migrations and create the administrator

```bash
python manage.py migrate --settings=schoolsystem.settings.development
python manage.py createsuperuser --settings=schoolsystem.settings.development
```

#### Step 7 — Run the checks and start the server

```bash
python manage.py check --settings=schoolsystem.settings.development
python manage.py runserver 127.0.0.1:8000 --settings=schoolsystem.settings.development
```

#### Step 8 — Background services (separate terminals)

On Linux and macOS the default prefork pool works, so no `--pool=solo` is needed:

```bash
celery -A schoolsystem worker --loglevel=INFO
celery -A schoolsystem beat   --loglevel=INFO
```

#### Platform-specific limitations

- **Redis version.** Linux/macOS distributions ship Redis 6+ in current releases; verify with
  `redis-server --version`. On older systems you may need a newer package or Docker.
- **PostgreSQL client tools.** `pg_dump` and `pg_restore` must be on `PATH` for the
  `tenant_backup`/`tenant_restore` commands. Install `postgresql-client` if `pg_dump` is missing.
- **Windows-only Celery pool.** `--pool=solo --concurrency=1` is required on Windows only.

---

## 5. First Successful Run

When installation works, this is what you should see.

1. **The server starts.** `runserver` prints something like:
   `Starting development server at http://127.0.0.1:8000/` and `Quit the server with CTRL-BREAK`.

2. **Confirm the database is connected.** The server prints no connection errors, and opening the
   site works. To confirm from the command line:
   ```powershell
   .\.venv\Scripts\python.exe .\manage.py showmigrations --settings=schoolsystem.settings.development
   ```
   Every migration should show `[X]` (applied). If any show `[ ]`, run `migrate` again.

3. **Open the application.**
   - School application (and the login page): <http://127.0.0.1:8000/>
   - You are redirected to the login page at <http://127.0.0.1:8000/login/>.
   - Django admin, if you need it: <http://127.0.0.1:8000/admin/>
   - The public marketing website (only when tenancy is off locally): <http://127.0.0.1:8000/site/>

4. **Log in.** Use the superuser you created in step 7. You should land on the dashboard at `/`.

5. **Initial configuration after first login.**
   - Confirm the school name/short name on the configuration page
     (`/configuration/`). On a newly provisioned tenant the school name is seeded automatically;
     in single-school mode you set it here.
   - Create houses, dormitories, rooms, programmes and class groups.
   - Add an academic term and mark it active (`/configuration/`).
   - Set up SMS only when you are ready (keep `SMS_ENABLED=false` until then).
   - Create staff accounts and assign each a role (see section 12.1). Roles are assigned through
     the Django admin (User profiles).

> The system deliberately ships **no default administrator account**. The first user is always the
> one you create with `createsuperuser`. Never create a production superuser with a weak or shared
> password.

---

## 6. Environment Variables Reference

All settings come from a single `.env` file in the project root, loaded by `python-dotenv` in
`schoolsystem/settings/base.py`. A safe, commented template is `.env.example`. The real `.env` is
listed in `.gitignore` and must never be committed.

Rules of thumb:

- **Development** values are convenient; **production** values must be secret and strict.
- Empty values fall back to the defaults shown below.
- Two settings are **validated**: `DATABASE_PROFILE` must be `local` or `neon`; and in production
  `PGSSLMODE` must be `require`, `verify-ca` or `verify-full`.

### 6.1 Django core

| Variable | Required? | Controls | Safe example | If missing |
| :--- | :--- | :--- | :--- | :--- |
| `DJANGO_SECRET_KEY` | **Required in production** (dev has an insecure fallback) | Cryptographic signing. Production requires ≥ 50 characters and ≥ 5 unique characters, or start-up fails. | `replace-with-50-plus-random-characters` | Dev: insecure default used. Prod: `ImproperlyConfigured`, app will not start. |
| `DEBUG` | No | Debug mode. Only honoured by `development.py`; `production.py` always forces `False`. | `True` (dev) | Defaults to `True` in development. |
| `DJANGO_SETTINGS_MODULE` | No | Which settings module to load. Usually auto-detected (see below). | `schoolsystem.settings.development` | Auto-selected: `production` when `RENDER=true` or `PORT` is set, otherwise `development`. |
| `ALLOWED_HOSTS` | **Required in single-school production** | Comma-separated hostnames. In tenancy mode it is forced to `*` and ignored (the `Domain` table is the real gate). | `127.0.0.1,localhost` | Dev default `127.0.0.1,localhost`. Prod (single-school): `ImproperlyConfigured`. |
| `CSRF_TRUSTED_ORIGINS` | **Required in production** | Full HTTPS origins allowed to submit forms. | `https://your-app.onrender.com` | Empty; browsers may reject form posts with a 403 CSRF error. |

### 6.2 Database — choose one profile

`DATABASE_PROFILE` decides which block is read. It must be `local` or `neon`.

| Variable | Required? | Controls | Safe example |
| :--- | :--- | :--- | :--- |
| `DATABASE_PROFILE` | Yes (defaults to `local`) | `local` = use the `DB_*` block; `neon` = use the `PG*` block. | `local` (dev) / `neon` (prod) |

**Local profile (`DATABASE_PROFILE=local`):**

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `DB_NAME` | `schoolsystem_db` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | `postgres` | Database password |
| `DB_HOST` | `localhost` | Database host |
| `DB_PORT` | `5432` | Database port |
| `DB_SSLMODE` | *(empty)* | Optional `sslmode` for the connection |

**Neon profile (`DATABASE_PROFILE=neon`):** all four of `PGHOST`, `PGDATABASE`, `PGUSER`,
`PGPASSWORD` are **required**; the app refuses to start without them.

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `PGHOST` | *(required)* | Neon **direct** host (do **not** use a `-pooler` host — see section 9.2) |
| `PGDATABASE` | *(required)* | Neon database name |
| `PGUSER` | *(required)* | Neon username |
| `PGPASSWORD` | *(required)* | Neon password |
| `PGPORT` | `5432` | Neon port |
| `PGSSLMODE` | `require` | TLS mode: `require`, `verify-ca`, `verify-full` |

### 6.3 Multi-tenancy

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `TENANCY_ENABLED` | `false` | Turn schema-per-school isolation on/off. Accepts `true/1/yes/on`. |
| `PLATFORM_ADMIN_DOMAIN` | *(empty)* | Optional hostname that returns a small JSON status payload instead of activating a school. It is **not** a browser admin interface. |

### 6.4 Static files and media

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `MEDIA_ROOT` | `<project>/media` | Local folder for uploads (filesystem mode only). |
| `MEDIA_STORAGE_BACKEND` | `filesystem` | `filesystem` or `neon_s3`. Any other value aborts start-up. |
| `NEON_STORAGE_BUCKET` | *(empty)* | S3 bucket name (required when `neon_s3`). |
| `AWS_ENDPOINT_URL_S3` | *(empty)* | S3 endpoint (required when `neon_s3`). |
| `AWS_REGION` | *(empty)* | S3 region (required when `neon_s3`). |
| `AWS_ACCESS_KEY_ID` | *(empty)* | S3 access key (required when `neon_s3`). |
| `AWS_SECRET_ACCESS_KEY` | *(empty)* | S3 secret key (required when `neon_s3`). |

When `MEDIA_STORAGE_BACKEND=neon_s3`, **all five** `NEON_STORAGE_BUCKET`/`AWS_*` variables are
required or the app will not start. Static files (`STATIC_ROOT`, `STATICFILES_DIRS`) are configured
in code and are always served by WhiteNoise; they have no environment variables.

### 6.5 Celery, Redis, cache and rate limiting

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` | Celery broker connection. |
| `CACHE_BACKEND` | `django.core.cache.backends.locmem.LocMemCache` | Cache used for rate limits. LocMem is per-process. |
| `CACHE_LOCATION` | `schoolsystem-cache` | Cache location/URL for the chosen backend. |
| `TRUSTED_PROXY_COUNT` | `1` | Number of proxy hops used to read the client IP from `X-Forwarded-For`. Render adds one hop, so `1` is correct. |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW` | `10` / `300` | Max login attempts per IP per window (seconds). |
| `PUBLIC_REPORT_RATE_LIMIT` / `PUBLIC_REPORT_RATE_WINDOW` | `5` / `3600` | Max public "report a concern" submissions per IP per window. |
| `STUDENT_ID_PUBLIC_TOKEN_TTL_DAYS` | `0` | Lifetime of public student-ID QR links in days (`0` = never expire). |

### 6.6 SMS (SMSOnlineGH)

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `SMS_ENABLED` | `false` | Master switch for sending SMS. Keep `false` while testing. |
| `SMS_ONLINE_GH_API_KEY` | *(empty)* | Your SMSOnlineGH API key. |
| `SMS_SENDER_ID` | `ATSEC` | Fallback sender ID when a school has not configured its own. |
| `SMS_TIME_ZONE` | `UTC` | Time zone for dispatch windows (also used as Celery time zone). |
| `SMS_HTTP_TIMEOUT` | `10` | Gateway HTTP timeout in seconds. |
| `SMS_MAX_ATTEMPTS` | `5` | Maximum delivery attempts before a message is marked failed. |

### 6.7 Email

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `EMAIL_BACKEND` | `django.core.mail.backends.console.EmailBackend` | Mail transport. The default prints mail to the log and **never sends real email**. |
| `DEFAULT_FROM_EMAIL` | `no-reply@localhost` | `From:` address for outgoing mail. |
| `EMAIL_HOST` | `localhost` | SMTP host. Only used when `EMAIL_BACKEND` is the SMTP backend. |
| `EMAIL_PORT` | `25` | SMTP port (587 for STARTTLS, 465 for SSL). |
| `EMAIL_HOST_USER` | *(empty)* | SMTP username. Empty sends without authentication. |
| `EMAIL_HOST_PASSWORD` | *(empty)* | SMTP password. **Never commit.** |
| `EMAIL_USE_TLS` | `false` | Use STARTTLS. Mutually exclusive with `EMAIL_USE_SSL`. |
| `EMAIL_USE_SSL` | `false` | Use SSL. Mutually exclusive with `EMAIL_USE_TLS`. |
| `EMAIL_TIMEOUT` | `10` | Seconds before an SMTP conversation is abandoned, so an unreachable mail host cannot hold a request open. |
| `SERVER_EMAIL` | `DEFAULT_FROM_EMAIL` | `From:` address for error mail (e.g. to `ADMINS`). |

> These SMTP variables are mapped in `schoolsystem/settings/base.py` because Django does **not**
> read them from the environment on its own. They are ignored by the console and `locmem`
> backends, so a staging deployment can leave them blank.

### 6.8 Security hardening (production)

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `SECURE_HSTS_SECONDS` | `31536000` in production | HSTS duration. Set `0` for a staging host you may need over plain HTTP. |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` | `false` | Apply HSTS to subdomains. Requires HSTS > 0. |
| `SECURE_HSTS_PRELOAD` | `false` | HSTS preload. Requires ≥ 1 year of HSTS and include-subdomains. |
| `CSP_REPORT_URI` | *(empty)* | A safe relative path or HTTPS URL appended as `report-uri` to the report-only CSP header. No collector is built in. |

### 6.9 Public marketing website (Eden Creative Systems)

These settings power the separate marketing site and demonstration requests. They do not affect
school routing.

The site is published at the main domain and links only to itself: canonical URLs, Open Graph tags,
the sitemap and robots.txt all name that domain, and nothing on the site points at a school's own
hostname. Staff sign in at the address their school provided, not from this site. `website/addressing.py`
is the single place that decides the site's own origin, and `website/checks.py` reports a
configuration that would break either rule.

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `PUBLIC_WEBSITE_DOMAIN` | *(empty)* | Comma-separated **hostnames** that serve the marketing site (no scheme, no port). Empty disables the bypass. |
| `PUBLIC_WEBSITE_ENABLED` | `true` | Master switch for the public-host bypass. |
| `PUBLIC_WEBSITE_URLCONF` | `schoolsystem.urls_public` | URLconf used on the public host. Leaving it pointed here keeps school URLs off the marketing host. |
| `SITE_URL` | *(empty)* | The site's own absolute origin for canonical links, Open Graph tags, the sitemap and the robots.txt `Sitemap:` line. Falls back to `https://EDEN_SYS_DOMAIN`, then to the request hostname (local development only). |
| `EDEN_SYS_COMPANY` | `Eden Creative Systems` | Company name shown on the site. |
| `EDEN_SYS_SHORT_NAME` | `Edensys` | Short company name. |
| `EDEN_SYS_PRODUCT_NAME` | `SHS Boarding & Dorm Management System` | Product name. |
| `EDEN_SYS_PHONE` | `+233 24 816 0391` | Contact phone. |
| `EDEN_SYS_EMAIL` | `berkojohnson@gmail.com` | Contact email. |
| `EDEN_SYS_DOMAIN` | `ecreativesys.com` | Company domain. |
| `EDEN_SYS_ATTRIBUTION` | `Developed by Eden Creative Systems (Edensys)` | Site attribution line. |
| `EDEN_PORTAL_LOGIN_URL` | *(empty)* | Where the site's "Login" link points. Published only when it is on the site's own domain (`SITE_URL` / `EDEN_SYS_DOMAIN`); anything else is dropped and reported by the `website.W001` check. Empty hides the link. |
| `PRICING_PUBLIC_ENABLED` | `false` | Publish pricing. Has no effect while `website/pricing.py` `APPROVED_PRICE_LIST` is empty. |
| `DEMO_REQUEST_NOTIFY_EMAILS` | *(empty)* | Comma-separated recipients for demo enquiries. Empty falls back to `EDEN_SYS_EMAIL`, then `DEFAULT_FROM_EMAIL`. |
| `DEMO_REQUEST_SMS_TO` | *(empty)* | Comma-separated numbers that receive an SMS alert for each new enquiry. Empty disables the alert. Independent of `SMS_ENABLED`, but needs `SMS_ONLINE_GH_API_KEY`. |
| `DEMO_REQUEST_SMS_SENDER` | `SMS_SENDER_ID` | Sender ID for that alert. Use only an ID approved on your SMSOnlineGH account. |
| `DEMO_REQUEST_RATE_LIMIT` / `DEMO_REQUEST_RATE_WINDOW` | `5` / `3600` | Demo-request throttling per client IP per window. A limit of `0` disables throttling. |

### 6.10 Staging safety

| Variable | Default | Controls |
| :--- | :--- | :--- |
| `SYNTHETIC_PILOT_DATABASE` | *(empty)* | Guard for `seed_synthetic_pilot`. Must exactly equal the database name you are seeding. |

> **Never** commit a populated `.env`. Never paste real API keys, passwords or connection strings
> into this README, tickets or chat.

---

## 7. Platform Commands and Administrative Operations

All commands are run from the project root with the virtual environment active. On Windows the
examples use `.\.venv\Scripts\python.exe .\manage.py`; on Linux/macOS use `python manage.py`.

### 7.1 Discovering commands

```powershell
.\.venv\Scripts\python.exe .\manage.py help
```

This lists every registered management command, grouped by app (`core`, `tenancy`, `website`,
`django_pgschemas`, and Django's built-ins). It tells you **what exists**, but not how a custom
command is meant to be used — the per-command sections below do that. For a single command:

```powershell
.\.venv\Scripts\python.exe .\manage.py help provision_school
```

The commands in this repository, as reported by `manage.py help`, are:

- **core:** `import_roster`, `seed_synthetic_pilot`
- **tenancy:** `add_school_domain`, `migrate_legacy_school`, `provision_school`,
  `set_school_status`, `support_access`, `tenant_backup`, `tenant_features`, `tenant_invoice`,
  `tenant_list`, `tenant_restore`, `tenant_subscription`, `tenant_usage`
- **website:** `build_brand_assets`, `demo_requests`
- **django_pgschemas** (third-party): `runschema`, `migrate` (extended), `migrateschema`,
  `whowill`, `cloneschema`, `createrefschema`

### 7.2 Standard Django commands

These are Django's own commands. They are always available.

| Command | Purpose | When to use | Changes data? |
| :--- | :--- | :--- | :--- |
| `manage.py check` | Run Django's system checks. | After installation and after pulling new code. | No |
| `manage.py check --deploy` | Extra checks for production safety. | Before deploying. **Warnings are expected for a non-HTTPS staging host.** | No |
| `manage.py migrate` | Apply pending migrations to the database. | Every install and every update. | **Yes** |
| `manage.py makemigrations` | Create new migration files from model changes. | Only when you have edited models. | No (writes files) |
| `manage.py showmigrations` | List migrations and whether they are applied. | To check for pending migrations. | No |
| `manage.py sqlmigrate <app> <migration>` | Print the SQL a migration will run. | To review a migration before applying it. | No |
| `manage.py createsuperuser` | Create an administrator account. | Once per install / per school schema. | **Yes** |
| `manage.py changepassword <user>` | Change a user's password. | When a password must be reset. | **Yes** |
| `manage.py collectstatic --noinput` | Copy static files into `STATIC_ROOT`. | Before/at production deploy. | Writes files |
| `manage.py runserver` | Start the development web server. | Local development only. | No |
| `manage.py shell` | Interactive Python shell with Django loaded. | Debugging, one-off queries. In multi-school mode use `runschema shell` (see 7.6). | Possible |
| `manage.py test` | Run the test suite. | Before merging/deploying. Needs PostgreSQL. | Temporarily |
| `manage.py sendtestemail` | Send a test email via the configured backend. | To verify email configuration. | Sends mail |
| `manage.py dbshell` | Open a database shell. | Advanced database inspection. | Possible |
| `manage.py dumpdata` / `loaddata` | Export/import model data as JSON. | Small data moves only. | `loaddata` writes |

**Examples:**

```powershell
# Health checks
.\.venv\Scripts\python.exe .\manage.py check --settings=schoolsystem.settings.development
.\.venv\Scripts\python.exe .\manage.py check --deploy --settings=schoolsystem.settings.production

# Migrations
.\.venv\Scripts\python.exe .\manage.py showmigrations --settings=schoolsystem.settings.development
.\.venv\Scripts\python.exe .\manage.py migrate --settings=schoolsystem.settings.development

# Accounts
.\.venv\Scripts\python.exe .\manage.py createsuperuser --settings=schoolsystem.settings.development
.\.venv\Scripts\python.exe .\manage.py changepassword admin --settings=schoolsystem.settings.development

# Static files (production)
.\.venv\Scripts\python.exe .\manage.py collectstatic --noinput --settings=schoolsystem.settings.build
```

> **`makemigrations` vs `migrate`:** `makemigrations` writes new migration *files* from your model
> changes; `migrate` applies migration files to the *database*. On a server you normally run only
> `migrate`. Never delete migration files or reset a production database as routine troubleshooting.

### 7.3 Custom command: `import_roster` (core)

Imports a student roster from `.csv` or `.xlsx` with the same validation as the web importer.
**Changes the database.**

| Argument | Required | Meaning |
| :--- | :--- | :--- |
| `file_path` (positional) | Yes | Path to the roster file. |
| `--schema` | Only when `TENANCY_ENABLED=True` | Target school schema. Rejected when tenancy is off. |

```powershell
# Single-school
.\.venv\Scripts\python.exe .\manage.py import_roster roster.xlsx
# Multi-school
.\.venv\Scripts\python.exe .\manage.py import_roster roster.xlsx --schema achimota_shs
```

Success prints `Roster import completed successfully: N student record(s) imported.` Validation
errors abort the **entire** batch and print the reasons (duplicate admission/index numbers, invalid
gender, boarder without a valid house, room capacity exceeded). The importer never silently
overwrites existing students. See section 12.2 for the expected columns.

### 7.4 Custom command: `seed_synthetic_pilot` (core)

Creates a fictional dataset (100 students, 4 houses, 8 dormitories, 16 rooms, 3 programmes, 6 class
groups, and disabled test staff accounts) for rehearsing workflows. **Writes to the database and is
strictly guarded.**

Safety guards (all enforced by the code):

1. Without `--apply` it only prints a plan (dry run).
2. Requires `--confirm-empty-staging`.
3. Requires the environment variable `SYNTHETIC_PILOT_DATABASE` to exactly equal the configured
   database name.
4. Refuses to run if school tables already contain records.
5. Refuses to run if SMS is enabled.
6. Requires an active superuser to exist first.

```powershell
# 1) Dry run (safe, no arguments beyond defaults)
.\.venv\Scripts\python.exe .\manage.py seed_synthetic_pilot

# 2) Apply to a dedicated, empty staging database only
$env:SYNTHETIC_PILOT_DATABASE = "schoolsystem_staging"
.\.venv\Scripts\python.exe .\manage.py seed_synthetic_pilot `
  --schema achimota_shs --apply --confirm-empty-staging
```

> **Warning:** never run this against a database that holds real school data. It is designed to
> refuse, but the guard variable is a safety net, not a licence. Test accounts it creates are
> inactive with unusable passwords.

### 7.5 Custom commands: `demo_requests` and `build_brand_assets` (website)

**`demo_requests`** reviews demonstration enquiries from the public site. Because the `website` app
lives in the `public` schema (not visible on school hosts) there is no admin page for it — this
command is the intended tool. Enquiries contain confidential contact details.

| Option | Meaning |
| :--- | :--- |
| *(no option)* / `--list` | List recent enquiries with a status summary. |
| `--all` | Include closed/triaged enquiries. |
| `--limit N` | Maximum rows to list (default 25). |
| `--show ID` | Show one enquiry in full. |
| `--mark ID --status S` | Change triage status: `new`, `contacted`, `scheduled`, `onboarding`, `closed`. |
| `--note "..."` | Append an internal note when marking. |
| `--export PATH` | Write all enquiries to a CSV file. |
| `--json` | Machine-readable output. |

```powershell
.\.venv\Scripts\python.exe .\manage.py demo_requests
.\.venv\Scripts\python.exe .\manage.py demo_requests --show=12
.\.venv\Scripts\python.exe .\manage.py demo_requests --mark=12 --status=contacted --note="Called 2026-10-18"
.\.venv\Scripts\python.exe .\manage.py demo_requests --export=.private/demo-requests.csv
```

**`build_brand_assets`** regenerates the public website's favicons and Open Graph image from
`static/img/eden-creative-systems-mark.png`. Run it only when the supplied brand mark changes.

```powershell
.\.venv\Scripts\python.exe .\manage.py build_brand_assets           # write assets
.\.venv\Scripts\python.exe .\manage.py build_brand_assets --check   # report targets only
```

Use `--check` first. The website's stylesheet is built separately:

```powershell
npm ci
npm run build        # school application CSS  -> static/css/output.css
npm run build:site   # public website CSS      -> static/css/site.css
```

Both built CSS files are committed; rebuild and commit them after any style change.

### 7.6 Third-party wrapper commands (`django_pgschemas`)

In multi-school mode, `django-pgschemas` adds schema-aware versions of some commands:

```powershell
# Run any command inside one or more school schemas
.\.venv\Scripts\python.exe .\manage.py runschema shell --schema achimota_shs
.\.venv\Scripts\python.exe .\manage.py runschema createsuperuser --schema achimota_shs

# Migrations (schema-aware)
.\.venv\Scripts\python.exe .\manage.py migrate --schema public
.\.venv\Scripts\python.exe .\manage.py migrate --include-dynamic-schemas

# Show which schemas a set of schema selectors resolves to, without acting
.\.venv\Scripts\python.exe .\manage.py whowill --include-dynamic-schemas

# Apply migrations across every schema
.\.venv\Scripts\python.exe .\manage.py migrateschema --include-all-schemas

# Clone an existing schema into a new one (advanced)
.\.venv\Scripts\python.exe .\manage.py cloneschema <source> <destination>
```

`--schema` accepts more than one name. `runschema` is the recommended way to query a school's data
from the shell in multi-school mode.

### 7.7 Custom commands: school provisioning and status (tenancy)

These four commands create and manage schools. All require `TENANCY_ENABLED=True` (they raise a
`CommandError` otherwise). They **change the database and create PostgreSQL schemas.**

#### `provision_school` — create a brand-new school

Creates the tenant record, attaches a verified hostname, creates the PostgreSQL schema, runs tenant
migrations, seeds the school's `SchoolConfiguration`, and leaves the school **inactive**.

| Argument | Required | Meaning |
| :--- | :--- | :--- |
| `--name` | Yes | School display name. |
| `--schema` | Yes | Schema name (lower-cased; validated against PostgreSQL rules and reserved names). |
| `--domain` | Yes | Primary hostname. |
| `--confirm-domain-verified` | Yes | Confirms you control the hostname. Without it the command refuses. |

```powershell
.\.venv\Scripts\python.exe .\manage.py provision_school `
  --name "Achimota Senior High" `
  --schema "achimota_shs" `
  --domain "achimota.yoursystem.com" `
  --confirm-domain-verified `
  --settings=schoolsystem.settings.production
```

If migrations fail after the schema is created, the command tells you to retry with
`migrate --schema=<schema>`.

#### `add_school_domain` — attach extra hostnames

Adds another verified hostname/alias to an existing school. There are **no wildcards**: every
hostname needs its own row.

| Argument | Required | Meaning |
| :--- | :--- | :--- |
| `--schema` | Yes | Target school schema. |
| `--domain` | Yes | Hostname to add. |
| `--primary` | No | Make this the single primary domain (demotes the previous one). |
| `--confirm-domain-verified` | Yes | Confirms you control the hostname. |

```powershell
.\.venv\Scripts\python.exe .\manage.py add_school_domain `
  --schema achimota_shs --domain portal.achimota.edu.gh --confirm-domain-verified
```

#### `set_school_status` — activate or suspend a school

Refuses to activate unless a verified domain exists, the PostgreSQL schema exists, the
`core_schoolconfiguration` table exists and no tenant migrations are pending.

| Argument | Required | Meaning |
| :--- | :--- | :--- |
| `--schema` | Yes | Target school schema. |
| `--activate` **or** `--deactivate` | Yes (mutually exclusive) | Action to perform. |

```powershell
.\.venv\Scripts\python.exe .\manage.py set_school_status --schema achimota_shs --activate
.\.venv\Scripts\python.exe .\manage.py set_school_status --schema achimota_shs --deactivate
```

#### `migrate_legacy_school` — adopt an existing single-school database

Copies an existing `public`-schema school's rows and uploaded files into a **new, inactive** tenant
schema without destroying the source. It applies tenant migrations to the new schema first, then
copies tables in foreign-key-safe order (topological sort) and renames referenced uploads to
`tenants/<schema>/...`. It does **not** require superuser privileges, so it works on Neon, RDS and
Supabase. Arguments are the same as `provision_school` (`--name`, `--schema`, `--domain`,
`--confirm-domain-verified`).

> **Before running:** stop the web server and Celery workers, take a backup, and (in `neon_s3`
> mode) upload the existing `media/` tree to the S3 bucket first, because the copy reads source
> objects from the configured storage backend. The source `public` data is left untouched.

```powershell
.\.venv\Scripts\python.exe .\manage.py migrate --schema public --settings=schoolsystem.settings.production
.\.venv\Scripts\python.exe .\manage.py migrate_legacy_school `
  --name "Atebubu Senior High" --schema "atebubu_shs" `
  --domain "atebubu.yoursystem.com" --confirm-domain-verified `
  --settings=schoolsystem.settings.production
```

### 7.8 The platform CLI suite (tenancy) — administration, quotas, billing, backups

Eight commands provide day-to-day platform operations. All **require `TENANCY_ENABLED=True`**,
print human-friendly tables (with `--json` for automation), and record every change in an immutable
platform audit log. Unless stated otherwise, they affect only the database (not school data files).

Quick reference:

```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_list --all
.\.venv\Scripts\python.exe .\manage.py tenant_backup --schema=<schema> --verify
.\.venv\Scripts\python.exe .\manage.py tenant_usage --schema=<schema>
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=<schema> --generate-pdf
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=<schema> --list
.\.venv\Scripts\python.exe .\manage.py support_access --schema=<schema> --status
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=<schema> --show
.\.venv\Scripts\python.exe .\manage.py tenant_restore --schema=<schema> --dry-run
```

#### 1. `tenant_list` — school inventory

Lists schools with status, plan, student counts and primary domain.

| Option | Meaning |
| :--- | :--- |
| `--all` / `--active` / `--inactive` | Filter by status (default `--all`). |
| `--plan {free,starter,standard,premium,pilot,enterprise}` | Filter by plan tier. |
| `--json` | Raw JSON output. |
| `--operator NAME` | Identifier recorded in the audit log (default `cli`). |

```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_list --active
.\.venv\Scripts\python.exe .\manage.py tenant_list --active --json
```

#### 2. `tenant_backup` — schema-isolated backup

Exports one schema (or all active schools) with `pg_dump`, writes a SHA-256 checksum, can archive
media, and can verify the dump. **Reads the database; writes backup files to disk.**
`pg_dump` must be on `PATH`.

| Option | Meaning |
| :--- | :--- |
| `--schema NAME` **or** `--all` | Target one schema or every active school (required, mutually exclusive). |
| `--output-dir PATH` | Destination (default `.private/backups/tenants/`). |
| `--format {custom,sql}` | `custom` (default, recommended) or plain SQL. |
| `--include-media` | Also archive tenant media (photos, crests, ID exports) as `.tar.gz`. |
| `--allow-empty-media` | Permit an empty media archive (only with `--include-media`). |
| `--verify` | Run `pg_restore -l` to prove the dump is readable. |
| `--json` | Machine-readable summary. |
| `--operator NAME` | Audit identifier. |

```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_backup --schema=atebubu_shs --include-media --verify
.\.venv\Scripts\python.exe .\manage.py tenant_backup --all
```

#### 3. `tenant_usage` — capacity and consumption

Aggregates students, staff, dormitories, SMS sent/failed, exeats and ID exports, and compares them
to plan quotas. Status badges: `[OK]` (<80%), `[WARNING]` (80–99%), `[EXCEEDED]` (≥100%).

| Option | Meaning |
| :--- | :--- |
| `--schema NAME` **or** `--all` | Target one school or all active schools. |
| `--start-date YYYY-MM-DD` / `--end-date YYYY-MM-DD` | Usage window (defaults: 30 days ago → today). |
| `--json`, `--operator NAME` | As above. |

```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_usage --schema=atebubu_shs
.\.venv\Scripts\python.exe .\manage.py tenant_usage --all
```

#### 4. `tenant_invoice` — termly billing and PDF

Calculates the three-pillar invoice (software subscription + cloud infrastructure + SMS
deliverability) and manages invoice records. `--generate-pdf` uses ReportLab and writes to
`.private/invoices/`.

| Option | Meaning |
| :--- | :--- |
| `--schema NAME` | Required. |
| `--billing-start` / `--billing-end YYYY-MM-DD` | Billing window. |
| `--generate-pdf` | Render a PDF invoice. |
| `--output PATH` | PDF output directory (default `.private/invoices/`). |
| `--list` | List invoices for the school. |
| `--show INVOICE_NO` | Print one invoice's line items. |
| `--mark-paid INVOICE_NO` | Mark an invoice paid. `--payment-ref "..."` records the reference. |
| `--cancel INVOICE_NO` | Cancel an unpaid invoice. |
| `--json`, `--operator NAME` | As above. |

```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=atebubu_shs --generate-pdf
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=atebubu_shs --list
.\.venv\Scripts\python.exe .\manage.py tenant_invoice --schema=atebubu_shs --mark-paid=INV-ATEBUBU_SHS-202610-001 --payment-ref="MTN-MOMO-9482019"
```

#### 5. `tenant_features` — feature flags and quotas

**Changes the database.** Enforced by tenant views and services.

| Option | Meaning |
| :--- | :--- |
| `--schema NAME` | Required. |
| `--list` | Show features and limits (default action). |
| `--enable NAME` / `--disable NAME` | Toggle a feature: `roll_call`, `exeat_gate`, `student_id_cards`, `sms_dispatch`, `roster_import`, `advanced_reports`, `priority_support`. |
| `--set-limit key=value` | Set a quota: `max_students`, `max_staff`, `max_dormitories`, `max_sms_monthly`. |
| `--reset-to-plan` | Restore the plan tier's defaults. |
| `--json`, `--operator NAME` | As above. |

```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=atebubu_shs --enable=exeat_gate
.\.venv\Scripts\python.exe .\manage.py tenant_features --schema=atebubu_shs --set-limit=max_students=2000
```

SMS requires **both** the platform `sms_dispatch` entitlement and the school's own SMS switch.
Monthly SMS quotas count sent messages plus outstanding queued/processing deliveries.

#### 6. `support_access` — time-bound support tokens

Generates/verifies/revokes 256-bit support tokens (hashed with bcrypt, cost 12) so an engineer can
troubleshoot one school. The plaintext is shown once and never stored. **Writes audit records.**

| Option | Meaning |
| :--- | :--- |
| `--schema NAME` | Required. |
| `--generate` | Create a token. |
| `--ttl-hours N` | Validity in hours (1–168, default 4). |
| `--verify TOKEN` | Check a candidate token. |
| `--revoke` | Invalidate the active token. |
| `--status` | Show token state and history (default action). |
| `--reason "..."` | Compliance note (logged). |
| `--json`, `--operator NAME` | As above. |

```powershell
.\.venv\Scripts\python.exe .\manage.py support_access --schema=atebubu_shs --generate --ttl-hours=4 --reason="Investigating SMS sync"
.\.venv\Scripts\python.exe .\manage.py support_access --schema=atebubu_shs --revoke --reason="Completed"
```

#### 7. `tenant_subscription` — contract and pricing

Configures plan tier, base fee, per-student rate, infrastructure cost/margin, SMS packages and
currency. **Changes the database.**

| Option | Meaning |
| :--- | :--- |
| `--schema NAME` | Required. |
| `--show` | Current contract terms (default action). |
| `--set-plan {free,starter,standard,premium,pilot,enterprise}` | Set plan tier. |
| `--set-base-fee`, `--set-rate-per-student` | Termly base fee / monthly per-student rate. |
| `--set-infra-cost`, `--set-infra-margin` | Cloud cost basis / markup %. |
| `--include-hosting` / `--exclude-hosting` | Itemise or omit hosting on invoices. |
| `--set-sms-package {10,20,50,100,200,500,1000,2000,3000,5000}` | SMS package preset. |
| `--set-sms-cost-basis`, `--set-sms-margin`, `--set-rate-per-sms` | SMS pricing controls. |
| `--set-currency CODE` | ISO 4217 code (default `GHS`). |
| `--apply-plan-defaults` | Also reset features/quotas to the new tier's defaults. |
| `--json`, `--operator NAME` | As above. |

```powershell
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=atebubu_shs --set-plan=pilot --apply-plan-defaults
.\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=amanten_shs --set-sms-package=500 --set-sms-margin=15.0
```

Standard tiers referenced in the code: Free/Trial (GH₵0), Starter (GH₵500/term, GH₵0.50/student/mo),
Standard (GH₵1,000/term, GH₵0.65/student/mo), Premium (GH₵2,000/term, GH₵0.80/student/mo),
Pilot (flat GH₵2,500/term), Enterprise (custom). Confirm current pricing with the vendor.

#### 8. `tenant_restore` — verified, non-destructive restore

Restores a schema from a `tenant_backup` archive. It verifies the SHA-256 checksum and the dump's
table of contents, refuses reserved/registered/existing schemas and schema-name mismatches, and
never issues `DROP`, `--clean` or `--if-exists`.

| Option | Meaning |
| :--- | :--- |
| `--schema NAME` | Required target schema. |
| `--dump PATH` | Archive to restore (default: newest under the backup dir). |
| `--checksum PATH` | `.sha256` file (default `<dump>.sha256`). |
| `--media-archive PATH` | Companion media `.tar.gz` from `tenant_backup --include-media`. |
| `--backup-dir PATH` | Approved backup directory. |
| `--dry-run` | Run all checks without restoring. |
| `--json`, `--operator NAME` | As above. |

```powershell
# Always validate first
.\.venv\Scripts\python.exe .\manage.py tenant_restore --schema=atebubu_shs --dry-run
# Then restore from the newest archive
.\.venv\Scripts\python.exe .\manage.py tenant_restore --schema=atebubu_shs
```

> **Warning:** restoring overwrites/installs data. Validating with `--dry-run` first is strongly
> recommended, and restoring into a live school requires authorisation. Existing media files are
> never overwritten.

### 7.9 Which commands to run when

| Situation | Commands |
| :--- | :--- |
| **First local install** | `migrate`, `createsuperuser`, `check` |
| **Every code update** | `pip install -r requirements.txt` (or `uv sync`), `migrate`, `collectstatic` (production only), restart services |
| **New school (multi-school)** | `provision_school`, `add_school_domain` (optional), `runschema createsuperuser`, `set_school_status --activate` |
| **Recurring operations** | `tenant_list`, `tenant_usage`, `tenant_invoice`, `tenant_backup` |
| **Suspicious/emergency** | `support_access --generate`, then `--revoke` |

---

## 8. Background Services and Scheduled Tasks

### 8.1 What actually needs Celery

The application uses **Celery** with **Celery Beat** and **Redis** for:

- **SMS delivery** to guardians (`send_sms_delivery_task`).
- **Scheduled jobs** defined in `CELERY_BEAT_SCHEDULE` (see the table below).
- **Student ID card export generation** (`generate_student_id_export`).

Redis is the **broker** (`REDIS_URL`). Celery Beat uses the **database scheduler**
(`django-celery-beat`), so periodic schedules are stored in the `django_celery_beat` tables in the
`public` schema.

**Is Celery mandatory?** For a basic local look at the application, no — you can start only the web
server. But **SMS will not be sent** and the scheduled tasks below will not run. To use SMS or any
scheduled job, you must run both a worker and Beat, plus Redis.

### 8.2 The Celery app

The Celery app is defined in `schoolsystem/celery.py` and named **`schoolsystem`**, and it is loaded
automatically from `schoolsystem/__init__.py`. That is why every command uses `-A schoolsystem`.

### 8.3 Scheduled periodic tasks

Defined in `schoolsystem/settings/base.py` (`CELERY_BEAT_SCHEDULE`). In multi-school mode each task
fans out to every active tenant.

| Scheduler key | Task | Interval | What it does |
| :--- | :--- | :--- | :--- |
| `dispatch-queued-sms-deliveries` | `core.tasks.dispatch_queued_sms_deliveries` | 60 s | Recovers stale deliveries and sends up to 100 queued SMS. |
| `check-overdue-exeats-for-sms` | `core.tasks.enqueue_overdue_exeat_reminders` | 15 min | Queues daily overdue-return reminders for students still out. |
| `expire-unfinished-roll-calls` | `core.tasks.expire_unfinished_roll_calls` | 60 s | Deletes roll-call sessions left in progress more than 1 hour and notifies the creator. |
| `cleanup-expired-student-id-exports` | `core.tasks.cleanup_expired_student_id_exports` | 24 h | Deletes expired student ID export archives. |

### 8.4 How to run them

**Local (Windows, separate windows, venv active):**

```powershell
# worker (Windows requires the solo pool)
.\.venv\Scripts\celery.exe -A schoolsystem worker --pool=solo --concurrency=1 --loglevel=INFO
# scheduler
.\.venv\Scripts\celery.exe -A schoolsystem beat --loglevel=INFO
```

**Local (Linux/macOS):**

```bash
celery -A schoolsystem worker --loglevel=INFO --concurrency=4
celery -A schoolsystem beat   --loglevel=INFO
```

**Production (Render):** run the same commands as Background Workers (`render.yaml` documents the
optional worker and Beat services — see section 9.4).

Worker connection hygiene (already implemented in `celery.py`): child processes close inherited
database sockets on start, and every task resets the PostgreSQL `search_path` to `public` before
and after running, so one school's schema cannot leak into another's over a long-lived connection.

### 8.5 How to check they are working

- The worker log prints `celery@<host> ready` and lists discovered tasks including
  `core.tasks.dispatch_queued_sms_deliveries`.
- Beat logs print `Scheduler: Sending due task ...` at the intervals above.
- To verify SMS end-to-end **without sending real messages**: keep `SMS_ENABLED=false`. Queued
  deliveries will remain in `SMSDelivery` and the dispatch task logs
  `remains queued because SMS is disabled`. This proves the plumbing without cost.

### 8.6 Common failure symptoms

| Symptom | Likely cause |
| :--- | :--- |
| Worker exits immediately with `unknown command 'HELLO'` | Redis older than 6.0. Upgrade Redis. |
| Worker crashes with `PermissionError [WinError 5]` on Windows | Missing `--pool=solo --concurrency=1`. |
| Worker starts but tasks never run | Beat not running, or `REDIS_URL` mismatch between services. |
| SMS stays `QUEUED` forever | SMS disabled, no worker/Beat, or quota reached. |
| `Connection refused` to Redis | Redis not running, or wrong `REDIS_URL`. |

### 8.7 SMS and email — safe testing

- **SMS (SMSOnlineGH).** Configure `SMS_ENABLED`, `SMS_ONLINE_GH_API_KEY` and `SMS_SENDER_ID`.
  **Keep `SMS_ENABLED=false` during every test** to avoid real messages and charges. To test the
  pipeline safely, leave SMS off and confirm deliveries queue but do not dispatch. Only enable SMS
  when you intend to send. There is no sandbox mode in the code.
- **Email.** The default `EMAIL_BACKEND` is the **console** backend: messages are printed to the
  log and never sent. This is deliberate and safe for staging. To send real mail, set
  `EMAIL_BACKEND` to `django.core.mail.backends.smtp.EmailBackend` and fill in the transport
  (`EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` or
  `EMAIL_USE_SSL`). Those variables **are** read from the environment by this project's settings,
  so no code change is needed. Verify with `manage.py sendtestemail you@example.com`.
- **SMS alert for demonstration requests.** `DEMO_REQUEST_SMS_TO` is deliberately separate from
  `SMS_ENABLED`: it is a platform alert to the business and never touches the school pipeline. A
  non-empty value sends a **real, chargeable SMS on every submission**, so leave it empty while
  testing and rely on `website.tests.test_demo_request` (which mocks the gateway) instead.

### 8.8 Where demonstration enquiries go, and how you are notified

Every submission from the public marketing site is stored in the PostgreSQL **`public` schema**,
table `website_demorequest`. It is never written to a school's tenant schema — the `website` app is
in `PUBLIC_APPS` — and there is deliberately no Django admin for it, so enquiries are read with the
`demo_requests` command:

```powershell
.\.venv\Scripts\python.exe .\manage.py demo_requests --list
.\.venv\Scripts\python.exe .\manage.py demo_requests --show=<id>
```

Two best-effort notifications are attempted after the record is committed. Each outcome is recorded
on the row so you can see whether it actually went out:

| Column | Meaning |
| :--- | :--- |
| `notification_sent` | The email alert was accepted by the configured `EMAIL_BACKEND`. |
| `notification_sms_sent` | The SMS alert was accepted by SMSOnlineGH. Only attempted when `DEMO_REQUEST_SMS_TO` is set. |

A failure in either path is logged and never changes the response the visitor sees, because the
enquiry is already stored. Note that with the default **console** `EMAIL_BACKEND` the email counts
as accepted without leaving the server, so `notification_sent=True` on its own does not prove a
message was delivered — confirm the backend is SMTP (or that mail actually arrives) first.

---

## 9. Deploying Online: Render + Neon

This section describes the architecture the repository is actually built for: a **Render Web
Service** running Gunicorn, an external **Neon PostgreSQL** database, optional **Render Background
Workers** for Celery, and optional **Neon object storage** for media. The repository ships a
`render.yaml` Blueprint and a `Procfile` for this setup.

### 9.1 Prepare the codebase

1. **Check your working tree.** Preserve any uncommitted work before deploying.
   ```powershell
   git status
   git add -A
   git commit -m "Prepare production deploy"
   ```
2. **Confirm dependencies are complete.** `requirements.txt` is the file Render installs. If you
   changed `pyproject.toml`, regenerate it:
   ```powershell
   uv export --format requirements.txt --no-hashes --locked --output-file requirements.txt
   ```
3. **Verify the production settings** load (this also validates `DATABASE_PROFILE=neon`,
   `PGSSLMODE`, `DJANGO_SECRET_KEY`, and the S3 variables if used):
   ```powershell
   .\.venv\Scripts\python.exe .\manage.py check --deploy --settings=schoolsystem.settings.production
   ```
   > A `security.W004` (HSTS) warning is expected if `SECURE_HSTS_SECONDS=0`. That is normal for a
   > staging host you may still reach over HTTP. Do **not** disable security checks to silence it.
4. **Confirm migrations are committed** (`core/migrations`, `tenancy/migrations`,
   `website/migrations`).
5. **Never commit** `.env`, `media/`, `.private/`, `staticfiles/` or `.dump` files — all are
   git-ignored already.

### 9.2 Configure Neon PostgreSQL

1. Sign in at <https://console.neon.tech/> and create (or select) a project, choosing the region
   nearest your users.
2. Open **Dashboard → Connection Details** and copy the **direct** connection parameters
   (host, database, user, password, port).
   > **Use the direct host, not the `-pooler` host.** Multi-school isolation depends on a
   > session-level `search_path`. Neon's pooled endpoint runs PgBouncer in transaction mode, which
   > does not preserve that state between statements, so a request could resolve against the wrong
   > school's schema. Never point `PGHOST` at a host containing `-pooler` — not for the web
   > service, and not for migrations.
3. Map the values to `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`, `PGPORT`, `PGSSLMODE`. Use
   `PGSSLMODE=require` (or `verify-full` for stricter validation).
4. Store the connection details as **secret environment variables in Render** (section 9.5). Never
   commit them.
5. **Verify connectivity without exposing the secret** by running a migration from your machine
   once the variables are available in your environment:
   ```powershell
   .\.venv\Scripts\python.exe .\manage.py migrate --schema public --settings=schoolsystem.settings.production
   ```
6. **Do not copy a local SQLite database into Neon** — this project does not use SQLite. To move
   data between PostgreSQL databases, use `pg_dump`/`pg_restore` or the `tenant_backup`/
   `tenant_restore` commands.

### 9.3 Configure Neon object storage for media (only if needed)

If `MEDIA_STORAGE_BACKEND=neon_s3`:

1. In the Neon dashboard open **Storage**, create a private bucket (for example `uploads`).
2. Generate S3 credentials with read and write scopes.
3. Record the endpoint URL, region, Access Key ID and Secret Key and map them to
   `NEON_STORAGE_BUCKET`, `AWS_ENDPOINT_URL_S3`, `AWS_REGION`, `AWS_ACCESS_KEY_ID`,
   `AWS_SECRET_ACCESS_KEY`.

All five are required together; the app refuses to start if any is missing. For a filesystem-only
staging deploy, leave `MEDIA_STORAGE_BACKEND=filesystem` — but note that on Render the local disk is
ephemeral, so uploaded files (student photos, crests) are **lost on every redeploy**. Use S3 for
anything you care about.

### 9.4 Configure Render

You can apply the included Blueprint or configure services by hand.

**Option A — Blueprint (`render.yaml`).** In the Render dashboard choose **New → Blueprint**, select
this repository, and apply. The Blueprint defines one web service (`shs-boarding-staging`) with the
build command, start command, health-check path, and environment-variable **names** (secrets are
`sync: false` and must be entered by you). It deliberately does **not** declare a Render database,
because Neon is external.

**Option B — Manual configuration.**

Web Service:

| Setting | Value |
| :--- | :--- |
| Type | Web Service (Python) |
| Root directory | repository root (contains `manage.py`) |
| Build command | `pip install -r requirements.txt && python manage.py collectstatic --noinput --settings=schoolsystem.settings.build` |
| Start command | `gunicorn schoolsystem.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --threads 2 --timeout 60` |
| Health check path | `/healthz` |
| Python version | `3.12` (set `PYTHON_VERSION=3.12`) |

The health path `/healthz` (and `/live`) is served directly by the tenant-routing middleware and
bypasses tenant resolution, so it works before any school is configured.

Optional Background Workers (required for SMS and scheduled jobs):

| Service | Start command |
| :--- | :--- |
| Worker | `celery -A schoolsystem worker --loglevel=INFO` |
| Beat | `celery -A schoolsystem beat --loglevel=INFO` |

Give both the same environment variables as the web service, plus `REDIS_URL`. Provision a Render
Key Value (Redis) instance, or an external Redis 6+ instance, and put its URL in `REDIS_URL` for all
three services. The `Procfile` in the repository defines equivalent `web`, `worker` and `beat`
processes if your platform uses it.

### 9.5 Production environment variables (Render dashboard)

Set these on **every** service that needs them (web, and worker/beat if used). All are secrets or
host-specific; use the exact names.

```env
DJANGO_SETTINGS_MODULE=schoolsystem.settings.production
PYTHON_VERSION=3.12
DATABASE_PROFILE=neon
TENANCY_ENABLED=True
PGHOST=ep-xxxx.<region>.aws.neon.tech          # DIRECT host, never a -pooler host
PGDATABASE=your_database_name
PGUSER=your_neon_user
PGPASSWORD=your_neon_password
PGPORT=5432
PGSSLMODE=require
DJANGO_SECRET_KEY=at-least-50-random-characters-kept-secret
CSRF_TRUSTED_ORIGINS=https://your-app.onrender.com,https://school.example.com
REDIS_URL=redis://<host>:6379
MEDIA_STORAGE_BACKEND=neon_s3
NEON_STORAGE_BUCKET=uploads
AWS_ENDPOINT_URL_S3=https://<bucket>.storage.<region>.neon.tech
AWS_REGION=<region>
AWS_ACCESS_KEY_ID=<access-key>
AWS_SECRET_ACCESS_KEY=<secret-key>
SMS_ENABLED=False                # keep False until you are ready to send real SMS
SMS_ONLINE_GH_API_KEY=<key>
SMS_SENDER_ID=<sender id>
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
CACHE_BACKEND=django.core.cache.backends.redis.RedisCache
CACHE_LOCATION=redis://<host>:6379/1
TRUSTED_PROXY_COUNT=1
SECURE_HSTS_SECONDS=0            # raise to 31536000 once HTTPS is confirmed
PUBLIC_WEBSITE_DOMAIN=           # e.g. ecreativesys.com,www.ecreativesys.com
PUBLIC_WEBSITE_ENABLED=True
SITE_URL=                        # e.g. https://ecreativesys.com
EDEN_PORTAL_LOGIN_URL=
PRICING_PUBLIC_ENABLED=False
DEMO_REQUEST_NOTIFY_EMAILS=
DEMO_REQUEST_SMS_TO=             # empty while staging; a real SMS costs money
```

Notes:

- **`ALLOWED_HOSTS` is ignored while `TENANCY_ENABLED=True`** (forced to `*`); tenant hostnames
  come from the `Domain` table. In single-school production you must set `ALLOWED_HOSTS` to your
  explicit hostnames.
- `CACHE_BACKEND` defaults to a per-process `LocMemCache`. Point it at the shared Redis backend (as
  above) so rate limits apply across all workers.
- `EMAIL_BACKEND` defaults to the console backend and never sends real mail. To send real mail, set
  it to `django.core.mail.backends.smtp.EmailBackend` and supply `EMAIL_HOST`, `EMAIL_PORT`,
  `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` and exactly one of `EMAIL_USE_TLS` / `EMAIL_USE_SSL`.
- Do not add the public marketing hostname (for example `ecreativesys.com`) to the `Domain` table;
  it is served by the public-host bypass and registering it as a tenant would show a school
  dashboard at the marketing address.

### 9.6 Production security (what the code already enforces)

`schoolsystem/settings/production.py` and `base.py` already:

- Force `DEBUG=False`.
- Require `DATABASE_PROFILE=neon`, a valid `PGSSLMODE`, and a strong `DJANGO_SECRET_KEY`.
- Set `SECURE_SSL_REDIRECT=True`, secure session/CSRF cookies, `SECURE_PROXY_SSL_HEADER`, secure
  referrer policy, `SECURE_CONTENT_TYPE_NOSNIFF`, and `SECURE_BROWSER_XSS_FILTER`.
- Set HSTS (default one year) and enforce that preload/subdomain options require HSTS first.
- Add a **report-only** Content-Security-Policy with a per-request nonce.
- Replace Django's CSRF middleware with `DynamicCsrfMiddleware`, which trusts verified tenant
  domains and rejects protocol-downgrade origins.

You generally should **not** change these. Do not disable them to make a deployment succeed.

### 9.7 First deployment: migrations and the first administrator

Migrations must run once against the new database. On a **paid** Render instance you can enable the
`preDeployCommand` in `render.yaml` (it is commented out because it requires a paid instance type)
so migrations run automatically before traffic is accepted. On the **free** tier, run them once from
the Render Shell after the first deploy:

```bash
python manage.py migrate --schema public --settings=schoolsystem.settings.production
python manage.py migrate --include-dynamic-schemas --settings=schoolsystem.settings.production
```

Create the first administrator:

- **Single-school production:** `python manage.py createsuperuser --settings=schoolsystem.settings.production`
- **Multi-school production:** provision a school, then create its administrator inside its schema:
  ```bash
  python manage.py provision_school --name "School Name" --schema school_schema \
    --domain school.example.com --confirm-domain-verified --settings=schoolsystem.settings.production
  python manage.py runschema createsuperuser --schema school_schema --settings=schoolsystem.settings.production
  python manage.py set_school_status --schema school_schema --activate --settings=schoolsystem.settings.production
  ```

Never create a production superuser with a default, guessable password.

### 9.8 Post-deployment verification checklist

Test with **synthetic or appropriately protected data only.**

| Check | How |
| :--- | :--- |
| Web service healthy | Render shows the service live; `https://your-app.onrender.com/healthz` returns `{"status": "ok"}`. |
| Home page and login | Open a school hostname; you are redirected to `/login/`. |
| Database connectivity | Log in; make a small change (e.g. save configuration) and reload. Check deploy logs for database errors. |
| Authentication and roles | Log in as each role and confirm accessible pages. |
| School/tenant isolation | With tenancy on, confirm host A shows only school A's data; an unregistered host returns 404. |
| Static files | Pages load with styling (WhiteNoise serving `output.css`). |
| Uploaded media | Upload a school crest/student photo; confirm it appears (and persists after a redeploy if using S3). |
| Student records and dormitories | Create a house, dormitory, room and student. |
| Roll call, exeats, gate | Start a roll call; request and approve an exeat; record gate departure/return. |
| QR ID cards | Export ID cards; scan a card's QR and confirm the public page loads (requires `student_id_cards`). |
| SMS | Keep `SMS_ENABLED=false`. Confirm deliveries queue without sending. |
| Email | `manage.py sendtestemail` with a real backend. |
| Background tasks | Worker/Beat logs show tasks being received and sent. |
| Error logs | Review Render logs for tracebacks. |

**Diagnosing which layer failed:**

- **Code** → a Python traceback in the logs, reproducible locally.
- **Database** → `OperationalError`, timeouts, `relation "..." does not exist` (often pending
  migrations or wrong schema).
- **Environment variables** → `ImproperlyConfigured` at start-up naming the missing setting.
- **Network** → connection refused/timeouts to Redis or Neon.
- **External service** → SMS/gateway errors logged against `SMSDelivery.last_error`.

---

## 10. Database Updates, Backups and Maintenance

### 10.1 Updating an installed application

1. **Back up first** (section 10.2).
2. **Pull the new code:**
   ```powershell
   git pull
   ```
3. **Install any new dependencies:**
   ```powershell
   pip install -r requirements.txt      # or: uv sync --locked
   ```
4. **Look for new migrations** before applying them:
   ```powershell
   .\.venv\Scripts\python.exe .\manage.py showmigrations --settings=schoolsystem.settings.development
   ```
5. **Apply migrations** (you normally do **not** run `makemigrations` on a server):
   ```powershell
   .\.venv\Scripts\python.exe .\manage.py migrate --settings=schoolsystem.settings.development
   ```
   In multi-school mode also apply to tenant schemas:
   ```powershell
   .\.venv\Scripts\python.exe .\manage.py migrate --include-dynamic-schemas --settings=schoolsystem.settings.production
   ```
6. **Rebuild static assets if CSS changed:**
   ```powershell
   npm run build
   npm run build:site
   .\.venv\Scripts\python.exe .\manage.py collectstatic --noinput --settings=schoolsystem.settings.build
   ```
7. **Restart / redeploy** (Render redeploys automatically on push).
8. **Verify:** run `manage.py check`, open the site, log in, load a school page.
9. **If it fails:** Render keeps the previous deploy — use **Rollback**. Reverting code does not
   undo applied migrations, so review the migration before rolling back.

`makemigrations` **creates** migration files; `migrate` **applies** them. Never delete migrations,
drop production tables, or reset the production database as routine troubleshooting.

### 10.2 Backing up

**Preferred (per-school):** `tenant_backup` (section 7.8) with `--include-media --verify`.

**Manual, whole database (PostgreSQL):**

```powershell
# Full database
pg_dump -h <host> -U <user> -d <db_name> -F c -f "backup_full.dump"
# One tenant schema
pg_dump -h <host> -U <user> -d <db_name> -n <schema> -F c -f "backup_<schema>.dump"
# Platform/public schema (tenant registry, domains, beat schedules)
pg_dump -h <host> -U <user> -d <db_name> -n public -F c -f "backup_public.dump"
```

Type your password at the prompt, or set `PGPASSWORD` in the environment for the command only.
Never paste the password into a script that is committed.

**Media files:**

- **Filesystem mode:** back up `media/`, `.private/student_photos/`, `.private/student_id_exports/`
  (and, per school, the `tenants/<schema>/` subfolders).
- **S3 mode:** sync the bucket prefix, e.g.
  ```powershell
  aws s3 sync s3://<bucket>/tenants/<schema>/ ./backup_<schema>_files/ --endpoint-url <endpoint>
  ```

### 10.3 Restoring

- **Per-school:** `tenant_restore --schema <name> --dry-run` first, then without `--dry-run`.
- **Whole database / public schema:** use `pg_restore` into a database. For the public schema in
  multi-school mode this is dangerous — the schema registry (`tenancy_tenant`, `tenancy_domain`) is
  what prevents a hostname resolving to the wrong school.
  > **Warning:** restoring can overwrite newer data and, for the public schema, change which school
  > a hostname points to. Production restores need careful verification and authorisation. Prefer
  > restoring into a fresh database first, verifying it, and only then switching.

### 10.4 Resetting or decommissioning a school (multi-school)

- **Reset school data while keeping the schema:** truncate the school's tables except
  `django_migrations` (advanced SQL; back up first), then re-seed `SchoolConfiguration`.
- **Decommission a school:** `set_school_status --deactivate`, drop the schema
  (`DROP SCHEMA <name> CASCADE`), delete the `Tenant` record (this also removes its `Domain` rows),
  and remove that school's media folder or S3 prefix. **This is irreversible — back up first.**

---

## 11. Multi-School Tenancy Rollout

Follow this runbook to onboard and verify a school. Each step is also documented with its command
options in section 7.

```
Step 1  Set TENANCY_ENABLED=true
Step 2  migrate --schema public
Step 3  provision_school            (creates schema, migrates apps, seeds config; still inactive)
Step 4  add_school_domain           (optional: extra hostnames/aliases)
Step 5  seed_synthetic_pilot        (optional: rehearsal data on a dedicated staging DB)
Step 6  runschema createsuperuser --schema <name>
Step 7  set_school_status --activate
        -> School is live at https://<its-hostname>/
```

```powershell
# 2) public/schema migrations
.\.venv\Scripts\python.exe .\manage.py migrate --schema public --settings=schoolsystem.settings.production
# 3) provision
.\.venv\Scripts\python.exe .\manage.py provision_school --name "Achimota Senior High" `
  --schema achimota_shs --domain achimota.yoursystem.com --confirm-domain-verified `
  --settings=schoolsystem.settings.production
# 6) administrator inside the schema
.\.venv\Scripts\python.exe .\manage.py runschema createsuperuser --schema achimota_shs `
  --settings=schoolsystem.settings.production
# 7) activate
.\.venv\Scripts\python.exe .\manage.py set_school_status --schema achimota_shs --activate `
  --settings=schoolsystem.settings.production
```

**Isolation guarantees:** every school's data lives in its own PostgreSQL schema; schema names are
validated and cannot collide with reserved names; at most one primary domain per school; and any
request to an unknown, unverified or inactive hostname receives a **404**. Remember there are no
wildcards — every hostname (including each subdomain) needs its own verified `Domain` row.

To convert an existing single-school install, use `migrate_legacy_school` (section 7.7).

---

## 12. Core Workflows and Access Control

### 12.1 Staff roles

| Role | Scope | Typical abilities |
| :--- | :--- | :--- |
| **Administrator (`ADMIN`)** | Whole school | Full configuration, terms, SMS, staff and student registry, audit logs. |
| **Senior Housemaster (`SENIOR_HOUSEMASTER`)** | All houses | School-wide student views, exeat approval across houses, roll-call reconciliation, discipline, ID cards. |
| **Housemaster (`HOUSEMASTER`)** | Assigned house | Approve house exeats, run roll calls, follow up absences, view house students. |
| **House Tutor (`HOUSE_TUTOR`)** | Assigned house | Create/view exeat requests, record incidents, view house students. Cannot approve exeats, run roll call or use gate. |
| **Gate Officer (`GATE_OFFICER`)** | Main gate | Gate scanner, record departures/returns, read-only exeat verification. |

Roles are assigned via user profiles (Django admin). Some pages are also gated by the school's
**subscription features** (`exeat_gate`, `roll_call`, `student_id_cards`, `roster_import`,
`advanced_reports`). A school with no subscription falls back to Free-plan defaults, which disable
`exeat_gate`, `student_id_cards` and `sms_dispatch` — see section 13.7.

### 12.2 Student roster import

Via the web (`/students/import/`) or `import_roster`. Excel `.xlsx` or UTF-8 CSV, up to 10 MB and
2,000 rows. Header names are matched case-insensitively with common aliases.

- **Required:** Admission No, Index No, Full Name, Gender (`Male`/`Female`), Programme.
- **Optional:** Date of Birth (`YYYY-MM-DD` or `DD/MM/YYYY`), Class, Residency (`Boarder`/`Day`),
  House, Dormitory, Room, Status, Guardian Name, Guardian Phone, Guardian Alt Phone.
- **Rules:** duplicate admission/index numbers reject the whole batch; boarders need a valid,
  gender-compatible house/dormitory; room capacity is enforced; existing students are never
  silently overwritten.

### 12.3 Exeat life cycle and SMS

```
Request -> Housemaster review -> (Approved) -> SMS: Approved -> Gate departure -> SMS: Left campus
                              -> (Rejected) -> SMS: Rejected                    -> Gate return -> SMS: Safe return
```
Submitting a request queues an "awaiting approval" SMS. If an approved student is not back by the
expected time, the overdue task queues daily reminders until they return.

### 12.4 Roll call

Sessions start with students Unmarked; active gate departures set a student to On Exeat; sessions
left in progress expire after one hour; unexcused absences enter the follow-up queue.

### 12.5 Student ID cards and public QR checks

Print CR80 cards with a portrait and a QR code pointing to `/verify/student/<token>/`. Scanning
shows only whether an active approved movement is recorded and whether a departure was logged — it
**never** shows the student's name, photo, admission number or exeat details. The public page also
accepts a movement concern, stored for admin review. Administrators can invalidate and reissue a
token if a card is lost. The public verification page requires the `student_id_cards` feature.

### 12.6 Key URL routes

| Path | Purpose |
| :--- | :--- |
| `/login/`, `/logout/`, `/` | Staff login, logout, dashboard |
| `/students/`, `/students/new/`, `/students/import/` | Student registry, registration, import |
| `/students/<uuid>/id-card/` | Student ID card |
| `/verify/student/<uuid>/` | Public QR verification |
| `/exeats/`, `/exeats/new/`, approvals, review | Exeat workflow |
| `/operations/roll-call/`, `/operations/gate/` | Roll call, gate |
| `/conduct/`, `/reports/`, `/configuration/` | Discipline, reports, configuration |
| `/admin/` | Django admin |
| `/site/` | Public marketing site (local preview when tenancy is off) |
| `/healthz` | Health probe (served by middleware) |

---

## 13. Troubleshooting

> Diagnoses below distinguish **confirmed** behaviour (verified in the code) from **likely** causes.
> Sources of truth: `schoolsystem/settings/*`, `tenancy/middleware.py`, `schoolsystem/celery.py`,
> and the management-command sources.

### 13.1 Virtual environment / Python errors

- **Observed:** `python: command not found`, or `Couldn't import Django`.
- **Cause:** Python not on PATH, or the virtual environment is not activated.
- **Check/fix:** run `py -3.12 --version` (Windows) or `python3.12 --version`; activate the venv;
  or call `.\.venv\Scripts\python.exe` directly. Confirm Django with
  `.\.venv\Scripts\python.exe -c "import django; print(django.get_version())"` (expect `6.0.9`).

### 13.2 Dependency installation failures

- **Observed:** `pip install` errors building `psycopg`, `pillow`, `pandas` or `numpy`.
- **Cause:** missing build tools or a wrong Python version.
- **Fix:** ensure Python 3.12; prefer the pinned `psycopg[binary]` (already in `pyproject.toml`); on
  Windows install "Desktop development with C++" if a wheel is unavailable. `uv sync --locked` is
  the most reproducible option.

### 13.3 Missing environment variables / settings will not load

- **Observed:** `ImproperlyConfigured` such as "Production requires DATABASE_PROFILE=neon ..." or
  "requires these environment variables: PGHOST, ...".
- **Cause:** a required variable is unset.
- **Check/fix:** confirm `.env` exists and is complete (section 6). Production checks the secret
  key length/entropy, `PGSSLMODE`, and (for S3) all five storage variables.

### 13.4 Database connection failures

- **Observed:** `connection refused` / `password authentication failed` / `could not connect`.
- **Cause:** wrong credentials/host in `.env`, PostgreSQL not running, or wrong `DATABASE_PROFILE`.
- **Check/fix:** confirm the profile matches the block you filled in; verify the server is running
  (`Test-NetConnection localhost -Port 5432` on Windows); check credentials with `psql`.
  **Note:** if `DATABASE_PROFILE=neon` your local Django commands will use Neon, not a local
  database. Set `DATABASE_PROFILE=local` for local work.

### 13.5 Pending migrations / missing tables

- **Observed:** `relation "core_schoolconfiguration" does not exist`, or unapplied-migration
  warnings.
- **Check:** `manage.py showmigrations`.
- **Fix:** `manage.py migrate`. In multi-school mode also
  `migrate --include-dynamic-schemas`, and per school `migrate --schema <name>`.

### 13.6 Allowed hosts / CSRF / HTTPS errors

- **Observed (single-school prod):** `DisallowedHost`, or
  `Origin checking failed - ... does not match any trusted origins`.
- **Cause:** missing `ALLOWED_HOSTS` / `CSRF_TRUSTED_ORIGINS`, or an `http://` origin posted to an
  `https://` endpoint (rejected on purpose).
- **Fix:** set the explicit hostnames/origins; for a tenant hostname, register and verify it
  (`add_school_domain`). `DynamicCsrfMiddleware` caches verified domains for 5 minutes, so a new
  domain may need a short wait or a service restart.

### 13.7 HTTP 403 on exeat / gate / roll-call / ID-card pages

- **Cause (confirmed):** the school's subscription does not entitle the feature. With no
  subscription the Free-plan defaults disable `exeat_gate`, `student_id_cards` and `sms_dispatch`.
- **Fix:**
  ```powershell
  .\.venv\Scripts\python.exe .\manage.py tenant_features --schema=<schema> --enable=exeat_gate
  .\.venv\Scripts\python.exe .\manage.py tenant_subscription --schema=<schema> --set-plan=standard --apply-plan-defaults
  ```

### 13.8 Celery / Redis

- **`unknown command 'HELLO'`** → Redis older than 6.0. Upgrade.
- **`PermissionError [WinError 5]` (Windows)** → start the worker with `--pool=solo --concurrency=1`.
- **Tasks not processed** → Beat not running, or `REDIS_URL` differs between services.
- **SMS stuck `QUEUED`** → SMS disabled, no worker/Beat, or monthly quota reached.

### 13.9 SMS and email

- **Nothing sent** → `SMS_ENABLED=false`, missing/incorrect `SMS_ONLINE_GH_API_KEY`, no credit, or
  `student_id_cards`/`sms_dispatch` feature disabled.
- **Email "sent" but nothing arrives** → the console backend is active (by design). Configure a real
  SMTP backend and test with `sendtestemail`.

### 13.10 Multi-school routing

- **404 "No active school is configured for this hostname."** → the host has no verified `Domain`
  row, or its tenant is inactive. Add the domain (`add_school_domain`) and activate the school.
- **`UndefinedTable: relation "auth_user" does not exist` in the shell** → you are querying the
  `public` schema, which has no school tables. Use `runschema shell --schema <name>`.
- **Neon statement timeout** (`canceling statement due to statement timeout`) → the 30-second
  `statement_timeout` was exceeded. For a one-off large operation in the shell, run
  `SET statement_timeout = 0;` (some commands do this automatically).

### 13.11 Render deploy and start-up problems

- **Build fails** → usually a dependency error (`pip install`) or a settings import error. Read the
  build log; run `check --deploy --settings=schoolsystem.settings.production` locally first.
- **Deploy healthy but 500s** → likely missing env vars, unapplied migrations, or the `-pooler`
  host being used (breaks schema isolation).
- **Health check failing** → confirm the Health Check Path is exactly `/healthz` and that the start
  command binds to `0.0.0.0:$PORT`.

### 13.12 Tests

- **`relation "auth_user" does not exist`** when running the standard suite in tenancy mode → run
  `core` tests with tenancy-off settings, and tenancy suites with tenancy-on settings:
  ```powershell
  .\.venv\Scripts\python.exe .\manage.py test --settings=schoolsystem.settings.test
  .\.venv\Scripts\python.exe .\manage.py test tenancy --settings=schoolsystem.settings.tenancy_test
  ```
  `test.py` uses `test_schoolsystem_core`; `tenancy_test.py` uses `test_schoolsystem_tenancy`, so
  they do not collide. **Tests need PostgreSQL and honour `DATABASE_PROFILE`.** Set
  `DATABASE_PROFILE=local` before running them, or you will create test databases in your configured
  (possibly production) database.

---

## 14. What Was Verified in This Audit

**Performed on this repository (Windows, Python 3.12.12, Django 6.0.9):**

| Check | Result |
| :--- | :--- |
| `manage.py check --settings=schoolsystem.settings.development` | `System check identified no issues (0 silenced).` |
| `manage.py check --deploy --settings=schoolsystem.settings.production` | Loaded successfully; reported `security.W004` (HSTS) because the local `.env` sets `SECURE_HSTS_SECONDS=0`; 1 issue, 1 silenced. **Expected for a staging/HTTPS-pending host.** |
| `manage.py help` | Confirmed the full command list in section 7.1. |
| Per-command option lists | Verified against source for the platform CLI, provisioning, and website commands. |
| Environment variables | Verified against `schoolsystem/settings/base.py` and `production.py`. |
| Deployment start command | Verified against `render.yaml` and `Procfile`. |
| Celery app name and schedule | Verified in `schoolsystem/celery.py` and `settings/base.py`. |

**Could not be executed during this audit (documented honestly):**

- **The database-backed test suite** was **not run**, because the local PostgreSQL instance did not
  accept the available credentials and the configured profile (`DATABASE_PROFILE=neon`) points at a
  remote Neon database — running the suite there would create test databases on it. Run the tests
  yourself against a local PostgreSQL with `DATABASE_PROFILE=local` before deploying.
- **A live Render/Neon deployment** was not performed here; section 9 is based on the repository's
  own `render.yaml`, `Procfile`, and settings, which are internally consistent, but the deployment
  itself should be validated in your own Render account.

**Gaps and cautions found:**

1. **The `.env` in the working tree is set to `DATABASE_PROFILE=neon`.** For local development,
   change it to `local` or Django commands and tests will target the remote Neon database. (`.env`
   is git-ignored — never commit it.)
2. **The repository currently has uncommitted work** in progress, including a new `website/`
   application, `schoolsystem/urls_public.py`, and template changes. Commit or review these before
   deploying.
3. **Filesystem media on Render is ephemeral.** Use `MEDIA_STORAGE_BACKEND=neon_s3` for any deploy
   where uploaded photos/crests must survive redeploys.
4. **The `website` app's migrations must be applied** (`website/migrations/0001_initial.py`) — this
   happens with the normal `migrate`/`migrate --schema public` steps.
5. **`build_brand_assets` requires Pillow** (a normal dependency) and only writes files; it does not
   touch the database.

**No code or configuration was changed as part of this documentation task.** Only `README.md`
was rewritten. If you later want a machine-readable environment template, `.env.example` already
exists and was left as found.
