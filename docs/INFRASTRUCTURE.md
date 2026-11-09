# Public works: Project Passports

The Public works module (`/infrastructure`) helps people in India find a public works project and see, from official records, who approved and funded it, who won the contract, for how much, and when it was due. It shows each record's source, and it says plainly what is not published.

It shares the web app, database, ingestion runs, worker and design system with the medicine and food modules. It has its own tables, so it does not change how either of them works.

## Rules

- **Only published records.** Every answer on a passport comes from a named record and links to it. If a source does not publish something, the passport says "Not published" and why. We never estimate, average or guess a missing value.
- **One calculation, labelled.** The only value we work out is the expected completion of a contracted work: award date plus the contract duration printed in the award notice. It is labelled "Calculated by us", with the working shown.
- **Planned financing close is not completion.** A World Bank programme's closing date is the end of the financing, and the page says so.
- **No allegations.** A contract award, a passed date or a change in cost is not presented as wrongdoing. When a date has passed and no completion is recorded, the passport says that "alone does not show a delay". Rejected bidders are counted, never named. Contractors' addresses are not stored.
- **Evidence labels** (`InfraEvent.evidence`): `official` (a government or funder's published record), `official_finding` (an auditor, inspector, court or regulator's finding), `calculated` (worked out by us, working shown), `report` (news or NGO), `citizen` (sent by a member of the public, not verified). The last three are designed in, but no source produces them yet.

## Connected sources (verified 9 November 2026)

| Source id | What it gives | Refresh |
|---|---|---|
| `infra-wb-projects` | Every World Bank project in India: Board approval date, total cost, World Bank commitment, borrower, implementing agencies, status, planned closing date. Programmes that are not infrastructure are stored but hidden unless they have civil works. | daily |
| `infra-wb-civil-works` | World Bank civil-works (CW) notices for India: tender invitations (date, deadline, tendering body) and contract awards (contractor, signed price, award date, duration, number of other bids evaluated or rejected). One passport per bid reference; a tender and its award merge. | every 12 hours |
| `infra-cag-reports` | Audit reports by the Comptroller and Auditor General whose title or sector covers public works, with their state. Shown on state pages; not linked to individual projects yet. | daily |

States come from the record itself (state names in the bid reference, scope, project name or tendering body). A work that names no state takes it from its programme only when the programme covers exactly one state. Infrastructure type is our reading of the description and is labelled that way.

## Checked, not connected yet

Listed on `/infrastructure/sources` (`NOT_CONNECTED` in `batchlight/infra/sources.py`):

- **PMGSY rural roads (omms.nic.in)**: the richest public source for rural roads (contractor, sanctioned cost, dates, quality monitor grades, 5-year maintenance). Could not be reached from our current servers; needs hosting in India.
- **CPPP and state e-procurement portals**: tenders and award names, but no machine-readable listing and captcha-protected pages.
- **MoSPI central project monitoring** (₹150 crore and above, original and revised cost and dates): website reachable; project-level data not located yet.
- **MoRTH and NHAI**: national highways; not yet assessed.
- **data.gov.in**: could not be reached from our servers.

Payments, completion certificates, inspection reports, defect liability periods and current condition are rarely published anywhere. Passports mark them missing and explain how to ask for them under the Right to Information Act.

## Nationwide by design

- All 28 states and 8 union territories are first-class (`batchlight/infra/geo.py`), each with its own page and an honest coverage note, even when no source covers it yet.
- A passport is one `infra_projects` row per programme or contracted work, with a timeline of `infra_events` (each with its own evidence label and source link), linked contractors (`infra_parties`) and state tags (`state_slugs`, GIN-indexed). Works link to their programme by the funder's project id.
- Adding a source means adding one `InfraSource` class to `REGISTRY` that yields `ProjectData` or `DocumentData`. Merging, change tracking, linking, search and the pages work unchanged. A state e-procurement adapter, PMGSY and MoSPI each fit this shape.
- Change tracking: when a source changes a planned date, a contract price, a total cost or a status, the passport gets a dated "change" event showing old and new values.

## Running it

```bash
alembic upgrade head                                        # creates the infra_* tables (migration 0007)
python -m batchlight.cli infra-ingest                       # all three sources now
python -m batchlight.cli infra-ingest --source infra-wb-civil-works   # first run reads notices back to 2018 (tens of minutes)
python -m batchlight.cli infra-ingest --source infra-cag-reports --cag-pages 50   # read further back in the CAG list
```

The worker refreshes each source on its own interval after the medicine and food sources.

## Next increments

1. Host ingestion in India and connect PMGSY (rural roads with quality grades and maintenance periods).
2. One state e-procurement portal, after checking its terms of use.
3. MoSPI's central project reports, for original and revised cost and dates.
4. Curators link CAG findings to specific projects as `official_finding` events.
5. Citizen evidence (photos, RTI replies) as `citizen` events, after legal review, moderation and a right of reply.
