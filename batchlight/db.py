"""Database engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url, pool_pre_ping=True, pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow
        )
        _session_factory = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)
    return _engine


def reset_engine() -> None:
    """Dispose the engine so the next call reads fresh settings (used by tests)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for scripts and the worker."""
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, committed by the handler."""
    session = session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
