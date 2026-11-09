# Batchlight

**Medicine safety alerts that reach the shelf and the patient.**

Every week, regulators find medicine and device batches that are contaminated, sub-potent, mislabelled, non-sterile or falsified. Their alerts are published one website at a time, often as PDFs, and mostly reach distributors rather than the pharmacies, clinics and homes holding the affected product. In low- and middle-income countries about one medical product in ten is substandard or falsified, and contaminated cough syrups killed children in several countries between 2022 and 2025.

Batchlight gathers alerts from regulators into one batch-level registry, matches them against the stock lists of pharmacies, clinics, distributors and households, notifies the people holding affected batches, and records what they did about it.

- **Anyone** can check a batch number against every source, with no account.
- **Pharmacies, clinics and distributors** list their stock (by hand, CSV or API) and are told when any regulator flags a batch they hold.
- **Families** list the medicines they take and get plain-language guidance if one is recalled.
- **Procurement teams** see each manufacturer's alert history across regulators.
- **Everyone** can see real outcomes: time to action, units removed, and match precision.

## How this product was chosen

This repository started with a research exercise: 16 research agents investigated 48 significant human problems across health, education, climate, food, agriculture, finance, safety, information integrity, accessibility, labour and housing. These were merged into 26 opportunities and ranked against impact, reach, urgency, uniqueness, feasibility, scalability, sustainability and measurability.

- [docs/research/SELECTION.md](docs/research/SELECTION.md): ranking, selection rationale, risks and runners-up
- [docs/research/opportunities.md](docs/research/opportunities.md): the 26 opportunities with competitive analysis
- [docs/research/problems.md](docs/research/problems.md): all 48 problems with sources
- [docs/PRODUCT.md](docs/PRODUCT.md): vision, personas, journeys, feature priorities, differentiation, outcomes and sustainability model
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): architecture, data model, matching algorithm, API, security and scalability
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): configuration and deployment

## What works today

| Area | Status |
|---|---|
| Live ingestion | US FDA drug and device recalls (openFDA), UK MHRA alerts (GOV.UK APIs), Health Canada recalls (open data plus alert pages), WHO Medical Product Alerts |
| Batch extraction | Parses free-text lot descriptions (about 98% of a 1,000-recall FDA sample yield batches or an "all lots" flag) and HTML batch tables |
| Curated alerts | Form and CSV import for PDF-only regulators such as CDSCO and NAFDAC |
| Public site | Batch check, alert search and detail, manufacturer histories, source freshness, live impact metrics |
| Workspaces | Sign up, roles (owner, manager, member), invitations, workspace switching |
| Inventory | Add, edit, remove, CSV import with row-level validation, CSV export |
| Matching | Batch-level and product-level matching with explained reasons, on new alerts and on new stock |
| Closed loop | Inbox, notifications, email digests, outcome recording, reopen, audit log |
| API | Public search and batch check; API-key access to holdings and matches; OpenAPI docs at `/api/docs` |
| Security | Argon2id passwords, hashed sessions, tokens and API keys, CSRF protection, strict CSP, login throttling, tenant isolation |

## Run it locally

Requirements: Python 3.12 or newer and PostgreSQL 14 or newer. Node.js is only needed to change the stylesheet.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                        # edit BATCHLIGHT_DATABASE_URL if needed
createdb batchlight
alembic upgrade head
python -m batchlight.cli ingest             # first refresh of every source, takes several minutes
uvicorn batchlight.main:app --reload        # http://localhost:8000
```

Sign up in the browser, then grant yourself the curator role to see the curator console:

```bash
python -m batchlight.cli make-curator you@example.org
```

Run the worker to keep alerts fresh: `python -m batchlight.worker` (or `--once` from cron).

With Docker instead: `docker compose up --build`, then open http://localhost:8000 (mail is captured at http://localhost:8025).

## Tests

```bash
createdb batchlight_test
pytest                                       # 100+ unit and integration tests against real PostgreSQL
ruff check batchlight tests && ruff format --check batchlight tests
```

Integration tests replay recorded responses from every regulator, so they run offline. Set `BATCHLIGHT_TEST_DATABASE_URL` to use a different test database; it is wiped on each run.

To change styles: `npm ci && npm run build:css` (Tailwind CSS 4), then commit `batchlight/static/app.css`.

## Project layout

```
batchlight/
  ingest/        source adapters (FDA, MHRA, Health Canada, WHO) and the ingestion pipeline
  lots.py        batch-number extraction from text and HTML tables
  matching.py    explainable alert-to-stock matching
  services/      holdings, alerts, curation, notifications, email, metrics, audit
  web/           FastAPI app, auth, routes (public, auth, workspace, curator, API)
  templates/     Jinja2 templates (server-rendered, work without JavaScript)
  worker.py      scheduled ingestion
migrations/      Alembic schema migrations
tests/           unit tests, integration tests and recorded regulator fixtures
docs/            research, product, architecture and deployment documents
```

## Data sources and licences

openFDA data is in the public domain. GOV.UK content is under the Open Government Licence v3.0. Health Canada data is under the Open Government Licence – Canada. WHO alerts are summarised with links to the original notice.

## Important

Batchlight relays official alerts and links to each source. It is not medical advice. "No alert found" is not proof that a product is genuine or of good quality.
