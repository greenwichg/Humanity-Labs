# India coverage

For the team. This page says what Indian data Humanity Labs holds today, what it does not hold, and what an operator has to do to close the gaps. It is based on the India-first readiness audit of 9 November 2026. Every fact here comes from that audit's verified items or from the lead's implementation decisions. Do not publish anything from the "not published" sections without checking it first.

## Summary

- **Automated feeds:** none from India. The automated sources are the US FDA, UK MHRA, Health Canada, the Canadian Food Inspection Agency and WHO, from 1 January 2024 onward (`ingest_since` in `batchlight/config.py`).
- **Hand-added rows:** 8 (3 medicine, 5 food), in `batchlight/data/india_alerts.json`.
- **Not covered:** India's CDSCO monthly drug alerts, state drug controllers' notices and most FSSAI actions.
- **Labs:** none listed. The testing page points people to NABL's lab search, https://nabl-india.org/lab-accreditation/.

## What is covered today

### Hand-added alerts

`python -m batchlight.cli seed-india` loads these rows (run `alembic upgrade head` first; the old command name `seed-food-india` still works). Each row is validated exactly like an alert added in the curator console, and medicine rows are matched against workspace stock. The seed is safe to re-run. Do not edit the JSON without the lead's agreement.

| # | Category | Title | Evidence | Source host | Regulator (country) | Batches |
|---|---|---|---|---|---|---|
| 1 | medicine | Coldrif cough syrup, batch SR-13 (Sresan Pharmaceutical): toxic diethylene glycol found | `lab_test` | sansad.in | Health Ministry of India (IN) | SR-13 |
| 2 | medicine | Respifresh TR cough syrup, batch R01GL2523 (Rednex Pharmaceutical): toxic diethylene glycol found | `lab_test` | cdn.who.int | WHO (IN) | R01GL2523 |
| 3 | medicine | ReLife cough syrup, batch LSL25160 (Shape Pharma): toxic diethylene glycol found | `lab_test` | cdn.who.int | WHO (IN) | LSL25160 |
| 4 | food | Everest Cumin Powder: recall of one batch ordered for pesticide residues above limits | `report` | businesstoday.in | FSSAI (India) (IN) | E080668761 |
| 5 | food | Patanjali red chilli powder: batch recall ordered; company cites pesticide residue above limit | `report` | business-standard.com | FSSAI (India) (IN) | none (batch number is in the reason text) |
| 6 | food | Hong Kong, April 2024: ethylene oxide found in samples of three MDH spice mixes and Everest Fish Curry Masala sold there | `lab_test` | cfs.gov.hk | Centre for Food Safety (Hong Kong) (HK) | none |
| 7 | food | Rajkot: FSSAI seizes about 6,500 kg of ghee from Korova Milk Product after a lab found it adulterated with vegetable fat | `lab_test` | fssai.gov.in | FSSAI (India) (IN) | none |
| 8 | food | SDP Industries, Daman: FSSAI orders a stop to sales of all ghee the company makes | `report` | business-standard.com | FSSAI (India) (IN) | all batches |

Notes:

- Rows 2 and 3 come from the annex PDF of WHO Medical Product Alert N°5/2025 on cdn.who.int. They replace the news-based versions of the same batches that the audit first proposed.
- Row 1 comes from a Health Ministry answer in the Lok Sabha (unstarred question 1094, 5 December 2025).
- Row 6 is about packs sold in Hong Kong. It is listed because the brands are Indian. Its text says the notice covers only the named packs sold in Hong Kong, and that FSSAI's later factory samples in India found no ethylene oxide.
- Rows 4, 5 and 8 are news reports of FSSAI actions. We could not find or open FSSAI's own notice for any of them.

### Automated WHO alerts about Indian-made products

The automated WHO feed holds WHO alerts from 2024 onward, including N°5/2025 (Coldrif, Respifresh TR and ReLife). WHO usually publishes the affected batches in a separate annex file, so these automated records have no batch numbers and can only be found by product name. The hand-added rows above add the batch numbers for three of those products. Do not hand-edit the automated WHO record: the next ingest would overwrite it.

### Decided not to add

