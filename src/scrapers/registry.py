"""Scraper adapter registry/dispatcher — Fase 2/3.

Maps group_name -> adapter class. src/cli.py `scrape` dispatches through
this module so new adapters only need to register here, not touch the CLI.
"""

from __future__ import annotations

from src.logging_setup import get_logger
from src.scrapers.siloam import SiloamScraper

log = get_logger(__name__)

# Populated incrementally. Only groups with a working adapter are listed —
# an "accessible" entry in config/sources.yaml does not imply an adapter
# exists yet (that's tracked separately, see Fase 3 target list).
ADAPTERS: dict[str, type] = {
    "siloam": SiloamScraper,
}


def run_scrape(group: str | None, scrape_all: bool) -> None:
    groups = list(ADAPTERS.keys()) if scrape_all else [group]

    for g in groups:
        if g not in ADAPTERS:
            available = ", ".join(sorted(ADAPTERS.keys())) or "(none)"
            raise ValueError(f"Tidak ada adapter untuk group '{g}'. Adapter tersedia: {available}")

        log.info("scrape_group_start", group=g)
        if g == "siloam":
            _run_siloam()
        else:  # pragma: no cover - unreachable until more adapters exist
            raise NotImplementedError(f"Adapter '{g}' terdaftar tapi run_scrape belum diimplementasikan.")


def _run_siloam() -> None:
    from src.scrapers.siloam import SiloamScraper

    scraper = SiloamScraper(use_cache=True)
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    print(f"Siloam: {len(records)} raw dermatologist records (Jabodetabek) fetched.")
    print("Catatan: ini data MENTAH (belum di-parse/disimpan ke DB doctors/schedule_slots).")
    print("Parsing credential/jadwal + persist ke DB dikerjakan di Fase 4.")
    for r in records[:10]:
        print(f"  - {r.raw_name} | {r.source_url}")
    if len(records) > 10:
        print(f"  ... dan {len(records) - 10} lainnya.")
