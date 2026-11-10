# Deploying Batchlight

Batchlight is two processes from one container image, plus PostgreSQL:

| Process | Command | Scales |
|---|---|---|
| Web (UI and API) | `batchlight-entrypoint web` | Horizontally, stateless |
| Worker (ingestion, matching, notifications) | `batchlight-entrypoint worker` | Run exactly one |
| Migrations | `batchlight-entrypoint migrate` | Run once per release, before web and worker start |

## Requirements

- PostgreSQL 14 or newer with the `citext` and `pgcrypto` extensions available (the first migration creates them).
- Outbound HTTPS from the worker to: `api.fda.gov`, `www.gov.uk`, `recalls-rappels.canada.ca`, `www.who.int`. The food sources use the same hosts.
- An SMTP service for email (any provider: Amazon SES, Postmark, Mailgun, your own relay).
- HTTPS termination in front of the web process (a load balancer or reverse proxy).

## Configuration

All settings are environment variables prefixed `BATCHLIGHT_`. See `.env.example` for the full list.

| Variable | Production value |
|---|---|
| `BATCHLIGHT_ENV` | `production` |
| `BATCHLIGHT_DATABASE_URL` | `postgresql+psycopg://user:password@host:5432/batchlight` |
| `BATCHLIGHT_BASE_URL` | Public URL, e.g. `https://batchlight.example.org` (used in email links) |
| `BATCHLIGHT_SECURE_COOKIES` | `true` (also enables HSTS) |
| `BATCHLIGHT_TRUST_PROXY_HEADERS` | `true` only behind a proxy that appends to `X-Forwarded-For`; the app uses the rightmost entry. Also set `FORWARDED_ALLOW_IPS` to the proxy's address |
| `BATCHLIGHT_EMAIL_BACKEND` | `smtp` |
| `BATCHLIGHT_SMTP_HOST`, `_PORT`, `_USERNAME`, `_PASSWORD`, `_STARTTLS` | From your email provider |
| `BATCHLIGHT_EMAIL_FROM` | A sender address your provider has verified |
| `BATCHLIGHT_OPENFDA_API_KEY` | Recommended; free from open.fda.gov, raises rate limits |
| `BATCHLIGHT_INGEST_INTERVAL_MINUTES` | `60` is a good default |
| `BATCHLIGHT_INGEST_SINCE` | How far back the first refresh reaches, default `2024-01-01` |
| `BATCHLIGHT_SECRET_KEY` | Required in production: at least 32 random characters (e.g. `openssl rand -hex 32`). Keys the hash of food reporters' network addresses; the app refuses to start in production without it |
| `BATCHLIGHT_REPORT_LIMIT_PER_HOUR`, `BATCHLIGHT_REPORT_EMAILS_PER_ADDRESS_PER_DAY` | Food report limits per network (IPv6 grouped by /64) and acknowledgement emails per address; defaults 5 and 3 |

Keep secrets (database password, SMTP password, API keys) in your platform's secret store, not in files.

## Option 1: Docker Compose on a single server

Suitable for a pilot with a few hundred workspaces.

```bash
cp .env.docker .env.production   # then edit: database, SMTP, BASE_URL, SECURE_COOKIES=true
docker compose --env-file .env.production up -d --build
```

Put a reverse proxy such as Caddy or nginx in front of port 8000 for HTTPS. Back up the `pgdata` volume, or better, use a managed PostgreSQL service and remove the `db` service.

## Option 2: A container platform (Render, Fly.io, Railway, Cloud Run, ECS)

For AWS there is a ready-made setup: Terraform in `infra/terraform` and GitHub Actions workflows for provisioning, updates, backups, restores and teardown. See [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md). The steps below are for other platforms.

1. Build and push the image: `docker build -t registry.example.org/batchlight:<version> .`
2. Create a managed PostgreSQL database.
3. Create a **web service** from the image with command `web`, port `8000`, health check path `/readyz`.
4. Create a **background worker** from the same image with command `worker`, one instance.
5. Configure a **release or pre-deploy command** of `migrate`, so migrations run once before new code starts.
6. Set the environment variables above on both services.

On platforms without long-running workers (for example Cloud Run), schedule `python -m batchlight.worker --once` with the platform's scheduler every hour instead.

## Option 3: A virtual machine without containers

```bash
python3.13 -m venv /opt/batchlight/.venv
/opt/batchlight/.venv/bin/pip install -r requirements.txt
cd /opt/batchlight && .venv/bin/alembic upgrade head
```

Run `uvicorn batchlight.main:app --host 127.0.0.1 --port 8000 --workers 2 --proxy-headers` and `python -m batchlight.worker` as two systemd services, with nginx in front for HTTPS. The committed `batchlight/static/app.css` means Node.js is not needed on the server.

## After the first deploy

1. Sign up through the web UI to create your account.
2. Make yourself a curator: `batchlight-entrypoint python -m batchlight.cli make-curator you@example.org`
3. Open **Curator console** and press **Run now** on each source, or wait for the worker's first pass. The first FDA drug, device and food refresh takes several minutes.
4. Load the hand-checked Indian medicine and food alerts: run `alembic upgrade head` first (the `migrate` step does this), then `batchlight-entrypoint python -m batchlight.cli seed-india`. It reads `batchlight/data/india_alerts.json`, is safe to re-run, and matches the medicine rows against workspace stock. The old command name `seed-food-india` still works. See [INDIA_COVERAGE.md](INDIA_COVERAGE.md) for what these rows cover and what is still missing.
5. Load public works data: `batchlight-entrypoint python -m batchlight.cli infra-ingest`. The first civil-works run reads notices back to 2018 and takes a while; afterwards the worker refreshes it. See [INFRASTRUCTURE.md](INFRASTRUCTURE.md).
6. Check `/sources` shows recent refresh times for every source, and `/infrastructure/sources` for public works.

## Operations

- **Health checks.** `/healthz` reports the process is up; `/readyz` also checks the database.
- **Source health.** The curator console lists every ingestion run with counts and errors. A source whose last run failed is flagged there and on `/sources`.
- **Logs.** Web and worker log to standard output. Search for `batchlight.ingest` for source runs and `unhandled error` for application errors.
- **Backups.** Back up PostgreSQL daily with point-in-time recovery if your provider offers it. All state is in the database.
- **Upgrades.** Deploy a new image; the `migrate` step applies schema changes. Every migration has a downgrade.
- **Scaling.** Add web instances freely. Keep one worker. Add a read replica if public search traffic grows. Put a CDN in front of public pages (`/`, `/alerts`, `/check`) with a short cache time if needed.

## Security checklist

- [ ] HTTPS everywhere, `BATCHLIGHT_SECURE_COOKIES=true`.
- [ ] Database not reachable from the internet; credentials in a secret store.
- [ ] `BATCHLIGHT_TRUST_PROXY_HEADERS` left `false` unless the proxy sets `X-Forwarded-For` and nothing else can reach the app directly.
- [ ] Rate limiting on public endpoints at the proxy or CDN. The app throttles logins itself, but not public search.
- [ ] Only trusted people have the curator role; curated alerts notify every matching workspace.
- [ ] Database backups tested by restoring them.
