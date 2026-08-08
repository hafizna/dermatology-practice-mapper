"""Fase 4.5: run scrape (cache replay) -> parse -> persist for every
registered adapter, in one DB transaction per source, and print a summary
table. Ad-hoc operational script (not part of the CLI yet — Fase 8 will
likely wire a proper `pipeline run` command).

IMPORTANT: main() clears the doctors/schedule_slots tables before
persisting, so re-running this script is idempotent — it will NOT keep
appending duplicate Doctor/ScheduleSlot rows the way it silently did
before this was added. (Real incident, 2026-08-09: repeated manual runs
during a dashboard-review session — each verifying a manual_overrides.csv
change without resetting the DB first — quadrupled some hospitals'
doctor counts, e.g. RS Pondok Indah - Puri Indah showing 52
"dermatologists" that were actually 13 real ones counted 4x. Caught by
the user visually spotting an implausible number on the dashboard map,
not by any automated check.) The Hospital registry table itself is left
untouched here — src/registry/merge.py's own fetch-registry pipeline
handles clearing/re-inserting that one.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db import session_scope
from src.models import Doctor, ScheduleSlot
from src.scrapers import eka as eka_module
from src.scrapers.bethsaida import BethsaidaScraper
from src.scrapers.brawijaya import BrawijayaScraper
from src.scrapers.emc import EmcScraper
from src.scrapers.hermina import HerminaScraper
from src.scrapers.mayapada import MayapadaScraper
from src.scrapers.mitra_keluarga import MitraKeluargaScraper
from src.scrapers.pipeline import persist_raw_doctor_records
from src.scrapers.primaya import PrimayaScraper
from src.scrapers.rs_pondok_indah import RsPondokIndahScraper
from src.scrapers.rs_premier import RsPremierScraper
from src.scrapers.sari_asih import SariAsihScraper
from src.scrapers.siloam import SiloamScraper

# (source key, adapter, preferred_rank_group as stored in Hospital rows)
NETWORK_SOURCES = [
    ("siloam", SiloamScraper, "Siloam"),
    ("mitra_keluarga", MitraKeluargaScraper, "Mitra Keluarga"),
    ("hermina", HerminaScraper, "Hermina"),
    ("primaya", PrimayaScraper, "Primaya"),
    ("emc", EmcScraper, "EMC"),
    ("mayapada", MayapadaScraper, "Mayapada"),
    ("bethsaida", BethsaidaScraper, "Bethsaida"),
    ("rs_pondok_indah", RsPondokIndahScraper, "RS Pondok Indah"),
    ("brawijaya", BrawijayaScraper, "Brawijaya"),
    ("sari_asih", SariAsihScraper, "Sari Asih"),
    ("rs_premier", RsPremierScraper, "RS Premier"),
]


def main() -> None:
    with session_scope() as session:
        n_slots = session.query(ScheduleSlot).delete()
        n_doctors = session.query(Doctor).delete()
    print(f"Dibersihkan sebelum re-run: {n_doctors} baris doctors, {n_slots} baris schedule_slots.\n")

    results = {}

    for source, adapter_cls, preferred_group in NETWORK_SOURCES:
        print(f"\n=== {source} ===")
        try:
            scraper = adapter_cls(use_cache=True)
            records = scraper.fetch_all_dermatology_doctors()
        except Exception as exc:  # noqa: BLE001 - operational script, report and continue
            print(f"  FETCH FAILED: {exc!r}")
            results[source] = {"error": str(exc)}
            continue

        with session_scope() as session:
            summary = persist_raw_doctor_records(
                session, records, source=source, preferred_group=preferred_group
            )
        results[source] = summary
        print(f"  {summary}")

    # Eka: manual snapshot, module-level function, no BaseScraper instance
    print("\n=== eka (manual snapshot) ===")
    try:
        records = eka_module.fetch_all_dermatology_doctors(jabodetabek_only=True)
        with session_scope() as session:
            summary = persist_raw_doctor_records(
                session, records, source="eka", preferred_group="Eka Hospital"
            )
        results["eka"] = summary
        print(f"  {summary}")
    except Exception as exc:  # noqa: BLE001
        print(f"  FETCH FAILED: {exc!r}")
        results["eka"] = {"error": str(exc)}

    print("\n\n=== RINGKASAN TOTAL ===")
    total_created = 0
    total_slots = 0
    total_unmatched = 0
    for source, s in results.items():
        if "error" in s:
            print(f"  {source:20s} ERROR: {s['error']}")
            continue
        total_created += s["doctors_created"]
        total_slots += s["schedule_slots_created"]
        total_unmatched += s["hospital_unmatched"]
        print(
            f"  {source:20s} total={s['total_records']:4d}  "
            f"not_derm={s['not_dermatologist']:3d}  "
            f"created={s['doctors_created']:4d}  "
            f"slots={s['schedule_slots_created']:4d}  "
            f"hosp_unmatched={s['hospital_unmatched']:3d}"
        )
    print(f"\n  TOTAL doctors_created={total_created}  schedule_slots_created={total_slots}  hospital_unmatched={total_unmatched}")

    # Collect the unique unmatched hospital names across all sources for review.
    unmatched_names = set()
    for s in results.values():
        if "unmatched_hospital_names" in s:
            unmatched_names.update(s["unmatched_hospital_names"])
    if unmatched_names:
        print(f"\n  Nama RS unik yang TIDAK match ke registry ({len(unmatched_names)}):")
        for n in sorted(unmatched_names):
            print(f"    - {n}")


if __name__ == "__main__":
    main()
