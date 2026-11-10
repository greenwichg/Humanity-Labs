"""MoSPI's monthly Flash Report on central sector infrastructure projects costing ₹150 crore and above.

The report is generated from MoSPI's PAIMANA portal (which refuses connections from outside India) and published
as a PDF on www.mospi.gov.in. Its appendix table "All Ongoing Projects" lists, for every project: name,
implementing agency, project code, state, date of approval and start, original and revised dates of
commissioning, original and revised cost and cumulative expenditure (in ₹ crore), and physical progress (%).

The table is read by position. Each project row has an agency line in bold italic and a project-code line in
bold, which is how rows are found; ministry and sector headings are told apart from wrapped project names by
their larger line spacing. Values are only taken from the column they sit in.
"""

from __future__ import annotations

import io
import re
from collections import defaultdict
from dataclasses import dataclass, field

MONTHS = "JANUARY FEBRUARY MARCH APRIL MAY JUNE JULY AUGUST SEPTEMBER OCTOBER NOVEMBER DECEMBER".split()
_MONTH_RE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(\d{4})\b")
_MMYYYY = re.compile(r"^\(?(\d{1,2})/(\d{4})\)?$")
_NUMBER = re.compile(r"^\(?(\d[\d,]*(?:\.\d+)?)\)?$")
_CODE = re.compile(r"^\((\d{3,})\)$")
HEADING_GAP = 14.5  # points; wrapped lines of one name are about 11-12 apart, headings 18


@dataclass
class FlashProject:
    code: str
    name: str
    agency: str | None = None
    ministry: str | None = None
    sector: str | None = None
    state_text: str | None = None
    serial: int | None = None
    approval: str | None = None  # "MM/YYYY"
    start: str | None = None
    doc_original: str | None = None
    doc_revised: str | None = None
    cost_original: float | None = None
    cost_revised: float | None = None
    expenditure: float | None = None
    progress: float | None = None
    extra: dict = field(default_factory=dict)


def _num(text: str) -> float | None:
    m = _NUMBER.match(text)
    return float(m.group(1).replace(",", "")) if m else None


def _columns(words: list[dict]) -> dict[str, float] | None:
    """Column boundaries from the header words on a table page."""

    def x(text: str, after: float = 0) -> float | None:
        hits = [w["x0"] for w in words if w["text"] == text and w["x0"] > after]
        return min(hits) if hits else None

    state, approval, doc = x("State"), x("Approval"), x("Orignal/Target") or x("Original/Target")
    cost, cumulative, physical = x("Cost", (doc or 0) + 20), x("Cumulative"), x("Physical")
    if None in (state, approval, doc, cost, cumulative, physical):
        return None
    date_word = x("Date", state)
    return {
        "text_max": state - 40,
        "state_max": (date_word or approval) - 5,
        "approval_max": doc - 5,
        "doc_max": cost - 45,
        "cost_max": cumulative - 5,
        "exp_max": physical - 5,
        "header_bottom": max(w["bottom"] for w in words if w["text"] in ("MM/YYYY", "Crore", "(%)")) + 2,
    }


def _lines(words: list[dict]) -> list[list[dict]]:
    rows: dict[int, list[dict]] = defaultdict(list)
    for w in words:
        rows[round(w["top"])].append(w)
    merged: list[list[dict]] = []
    for top in sorted(rows):
        if merged and abs(round(merged[-1][0]["top"]) - top) <= 1:
            merged[-1].extend(rows[top])
        else:
            merged.append(list(rows[top]))
    return [sorted(line, key=lambda w: w["x0"]) for line in merged]


def _runs(line: list[dict], gap: float = 12.0) -> list[list[dict]]:
    """Split a line into runs of words separated by more than ``gap`` points (one run per table cell)."""
    runs: list[list[dict]] = []
    for w in line:
        if runs and w["x0"] - runs[-1][-1]["x1"] <= gap:
            runs[-1].append(w)
        else:
            runs.append([w])
    return runs


def _left_run(line: list[dict]) -> list[dict]:
    """The project-name column: the run that starts at the left margin of the name column (serial numbers excluded)."""
    for run in _runs([w for w in line if w["x0"] >= 80]):
        if 80 <= run[0]["x0"] < 130:
            return run
    return []


def report_month(first_pages_text: str) -> str | None:
    m = _MONTH_RE.search(first_pages_text.upper())
    return f"{m.group(1).title()} {m.group(2)}" if m else None


@dataclass
class Page:
    text: str
    words: list[dict]  # pdfplumber words: text, x0, x1, top, bottom, fontname
    height: float


