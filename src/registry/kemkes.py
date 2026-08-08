"""rs.kemkes.go.id registry source — placeholder for Fase 1.

Spec §9 Fase 1: look for an API/structured endpoint before falling back to
HTML parsing (§3.7). Never rely on Kemkes as the sole registry source.
"""

from __future__ import annotations


def fetch_kemkes_hospitals() -> list[dict]:
    raise NotImplementedError("Fase 1 belum diimplementasikan. Lihat PROJECT_SPEC.md §9 Fase 1.")
