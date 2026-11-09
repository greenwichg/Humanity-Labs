"""The citizen-facing Project Passport: every question, its answer from a named source, or why it is missing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from ..models import InfraProject
from .classify import ASSET_TYPES
from .geo import region_name
from .sources import UNPUBLISHED

EVIDENCE_LABELS = {
    "official": "Official record",
    "official_finding": "Official finding",
    "calculated": "Calculated by us",
    "report": "News or NGO report",
    "citizen": "Citizen evidence, not verified",
}
EVIDENCE_HELP = {
    "official": "Taken from a government or funder's published record, linked here.",
    "official_finding": "A finding published by an auditor, inspector, court or regulator. It is their finding, linked here.",
    "calculated": "Worked out by us from official figures. The working is shown.",
    "report": "From a news or NGO report, not from the official record.",
    "citizen": "Sent by a member of the public. We have not verified it.",
}
STATE_BASIS = {
    "source": "named in the source record",
    "programme": "from the programme this work belongs to",
}


@dataclass
class Fact:
    question: str
    answer: str | None
    evidence: str | None = None
    note: str | None = None
    source_url: str | None = None

    @property
    def missing(self) -> bool:
        return self.answer is None


def money(amount, currency: str | None) -> str | None:
    if amount is None or not currency:
        return None
    value = float(amount)
    if currency == "INR":
        if value >= 1e7:
            return f"₹{value / 1e7:,.2f} crore"
        if value >= 1e5:
            return f"₹{value / 1e5:,.2f} lakh"
        return f"₹{value:,.0f}"
    if currency == "USD" and value >= 1e6:
        return f"US${value / 1e6:,.1f} million"
    return f"{currency} {value:,.2f}"


def day(value: date | None) -> str | None:
    return f"{value.day} {value:%b %Y}" if value else None


def facts(project: InfraProject, parent: InfraProject | None, today: date) -> list[Fact]:
    """Answers to the questions citizens ask, in order. Missing answers say why."""
    p, prog = project, parent or (project if project.level == "programme" else None)
    rows: list[Fact] = []
    states = ", ".join(region_name(s) for s in p.state_slugs)
    rows.append(
        Fact(
            "Where is it?",
            states or None,
            "official" if p.state_basis == "source" else ("official" if states else None),
            f"State {STATE_BASIS.get(p.state_basis, 'from the record')}."
            if states
            else "The source record does not name a state.",
            p.source_url,
        )
    )
    rows.append(
        Fact(
            "What kind of work is it?",
            ASSET_TYPES.get(p.asset_type, ASSET_TYPES["other"]),
            "calculated",
            "Our reading of the description in the official record.",
        )
    )
    approval = f"Financing approved by the World Bank's Board on {day(prog.approved_on)}" if prog and prog.approved_on else None
    rows.append(
        Fact(
            "Who approved it?",
            approval,
            "official" if approval else None,
            "The government's own sanction order is not published in the sources we use."
            if approval
            else "No approval or sanction record is published in the sources we use.",
            prog.source_url if prog else None,
        )
    )
    funders = " · ".join(
        x for x in ((prog.funder if prog else None), (f"Borrower: {prog.borrower}" if prog and prog.borrower else None)) if x
    )
    funded_note, funded_url = None, prog.source_url if prog else None
    if not funders and p.level == "work" and p.parent_external_id and p.source_id.startswith("infra-wb"):
        name = (p.data or {}).get("project_name")
        funders = f"World Bank–financed project {p.parent_external_id}" + (f" ({name})" if name else "")
        funded_note, funded_url = "Named in the procurement notice.", p.source_url
    rows.append(Fact("Who funded it?", funders or None, "official" if funders else None, funded_note, funded_url))
    if prog and prog.implementing_agency:
        rows.append(
            Fact(
                "Who carries it out?",
                prog.implementing_agency,
                "official",
                "Implementing agencies named in the programme record.",
                prog.source_url,
            )
        )
    if p.level == "work":
        rows.append(
            Fact(
                "Who invited the tender?",
                p.tendering_body,
                "official" if p.tendering_body else None,
                None if p.tendering_body else "Not given in the notices we have.",
                p.tender_url,
            )
        )
    if prog:
        budget = money(prog.budget_amount, prog.budget_currency)
        rows.append(
            Fact(
                "What was the sanctioned budget?" if p.level == "programme" else "What is the programme's total cost?",
                budget,
                "official" if budget else None,
                "Total cost of the programme in the World Bank's record, for all its works."
                if budget
                else "Not published in the sources we use.",
                prog.source_url,
            )
        )
    if p.level == "work":
        rows.append(
            Fact("What was this work's estimated cost?", None, None, "The tender estimate is not published in these notices.")
        )
        if p.tendered_on:
            deadline = f"; bids due {day(p.bid_deadline_on)}" if p.bid_deadline_on else ""
            rows.append(
                Fact("When was it tendered?", f"{day(p.tendered_on)}{deadline}", "official", p.procurement_method, p.tender_url)
            )
        else:
            rows.append(Fact("When was it tendered?", None, None, "We do not have the tender notice for this work."))
        rows.append(
            Fact(
                "Which contractor got the contract?",
                p.contractors_text,
                "official" if p.contractors_text else None,
                "Winning a contract is not evidence of anything wrong."
                if p.contractors_text
                else "No contract award notice in our sources yet.",
                p.source_url,
            )
        )
        price = money(p.contract_amount, p.contract_currency)
        rows.append(
            Fact(
                "What is the contract price?",
                price,
                "official" if price else None,
                "Signed contract price in the award notice." if price else None,
                p.source_url,
            )
        )
        bids = None
        if p.bids_evaluated is not None or p.bids_rejected:
            parts = [
                f"{p.bids_evaluated} evaluated" if p.bids_evaluated is not None else None,
                f"{p.bids_rejected} rejected" if p.bids_rejected else None,
            ]
            bids = ", ".join(x for x in parts if x)
        rows.append(
            Fact(
                "How many bids were there?",
                bids,
                "official" if bids else None,
                None if bids else "Not given in the notices we have.",
                p.source_url,
            )
        )
        rows.append(Fact("When was it awarded?", day(p.awarded_on), "official" if p.awarded_on else None, None, p.source_url))
    promised = day(p.promised_completion_on)
    if p.promised_basis == "calculated":
        note = f"Award date plus the contract duration in the notice ({p.duration_text}). The notice gives no completion date."
        evidence = "calculated"
    elif p.promised_basis == "financing_close":
        note = "This is when the World Bank financing is due to end, not a completion date for the works."
        evidence = "official"
    else:
        note, evidence = "No promised completion date is published in the sources we use.", None
    rows.append(
        Fact(
            "When was it promised to finish?" if p.level == "work" else "When is the financing due to close?",
            promised,
            evidence if promised else None,
            note,
            p.source_url,
        )
    )
    finished = day(p.actual_completion_on)
    rows.append(
        Fact(
            "When did it actually finish?",
            finished,
            "official" if finished else None,
            None if finished else UNPUBLISHED["actual_completion"],
        )
    )
    if p.promised_completion_on and not p.actual_completion_on and p.promised_completion_on < today and p.level == "work":
        rows[
            -1
        ].note = (
            f"{UNPUBLISHED['actual_completion']} The expected date ({promised}) has passed, but that alone does not show a delay."
        )
    rows.append(Fact("How much has been spent?", None, None, UNPUBLISHED["expenditure"]))
    rows.append(Fact("Inspections and quality tests", None, None, UNPUBLISHED["inspection"]))
    rows.append(
        Fact("Audit findings", None, None, "No audit finding is linked to this project yet. See the audit reports for its state.")
    )
    if p.level == "work":
        rows.append(Fact("Defect liability and maintenance", None, None, UNPUBLISHED["maintenance"]))
    rows.append(Fact("Current condition", None, None, UNPUBLISHED["condition"]))
    if p.status:
        rows.insert(2, Fact("Status", p.status, "official", None, p.source_url))
    return rows
