# Batchlight: product definition

## Vision

No one takes a medicine that a regulator has already flagged as dangerous.

## Mission

Carry every medicine safety alert, batch by batch, from the regulator that issues it to every shelf and home that holds the affected product, across borders, and record that something was done about it.

## Problem statement

Every week, regulators identify medicine and device batches that are contaminated, sub-potent, mislabelled, non-sterile or falsified. The information then dies on the way to the people holding the product:

- **Alerts are fragmented.** Each regulator publishes on its own website, in its own format. India's CDSCO publishes monthly PDF lists of failed batches; WHO often publishes batch tables as images. Nobody normalises them into one batch-level record.
- **Alerts stop at the distributor.** Recall processes notify "consignees" (wholesalers, hospitals). Independent pharmacies, drug shops, clinics, care homes and patients rely on wholesaler letters, WhatsApp groups and news.
- **Alerts don't cross borders.** A batch flagged in one country is often sold in others. The only propagation today is press coverage, weeks later.
- **Nobody can tell whether an alert worked.** There is no record of who checked their shelves, what they found, and how fast they acted.

The result is that recalled and falsified batches keep being dispensed. The cost is measured in children poisoned by contaminated syrups, treatment failures from sub-potent antibiotics, and patients using devices that were recalled years ago.

## What Batchlight is

A shared, cross-regulator registry of medicine safety alerts at batch level, connected to the stock lists of pharmacies, clinics and households, with a closed loop that records action.

1. **Ingest.** Pull alerts from regulator feeds (FDA drugs and devices, UK MHRA, Health Canada, WHO), extract batch numbers from free text and tables, and normalise everything into one schema. Curators add alerts from regulators that publish only PDFs.
2. **Check.** Anyone, with no account, can check a batch number or product against every source and see the evidence and the source link.
3. **Match.** Organisations and households list what they hold. When a new alert arrives, Batchlight matches it against every list by batch number and product, and flags affected items.
4. **Act and record.** The holder is notified, confirms what they found, and records the action taken (quarantined, returned, not in stock, false match). Batchlight measures time to action.
5. **Learn.** Each manufacturer gets a public track record of alerts across all regulators, which procurement teams can check before buying.

Batchlight is useful without any AI. Its core is data normalisation, deterministic batch parsing, matching rules and a notification workflow.

## Target users and personas

**Amina, independent pharmacist, Kano, Nigeria.** Runs a two-person pharmacy and buys from three wholesalers. Hears about bad batches from a WhatsApp group, days late, and has no way to check a batch number in front of her. Uses an inexpensive Android phone on mobile data. *Needs:* a fast batch check, an alert when something she stocks is flagged, and a record to show inspectors that she acted.

**Wanjiru, quality and procurement officer, faith-based medical supplies distributor, Nairobi.** Buys from manufacturers in India, China and Europe and supplies 300 mission hospitals. A batch failing in India may already be in her warehouse. *Needs:* alerts from every regulator matched against her inventory, and a manufacturer track record for tender decisions.

**Linda, family caregiver, Ohio.** Manages nine medicines for her father, from four prescribers and two pharmacies. Learned about a blood-pressure drug recall on television. *Needs:* to list his medicines once and be told, in plain language, if one is recalled, and what to do.

**Dr. Okafor, chief pharmacist, district hospital.** Holds hundreds of stock lines across a main store and ward cabinets. *Needs:* stock imported from a spreadsheet, a team inbox with severity ordering, and an audit trail.

**Sam, Batchlight data curator.** Works with regulators and NGOs. *Needs:* to see which sources are healthy, review what ingestion produced, and enter alerts from PDF-only regulators with their batch tables.

## Core user journeys