def read_pages(pdf_bytes: bytes) -> list[Page]:
    import pdfplumber

    keys = ("text", "x0", "x1", "top", "bottom", "fontname")
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        return [
            Page(
                page.extract_text() or "",
                [{k: w[k] for k in keys} for w in page.extract_words(extra_attrs=["fontname"], keep_blank_chars=False)],
                float(page.height),
            )
            for page in pdf.pages
        ]


def parse_flash_report(pdf_bytes: bytes) -> tuple[str | None, int | None, list[FlashProject]]:
    """Returns (report month, number of ongoing projects the report states, projects parsed)."""
    return parse_pages(read_pages(pdf_bytes))


def parse_pages(pages: list[Page]) -> tuple[str | None, int | None, list[FlashProject]]:
    projects: list[FlashProject] = []
    head = "\n".join(p.text for p in pages[:3])
    month = report_month(head)
    stated = re.search(r"\b(\d{3,5})\s*\|\s*\d+\s*\n?\s*Ongoing Projects", head)
    ministry = sector = None
    pending: list[tuple[float, str]] = []  # (absolute top, text) of left-column lines since the last row
    current: FlashProject | None = None
    span: tuple[int, float] | None = None
    in_table = False
    for index, page in enumerate(pages):
        words = page.words
        if not in_table:
            text = page.text
            if "All Ongoing Projects" in text and "Sl.No" in text:
                in_table = True
            else:
                continue
        cols = _columns(words)
        if cols is None:
            continue
        body = [w for w in words if w["top"] > cols["header_bottom"]]
        # The page footer ("Project Assessment, Infrastructure Monitoring ...") sits in the bottom fifth of the page.
        footer = min((w["top"] for w in body if w["text"] == "Assessment," and w["top"] > page.height * 0.8), default=page.height)
        body = [w for w in body if w["top"] < footer - 2]
        offset = index * 10_000
        for line in _lines(body):
            left = _left_run(line)
            if not left:
                continue
            text = " ".join(w["text"] for w in left)
            font = left[0]["fontname"]
            top = line[0]["top"] + offset
            if "BoldOblique" in font and text.startswith("("):
                segments: list[list[tuple[float, str]]] = []
                for item in pending:
                    if segments and item[0] - segments[-1][-1][0] <= HEADING_GAP:
                        segments[-1].append(item)
                    else:
                        segments.append([item])
                for seg in segments[:-1]:
                    heading = " ".join(t for _, t in seg)
                    if re.match(r"^(Ministry|Department) of\b", heading):
                        ministry, sector = heading, None
                    else:
                        sector = heading
                name_lines = segments[-1] if segments else []
                name = " ".join(t for _, t in name_lines)
                agency = text.strip("() ").strip() or None
                if agency and agency.upper().startswith("INVALID"):
                    agency = None  # a placeholder in the source, not an agency's name
                current = FlashProject(code="", name=name, agency=agency, ministry=ministry, sector=sector)
                span = (index, (name_lines[0][0] - offset) if name_lines else line[0]["top"])
                pending = []
                continue
            if current is not None and not current.code and (m := _CODE.match(left[0]["text"])):
                current.code = m.group(1)
                continue
            if current is not None and current.code and text.startswith("(") and "Bold" in font:
                _fill(current, page_words=words, page_index=index, start=span, end_top=line[0]["top"], cols=cols)
                if current.name and current.code:
                    projects.append(current)
                current, span = None, None
                continue
            if "Bold" not in font:
                pending.append((top, text))
    return month, int(stated.group(1)) if stated else None, projects


def _fill(p: FlashProject, page_words: list[dict], page_index: int, start, end_top: float, cols: dict) -> None:
    """Values for one row: words between the row's first name line and its last code line, by column."""
    top = start[1] if start and start[0] == page_index else cols["header_bottom"]
    in_row = [w for w in page_words if top - 4 <= w["top"] <= end_top + 4]
    words = []
    state = []
    for line in _lines(in_row):
        for run in _runs([w for w in line if w["x0"] >= 80]):
            if 80 <= run[0]["x0"] < 130:
                continue  # name, agency and code column
            if cols["text_max"] - 40 <= run[0]["x0"] < cols["state_max"] and not _MMYYYY.match(run[0]["text"]):
                state.extend(w["text"] for w in run)
            else:
                words.extend(run)
    p.state_text = " ".join(state).strip("() ") or None
    words += [w for w in in_row if w["x0"] < 80]
    serial = [w["text"] for w in words if w["x0"] < 80 and w["text"].isdigit()]
    p.serial = int(serial[0]) if serial else None
    for w in words:
        x, t = w["x0"], w["text"]
        paren = t.startswith("(")
        if cols["state_max"] <= x < cols["approval_max"] and _MMYYYY.match(t):
            if paren:
                p.start = p.start or t.strip("()")
            else:
                p.approval = p.approval or t
        elif cols["approval_max"] <= x < cols["doc_max"] and _MMYYYY.match(t):
            if paren:
                p.doc_revised = p.doc_revised or t.strip("()")
            else:
                p.doc_original = p.doc_original or t
        elif cols["doc_max"] <= x < cols["cost_max"] and (n := _num(t)) is not None:
            if paren:
                p.cost_revised = p.cost_revised if p.cost_revised is not None else n
            else:
                p.cost_original = p.cost_original if p.cost_original is not None else n
        elif cols["cost_max"] <= x < cols["exp_max"] and (n := _num(t)) is not None and not paren:
            p.expenditure = p.expenditure if p.expenditure is not None else n
        elif x >= cols["exp_max"] and (n := _num(t)) is not None and not paren:
            p.progress = p.progress if p.progress is not None else n


