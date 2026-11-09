# Humanity Labs

**A consumer-first safety platform: check medicines and food against official safety alerts.**

Regulators and scientists regularly find medicines and food that are contaminated, mislabelled, sub-standard or fake. Their findings are scattered across websites, PDFs and press releases and rarely reach the people holding the product. Humanity Labs gathers them, links every one to its source, and makes them easy to check. It has two modules:

- **Medicines (Batchlight).** Batch-level medicine and device alerts from the US FDA, UK MHRA, Health Canada and the WHO. Anyone can check a batch number. Pharmacies, clinics and distributors can list their stock and be told when a regulator flags a batch they hold, then record what they did.
- **Food Safety.** Food recalls from the US FDA and the Canadian Food Inspection Agency plus curated notices from India's FSSAI, searchable by product, brand, batch code or barcode. Plain-language summaries of what official surveys and peer-reviewed studies found in milk, spices, turmeric, oils, honey and more. Pack checks for the FSSAI licence number, the barcode and the label. A report form for suspicious products, and guidance on getting food tested at an accredited lab. See [docs/FOOD_SAFETY.md](docs/FOOD_SAFETY.md).

Humanity Labs never claims to detect adulteration. Every finding comes from a regulator, a peer-reviewed study or a credible report, and says which.

## How this product was chosen

This repository started with a research exercise: 16 research agents investigated 48 significant human problems across health, education, climate, food, agriculture, finance, safety, information integrity, accessibility, labour and housing. These were merged into 26 opportunities and ranked against impact, reach, urgency, uniqueness, feasibility, scalability, sustainability and measurability.

- [docs/research/SELECTION.md](docs/research/SELECTION.md): ranking, selection rationale, risks and runners-up
- [docs/research/opportunities.md](docs/research/opportunities.md): the 26 opportunities with competitive analysis
- [docs/research/problems.md](docs/research/problems.md): all 48 problems with sources
- [docs/PRODUCT.md](docs/PRODUCT.md): vision, personas, journeys, feature priorities, differentiation, outcomes and sustainability model
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): architecture, data model, matching algorithm, API, security and scalability
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md): configuration and deployment
- [docs/FOOD_SAFETY.md](docs/FOOD_SAFETY.md): the Food Safety module, its evidence policy and data coverage
- [docs/research/FOOD_SAFETY_EVIDENCE.md](docs/research/FOOD_SAFETY_EVIDENCE.md): graded, fact-checked research on food safety in India

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
| Food Safety | Food recall search by name, batch or barcode; sourced findings on 8 common foods; FSSAI number, barcode and label checks; consumer reports with a curator queue; testing guide and curated lab directory |
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
python -m batchlight.cli seed-food-india    # curated India food regulatory actions (safe to re-run)
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
