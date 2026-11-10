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
    "official_copy": "Official data, republished",
    "official_finding": "Official finding",
    "calculated": "Calculated by us",
    "report": "News or NGO report",
    "citizen": "Citizen evidence, not verified",
}
EVIDENCE_HELP = {
    "official": "Taken from a government or funder's published record, linked here.",
    "official_copy": "Government data republished by another organisation, linked here. Check the official portal where you can.",
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
    data = p.data or {}
    ev = data.get("evidence", "official")  # how this record's own facts are supported
    none = "Not in the records we have."
    rows: list[Fact] = []
    states = ", ".join(region_name(s) for s in p.state_slugs)
    where = states
    if states and data.get("district"):
        where = f"{data.get('block') + ' block, ' if data.get('block') else ''}{data['district']} district, {states}"
    rows.append(
        Fact(
            "Where is it?",
            where or None,
            (ev if p.state_basis == "source" else "official") if where else None,
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
    for item in data.get("extra_facts") or []:
        question, answer, *rest = item
        rows.append(
            Fact(question, answer, (rest[0] if rest and rest[0] else ev), rest[1] if len(rest) > 1 else None, p.source_url)
        )
    if p.level == "project":
        return rows + project_rows(p)
    if p.status:
        rows.append(Fact("What stage is it at?", p.status, ev, "As recorded in the source.", p.source_url))

    # Approval and funding
    if prog and prog.approved_on:
        rows.append(
            Fact(
                "Who approved it?",
                f"Financing approved by the World Bank's Board on {day(prog.approved_on)}",
                "official",
                "The government's own sanction order is not published in the sources we use.",
                prog.source_url,
            )
        )
    elif data.get("approval"):
        rows.append(
            Fact("Who approved it?", data["approval"], ev, "The sanction order itself is not in this data.", p.source_url)
        )
    else:
        rows.append(Fact("Who approved it?", None, None, "No approval or sanction record is published in the sources we use."))
    funders = " · ".join(
        x for x in ((prog.funder if prog else None), (f"Borrower: {prog.borrower}" if prog and prog.borrower else None)) if x
    )
    funded_ev, funded_note, funded_url = "official", None, prog.source_url if prog else None
    if not funders and p.funder:
        funders, funded_ev, funded_url = p.funder, ev, p.source_url
    elif not funders and p.level == "work" and p.parent_external_id and p.source_id.startswith("infra-wb"):
        name = data.get("project_name")
        funders = f"World Bank–financed project {p.parent_external_id}" + (f" ({name})" if name else "")
        funded_note, funded_url = "Named in the procurement notice.", p.source_url
    rows.append(Fact("Who funded it?", funders or None, funded_ev if funders else None, funded_note, funded_url))
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

    # Money
    cost_note = data.get("cost_note")
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
            Fact(
                "What was this work's estimated or sanctioned cost?",
                None,
                None,
                cost_note or "The estimate is not published in these notices.",
            )
        )

    # Tender and contract
    if p.level == "work":
        if p.tendering_body:
            rows.append(Fact("Who invited the tender?", p.tendering_body, ev, None, p.tender_url))
        if p.tendered_on:
            deadline = f"; bids due {day(p.bid_deadline_on)}" if p.bid_deadline_on else ""
            rows.append(Fact("When was it tendered?", f"{day(p.tendered_on)}{deadline}", ev, p.procurement_method, p.tender_url))
        else:
            rows.append(Fact("When was it tendered?", None, None, "The tender notice is not in the records we have."))
        rows.append(
            Fact(
                "Which contractor got the contract?",
                p.contractors_text,
                ev if p.contractors_text else None,
                "Winning a contract is not evidence of anything wrong." if p.contractors_text else none,
                p.source_url,
            )
        )
        price = money(p.contract_amount, p.contract_currency)
        rows.append(
            Fact(
                "What is the contract price?",
                price,
                ev if price else None,
                "Signed contract price in the award notice." if price else (cost_note or none),
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
        rows.append(Fact("How many bids were there?", bids, ev if bids else None, None if bids else none, p.source_url))
        rows.append(
            Fact(
                "When was it awarded?",
                day(p.awarded_on),
                ev if p.awarded_on else None,
                None if p.awarded_on else none,
                p.source_url,
            )
        )

    # Dates
    promised = day(p.promised_completion_on)
    if p.promised_basis == "calculated":
        note, evidence = (
            f"Award date plus the contract duration in the notice ({p.duration_text}). The notice gives no completion date.",
            "calculated",
        )
    elif p.promised_basis == "financing_close":
        note, evidence = "This is when the World Bank financing is due to end, not a completion date for the works.", "official"
    else:
        note, evidence = "No promised completion date is in the records we have.", None
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
            ev if finished else None,
            None if finished else UNPUBLISHED["actual_completion"],
            p.source_url if finished else None,
        )
    )
    if p.promised_completion_on and not p.actual_completion_on and p.promised_completion_on < today and p.level == "work":
        rows[
            -1
        ].note = (
            f"{UNPUBLISHED['actual_completion']} The expected date ({promised}) has passed, but that alone does not show a delay."
        )

    # Rarely published
    rows.append(Fact("How much has been spent?", None, None, cost_note or UNPUBLISHED["expenditure"]))
    if data.get("scheme") == "PMGSY":
        rows.append(
            Fact(
                "Inspections and quality tests",
                None,
                None,
                "Quality monitors' grades are on PMGSY's own portal (OMMAS), which we cannot reach yet.",
            )
        )
    else:
        rows.append(Fact("Inspections and quality tests", None, None, UNPUBLISHED["inspection"]))
    rows.append(
        Fact("Audit findings", None, None, "No audit finding is linked to this project yet. See the audit reports for its state.")
    )
    if p.level == "work":
        if p.status == "Maintenance":
            rows.append(
                Fact(
                    "Defect liability and maintenance",
                    "The record lists this road's stage as “Maintenance”.",
                    ev,
                    "The maintenance contract's terms and end date are not in this data.",
                    p.source_url,
                )
            )
        else:
            rows.append(Fact("Defect liability and maintenance", None, None, UNPUBLISHED["maintenance"]))
    rows.append(Fact("Current condition", None, None, UNPUBLISHED["condition"]))
    return rows


def project_rows(p: InfraProject) -> list[Fact]:
    """The rest of a central project's passport. Its figures come first, from ``extra_facts``."""
    data = p.data or {}
    as_of = f"as of {data['report_month']}" if data.get("report_month") else "in the latest report"
    return [
        Fact("What stage is it at?", f"Not yet completed: listed as ongoing {as_of}.", "official", None, p.source_url),
        Fact("Who funded it?", None, None, "The report does not say how the project is funded."),
        Fact("Which contractor is building it?", None, None, "The report does not name contractors."),
        Fact("Inspections and quality tests", None, None, UNPUBLISHED["inspection"]),
        Fact(
            "Audit findings", None, None, "No audit finding is linked to this project yet. See the audit reports for its state."
        ),
        Fact("Current condition", None, None, UNPUBLISHED["condition"]),
    ]