- WHO alerts N°6/2022 (Maiden Pharmaceuticals) and N°1/2023 (Marion Biotech): the lead decided not to add them. The products were for export only, and all batches have expired.
- All labs (see "Lab shortlist" below).

### Verified, but held (not published)

| Item | Why it is held |
|---|---|
| WHO N°4/2023 Guaifenesin TG Syrup; WHO N°6/2023 COLD OUT (Fourrts) | WHO named one batch, and its number is only in the annex. A product-level row would flag every pack of the brand, and COLD OUT is sold in India. Add once the batch is transcribed. |
| Nurokind LC B95Z020 (CDSCO September 2026 state list) | Must be checked against the CDSCO PDF first. Risk of implying the label company made a fake. Sibling batch B95Y032 is not separately verified. |
| J&K FDA ghee, 10 batches | Hindi-only source. The Latin-script batch codes are unverified. |
| Nestle Low Fat Dairy Whitener | A single first-stage sample, disputed by the company, not a recall. Listing it risks wrongly implicating the product. |
| Tamil Nadu cotton candy | No product to check. Already covered as a finding on the food topics pages. |

## Evidence rules

Every alert carries one `evidence` value. It describes the page we link to, not what that page talks about.

| Value | Badge | Use it when |
|---|---|---|
| `official` | Official source | The link is published by a regulator, government body, Parliament or the WHO: a notice, recall, order, statement, aggregate statistic or guidance. |
| `lab_test` | Official lab result | The link is published by a government body or regulator and states that a laboratory tested a specific sample or batch and it failed a legal standard. Never for aggregate statistics, never for passes, never inferred. |
| `report` | News or NGO report | The link is a news outlet or NGO, including news of an official order or a lab result. |

