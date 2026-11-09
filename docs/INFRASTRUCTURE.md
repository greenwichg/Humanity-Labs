# Public works: Project Passports

The Public works module (`/infrastructure`) helps people in India find a public works project and see, from official records, who approved and funded it, who won the contract, for how much, and when it was due. It shows each record's source, and it says plainly what is not published.

It shares the web app, database, ingestion runs, worker and design system with the medicine and food modules. It has its own tables, so it does not change how either of them works.

## Rules

- **Only published records.** Every answer on a passport comes from a named record and links to it. If a source does not publish something, the passport says "Not published" and why. We never estimate, average or guess a missing value.
- **One calculation, labelled.** The only value we work out is the expected completion of a contracted work: award date plus the contract duration printed in the award notice. It is labelled "Calculated by us", with the working shown.
- **Planned financing close is not completion.** A World Bank programme's closing date is the end of the financing, and the page says so.
- **No allegations.** A contract award, a passed date or a change in cost is not presented as wrongdoing. When a date has passed and no completion is recorded, the passport says that "alone does not show a delay". Rejected bidders are counted, never named. Contractors' addresses are not stored.
- **Costs with no stated unit are held back.** The PMGSY dataset gives cost and spending figures without saying whether they are rupees, lakh or crore. They are kept in each record's `data` (`unconfirmed_unit_costs`) but never shown. Passports say why. Show them only after confirming the unit from PMGSY's own reports (see below).
- **Evidence labels** (`InfraEvent.evidence`): `official` (a government or funder's published record), `official_copy` (official data republished by another organisation, such as India Data Portal's copy of PMGSY data), `official_finding` (an auditor, inspector, court or regulator's finding), `calculated` (worked out by us, working shown), `report` (news or NGO), `citizen` (sent by a member of the public, not verified). The last three are designed in, but no source produces them yet.

## Connected sources (verified 9 November 2026)

| Source id | What it gives | Refresh |
|---|---|---|
| `infra-wb-projects` | Every World Bank project in India: Board approval date, total cost, World Bank commitment, borrower, implementing agencies, status, planned closing date. Programmes that are not infrastructure are stored but hidden unless they have civil works. | daily |
| `infra-wb-civil-works` | World Bank civil-works (CW) notices for India: tender invitations (date, deadline, tendering body) and contract awards (contractor, signed price, award date, duration, number of other bids evaluated or rejected). One passport per bid reference; a tender and its award merge. | every 12 hours |
| `infra-pmgsy-roads` | PMGSY rural roads nationwide (about 169,000 roads in 30 states and UTs): state, district, block, habitations served, contractor, sanction year, award date, completion date (only for roads whose stage is Completed or Maintenance), length, surface and stage. Read from India Data Portal's copy of the Ministry of Rural Development's OMMAS data (Open Data Commons Attribution licence), and labelled "Official data, republished". | monthly |
| `infra-cag-reports` | Audit reports by the Comptroller and Auditor General whose title or sector covers public works, with their state. Shown on state pages; not linked to individual projects yet. | daily |

States come from the record itself (state names in the bid reference, scope, project name or tendering body). A work that names no state takes it from its programme only when the programme covers exactly one state. Infrastructure type is our reading of the description and is labelled that way.

## Checked, not connected yet

Listed on `/infrastructure/sources` (`NOT_CONNECTED` in `batchlight/infra/sources.py`):

- **PMGSY's own portal (omms.nic.in)**: up-to-date records, quality monitors' grades and maintenance details. Could not be reached from our current servers; needs hosting in India. Its data is meanwhile read from India Data Portal's copy (`infra-pmgsy-roads`).
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
python -m batchlight.cli infra-ingest --source infra-pmgsy-roads      # ~127 MB download, ~169,000 roads; takes a while
```

The worker refreshes each source on its own interval after the medicine and food sources.

## Hosting ingestion in India

Several official sites refuse connections from servers outside India: PMGSY's OMMAS, data.gov.in, CDSCO, the central pollution board and others (checked 9 November 2026). The web app can stay where it is. Only the ingestion needs an Indian network:

1. Create a small Linux VM in an Indian cloud region (for example AWS Mumbai `ap-south-1`, Google Cloud Mumbai `asia-south1` or Azure Central India). 1 vCPU and 2 GB of memory are enough.
2. Install Docker, then run the same image with the `worker` command and the same environment variables as the production worker, including `BATCHLIGHT_DATABASE_URL` pointing at the production database. Allow the VM's address in the database's network rules.
3. Stop the worker that runs outside India, so only one worker refreshes the sources.
4. From the VM, check the blocked sites answer: `curl -I https://omms.nic.in/`.

Building the direct OMMAS connector needs someone working from an Indian network, to read its real pages first: for example a Claude Code session on a computer in India, connected with `claude remote-control`. Until then the India Data Portal copy is used.

## Confirming the PMGSY cost unit

Before cost and spending are shown, confirm the unit from PMGSY's own reports (from an Indian network, OMMAS reports label their amount columns), and compare a few roads' figures with the dataset. Then add the unit and a conversion to `map_pmgsy_road` in `batchlight/infra/sources.py`, fill `budget_amount`/`contract_amount` only for the fields that match, and re-run `infra-ingest --source infra-pmgsy-roads`.

## Next increments

1. Host ingestion in India (above), confirm the PMGSY cost unit, and connect OMMAS directly for quality grades and maintenance periods.
2. One state e-procurement portal, after checking its terms of use.
3. MoSPI's central project reports, for original and revised cost and dates.
4. Curators link CAG findings to specific projects as `official_finding` events.
5. Citizen evidence (photos, RTI replies) as `citizen` events, after legal review, moderation and a right of reply.
