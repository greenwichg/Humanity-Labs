# Food Safety module

Humanity Labs is a consumer-first safety platform. Alongside Batchlight's medicine alerts, the Food Safety module helps everyday people, starting in India, answer five questions:

1. **Has this food been recalled?** Search recalls by product, brand, batch code or barcode.
2. **What have tests actually found in the foods I buy?** Plain-language summaries of official tests, peer-reviewed studies and news reports, each labelled and linked to its source.
3. **Does this pack look right?** Check the FSSAI licence number format, the barcode's check digit, and what a label must show.
4. **Something's wrong. Who do I tell?** Report it here, and get pointed to the official complaint channels.
5. **How do I get it tested properly?** How to choose an accredited lab, what to ask for, and typical costs. The lab directory stays empty until our team has verified a lab's accreditation and whether it accepts samples from the public.

The research behind this scope is in [research/FOOD_SAFETY_EVIDENCE.md](research/FOOD_SAFETY_EVIDENCE.md).

## The consumer problem in India

These figures come from verified official sources and the research evidence file. Every figure shown in the product links to its source.

- **Many samples fail a standard.** In 2023-24, State and UT food safety authorities analysed 170,513 food samples, and 33,808 (19.8%) did not meet a standard or rule (Lok Sabha unstarred question 1092, Annexure-III). The reply does not say how many were unsafe and how many failed only on quality or labelling.
- **Serious hazards are specific.** Examples are lead chromate in some turmeric (Forsyth et al., 2024), aflatoxin M1 in milk (FSSAI milk survey, 2018), pesticide residues above Indian limits in a small share of samples (government figures reported in the news), and banned dyes in street food.
- **Recalls don't reach people.** FSSAI does not publish recalls in a form we can collect automatically. For two FSSAI recall orders (Everest cumin powder, Patanjali red chilli powder) we could find only news reports, not FSSAI's own notice. Consumers hear about problems from news, if at all.
- **Misinformation fills the gap.** Viral "home tests" and apps claim to detect adulteration. A 2026 systematic review of 83 AI food-fraud studies found none validated across laboratories and none in routine use.

## Evidence policy

These rules are enforced in code and tests (`tests/unit/test_food_knowledge.py`, `tests/unit/test_food_safeguards.py`, and the curated-alert validator in `batchlight/services/curation.py`).

**One label per item, and the label describes the page we link to, not what that page talks about.** Alerts use the first three labels; findings in `batchlight/food/knowledge.py` can use all five. The definitions below are copied from the `knowledge.py` docstring; the labels and help texts shown to the public are `EVIDENCE_LABELS` and `EVIDENCE_HELP` in the same file.

| Key | Label shown | Rule |
|---|---|---|
| `official` | Official source | Published by a regulator, government body, Parliament or the WHO (notices, orders, statistics, guidance). |
| `lab_test` | Official lab result | Published by a government body or regulator and stating that a laboratory tested a specific sample or batch and it failed a legal standard. Never used for aggregate statistics. |
| `report` | News or NGO report | A news or NGO report, including news of an official order or test. |
| `study` | Peer-reviewed study | A peer-reviewed paper. Findings only. |
| `provider` | Provider's own information | A service provider's information about itself, such as a laboratory's price list. Findings only. |

