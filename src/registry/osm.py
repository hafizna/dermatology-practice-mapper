"""Overpass API registry source — placeholder for Fase 1.

Query `amenity=hospital` over the Jabodetabek bbox declared in
config/sources.yaml (registry_sources.overpass.bbox_jabodetabek).

IMPORTANT (spec §9 Fase 1): verify that bbox yourself before treating it as
final — it must not clip any relevant area.
"""

from __future__ import annotations


def fetch_osm_hospitals() -> list[dict]:
    raise NotImplementedError("Fase 1 belum diimplementasikan. Lihat PROJECT_SPEC.md §9 Fase 1.")
