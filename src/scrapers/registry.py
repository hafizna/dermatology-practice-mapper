"""Scraper adapter registry/dispatcher — placeholder for Fase 2/3.

Once adapters exist (src/scrapers/eka.py, src/scrapers/siloam.py, ...),
this module maps group_name -> adapter class and is the single place
`src/cli.py scrape` dispatches through.
"""

from __future__ import annotations

from src.logging_setup import get_logger

log = get_logger(__name__)

# Populated incrementally starting Fase 2 (pilot: eka).
ADAPTERS: dict[str, str] = {}


def run_scrape(group: str | None, scrape_all: bool) -> None:
    raise NotImplementedError(
        "Belum ada adapter scraper yang diimplementasikan. "
        "Lihat PROJECT_SPEC.md §9 'Fase 2 — Scraper Framework + Satu Adapter Pilot'."
    )
