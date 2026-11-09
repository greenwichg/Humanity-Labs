"""Connected sources for Project Passports, and the ones we checked but could not connect yet.

Every value comes from the source record. Nothing is estimated except where a field says so
(``promised_basis == "calculated"``: award date plus the contract duration printed in the award notice).
"""

from __future__ import annotations

import calendar
import html
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import httpx

from ..ingest.base import get_with_retry
from .classify import classify
from .geo import find_regions

WB_API = "https://search.worldbank.org/api/v2"
WB_NOTICE_URL = "https://projects.worldbank.org/en/projects-operations/procurement-detail/{id}"
CAG_BASE = "https://cag.gov.in"


@dataclass
class EventData:
    key: str
    kind: str  # approval, funding, plan, tender, award, completion, change, audit, inspection
    occurred_on: date | None
    title: str
    detail: str | None = None
    evidence: str = "official"
    source_name: str = ""
    source_url: str | None = None


@dataclass
class ProjectData:
    external_id: str
    level: str
    title: str
    source_url: str
    description: str | None = None
    asset_type: str = "other"
    state_slugs: list[str] = field(default_factory=list)
    state_basis: str | None = None
    parent_external_id: str | None = None
    funder: str | None = None
    borrower: str | None = None
    implementing_agency: str | None = None
    tendering_body: str | None = None
    budget_amount: float | None = None
    budget_currency: str | None = None
    funder_amount: float | None = None
    funder_currency: str | None = None
    contract_amount: float | None = None
    contract_currency: str | None = None
    contractors: list[str] = field(default_factory=list)
    procurement_method: str | None = None
    bids_evaluated: int | None = None
    bids_rejected: int | None = None
    approved_on: date | None = None
    tendered_on: date | None = None
    bid_deadline_on: date | None = None
    awarded_on: date | None = None
    duration_text: str | None = None
    promised_completion_on: date | None = None
    promised_basis: str | None = None
    status: str | None = None
    tender_url: str | None = None
    events: list[EventData] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class DocumentData:
    external_id: str
    title: str
    url: str
    kind: str = "audit_report"
    report_type: str | None = None
    sectors: str | None = None
    state_slugs: list[str] = field(default_factory=list)
    published_on: date | None = None


class InfraSource:
    id: str
    name: str
    publisher: str
    homepage_url: str
    provides: str
    interval_hours: int = 24

    def fetch(self, client: httpx.Client, since: date) -> Iterator[ProjectData | DocumentData]:
        raise NotImplementedError


# --------------------------------------------------------------------------- helpers


