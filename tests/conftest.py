from __future__ import annotations

import pytest
from sqlalchemy import Engine

from src.db import get_engine, init_db


@pytest.fixture()
def in_memory_engine() -> Engine:
    """Isolated in-memory SQLite engine with schema created — no network,
    no shared state with data/processed/derm_mapper.sqlite (spec §14: no
    network calls in the test suite).
    """
    engine = get_engine(database_url="sqlite:///:memory:")
    init_db(engine)
    return engine
