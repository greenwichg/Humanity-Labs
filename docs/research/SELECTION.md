# Opportunity ranking and selection

## How the ranking was produced

1. **Discovery.** Sixteen research agents each investigated one problem domain with web search and returned the three most significant under-solved problems, answering the seven questions in the brief ([problems.md](problems.md)).
2. **Merging.** The 48 problems were clustered into 26 distinct opportunities ([opportunities.md](opportunities.md)). Six weak-evidence problems were set aside during merging and are listed there with reasons.
3. **Competitive analysis.** Completed for 11 opportunities, including the selected one, before the research run was stopped to save compute.
4. **Scoring.** The planned three-judge panel and adversarial review did not run. The scores below are a single evaluator's assessment against the brief's eight criteria, informed by the research and competitive analysis. Treat them as a reasoned shortlist, not a measurement.

Scale: 1 (weak) to 10 (exceptional). *Feasibility* means a working web MVP on real public data. *Scalability* means across countries and communities.

| # | Opportunity | Impact | Reach | Urgency | Uniqueness | Feasibility | Scalability | Sustainability | Measurable | **Total** |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Medicine safety alerts that reach the shelf and the patient | 8 | 8 | 8 | 8 | 9 | 9 | 7 | 9 | **66** |
| 2 | Severe child wasting: therapeutic-food pipeline early warning | 10 | 6 | 10 | 8 | 5 | 7 | 5 | 8 | **59** |
| 3 | Warning-to-action for people who can't act alone (heat, flood, evacuation) | 9 | 7 | 9 | 6 | 7 | 8 | 6 | 7 | **59** |
| 4 | Decodable readers in children's own languages | 8 | 9 | 7 | 7 | 7 | 8 | 5 | 6 | **57** |
| 5 | Benefits lost to paperwork (renewal and reinstatement) | 7 | 9 | 7 | 5 | 6 | 4 | 8 | 9 | **55** |
| 6 | Post-psychiatric-discharge safety bridge | 9 | 5 | 9 | 6 | 6 | 6 | 6 | 7 | **54** |
| 7 | Medicine availability and price at the point of sale | 7 | 9 | 7 | 5 | 5 | 8 | 6 | 7 | **54** |
| 8 | Quality system for task-shared mental-health care | 8 | 8 | 7 | 6 | 6 | 7 | 5 | 7 | **54** |
| 9 | Smallholder store-or-sell decisions | 7 | 8 | 6 | 7 | 6 | 7 | 6 | 7 | **54** |
| 10 | Older-adult medication and discharge continuity | 7 | 8 | 7 | 5 | 6 | 6 | 7 | 7 | **53** |
| 11 | Scam verification and recovery | 6 | 9 | 7 | 4 | 6 | 8 | 6 | 7 | **53** |
| 12 | Rural water points that fail silently | 8 | 7 | 7 | 6 | 5 | 7 | 5 | 8 | **53** |
| 13 | Postpartum follow-up gap | 9 | 6 | 9 | 5 | 4 | 7 | 5 | 8 | **53** |
| 14 | Air-quality data deserts | 6 | 9 | 6 | 6 | 8 | 9 | 4 | 5 | **53** |
| 15 | Field-level pest and disease early warning | 7 | 9 | 7 | 5 | 6 | 8 | 5 | 6 | **53** |
| 16 | Counterfeit and diluted farm inputs | 7 | 8 | 6 | 7 | 5 | 7 | 6 | 7 | **53** |
| 17 | Zero-dose immunization tracing | 9 | 6 | 8 | 5 | 4 | 7 | 5 | 8 | **52** |
| 18 | Eviction default judgments | 8 | 6 | 8 | 6 | 6 | 3 | 6 | 9 | **52** |
| 19 | Migrant credential recognition | 7 | 6 | 5 | 6 | 6 | 7 | 7 | 7 | **51** |
| 20 | Family caregiver support gap | 7 | 8 | 6 | 4 | 7 | 6 | 6 | 6 | **50** |
| 21 | Teacher misallocation | 7 | 8 | 6 | 7 | 5 | 6 | 4 | 7 | **50** |
| 22 | Inaccessible public digital services | 6 | 6 | 5 | 5 | 8 | 8 | 6 | 6 | **50** |
| 23 | Migrant worker recruitment-fee and wage ledger | 8 | 7 | 7 | 7 | 5 | 6 | 4 | 6 | **50** |
| 24 | Unwatched public records (local news, procurement) | 6 | 7 | 5 | 6 | 8 | 7 | 5 | 5 | **49** |
| 25 | Step-free journey data | 6 | 5 | 5 | 6 | 7 | 8 | 5 | 6 | **48** |
| 26 | Disaster recovery aid navigation | 7 | 6 | 7 | 5 | 5 | 4 | 5 | 8 | **47** |

