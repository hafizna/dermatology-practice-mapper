"""Database engine/session management.

Zero-config SQLite per spec §6. `init_db()` creates all tables from
`src.models.Base.metadata` — this is the "SQLite schema" deliverable of
Fase 0.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings
from src.models import Base


def _sqlite_path_from_url(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    return Path(database_url[len(prefix) :])


def get_engine(database_url: str | None = None) -> Engine:
    url = database_url or get_settings().database_url
    sqlite_path = _sqlite_path_from_url(url)
    if sqlite_path is not None:
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(url, echo=False, future=True)


_SessionLocal: sessionmaker[Session] | None = None


def get_sessionmaker(engine: Engine | None = None) -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=engine or get_engine(), expire_on_commit=False, future=True)
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Provide a transactional scope around a series of operations."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Engine | None = None) -> None:
    """Create all tables (idempotent — CREATE TABLE IF NOT EXISTS semantics)."""
    Base.metadata.create_all(bind=engine or get_engine())