1. **Public batch check.** Enter a batch number, optionally with product name. See matching alerts with severity, reason, regulator, date and a link to the original notice. If nothing matches, see which sources were searched and when, with a plain statement that no alert does not prove the product is genuine.
2. **Organisation onboarding.** Sign up, create a workspace (pharmacy, clinic, hospital, distributor or household) with country, add stock by hand or by CSV import. Batchlight immediately checks the stock against all existing alerts.
3. **New alert to action.** The worker ingests a new alert, parses its batches and matches it. Every affected workspace gets an inbox item and an email. A member opens it, checks the shelf, and records the outcome and unit count. The match closes and time to action is recorded.
4. **Household use.** A caregiver creates a household workspace and adds each medicine, with batch number from the pack if available. Matches come with patient-safe guidance: keep taking the medicine and talk to a pharmacist unless the alert says otherwise.
5. **Curated alerts.** A curator enters an alert from a PDF notice, with each batch and expiry, or imports a CSV. It flows through the same matching and notification pipeline.
6. **System integration.** A pharmacy management system uses an API key to sync stock and pull open matches.
7. **Procurement check.** A procurement officer looks up a manufacturer and sees every alert against it across regulators.

## Feature prioritisation

**Must have, in this MVP**

- Ingestion from FDA drug and device recalls, UK MHRA, Health Canada and WHO, with batch-number extraction and run history.
- Public alert search, batch check, alert detail and manufacturer track records.
- Accounts, workspaces, roles (owner, manager, member), invitations.
- Holdings: manual entry, edit, archive, CSV import with row-level validation.
- Matching engine on new alerts and new holdings, with match confidence levels.
- Inbox, match detail and action recording, with audit log.
- Email notifications (SMTP), with in-app notifications as the reliable baseline.
- JSON API with per-workspace API keys and OpenAPI docs.
- Curator console: source health, run now, manual alert entry, CSV alert import.
- Public impact page with outcome metrics.

**Should have, next**

- SMS and WhatsApp notifications for low-data settings.
- Parsers for CDSCO and NAFDAC PDF notices; OCR with human review for image-only tables.
- GS1 DataMatrix scanning in the browser: the 2D code on packs encodes product, batch and expiry, which removes typing.
- Translations: French, Portuguese, Swahili, Hindi, Spanish.
- Watchlists for manufacturers and products not currently in stock; outbound webhooks.

**Could have, later**

- A free publishing portal for regulators to issue structured, batch-level alerts.
- Recall-effectiveness reports for regulators (what share of affected stock was actioned, and how fast).
- Offline-first mobile app.

## Differentiation

| Existing approach | Limitation | Batchlight |
|---|---|---|
| Enterprise recall tools (Inmar OneRecall, ECRI, TraceLink) | Priced for large US hospitals, stop at the institution, US sources only | Free for pharmacies and households, global sources, reaches end holders |
| Pack authentication (Sproxil, mPedigree, NAFDAC MAS) | Confirms the pack is genuine, not that the batch passed testing | Batch-level alerts from regulators, regardless of brand participation |
| EU FMD verification | Flags only whole-market recalls; one region | Partial recalls and cross-border alerts |
| Regulator websites and apps | Free text, one jurisdiction, no push, no confirmation | One normalised schema, matching, notifications, action record |

## Measurable outcomes

Batchlight tracks outcomes rather than sign-ups or page views:

- **Alert latency:** time from regulator publication to availability in Batchlight.
- **Time to action:** time from a match to a recorded action by the holder.
- **Action rate:** share of matches that end in a recorded action within 72 hours.
- **Units removed:** units quarantined, returned or destroyed because of a match.
- **Precision:** share of matches marked as false matches by users.
- **Coverage:** number of regulators and countries ingested, and batch-level share of alerts.

These are computed from real records and shown on the public impact page.

## Sustainability model

- **Free forever:** public batch check, households, and independent pharmacies and clinics in low- and middle-income countries.
- **Paid tiers:** pharmacy chains, hospital groups and distributors pay for multi-site workspaces, API volume, integrations and support.
- **Procurement intelligence:** procurement agencies, wholesalers and donor-funded supply programmes pay for manufacturer quality history and alert analytics.
- **Grants and partnerships:** global health funders and regulators fund coverage expansion and the regulator publishing portal.
- **Never:** selling health data, charging patients, or ranking alerts by who pays.

## Risks

See [research/SELECTION.md](research/SELECTION.md) for false reassurance, false alarms, PDF-only sources, patient panic and liability, and how each is handled.