def _date(value: str | None, *formats: str) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in formats or ("%Y-%m-%dT%H:%M:%SZ",):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _amount(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        return None
    return number if number > 0 else None


def add_duration(start: date, number: int, unit: str) -> date:
    unit = unit.lower()
    if unit.startswith("day"):
        return start + timedelta(days=number)
    if unit.startswith("week"):
        return start + timedelta(weeks=number)
    months = number * (12 if unit.startswith("year") else 1)
    year, month = divmod(start.month - 1 + months, 12)
    year += start.year
    month += 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def notice_text(raw: str | None) -> str:
    text = re.sub(r"<br\s*/?>", "\n", raw or "", flags=re.I)
    text = re.sub(r"</(p|h\d|tr|div|td|li)>", "\n", text, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


_BIDDER_RE = re.compile(r"^(.+?)\s*\((\d{3,})\)\s*$")
_MONEY_RE = re.compile(r"\b([A-Z]{3})\s*([\d,]+(?:\.\d+)?)\b")


def _section(lines: list[str], start: str, stops: tuple[str, ...]) -> list[str]:
    out, inside = [], False
    for line in lines:
        if line.lower().startswith(start.lower()):
            inside = True
            continue
        if inside and any(line.lower().startswith(s.lower()) for s in stops):
            break
        if inside:
            out.append(line)
    return out


def parse_award(text: str) -> dict:
    """Fields from a World Bank contract award notice (plain text from ``notice_text``)."""
    lines = text.splitlines()
    out: dict = {}
    for i, line in enumerate(lines):
        low = line.lower()
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if low.startswith("scope of contract:"):
            out["scope"] = line.split(":", 1)[1].strip()
        elif low.startswith("procurement method:"):
            out["method"] = line.split(":", 1)[1].strip()
        elif low.startswith("notice version no:"):
            out["version"] = line.split(":", 1)[1].strip()
        elif low.startswith("date notification of award issued"):
            for candidate in lines[i + 1 : i + 3]:
                parsed = _date(candidate, "%Y/%m/%d")
                if parsed:
                    out["awarded_on"] = parsed
                    break
        elif low.startswith("duration of contract"):
            match = re.search(r"(\d+)\s*(Day|Week|Month|Year)", nxt, re.I)
            if match:
                out["duration"] = (int(match.group(1)), match.group(2).title())
    stops = ("Rejected Bidder", "Evaluated Bidder")
    awarded_block = _section(lines, "Awarded Bidder", stops)
    out["contractors"] = [m.group(1).strip() for line in awarded_block if (m := _BIDDER_RE.match(line))]
    for i, line in enumerate(awarded_block):
        if line.lower().startswith("signed contract price"):
            money = [m for candidate in awarded_block[i : i + 3] for m in _MONEY_RE.finditer(candidate)]
            if money:
                out["currency"], out["amount"] = money[0].group(1), _amount(money[0].group(2))
            break
    out["rejected"] = sum(1 for line in _section(lines, "Rejected Bidder", ("Evaluated Bidder",)) if _BIDDER_RE.match(line))
    out["evaluated_others"] = sum(
        1 for line in _section(lines, "Evaluated Bidder", ("Rejected Bidder",)) if _BIDDER_RE.match(line)
    )
    return out


# --------------------------------------------------------------------------- World Bank


class WorldBankProjects(InfraSource):
    id = "infra-wb-projects"
    name = "World Bank projects in India"
    publisher = "World Bank"
    homepage_url = "https://projects.worldbank.org/en/projects-operations/projects-list?countrycode_exact=IN"
    provides = "Approval date, total cost, World Bank financing, implementing agencies, status and planned closing date of each financed programme."
    interval_hours = 24
    page_size = 500
    fields = (
        "id,project_name,boardapprovaldate,closingdate,curr_project_cost,curr_total_commitment,curr_ibrd_commitment,"
        "curr_ida_commitment,status,sector,mjsector_namecode,url,borrower,impagency,project_abstract"
    )

    def fetch(self, client: httpx.Client, since: date) -> Iterator[ProjectData]:
        offset = 0
        while True:
            params = {"format": "json", "countrycode_exact": "IN", "rows": self.page_size, "os": offset, "fl": self.fields}
            payload = get_with_retry(client, f"{WB_API}/projects", params=params).json()
            projects = list((payload.get("projects") or {}).values())
            for raw in projects:
                record = map_wb_project(raw)
                if record:
                    yield record
            offset += self.page_size
            if not projects or offset >= int(payload.get("total") or 0):
                return


def map_wb_project(raw: dict) -> ProjectData | None:
    pid, name = (raw.get("id") or "").strip(), " ".join((raw.get("project_name") or "").split())
    if not pid or not name:
        return None
    abstract = raw.get("project_abstract")
    if isinstance(abstract, dict):
        abstract = abstract.get("cdata!") or ""
    sectors = [s.get("Name", "") for s in raw.get("sector") or [] if isinstance(s, dict)]
    sectors += [s.get("name", "") for s in raw.get("mjsector_namecode") or [] if isinstance(s, dict)]
    agencies = " ".join((raw.get("impagency") or "").split()) or None
    regions = find_regions(name, agencies)
    ibrd, ida = _amount(raw.get("curr_ibrd_commitment")), _amount(raw.get("curr_ida_commitment"))
    lenders = " and ".join(x for x, v in (("IBRD", ibrd), ("IDA", ida)) if v)
    funder = f"World Bank ({lenders})" if lenders else "World Bank"
    commitment = _amount(raw.get("curr_total_commitment"))
    approved = _date(raw.get("boardapprovaldate"))
    closing = _date(raw.get("closingdate"), "%m/%d/%Y %I:%M:%S %p")
    url = raw.get("url") or f"https://projects.worldbank.org/en/projects-operations/project-detail/{pid}"
    record = ProjectData(
        external_id=pid,
        level="programme",
        title=name,
        description=" ".join(str(abstract or "").split())[:3000] or None,
        asset_type=classify(name) if classify(name) != "other" else classify(" ".join(sectors)),
        state_slugs=regions,
        state_basis="source" if regions else None,
        funder=funder,
        borrower=(raw.get("borrower") or "").strip() or None,
        implementing_agency=agencies,
        budget_amount=_amount(raw.get("curr_project_cost")),
        budget_currency="USD" if _amount(raw.get("curr_project_cost")) else None,
        funder_amount=commitment * 1_000_000 if commitment else None,
        funder_currency="USD" if commitment else None,
        approved_on=approved,
        promised_completion_on=closing,
        promised_basis="financing_close" if closing else None,
        status=(raw.get("status") or "").strip() or None,
        source_url=url,
        data={"sectors": [s for s in sectors if s]},
    )
    name_src = "World Bank project record"
    if approved:
        record.events.append(
            EventData(
                f"wb-approval-{pid}",
                "approval",
                approved,
                "Financing approved by the World Bank's Board",
                None,
                "official",
                name_src,
                url,
            )
        )
    if commitment:
        record.events.append(
            EventData(
                f"wb-commitment-{pid}",
                "funding",
                approved,
                f"World Bank financing: US${commitment:,.0f} million",
                "Current commitment shown in the World Bank's project record.",
                "official",
                name_src,
                url,
            )
        )
    if closing:
        record.events.append(
            EventData(
                f"wb-closing-{pid}",
                "plan",
                closing,
                "Planned closing date of the World Bank financing",
                "The date the financing is due to end. It is not a completion date for the works.",
                "official",
                name_src,
                url,
            )
        )
    return record


class WorldBankCivilWorks(InfraSource):
    id = "infra-wb-civil-works"
    name = "World Bank civil-works tenders and contract awards in India"
    publisher = "World Bank (notices published for the Indian agencies that run the works)"
    homepage_url = "https://projects.worldbank.org/en/projects-operations/procurement?srce=both&countrycode_exact=IN"
    provides = "Tender notices and contract awards for civil works: contractor, signed price, award date, contract duration and the number of other bids."
    interval_hours = 12
    page_size = 500

    def fetch(self, client: httpx.Client, since: date) -> Iterator[ProjectData]:
        offset = 0
        while True:
            params = {
                "format": "json",
                "project_ctry_name_exact": "India",
                "procurement_group_exact": "CW",
                "rows": self.page_size,
                "os": offset,
                "srt": "submission_date",
                "order": "desc",
            }
            payload = get_with_retry(client, f"{WB_API}/procnotices", params=params).json()
            notices = payload.get("procnotices") or []
            oldest = None
            for raw in notices:
                notice_date = _date(raw.get("noticedate"), "%d-%b-%Y")
                oldest = notice_date if oldest is None or (notice_date and notice_date < oldest) else oldest
                record = map_wb_notice(raw)
                if record:
                    yield record
            offset += self.page_size
            if not notices or offset >= int(payload.get("total") or 0) or (oldest and oldest < since):
                return


def map_wb_notice(raw: dict) -> ProjectData | None:
    kind = (raw.get("notice_type") or "").strip()
    if kind not in ("Invitation for Bids", "Contract Award"):
        return None
    notice_id = (raw.get("id") or "").strip()
    reference = " ".join((raw.get("bid_reference_no") or "").split()) or notice_id
    if not reference:
        return None
    url = WB_NOTICE_URL.format(id=notice_id)
    project_id = (raw.get("project_id") or "").strip() or None
    project_name = " ".join((raw.get("project_name") or "").split())
    description = " ".join((raw.get("bid_description") or "").split())
    organisation = " ".join((raw.get("contact_organization") or "").split()) or None
    source_name = "World Bank procurement notice"
    record = ProjectData(
        external_id=reference,
        level="work",
        title=description[:300] or reference,
        source_url=url,
        parent_external_id=project_id,
        procurement_method=(raw.get("procurement_method_name") or "").strip() or None,
        data={"notices": {kind: notice_id}, "project_name": project_name},
    )
    if kind == "Invitation for Bids":
        record.description = description or None
        record.tendering_body = organisation
        record.tendered_on = _date(raw.get("noticedate"), "%d-%b-%Y")
        deadline_on = _date(raw.get("submission_deadline_date"))
        record.bid_deadline_on = deadline_on if deadline_on and record.tendered_on and deadline_on > record.tendered_on else None
        record.tender_url = url
        record.asset_type = classify(description, project_name)
        regions = find_regions(reference, description, organisation, project_name)
        deadline = f" Bids were due by {record.bid_deadline_on:%d %b %Y}." if record.bid_deadline_on else ""
        record.events.append(
            EventData(
                f"wb-tender-{notice_id}",
                "tender",
                record.tendered_on,
                "Tender invited",
                f"{description}.{deadline}".strip(),
                "official",
                source_name,
                url,
            )
        )
    else:
        award = parse_award(notice_text(raw.get("notice_text")))
        scope = award.get("scope") or description
        record.description = scope or None
        record.title = (scope or description or reference)[:300]
        record.asset_type = classify(scope, description, project_name)
        record.awarded_on = award.get("awarded_on")
        record.contractors = award.get("contractors", [])
        record.contract_amount = award.get("amount")
        record.contract_currency = award.get("currency") if award.get("amount") else None
        record.procurement_method = award.get("method") or record.procurement_method
        record.bids_rejected = award.get("rejected")
        record.bids_evaluated = len(record.contractors) + award.get("evaluated_others", 0) if record.contractors else None
        regions = find_regions(reference, scope, description, project_name)
        if award.get("duration"):
            number, unit = award["duration"]
            record.duration_text = f"{number} {unit.lower()}{'s' if number != 1 else ''}"
            if record.awarded_on:
                record.promised_completion_on = add_duration(record.awarded_on, number, unit)
                record.promised_basis = "calculated"
        who = ", ".join(record.contractors) or "a contractor not named in the notice"
        price = f" for {record.contract_currency} {record.contract_amount:,.2f}" if record.contract_amount else ""
        others = []
        if award.get("evaluated_others"):
            others.append(f"{award['evaluated_others']} other bid(s) evaluated")
        if award.get("rejected"):
            others.append(f"{award['rejected']} bid(s) rejected")
        record.events.append(
            EventData(
                f"wb-award-{notice_id}",
                "award",
                record.awarded_on,
                f"Contract awarded to {who}{price}",
                ("The notice lists " + " and ".join(others) + ".") if others else "The notice lists no other bids.",
                "official",
                source_name,
                url,
            )
        )
        if record.promised_completion_on:
            record.events.append(
                EventData(
                    f"calc-completion-{notice_id}",
                    "plan",
                    record.promised_completion_on,
                    "Expected completion (calculated by us)",
                    f"The award date plus the contract duration in the notice ({record.duration_text}). "
                    "The notice does not give a completion date.",
                    "calculated",
                    source_name,
                    url,
                )
            )
        if award.get("version") not in (None, "", "0"):
            record.data["award_notice_version"] = award["version"]
    record.state_slugs = regions
    record.state_basis = "source" if regions else None
    return record


# --------------------------------------------------------------------------- CAG audit reports

INFRA_AUDIT_WORDS = re.compile(
    r"public works|\bpwd\b|roads?\b|highways?|bridges?|infrastructure|construction|irrigation|water resources|drinking water|"
    r"urban|housing|buildings?|railways?|transport|smart cit|dams?\b|drainage|sewer|economic sector|rural development|works department",
    re.IGNORECASE,
)
_CAG_ITEM_RE = re.compile(
    r'<span class="dtn">(?P<date>[^<]+)</span>.*?<div class="reportType">\s*<span>(?P<type>[^<]*)</span>.*?'
    r'href="(?P<href>/en/audit-report/details/(?P<id>\d+))">(?P<title>[^<]+)</a>.*?Sector:</div>(?P<sectors>.*?)</div>\s*</div>',
    re.S,
)


def parse_cag_listing(page: str) -> list[DocumentData]:
    docs = []
    for m in _CAG_ITEM_RE.finditer(page):
        title = " ".join(html.unescape(m.group("title")).split())
        if not title.lower().startswith(("report", "“report", '"report')):
            continue  # skips test entries on the listing
        sectors = ", ".join(s for s in (" ".join(x.split()) for x in re.sub(r"<[^>]+>", "|", m.group("sectors")).split("|")) if s)
        if not INFRA_AUDIT_WORDS.search(f"{title} {sectors}"):
            continue
        union = "union government" in title.lower()
        docs.append(
            DocumentData(
                external_id=m.group("id"),
                title=title,
                url=CAG_BASE + m.group("href"),
                report_type=m.group("type").strip() or None,
                sectors=sectors or None,
                state_slugs=[] if union else find_regions(title),
                published_on=_date(m.group("date"), "%d %B %Y"),
            )
        )
    return docs


class CagAuditReports(InfraSource):
    id = "infra-cag-reports"
    name = "CAG audit reports on public works"
    publisher = "Comptroller and Auditor General of India"
    homepage_url = "https://cag.gov.in/en/audit-report"
    provides = (
        "Audit reports by India's national auditor that cover public works, by state. Not yet linked to individual projects."
    )
    interval_hours = 24
    pages = 3  # the newest reports are on the first pages; raise for a backfill

    def fetch(self, client: httpx.Client, since: date) -> Iterator[DocumentData]:
        for page in range(1, self.pages + 1):
            response = get_with_retry(client, f"{CAG_BASE}/en/audit-report", params={"page": page} if page > 1 else None)
            yield from parse_cag_listing(response.text)


REGISTRY: dict[str, InfraSource] = {s.id: s for s in (WorldBankProjects(), WorldBankCivilWorks(), CagAuditReports())}


@dataclass(frozen=True)
class PendingSource:
    name: str
    publisher: str
    url: str
    would_provide: str
    status: str


# Checked on 9 November 2026 from our servers. Shown on the coverage page so nobody mistakes a gap for "no problems".
NOT_CONNECTED: tuple[PendingSource, ...] = (
    PendingSource(
        "PMGSY rural roads (OMMAS)",
        "Ministry of Rural Development, NRIDA",
        "https://omms.nic.in/",
        "Every rural road under PMGSY: sanctioned cost, contractor, award and completion dates, quality monitor grades and the 5-year maintenance period.",
        "Could not be reached from our servers. Needs hosting in India.",
    ),
    PendingSource(
        "Central Public Procurement Portal (CPPP)",
        "Ministry of Finance, Department of Expenditure",
        "https://eprocure.gov.in/cppp/",
        "Tenders and the names of successful bidders for central government works.",
        "No machine-readable listing found; the pages need a browser session.",
    ),
    PendingSource(
        "State e-procurement portals",
        "State governments",
        "https://eprocure.gov.in/cppp/",
        "Tenders and awards for state public works departments, water and urban bodies.",
        "Protected by captchas; not connected.",
    ),
    PendingSource(
        "Central project monitoring (₹150 crore and above)",
        "Ministry of Statistics and Programme Implementation",
        "https://www.mospi.gov.in/",
        "Original and revised cost and dates for large central projects.",
        "Website reachable, but the project-level data has not been located yet.",
    ),
    PendingSource(
        "National highways project data",
        "MoRTH and NHAI",
        "https://nhai.gov.in/",
        "National highway projects, contractors and progress.",
        "Website reachable; project data not yet assessed.",
    ),
    PendingSource(
        "Open Government Data Platform",
        "Ministry of Electronics and IT",
        "https://data.gov.in/",
        "Scheme-level datasets published by ministries and states.",
        "Could not be reached from our servers. Needs hosting in India.",
    ),
)

# Records that are rarely published at all. Passports say so instead of leaving a blank.
UNPUBLISHED = {
    "actual_completion": "Completion certificates are not published as open data by the sources we use.",
    "expenditure": "Payments to contractors are not published per work in the sources we use.",
    "inspection": "Inspection and quality-test reports are not published for these works in the sources we use.",
    "maintenance": "Defect liability and maintenance periods are not given in these notices.",
    "condition": "No official source we use reports the current condition of individual works.",
}