## Selected: medicine safety alerts that reach the shelf and the patient

**The problem.** Regulators find dangerous medicine batches every week: contaminated, sub-potent, mislabelled, sterility failures and outright fakes. WHO estimates about one in ten medical products in low- and middle-income countries is substandard or falsified. Diethylene-glycol cough syrups killed children in The Gambia, Indonesia, Uzbekistan and India between 2022 and 2025. Yet alerts are published one regulator at a time, as web pages and PDFs, to distributors rather than to the shelves and homes that hold the product. In the United States, recall effectiveness checks run down the distribution chain and stop before the patient.

**Why it ranked first.**

- It scores high on every criterion at once. Others beat it on a single dimension, such as child wasting on impact, but are much weaker on feasibility or sustainability.
- The gap is confirmed by the competitive analysis. Enterprise recall tools (Inmar OneRecall, ECRI, TraceLink) serve large US hospitals and stop at the institution. Pack-authentication schemes (Sproxil, mPedigree, NAFDAC MAS) verify the package, not whether the batch was later declared substandard. EU FMD flags only whole-market recalls. Regulator websites have no structured batch data, no push channel and no cross-border propagation.
- Real data is available on day one. FDA, UK MHRA and Health Canada publish machine-readable recalls, and WHO publishes falsified-product alerts. A working product can match a pharmacy's stock against live recalls immediately.
- One batch registry serves every country. Medicines made in India and China are sold in dozens of markets, so an alert anywhere is useful everywhere.
- Outcomes are measurable without vanity metrics: time from regulator publication to the shelf, share of matched stock actioned, units quarantined.

**Key risks and how the product handles them.**

| Risk | Mitigation |
|---|---|
| False reassurance ("no alert found" read as "safe") | Every negative result states which sources were searched and when, and says that absence of an alert is not proof of quality. |
| False alarms from short batch codes colliding across products | A batch match counts only with corroborating product evidence (name, ingredient, manufacturer or NDC). Users can mark false matches, which is tracked as a quality metric. |
| Alerts only available as PDFs or images (CDSCO, NAFDAC, many WHO alerts) | A curator workflow and CSV import bring these in with source links. Automated PDF extraction is on the roadmap. |
| Patients stopping essential medicines in panic, or using a contaminated one | Patient-facing guidance says to follow the advice in the original notice (which, for contaminated or fake products, may be not to use it at all, and to see a doctor if someone has taken it), and not to stop a medicine they need without speaking to a pharmacist or doctor. |
| Liability | The product relays official alerts with links to the source and gives no clinical advice. |

## Runners-up and why not first

- **Severe child wasting supply early warning.** It has the highest impact per person. However, the facility admissions and stock data it needs sit in government systems that require permission, so a day-one product would run on uploaded spreadsheets for a small number of district officers.
- **Warning-to-action for vulnerable people.** It is life-saving and has open forecast data, but it depends on municipalities adopting new operational procedures before it helps anyone, and mass-notification incumbents are entrenched.
- **Decodable readers in children's own languages.** It has very large reach. Its value comes from content production in each language, which is slow and grant-dependent.
- **Benefits lost to paperwork.** It affects many millions of people, but eligibility rules differ by state and country, it doesn't carry across borders, and several funded organisations already work on it.
