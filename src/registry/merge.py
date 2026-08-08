"""Registry merge/dedup pipeline — placeholder for Fase 1.

Implemented in Fase 1 per PROJECT_SPEC.md §9:
1. Pull from Overpass API (src/registry/osm.py).
2. Pull from rs.kemkes.go.id (src/registry/kemkes.py).
3. Normalize + rapidfuzz dedup (threshold ~85, no silent auto-merge on
   borderline cases).
4. Join with config/hospital_preferences.yaml to set is_preferred_group.
"""

from __future__ import annotations

from src.logging_setup import get_logger

log = get_logger(__name__)


def run_registry_pipeline(source: str = "all") -> None:
    raise NotImplementedError(
        "Fase 1 (Hospital Master Registry) belum diimplementasikan. "
        "Lihat PROJECT_SPEC.md §9 'Fase 1 — Hospital Master Registry'."
    )
