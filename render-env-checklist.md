# Render environment variables — complete checklist

Everything below is read directly from this codebase (`schoolsystem/settings/base.py`,
`schoolsystem/settings/production.py`) or declared in `render.yaml`. Nothing here is invented.

Where to put them: **Render → your service → Environment → Edit**, then paste the block in
[§6](#6-paste-ready-block) (Render accepts a `KEY=value` block). `render.yaml` already declares most
of these keys, so applying the Blueprint first and then filling in the `sync: false` values works too.

Legend: **R** = required · **S** = secret (never commit) · **O** = optional, safe default exists.

---

## 0. Two ways values reach Render — pick one per key

`render.yaml` is applied as a **Blueprint**, which manages env vars in two ways:

| Mechanism | Behaviour |
| :--- | :--- |
| `value: "..."` | Set from the file, and **re-applied on every Blueprint sync**. A dashboard edit to that key is silently reverted on the next sync. |
| `sync: false` | Render **prompts once, on the *first* Blueprint apply**, in a single list. Afterwards the dashboard owns the value and later syncs never touch it. |

So you never have to type them one by one: the prompted values are collected together on the first apply.
Two caveats worth memorising:

- Adding a **new** `sync: false` key to an already-created Blueprint does **not** re-prompt — set that one
  in the dashboard yourself.
- Keys using `value:` belong to the Blueprint. If you would rather tune one in the dashboard, move it to
  `sync: false` first, or the next sync will overwrite your change.

This repository's `render.yaml` currently sets **24** keys inline and leaves **20** for the first-apply prompt:

```
PGHOST  PGDATABASE  PGUSER  PGPASSWORD  DJANGO_SECRET_KEY  ALLOWED_HOSTS
NEON_STORAGE_BUCKET  AWS_ENDPOINT_URL_S3  AWS_REGION  AWS_ACCESS_KEY_ID  AWS_SECRET_ACCESS_KEY
EMAIL_BACKEND  EMAIL_HOST_USER  EMAIL_HOST_PASSWORD
SMS_ONLINE_GH_API_KEY  DEMO_REQUEST_SMS_TO
EDEN_PORTAL_LOGIN_URL  CACHE_BACKEND  CACHE_LOCATION  REDIS_URL
```

> ⚠️ `sync: false` is **not** honoured inside an `envVarGroup` — Render accepts it silently and the variable
> ends up unset with no prompt. Keep secrets on the service itself, or pre-fill the group in the dashboard
> before syncing.

---

## 1. Database — Neon (required)

`DATABASE_PROFILE=neon` makes `base.py` read the standard libpq `PG*` names instead of the `DB_*`
ones. `production.py` refuses to boot if any are missing.

| Key | Value | |
| :--- | :--- | :--- |
| `DATABASE_PROFILE` | `neon` | R |
| `PGHOST` | your Neon **direct** host, e.g. `ep-xxx-xxx.eu-central-1.aws.neon.tech` | R S |
| `PGDATABASE` | Neon database name | R S |
| `PGUSER` | Neon role | R S |
| `PGPASSWORD` | Neon password | R S |
| `PGPORT` | `5432` | R |
| `PGSSLMODE` | `require` (or `verify-ca` / `verify-full`) | R |

> ⚠️ `PGHOST` must be the **direct** host, **never** the `-pooler` host. Schema-per-tenant isolation
> depends on session-level `search_path`, which transaction pooling does not preserve.
>
> `PGSSLMODE` must be exactly one of `require`, `verify-ca`, `verify-full` — anything else is a hard
> startup error in production.

## 2. Core Django (required)

| Key | Value | |
| :--- | :--- | :--- |
| `DJANGO_SETTINGS_MODULE` | `schoolsystem.settings.production` | R |
| `PYTHON_VERSION` | `3.12` | R |
| `DJANGO_SECRET_KEY` | generate: `python -c "import secrets;print(secrets.token_urlsafe(64))"` | R S |
| `TENANCY_ENABLED` | `True` for a multi-school deployment, `False` for a single school | R |
| `CSRF_TRUSTED_ORIGINS` | see below | R |
| `ALLOWED_HOSTS` | leave **empty** while `TENANCY_ENABLED=True` | O |

`DJANGO_SECRET_KEY` must be **≥ 50 characters with ≥ 5 unique characters**, or production refuses to start.
Do **not** use Render's `generateValue: true` for it: that mints a base64-encoded 256-bit value
(~44 characters), which is below the minimum and will fail the boot check with
`DJANGO_SECRET_KEY must be at least 50 characters`. Generate one yourself instead
(`python -c "import secrets;print(secrets.token_urlsafe(64))"` produces 86 characters).

`ALLOWED_HOSTS` is forced to `*` in tenancy mode (tenant hostnames come from the `Domain` table and are
validated per request). It is only consulted when `TENANCY_ENABLED=False`, where it must list explicit
hostnames and may not be `*`.

**`CSRF_TRUSTED_ORIGINS` matters more than it looks.** `DynamicCsrfMiddleware` trusts an HTTPS `Origin`
only if it is in `CSRF_TRUSTED_ORIGINS` **or** matches a verified, active tenant `Domain`. The marketing
domain is deliberately not a tenant, so it must be listed here or the demo-request form will be rejected.

```
CSRF_TRUSTED_ORIGINS=https://shs-boarding-staging.onrender.com,https://ecreativesys.com,https://www.ecreativesys.com
```

## 3. Public marketing website (required for ecreativesys.com)

| Key | Value | |
| :--- | :--- | :--- |
| `PUBLIC_WEBSITE_DOMAIN` | `ecreativesys.com,www.ecreativesys.com` | R |
| `PUBLIC_WEBSITE_ENABLED` | `True` | R |
| `SITE_URL` | `https://ecreativesys.com` | R |
| `EDEN_PORTAL_LOGIN_URL` | *(empty)* — hides the "Login" link | O |
| `PRICING_PUBLIC_ENABLED` | `False` until an approved price list exists | O |
| `PUBLIC_WEBSITE_URLCONF` | default `schoolsystem.urls_public` — do not change | O |

- Hostnames only: no scheme, no port, no path.
- **Never** put a school's hostname here, and never add `ecreativesys.com` to the `Domain` table —
  either would present a school dashboard at the marketing address.
- `SITE_URL` drives canonical URLs, Open Graph tags, the sitemap and the `Sitemap:` line in robots.txt.

## 4. Demo-request notifications (email + SMS)

These are independent: either can be enabled without the other.

### Email

| Key | Value | |
| :--- | :--- | :--- |
| `EMAIL_BACKEND` | `django.core.mail.backends.smtp.EmailBackend` | R |
| `EMAIL_HOST` | `smtp.gmail.com` | R |
| `EMAIL_PORT` | `587` | R |
| `EMAIL_HOST_USER` | `berkojohnson@gmail.com` | R S |
| `EMAIL_HOST_PASSWORD` | the **16-character Gmail app password** (not your account password) | R S |
| `EMAIL_USE_TLS` | `True` | R |
| `DEFAULT_FROM_EMAIL` | `berkojohnson@gmail.com` | R |
| `DEMO_REQUEST_NOTIFY_EMAILS` | `berkojohnson@gmail.com` | R |
| `SERVER_EMAIL` | defaults to `DEFAULT_FROM_EMAIL` | O |
| `EMAIL_USE_SSL` | `False` — mutually exclusive with `EMAIL_USE_TLS` | O |
| `EMAIL_TIMEOUT` | `10` | O |

> ⚠️ `render.yaml` deliberately leaves `EMAIL_BACKEND` as `sync: false`. **Leave it blank and email is
> printed to the log and never sent — while the record still shows `notification_sent=True`.** Set it to
> `django.core.mail.backends.smtp.EmailBackend` on any service that must send real mail.
>
> `DEMO_REQUEST_NOTIFY_EMAILS` is what actually receives enquiries (the Blueprint sets it inline). If you
> leave it blank the code falls back to `EDEN_SYS_EMAIL` (default `berkojohnson@gmail.com`), then
> `DEFAULT_FROM_EMAIL`.

### SMS

| Key | Value | |
| :--- | :--- | :--- |
| `SMS_ONLINE_GH_API_KEY` | your SMSOnlineGH API key | R S |
| `DEMO_REQUEST_SMS_TO` | `+233248160391` (comma-separated for several numbers) | R |
| `DEMO_REQUEST_SMS_SENDER` | `EDENSYS` — must be approved on your SMSOnlineGH account | O |
| `SMS_SENDER_ID` | `EDENSYS` — used as the fallback sender for the alert, and by school SMS | O |
| `SMS_ENABLED` | `False` — keeps **guardian** SMS off | O |

> ⚠️ `SMS_ENABLED=False` does **not** disable the demo-request alert. The alert is driven only by
> `DEMO_REQUEST_SMS_TO` and shares just the API key. Empty `DEMO_REQUEST_SMS_TO` = alert off.
>
> Each submission sends a **real, chargeable SMS**. Leave it empty in a staging service if you only
> want email.

## 5. Media storage, cache, workers

| Key | Value | |
| :--- | :--- | :--- |
| `MEDIA_STORAGE_BACKEND` | `neon_s3` (durable) or `filesystem` (ephemeral on Render) | R |
| `NEON_STORAGE_BUCKET` | bucket name | R with `neon_s3` |
| `AWS_ENDPOINT_URL_S3` | Neon S3 endpoint | R with `neon_s3` |
| `AWS_REGION` | region | R with `neon_s3` |
| `AWS_ACCESS_KEY_ID` | access key | R S |
| `AWS_SECRET_ACCESS_KEY` | secret key | R S |
| `CACHE_BACKEND` | `django.core.cache.backends.redis.RedisCache` | R |
| `CACHE_LOCATION` | `redis://<host>:6379/1` | R |
| `REDIS_URL` | Celery broker — needed only for the worker/beat services | O |
| `BACKGROUND_JOBS_ENABLED` | `False` on the web-only blueprint; set `True` only when worker + Beat run | O |
| `TRUSTED_PROXY_COUNT` | `1` | O |

> `MEDIA_STORAGE_BACKEND=filesystem` loses uploads on every deploy and restart. Use `neon_s3` in production.
>
> Production refuses to start without an explicit shared cache backend and location. If the cache
> becomes unavailable, login and public concern-report submissions return HTTP 503 rather than
> bypassing their rate limits.
>
> The supplied `render.yaml` is **web-only**: it sets `BACKGROUND_JOBS_ENABLED=False`, so SMS
> dispatch, overdue reminders, roll-call expiry, ID-export cleanup, and SHS 3 phase-out do not run.
> `manage.py check` warns with `core.W003`; `SMS_ENABLED=True` with background jobs off is a hard
> error (`core.E004`). Add paid worker + Beat services, set `REDIS_URL`, and set
> `BACKGROUND_JOBS_ENABLED=True` on every service before enabling SMS.

## 6. Paste-ready block

Replace every `<...>` before pasting.

```ini
DJANGO_SETTINGS_MODULE=schoolsystem.settings.production
PYTHON_VERSION=3.12
DATABASE_PROFILE=neon
TENANCY_ENABLED=True
PGHOST=<neon-direct-host-no-pooler>
PGDATABASE=<neon-db-name>
PGUSER=<neon-user>
PGPASSWORD=<neon-password>
PGPORT=5432
PGSSLMODE=require
DJANGO_SECRET_KEY=<64-char-random-string>
ALLOWED_HOSTS=
CSRF_TRUSTED_ORIGINS=https://shs-boarding-staging.onrender.com,https://ecreativesys.com,https://www.ecreativesys.com
MEDIA_STORAGE_BACKEND=neon_s3
NEON_STORAGE_BUCKET=<bucket>
AWS_ENDPOINT_URL_S3=<endpoint>
AWS_REGION=<region>
AWS_ACCESS_KEY_ID=<access-key-id>
AWS_SECRET_ACCESS_KEY=<secret-access-key>
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_HOST_USER=berkojohnson@gmail.com
EMAIL_HOST_PASSWORD=<16-char-gmail-app-password>
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
DEFAULT_FROM_EMAIL=berkojohnson@gmail.com
SERVER_EMAIL=berkojohnson@gmail.com
DEMO_REQUEST_NOTIFY_EMAILS=berkojohnson@gmail.com
SMS_ONLINE_GH_API_KEY=<smsonlinegh-api-key>
SMS_SENDER_ID=EDENSYS
SMS_ENABLED=False
BACKGROUND_JOBS_ENABLED=False
DEMO_REQUEST_SMS_TO=+233248160391
DEMO_REQUEST_SMS_SENDER=EDENSYS
PUBLIC_WEBSITE_DOMAIN=ecreativesys.com,www.ecreativesys.com
PUBLIC_WEBSITE_ENABLED=True
SITE_URL=https://ecreativesys.com
EDEN_PORTAL_LOGIN_URL=
PRICING_PUBLIC_ENABLED=False
SECURE_HSTS_SECONDS=0
TRUSTED_PROXY_COUNT=1
```

## 7. Optional keys you can leave unset

Safe defaults already exist for all of these; set them only if you want to tune behaviour.

| Key | Default | Purpose |
| :--- | :--- | :--- |
| `EDEN_SYS_COMPANY` / `EDEN_SYS_SHORT_NAME` / `EDEN_SYS_PRODUCT_NAME` | `Eden Creative Systems` / `Edensys` / `SHS Boarding & Dorm Management System` | Site branding |
| `EDEN_SYS_PHONE` / `EDEN_SYS_EMAIL` / `EDEN_SYS_DOMAIN` | `+233 24 816 0391` / `berkojohnson@gmail.com` / `ecreativesys.com` | Site contact details |
| `EDEN_SYS_ATTRIBUTION` | `Developed by Eden Creative Systems (Edensys)` | Footer line |
| `PLATFORM_ADMIN_DOMAIN` | *(empty)* | Platform-admin hostname |
| `SMS_TIME_ZONE` / `SMS_HTTP_TIMEOUT` / `SMS_MAX_ATTEMPTS` | `UTC` / `10` / `5` | School SMS dispatch |
| `DEMO_REQUEST_RATE_LIMIT` / `DEMO_REQUEST_RATE_WINDOW` | `5` / `3600` | Demo-form throttle per IP |
| `LOGIN_RATE_LIMIT` / `LOGIN_RATE_WINDOW` | `10` / `300` | Login throttle |
| `PUBLIC_REPORT_RATE_LIMIT` / `PUBLIC_REPORT_RATE_WINDOW` | `5` / `3600` | Public report throttle |
| `STUDENT_ID_PUBLIC_TOKEN_TTL_DAYS` | `0` (never expires) | QR token lifetime |
| `MEDIA_ROOT` | `<project>/media` | Only for `filesystem` storage |
| `CSP_REPORT_URI` | *(empty)* | Must be a safe relative path or HTTPS URL |
| `SECURE_HSTS_INCLUDE_SUBDOMAINS` / `SECURE_HSTS_PRELOAD` | `false` | Require HSTS > 0 first |

### Raising HSTS for real production

`SECURE_HSTS_SECONDS=0` is a **staging** value so the onrender.com host is not pinned to HTTPS for a
year. Once `ecreativesys.com` is live over HTTPS and verified, raise it:

```ini
SECURE_HSTS_SECONDS=31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS=true
```

`SECURE_HSTS_PRELOAD=true` additionally needs ≥ 1 year **and** include-subdomains, or startup fails.

## 8. After deploying — sanity checks

1. `https://<service>.onrender.com/healthz` returns 200.
2. `https://ecreativesys.com/` serves the marketing site (not a school login page).
3. `https://<service>.onrender.com/admin/` returns 404 — the marketing host never exposes school URLs.
4. Run the migrations once from the Render Shell (Free plan; on paid plans enable `preDeployCommand`
   in `render.yaml` instead):
   ```bash
   python manage.py migrate --schema public --settings=schoolsystem.settings.production
   python manage.py migrate --include-dynamic-schemas --settings=schoolsystem.settings.production
   ```
5. Send yourself a real message:
   ```bash
   python manage.py sendtestemail you@example.com --settings=schoolsystem.settings.production
   ```
6. Submit the public form once, then confirm both channels:
   ```bash
   python manage.py demo_requests --list --settings=schoolsystem.settings.production
   ```
   The row's `notification_sent` (email) and `notification_sms_sent` (SMS) show what actually went out —
   or use `demo_requests --json` for machine-readable output.

## 9. Git-ignored, never committed

`.env` is git-ignored and holds your real credentials locally. Keep Render's values in the dashboard,
never in `render.yaml` (which is committed) — that is why those entries use `sync: false`.
