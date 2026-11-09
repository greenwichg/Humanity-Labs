from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..models import AuditEvent


def record(
    session: Session,
    action: str,
    *,
    org_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    subject: str | None = None,
    ip: str | None = None,
    **data: Any,
) -> None:
    session.add(
        AuditEvent(
            action=action,
            org_id=org_id,
            user_id=user_id,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            subject=subject[:320] if subject else None,
            ip=ip,
            data=data,
        )
    )
