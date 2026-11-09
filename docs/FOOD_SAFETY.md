# Food Safety module

Humanity Labs is a consumer-first safety platform. Alongside Batchlight's medicine alerts, the Food Safety module helps everyday people, starting in India, answer five questions:

1. **Has this food been recalled?** Search recalls by product, brand, batch code or barcode.
2. **What have tests actually found in the foods I buy?** Plain-language summaries of official surveys and peer-reviewed studies, each with its source.
3. **Does this pack look right?** Check the FSSAI licence number format, the barcode's check digit, and what a label must show.
4. **Something's wrong. Who do I tell?** Report it here, and get pointed to the official complaint channels.
5. **How do I get it tested properly?** How to use an accredited lab, what to ask for, typical costs, and labs whose accreditation a curator has checked.

The research behind this scope is in [research/FOOD_SAFETY_EVIDENCE.md](research/FOOD_SAFETY_EVIDENCE.md).

## The consumer problem in India

These figures come from the research evidence file. Every figure shown in the product links to its source.

- **Many failures, mostly not toxic.** About one in five food samples tested by food safety officers fails a standard, but most failures are quality or labelling problems. About 4% of samples were judged unsafe in 2023-24 (Rajya Sabha reply, Dec 2024).
- **Serious hazards are specific.** Examples are lead chromate in some turmeric (Forsyth et al., 2024), aflatoxin M1 from cattle feed in milk (FSSAI milk survey, 2018), pesticide residues above limits in about 3% of samples, and banned dyes in street food.
- **Recalls don't reach people.** FSSAI has ordered batch recalls without public alerts, publishes no brand-level test results, and has no open recall data feed. Consumers hear about problems from news, if at all.
- **Misinformation fills the gap.** Viral "home tests" and apps claim to detect adulteration. A 2026 systematic review of 83 AI food-fraud studies found none validated across laboratories and none in routine use.

## Evidence policy

These rules are enforced in code and tests (`tests/unit/test_food_knowledge.py`):

- **Every finding is sourced and graded.** It names its source, year and evidence type (official data, peer-reviewed study, news/NGO report, or information published by the provider, such as a laboratory's price list) and links to it. Curated recalls are marked "Added by curators", and say whether they link to the regulator's notice or to a published report of it. Recalls without published batch codes or barcodes say so instead of telling people to compare codes.
- **Samples are not packs.** A finding about samples is never presented as a finding about a brand or a pack.
- **No unproven detection.** The product never claims or implies that a home test, photo, app or AI can detect adulteration. Each topic says plainly what cannot be checked at home.
- **Reports are never findings.** Consumer reports are unverified. They go to a curator queue and are never published.
- **Labs are checked first.** A lab is listed only after a curator checks its accreditation against an official source, which is linked.

## Features in this MVP

| Feature | Route | Data |
|---|---|---|
| Consumer home for medicines and food | `/` | Both modules |
| Food overview | `/food` | India actions, latest recalls, sources |
| Recall search by name, brand, batch code or barcode, with country filter | `/food/recalls` | US FDA food recalls (openFDA), CFIA food recalls, curated FSSAI and other notices |
| Recall detail with consumer instructions | `/food/recalls/{id}` | As above |
| What tests have found: 8 topics, 18 sourced findings | `/food/reported`, `/food/reported/{slug}` | `batchlight/food/knowledge.py` |
| Check a product: FSSAI number format, GS1 barcode check digit and recall lookup, label checklist | `/food/verify` | `batchlight/food/verify.py` |
| Report a problem, with spam controls and official channels | `/food/report` | `food_reports` table |
| Get food tested: guide, indicative costs, curated lab directory | `/food/testing` | `testing_labs` table |
| Curator queue for reports | `/admin/food/reports` | |
| Curator lab directory | `/admin/food/labs` | |
| Curated food alerts, by form or CSV, with category "food" | `/admin/alerts/new` | |
| India seed: three sourced regulatory actions | `python -m batchlight.cli seed-food-india` | `batchlight/food/data/india_regulatory_actions.json` |
| API: `category=food` and `country=` on `/api/v1/alerts` and `/api/v1/check` | `/api/docs` | |

## Data sources and coverage

| Source | How | Batch or barcode data |
|---|---|---|
| US FDA food recalls | openFDA food enforcement API, automated | Lot codes from `code_info`; UPC barcodes when labelled and valid |
| Canadian Food Inspection Agency | Government of Canada recalls open data, plus recall pages for barcodes and codes | UPC and printed codes from the affected-products table |
| FSSAI (India) and other regulators | Curated from official statements and reliable reports, with source links | Batch numbers when published |

In a live run (9 Nov 2026), the FDA feed loaded 4,156 food recalls since 2024: 2,188 with batch codes and 1,645 with barcodes. CFIA loaded 669, and its barcode coverage grows as each run reads more recall pages.

**India coverage is incomplete.** FSSAI has no machine-readable recall feed, and its 2026 FoSCoS recall module could not be read from the build environment. Curators add FSSAI notices by hand.

## Not built yet

- **More feeds:** UK FSA food alerts (an open API with batch descriptions) and Hong Kong CFS alerts (XML). Both need their hosts allowed in the network policy.
- **Languages:** Hindi and other Indian languages.
- **Scanning:** reading barcodes with the phone camera. GS1 barcodes need no AI; it is a browser feature.
- **Photos:** attaching photos to reports, which needs file storage and moderation.
- **Forwarding:** sending reports to state food safety departments, which needs agreements with them.
- **Lab bookings:** consumer lab bookings and prices, once labs agree to list them.
