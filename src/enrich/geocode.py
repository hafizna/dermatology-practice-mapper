"""Nominatim fallback geocoding — placeholder for Fase 5.

Rate limit max 1 request/second, honest User-Agent with contact, persistent
cache, geocode_confidence recorded, never re-geocode the same address
without reason. Never treat a kecamatan/kota-level result as
hospital-precise. See PROJECT_SPEC.md §9 Fase 5.
"""

from __future__ import annotations


def geocode_address(address: str) -> dict:
    raise NotImplementedError("Fase 5 belum diimplementasikan. Lihat PROJECT_SPEC.md §9 Fase 5.")
