"""Turn new matches into in-app notifications and one email digest per member."""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..models import Match, Membership, Notification, Organization, User
from .email import send_email

SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3, "info": 4}


def _summary(match: Match) -> str:
    """One line per match. Alerts based on a news report say so first, so nobody mistakes them for a regulator's notice."""
    holding, alert = match.holding, match.alert
    batch = f" batch {holding.lot}" if holding.lot else ""
    prefix = "[News report] " if alert.evidence == "report" else ""
    return f"{prefix}{holding.product_name}{batch}: {alert.title}"


def notify_new_matches(session: Session, match_ids: Iterable[uuid.UUID], send_email_digest: bool = True) -> int:
    """Create notifications for new matches. Returns the number of emails attempted."""
    settings = get_settings()
    matches = session.scalars(
        select(Match).where(Match.id.in_(list(match_ids))).options(joinedload(Match.alert), joinedload(Match.holding))
    ).all()
    by_org: dict[uuid.UUID, list[Match]] = defaultdict(list)
    for match in matches:
        by_org[match.org_id].append(match)
    emails = 0
    for org_id, org_matches in by_org.items():
        org = session.get(Organization, org_id)
        members = session.scalars(select(User).join(Membership).where(Membership.org_id == org_id)).all()
        org_matches.sort(key=lambda m: (SEVERITY_ORDER.get(m.alert.severity, 9), -m.confidence))
        for user in members:
            for match in org_matches:
                session.add(
                    Notification(
                        user_id=user.id,
                        org_id=org_id,
                        match_id=match.id,
                        channel="in_app",
                        status="sent",
                        title=f"{match.alert.severity.title()} alert matches your stock",
                        body=_summary(match),
                        sent_at=datetime.now(UTC),
                    )
                )
            if not (send_email_digest and org and org.notify_email):
                continue
            lines = [f"{len(org_matches)} item(s) in {org.name} match medicine safety alerts.", ""]
            for match in org_matches[:20]:
                lines.append(f"- [{match.alert.severity.upper()}] {_summary(match)}")
                lines.append(f"  Review: {settings.base_url}/app/matches/{match.id}")
            if len(org_matches) > 20:
                lines.append(f"...and {len(org_matches) - 20} more: {settings.base_url}/app/matches")
            lines += [
                "",
                "Check the affected stock, then record what you did in Batchlight.",
                "Patients: follow the advice in the original notice. For contaminated or fake products it may say do not use "
                "it at all. If someone has taken it, or may have taken it, they should see a doctor now. For a medicine taken "
                "every day, speak to a pharmacist or doctor before stopping.",
                "",
                f"Notification settings: {settings.base_url}/app/settings",
            ]
            result = send_email(user.email, f"Batchlight: {len(org_matches)} stock item(s) match safety alerts", "\n".join(lines))
            emails += 1
            session.add(
                Notification(
                    user_id=user.id,
                    org_id=org_id,
                    channel="email",
                    status=result.status,
                    title=f"Email digest: {len(org_matches)} match(es)",
                    body="\n".join(lines[:3]),
                    error=result.error,
                    sent_at=datetime.now(UTC) if result.status == "sent" else None,
                )
            )
    session.flush()
    return emails