Findings on the food topic pages can also use `study` (peer-reviewed paper) and `provider` (a provider's information about itself). Full definitions are in the `batchlight/food/knowledge.py` docstring and [FOOD_SAFETY.md](FOOD_SAFETY.md).

The code enforces these rules (`CuratedAlertInput` in `batchlight/services/curation.py`):

- `evidence` is required in the curator form, the CSV import and the seed. There is no default. A CSV without an `evidence` column is rejected.
- `official` and `lab_test` are rejected unless the link is on a government, Parliament or WHO site (`OFFICIAL_DOMAINS` in `batchlight/text.py`; this includes gov.in, nic.in, sansad.in, gov.hk and who.int).
- `lab_test` is rejected without a reason saying what the laboratory found.
- `report` is allowed on any link.
- Automated feeds are always `official`.

If an official notice later becomes reachable for a `report` row, change its `source_url` and `evidence` in `batchlight/data/india_alerts.json` (keep its `external_id`) and re-run `seed-india`. The row is updated in place.

### Wording the team must use

- Medicine coverage: "We check medicine and medical device alerts from the US FDA, UK MHRA, Health Canada and WHO, mostly from January 2024 onward, and a few Indian alerts added by our team. India's CDSCO monthly lists of failed medicine samples are not included yet."
- Never write "Indian alerts are not included yet" or "no Indian medicine alerts": a few are now included.
- CDSCO pointer: name the domain cdsco.gov.in and the "Drug Alert" heading only. No deep CDSCO URL has been verified.
- The only phone numbers allowed in the product are: Pharmacovigilance Programme of India 1800-180-3024 (toll-free, Monday to Friday, 9 am to 5:30 pm); FSSAI helpline 1800-11-2100; National Consumer Helpline 1915 (8 am to 8 pm) or 1800114000, WhatsApp +91 88000 01915. No national number exists for fake-medicine complaints (send people to their state's drug control department). No Indian emergency number has been verified; do not add one.

## What is not covered

Updated from section 6 of the audit. As of 9 November 2026, from the build network: WHO annex PDFs on cdn.who.int and www.who.int/docs, www.pib.gov.in, pmc.ncbi.nlm.nih.gov, mdpi-res.com and fsi.stanford.edu are reachable. www.fssai.gov.in is intermittent: it returned errors (502) for most of the day but served the milk survey, edible oil survey, DART booklet and Korova press release PDFs at least once. cdsco.gov.in is never reachable.

| Gap | Why it is still open | Operator action |
|---|---|---|
| CDSCO monthly lists of Not of Standard Quality, spurious and adulterated medicines (central and state laboratories) | cdsco.gov.in resets every connection from the build network; www.cdsco.gov.in and cdscoonline.gov.in are refused. | Enter them from an Indian network (see "Operator actions", step 1). |
| State drug controllers and state food safety departments | Their sites are blocked or not on the network allowlist (for example dca.telangana.gov.in and the Kerala, Maharashtra and Tamil Nadu sites). | Enter state notices from the official PDFs when reachable. Add verified state contact pages to the report and check pages once checked. |
| WHO annex batches for other alerts (N°5/2024 and N°3/2025 IMFINZI; N°4/2023 Guaifenesin TG; N°6/2023 COLD OUT; other pre-2024 alerts) | The annexes are now reachable but have not been transcribed. | See "Operator actions", step 5. |
| WHO alerts before 2024 | The automated feed starts on 1 January 2024. | Either run a one-off WHO backfill from 2021 or add single rows by hand. Do not do both, or the alerts will be duplicated. |
| FSSAI notified-lab lists and the FoSCoS food recall list | www.fssai.gov.in is intermittent from the build network, and the FoSCoS recall service needs a browser session. (The milk survey and edible oil survey findings were re-checked against www.fssai.gov.in on 9 November 2026 and kept.) | Confirm the FoSCoS food recall list loads before ever linking it; fetch the lab lists (step 2). |
| Honey: UK FSA NMR finding | The food.gov.uk page now returns 410 (archived) and the archive copy is blocked. The Foods study was opened on mdpi-res.com and kept with corrected text. | Removed. Re-add only from an official page that can be opened. |
| RAFT "88 kits" | Closed: the PIB release of 31 July 2026 was opened on www.pib.gov.in and supports the figure, so the finding was kept. | None. |
| Rajya Sabha 2023-24 breakdown of unsafe, sub-standard and labelling failures | rsdebate.nic.in redirects to a generic page. | Re-add only if the answer is re-read on sansad.in. |
| Drugs and Cosmetics Act section texts; the current FSS Act text; the Gazette originals of G.S.R. 823(E), 506(E) and 477(E) | indiacode.gov.in is blocked and egazette.gov.in is unreachable. | Verify before adding legal definitions, the medicine QR code rule or the 14-day Section 40 report period. |
| Patanjali batch AJD2400012 or JD2400012 | The BSE/NSE filing could not be opened. | See "Operator actions", step 3. |
| Everest and SDP Industries: FSSAI's own text (FSSAI posted on X) | x.com is not reachable. | See "Operator actions", step 4. |
| PJTAU MFPI-QCL accreditation | The two verifiers disagreed. | Check TC-6135 on NABL's certificate check. List only with confirmed scope and public access. |
| Singapore Food Agency notice on Everest Fish Curry Masala | The old URL returns 404. | Keep it as a news-based finding. |

## Operator actions

1. **CDSCO monthly Drug Alert lists.** From an Indian network, download each monthly CDSCO Drug Alert PDF (central and state laboratory lists). Enter the rows through the curator CSV import (`/admin/alerts/import`) with:
   - `evidence` = `lab_test` and `source_url` = the CDSCO PDF;
   - `alert_type` = `substandard` for Not of Standard Quality and `falsified` for spurious;
   - one row per batch, with the product and the label company exactly as printed, and a reason saying what the laboratory found.

   Start with September 2026. Medical Dialogues reproduced 42 central and 288 state rows for that month; use those reproductions only as a checklist, never as the source. Also verify the Drug Alert page URL, and replace the CSV template's example link (now `https://cdsco.gov.in/`) once it is verified. Repeat after each monthly list.
2. **FSSAI lab lists.** Verifiers reported an FSSAI recognised-lab list at `https://www.fssai.gov.in/docs/food-testing/food-lab/ValidityorderSeptemberfinal.pdf` and a referral-lab list at a path ending `Referral%20Laboratories%20notified%20under%20section%2043.pdf`. Neither was confirmed. Fetch them from an Indian network. Then build lab entries, confirming by phone or email that each lab accepts samples from the public, and re-check the two candidate labs below.
3. **Patanjali batch number.** Read Patanjali Foods' stock exchange filing. If it gives AJD2400012, ask the lead to move that number into the row's `batches` field (row 5 above). Until then it stays in the reason text only.
4. **Everest and SDP Industries official notices.** Look on fssai.gov.in for FSSAI's own notice or order for rows 4 and 8. If one is published there, link it and change `evidence` to `official` (or `lab_test` if it states the failed laboratory result). For SDP Industries, also check whether the order has since been lifted.
5. **Turning WHO annex batches into alerts.** The annex PDFs are reachable on cdn.who.int and www.who.int/docs. For each WHO alert about products made or sold in India, transcribe the affected batches into hand-added rows (one row per batch), linking the annex PDF. Use `lab_test` only when the annex states that a laboratory tested that batch and it failed; otherwise use `official`. Start with the held COLD OUT and Guaifenesin TG rows. For IMFINZI, note that BAZR is a genuine India-only lot and the India-linked fake lot is reported as BAVX; verify before entering anything. Do not edit the automated WHO records.

## Lab shortlist

**NOT PUBLISHED. CONFIRM BEFORE LISTING.** None of these labs is recommended for public listing in this release. No verified lab has confirmed that it accepts samples from the public, and none has a confirmed FSSAI notification. By the lead's decision these rows are **not** in the database. If an operator later adds them, insert them with `active=false` and switch them on only after confirming public access.

Staged candidates, exactly as the audit gives them (TestingLab format):

```json
[
 {"name":"Food Laboratory, Food and Drug Administration Punjab, Kharar","city":"Kharar","region":"Punjab","country_code":"IN",
  "accreditation":"NABL TC-9014 (biological, chemical); NABL list as on 28-10-2026 shows validity extended to 27-12-2026",
  "tests":[],"accepts_public_samples":null,"website":null,"phone":null,
  "source_url":"https://nabl-india.org/wp-content/uploads/2026/10/List-of-CABs-whose-accreditation-cycle-has-been-extended-28-Oct-2026.pdf",
  "verified_on":"2026-11-09",
  "notes":"Food laboratory of the Food and Drug Administration, Punjab. Test scope, FSSAI notification and whether it accepts samples from the public are not confirmed. Not on NABL's suspended or withdrawn lists as on 04-11-2026. Re-check NABL status before 27-12-2026.",
  "active":false},
 {"name":"Food Testing Laboratory, National Institute of Food Technology Entrepreneurship and Management - Thanjavur (formerly IIFPT)","city":"Thanjavur","region":"Tamil Nadu","country_code":"IN",
  "accreditation":"NABL TC-11043 (chemical, biological, mechanical); NABL list as on 28-10-2026 shows validity extended to 28-12-2026",
  "tests":[],"accepts_public_samples":null,"website":null,"phone":null,
  "source_url":"https://nabl-india.org/wp-content/uploads/2026/10/List-of-CABs-whose-accreditation-cycle-has-been-extended-28-Oct-2026.pdf",
  "verified_on":"2026-11-09",
  "notes":"Specific food tests covered, FSSAI status and public sample acceptance are not confirmed. Not on NABL's suspended or withdrawn lists as on 04-11-2026. Re-check after 28-12-2026.",
  "active":false}
]
```

Expiry caveats: the Kharar laboratory's NABL validity (TC-9014) is extended only to 27-12-2026, and the Thanjavur laboratory's (TC-11043) only to 28-12-2026. Re-check both on NABL's site before listing either one.

Do not list:

- CFTRI Mysore: its extension ends 22-11-2026.
- Spices Board QELs (Thane, Thiruvallur, Guntur), CFDL Patna, FDA Mumbai: no accreditation evidence; "FDA Mumbai" is a description, not a name.
- PJTAU AINP pesticide lab: its own page says accreditation ran to 20-07-2026, which has passed.
- PJTAU MFPI-QCL: the verifiers conflict. One found NABL TC-6135 valid to 13-10-2028; the other could not confirm it, and the university's accreditation page omits it. Held for an operator check.
- FDA Chhatrapati Sambhajinagar: NABL accreditation suspended from 06-10-2026.
- Belagavi and Jaipur: extensions lapsed 03-11-2026.