- **Every item is sourced and labelled.** Each finding names its source, year and evidence label and links to it. Each alert shows its evidence badge on list rows and detail pages. A news-based alert's list row reads "FSSAI (India) action, as reported by businesstoday.in", never just "FSSAI (India)".
- **Hand-added (curated) alerts must declare their evidence.** The curator form, the CSV import and the India seed all require `evidence` (`official`, `lab_test` or `report`); there is no default. `official` and `lab_test` are rejected unless the link is on a government, Parliament or WHO website (`OFFICIAL_DOMAINS` in `batchlight/text.py`), and `lab_test` is rejected without a reason saying what the laboratory found. `report` is allowed on any link. A CSV without an `evidence` column is rejected.
- **Automated feeds are `official`.** Every automated source (FDA, MHRA, Health Canada, CFIA, WHO) is labelled `official`, never `lab_test`.
- **Samples are not packs.** A finding about samples is never presented as a finding about a brand or a pack. Recalls without published batch codes or barcodes say so instead of telling people to compare codes.
- **No unproven detection.** The product never claims or implies that a home test, photo, app or AI can detect adulteration. Each topic says plainly what cannot be checked at home.
- **Reports are never findings.** Consumer reports are unverified. They go to a curator queue and are never published.
- **Labs are checked first.** A lab is listed only after our team checks its accreditation against an official source, which is linked, and confirms that it accepts samples from the public. No lab is listed yet; see [INDIA_COVERAGE.md](INDIA_COVERAGE.md).

## Features in this MVP

| Feature | Route | Data |
|---|---|---|
| Consumer home for medicines and food | `/` | Both modules |
| Food overview | `/food` | India actions, latest recalls, sources |
| Recall search by name, brand, batch code or barcode, with country filter | `/food/recalls` | US FDA food recalls (openFDA), CFIA food recalls, a few hand-added Indian and other actions, each labelled |
| Recall detail with consumer instructions | `/food/recalls/{id}` | As above |
| What tests have found: 8 topics, each finding sourced and labelled | `/food/reported`, `/food/reported/{slug}` | `batchlight/food/knowledge.py` |
| Check a product: FSSAI number format, GS1 barcode check digit and recall lookup, label checklist | `/food/verify` | `batchlight/food/verify.py` |
| Report a problem, with spam controls and official channels | `/food/report` | `food_reports` table |
| Get food tested: guide, indicative costs, lab directory (empty until a lab is verified) | `/food/testing` | `testing_labs` table |
| Curator queue for reports | `/admin/food/reports` | |
| Curator lab directory | `/admin/food/labs` | |
| Curated food alerts, by form or CSV, with category "food" and a required evidence label | `/admin/alerts/new` | |
| India seed: 8 hand-checked alerts (3 medicine, 5 food), each with its evidence label | `python -m batchlight.cli seed-india` (old name `seed-food-india` still works; run `alembic upgrade head` first) | `batchlight/data/india_alerts.json` |
| API: `category=food` and `country=` on `/api/v1/alerts` and `/api/v1/check` | `/api/docs` | |

## Data sources and coverage

| Source | How | Batch or barcode data |
|---|---|---|
| US FDA food recalls | openFDA food enforcement API, automated | Lot codes from `code_info`; UPC barcodes when labelled and valid |
| Canadian Food Inspection Agency | Government of Canada recalls open data, plus recall pages for barcodes and codes | UPC and printed codes from the affected-products table |
| FSSAI (India) and other regulators | Added by hand by our team, each with a required evidence label (official notice, official lab result or news report) and a source link | Batch numbers when published |

In a live run (9 Nov 2026), the FDA feed loaded 4,156 food recalls since 2024: 2,188 with batch codes and 1,645 with barcodes. CFIA loaded 669, and its barcode coverage grows as each run reads more recall pages.

**India coverage is very limited.** FSSAI has no recall feed we can collect automatically, and its FoSCoS recall list could not be read from the build environment. Our team adds a few Indian actions by hand, and most FSSAI actions are not covered. [INDIA_COVERAGE.md](INDIA_COVERAGE.md) lists what is covered today, what is not, and the operator actions to close the gaps.

## Not built yet

- **More feeds:** UK FSA food alerts (an open API with batch descriptions) and Hong Kong CFS alerts (XML). Both need their hosts allowed in the network policy.
- **Languages:** Hindi and other Indian languages.
- **Scanning:** reading barcodes with the phone camera. GS1 barcodes need no AI; it is a browser feature.
- **Photos:** attaching photos to reports, which needs file storage and moderation.
- **Forwarding:** sending reports to state food safety departments, which needs agreements with them.
- **Lab bookings:** consumer lab bookings and prices, once labs agree to list them.