# --------------------------------------------------------------------------- source adapter

MOSPI_API = "https://www.mospi.gov.in/api/publications-reports/get-web-publications-report-list"
MOSPI_FILES = "https://www.mospi.gov.in/"
FLASH_TITLE = re.compile(r"flash\s*report.*(infrastructure|projects)", re.IGNORECASE)


def month_text(mmyyyy: str | None) -> str | None:
    m = _MMYYYY.match(mmyyyy or "")
    if not m or not 1 <= int(m.group(1)) <= 12:
        return None
    return f"{MONTHS[int(m.group(1)) - 1].title()[:3]} {m.group(2)}"


def month_date(mmyyyy: str | None):
    from datetime import date

    m = _MMYYYY.match(mmyyyy or "")
    if not m or not 1 <= int(m.group(1)) <= 12:
        return None
    return date(int(m.group(2)), int(m.group(1)), 1)


def months_between(a: str | None, b: str | None) -> int | None:
    da, db = month_date(a), month_date(b)
    if not da or not db:
        return None
    return (db.year - da.year) * 12 + db.month - da.month


def latest_flash_report(client) -> dict | None:
    """Newest flash report in MoSPI's publication list: {"title", "published", "url"}."""
    response = client.post(
        MOSPI_API, json={"page": 1, "page_size": 50, "search": "Flash Report", "lang": "en", "data_source": "web"}
    )
    response.raise_for_status()
    best = None
    for item in response.json().get("data") or []:
        title = re.sub(r"<[^>]+>", " ", item.get("title") or "")
        title = " ".join(title.replace("&nbsp;", " ").split())
        path = (item.get("file_one") or {}).get("path")
        if not path or not FLASH_TITLE.search(title):
            continue
        published = item.get("published_year") or ""
        if best is None or published > best["published"]:
            best = {"title": title, "published": published, "url": MOSPI_FILES + path.lstrip("/")}
    return best


# --------------------------------------------------------------------------- passports

MOSPI_SOURCE_NAME = "MoSPI Flash Report on central sector infrastructure projects"


