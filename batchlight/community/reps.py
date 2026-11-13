"""Tag each post with the elected representatives for the place it shows.

How it works: the post's map pin is located inside an Assembly constituency boundary (point in polygon, with
bounding boxes as a first filter). That boundary also says which Lok Sabha constituency it belongs to. The sitting
MLA and MP are then looked up in a roster that names its source for every row.

Shown as "representatives for this area", never as people responsible for the asset: who owns and maintains a road,
school or drain depends on the asset, not on the constituency. Ward councillors and village heads are not tagged yet
because there is no national open dataset of them; the page says so.

Boundaries: DataMeet India, Assembly constituencies (CC BY 4.0). Some boundaries predate the latest delimitation
(for example Assam 2023, Jammu and Kashmir 2022); those are labelled and their tags are marked uncertain.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from ..infra.geo import BY_SLUG, find_regions
from .models import CommunityPost, Constituency, PostRepresentative, Representative

AC_SOURCE = "DataMeet India: Assembly constituencies (CC BY 4.0)"
AC_URL = "https://raw.githubusercontent.com/datameet/maps/master/website/docs/data/geojson/ac.geojson"
LEVELS = {
    "lok_sabha": ("National", "Member of Parliament (Lok Sabha)"),
    "assembly": ("State", "Member of the Legislative Assembly"),
}
NOT_AVAILABLE = (
    ("City or town", "Municipal ward councillor", "Not tagged yet: there is no national open list of wards and councillors."),
    (
        "Village",
        "Gram panchayat head (sarpanch)",
        "Not tagged yet: there is no national open list of panchayats and their heads.",
    ),
)


# ------------------------------------------------------------------------------------------------ geometry
def _ring_contains(ring: list, x: float, y: float) -> bool:
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def contains(geometry: dict, lng: float, lat: float) -> bool:
    """Whether a GeoJSON Polygon or MultiPolygon contains the point (holes respected)."""
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    for poly in polygons:
        if poly and _ring_contains(poly[0], lng, lat) and not any(_ring_contains(h, lng, lat) for h in poly[1:]):
            return True
    return False


def _bbox(geometry: dict) -> tuple[float, float, float, float]:
    polygons = [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]
    xs = [p[0] for poly in polygons for ring in poly for p in ring]
    ys = [p[1] for poly in polygons for ring in poly for p in ring]
    return min(xs), min(ys), max(xs), max(ys)


def _polygons(geometry: dict) -> list:
    return [geometry["coordinates"]] if geometry["type"] == "Polygon" else list(geometry["coordinates"])


# ------------------------------------------------------------------------------------------------ importing
def import_assembly_boundaries(db: Session, collection: dict, source: str = AC_SOURCE, url: str = AC_URL) -> dict[str, int]:
    """Load Assembly constituencies, and Lok Sabha constituencies built from them (PC_NO/PC_NAME). Replaces earlier loads."""
    acs: dict[tuple[str, int], dict] = {}
    pcs: dict[tuple[str, int], dict] = {}
    skipped = 0
    for feat in collection.get("features", []):
        p, g = feat.get("properties") or {}, feat.get("geometry")
        regions = find_regions(str(p.get("ST_NAME", "")))
        if not g or g.get("type") not in ("Polygon", "MultiPolygon") or not regions or p.get("AC_NO") in (None, ""):
            skipped += 1
            continue
        state = regions[0]
        ac_no = int(float(p["AC_NO"]))
        status = str(p.get("STATUS") or "").strip()
        ac = acs.setdefault(
            (state, ac_no), {"name": str(p.get("AC_NAME") or "").strip().title(), "pc": None, "polys": [], "status": status}
        )
        ac["polys"].extend(_polygons(g))
        if p.get("PC_NO") not in (None, ""):
            pc_no = int(float(p["PC_NO"]))
            ac["pc"] = pc_no
            pc = pcs.setdefault(
                (state, pc_no), {"name": str(p.get("PC_NAME") or "").strip().title(), "polys": [], "status": status}
            )
            pc["polys"].extend(_polygons(g))
            if status and status not in pc["status"]:
                pc["status"] = status
    db.execute(delete(Constituency).where(Constituency.source == source))
    for kind, items in (("assembly", acs), ("lok_sabha", pcs)):
        for (state, number), v in items.items():
            geom = {"type": "MultiPolygon", "coordinates": v["polys"]}
            x0, y0, x1, y1 = _bbox(geom)
            db.add(
                Constituency(
                    kind=kind,
                    state_slug=state,
                    number=number,
                    name=v["name"] or f"No. {number}",
                    parent_number=v.get("pc") if kind == "assembly" else None,
                    geometry=geom,
                    min_lng=x0,
                    min_lat=y0,
                    max_lng=x1,
                    max_lat=y1,
                    boundary_status=v["status"][:60],
                    source=source,
                    source_url=url,
                )
            )
    db.flush()
    return {"assembly": len(acs), "lok_sabha": len(pcs), "skipped": skipped}


ROSTER_COLUMNS = ("kind", "state", "number", "name", "party", "term_start", "term_end", "source", "source_url")


def import_roster(db: Session, text: str) -> dict[str, int]:
    """Load sitting representatives from a CSV with ROSTER_COLUMNS. Every row must name its source."""
    added, unmatched = 0, []
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in ROSTER_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError("Missing columns: " + ", ".join(missing))
    for i, row in enumerate(reader, start=2):
        kind = row["kind"].strip()
        regions = find_regions(row["state"]) or ([row["state"].strip()] if row["state"].strip() in BY_SLUG else [])
        if (
            kind not in LEVELS
            or not regions
            or not row["number"].strip().isdigit()
            or not row["name"].strip()
            or not row["source_url"].strip()
        ):
            raise ValueError(f"Row {i}: needs a valid kind, state, number, name and source_url")
        c = db.scalar(
            select(Constituency).where(
                Constituency.kind == kind, Constituency.state_slug == regions[0], Constituency.number == int(row["number"])
            )
        )
        if c is None:
            unmatched.append(f"{kind} {regions[0]} {row['number']}")
            continue
        start = date.fromisoformat(row["term_start"]) if row["term_start"].strip() else None
        end = date.fromisoformat(row["term_end"]) if row["term_end"].strip() else None
        db.add(
            Representative(
                constituency_id=c.id,
                role="MP" if kind == "lok_sabha" else "MLA",
                name=row["name"].strip(),
                party=row["party"].strip(),
                term_start=start,
                term_end=end,
                source=row["source"].strip() or "Roster",
                source_url=row["source_url"].strip(),
            )
        )
        added += 1
    db.flush()
    return {"added": added, "unmatched": len(unmatched)}


# ------------------------------------------------------------------------------------------------ tagging
def constituencies_at(db: Session, lat: float, lng: float) -> list[Constituency]:
    candidates = db.scalars(
        select(Constituency).where(
            Constituency.min_lng <= lng, Constituency.max_lng >= lng, Constituency.min_lat <= lat, Constituency.max_lat >= lat
        )
    ).all()
    return [c for c in candidates if contains(c.geometry, lng, lat)]


def sitting(db: Session, constituency: Constituency, on: date) -> Representative | None:
    return db.scalars(
        select(Representative)
        .where(
            Representative.constituency_id == constituency.id,
            or_(Representative.term_start.is_(None), Representative.term_start <= on),
            or_(Representative.term_end.is_(None), Representative.term_end >= on),
        )
        .order_by(Representative.term_start.desc().nulls_last())
        .limit(1)
    ).first()


def tag(db: Session, post: CommunityPost, on: date | None = None) -> int:
    on = on or date.today()
    db.execute(delete(PostRepresentative).where(PostRepresentative.post_id == post.id))
    found = constituencies_at(db, post.latitude, post.longitude)
    for c in found:
        rep = sitting(db, c, on)
        db.add(PostRepresentative(post_id=post.id, constituency_id=c.id, representative_id=rep.id if rep else None))
    return len(found)


def for_post(db: Session, post_id) -> list[PostRepresentative]:
    rows = db.scalars(select(PostRepresentative).join(Constituency).where(and_(PostRepresentative.post_id == post_id))).all()
    order = {"lok_sabha": 0, "assembly": 1}
    return sorted(rows, key=lambda r: order.get(r.constituency.kind, 9))