def map_mospi_project(project: FlashProject, report: dict, month: str | None):
    """One Project Passport per central project. Every figure is the report's; changes are worked out and labelled."""
    from .classify import classify
    from .geo import find_regions
    from .sources import EventData, ProjectData

    if not project.code or not project.name:
        return None
    url = report["url"]
    as_of = f"as of {month}" if month else "in the latest report"
    regions = find_regions(project.state_text)
    original, revised = project.cost_original, project.cost_revised
    current_cost = revised if revised is not None else original
    doc_now = project.doc_revised or project.doc_original
    facts: list[list] = []
    if project.ministry:
        facts.append(["Which ministry reports it?", project.ministry, "official", None])
    if project.sector:
        facts.append(["Sector", project.sector, "official", None])
    if project.agency:
        facts.append(["Who carries it out?", project.agency, "official", "Implementing agency named in the report."])
    if project.approval:
        facts.append(
            ["When was it approved?", month_text(project.approval), "official", "Month of approval given in the report."]
        )
    if original is not None:
        facts.append(["What was the approved (original) cost?", f"₹{original:,.2f} crore", "official", None])
    if revised is not None:
        facts.append(["What is the revised cost?", f"₹{revised:,.2f} crore", "official", f"Revised cost {as_of}."])
    if original and revised is not None and abs(revised - original) >= 0.01:
        change = revised - original
        facts.append(
            [
                "How has the cost changed?",
                f"It has {'risen' if change > 0 else 'fallen'} by ₹{abs(change):,.2f} crore ({abs(change) / original:.0%}) since approval.",
                "calculated",
                "Worked out by us from the original and revised cost in the report. "
                "A cost change is not, by itself, evidence of wrongdoing.",
            ]
        )
    if project.expenditure is not None:
        basis = "revised" if revised is not None else "original"
        share = f" ({project.expenditure / current_cost:.0%} of the {basis} cost, worked out by us)" if current_cost else ""
        facts.append(
            [
                "How much has been spent?",
                f"₹{project.expenditure:,.2f} crore{share}",
                "official",
                f"Cumulative expenditure {as_of}.",
            ]
        )
    if project.progress is not None:
        facts.append(
            ["How far has the work progressed?", f"{project.progress:g}% physical progress", "official", f"As reported {as_of}."]
        )
    if project.doc_original:
        facts.append(
            [
                "When was it first due to be completed?",
                month_text(project.doc_original),
                "official",
                "Original date of commissioning.",
            ]
        )
    if project.doc_revised:
        facts.append(
            ["When is it now due?", month_text(project.doc_revised), "official", f"Revised date of commissioning {as_of}."]
        )
        moved = months_between(project.doc_original, project.doc_revised)
        if moved:
            facts.append(
                [
                    "How has the date changed?",
                    f"Moved {'later' if moved > 0 else 'earlier'} by {abs(moved)} month{'s' if abs(moved) != 1 else ''}.",
                    "calculated",
                    "Worked out by us from the original and revised dates in the report. "
                    "A later date is not, by itself, evidence of wrongdoing.",
                ]
            )
    record = ProjectData(
        external_id=f"MOSPI-{project.code}",
        level="project",
        title=project.name[:500],
        description=" · ".join(x for x in (project.sector, project.ministry, project.agency) if x) or None,
        asset_type=classify(project.name, project.sector),
        state_slugs=regions,
        state_basis="source" if regions else None,
        implementing_agency=project.agency,
        budget_amount=current_cost * 1e7 if current_cost is not None else None,
        budget_currency="INR" if current_cost is not None else None,
        approved_on=month_date(project.approval),
        promised_completion_on=month_date(doc_now),
        promised_basis="source" if doc_now else None,
        status=f"Ongoing {as_of}",
        source_url=url,
        data={
            "scheme": "MOSPI",
            "month_precision": True,
            "report_month": month,
            "report_published": report.get("published"),
            "project_code": project.code,
            "ministry": project.ministry,
            "sector": project.sector,
            "state_text": project.state_text,
            "extra_facts": facts,
            "figures": {
                "approval": project.approval,
                "start": project.start,
                "doc_original": project.doc_original,
                "doc_revised": project.doc_revised,
                "cost_original_crore": original,
                "cost_revised_crore": revised,
                "expenditure_crore": project.expenditure,
                "progress_pct": project.progress,
            },
            "official_portal": "https://paimana-proj.mospi.gov.in/",
        },
    )
    if project.approval:
        record.events.append(
            EventData(
                f"mospi-approval-{project.code}",
                "approval",
                month_date(project.approval),
                f"Approved ({month_text(project.approval)})",
                None,
                "official",
                MOSPI_SOURCE_NAME,
                url,
            )
        )
    if project.doc_original:
        record.events.append(
            EventData(
                f"mospi-doc-original-{project.code}",
                "plan",
                month_date(project.doc_original),
                f"Originally due to be completed ({month_text(project.doc_original)})",
                None,
                "official",
                MOSPI_SOURCE_NAME,
                url,
            )
        )
    if project.doc_revised and project.doc_revised != project.doc_original:
        record.events.append(
            EventData(
                f"mospi-doc-revised-{project.code}",
                "plan",
                month_date(project.doc_revised),
                f"Now due to be completed ({month_text(project.doc_revised)})",
                f"Revised date {as_of}.",
                "official",
                MOSPI_SOURCE_NAME,
                url,
            )
        )
    return record


def mospi_source():
    """Built lazily so this module and ``sources`` can import each other."""
    from .sources import InfraSource

    class MospiCentralProjects(InfraSource):
        id = "infra-mospi-central"
        name = "Central infrastructure projects of ₹150 crore and above (MoSPI Flash Report)"
        publisher = "Ministry of Statistics and Programme Implementation (MoSPI)"
        homepage_url = "https://www.mospi.gov.in/publications-reports"
        provides = (
            "Every ongoing central sector infrastructure project costing ₹150 crore or more: ministry, agency, state, approval "
            "date, original and revised cost and completion date, spending and physical progress. Updated monthly."
        )
        interval_hours = 24

        def fetch(self, client, since):
            report = latest_flash_report(client)
            if report is None:
                raise RuntimeError("No flash report found in MoSPI's publication list")
            response = client.get(report["url"])
            response.raise_for_status()
            month, _, projects = parse_flash_report(response.content)
            if not projects:
                raise RuntimeError(f"No projects could be read from {report['url']}")
            for project in projects:
                record = map_mospi_project(project, report, month)
                if record:
                    yield record

    return MospiCentralProjects()
