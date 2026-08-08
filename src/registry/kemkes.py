"""rs.kemkes.go.id registry source — SKIPPED by explicit decision (Fase 1).

Reconnaissance 2026-08-08 found the structured endpoint per spec §3.7:

    GET https://rs.kemkes.go.id/api/v1/hospitals?page=1&limit=50

robots.txt allows it (`Allow: /`), and it does return clean JSON. However
the dataset itself is materially insufficient for this project's purpose:

- Only 42 records total, covering all of Indonesia (not just Jabodetabek).
- Every record inspected is a national vertical hospital (RSUP — Rumah
  Sakit Umum Pusat), i.e. central-government-owned referral hospitals
  (examples: RSCM, RS Fatmawati, RS Dr. Sardjito).
- No `ownership`, `hospital_class`, or `hospital_type` field is present in
  the payload — it cannot be used to populate Hospital.ownership as the
  spec's Fase 1 description assumed it might.
- It does not include any of the target private hospital groups (Eka,
  Siloam, Mitra Keluarga, etc.).

Given the user's explicit instruction to focus on RS swasta (private
hospitals), this source would add only out-of-scope government hospitals
without providing the ownership/class metadata that was its main expected
value. Per user decision, it is skipped entirely for Fase 1 rather than
included as a low-value cross-check.

This module is kept as a stub (not deleted) so the decision and its
reasoning are auditable, and so the source can be revisited if scope ever
expands beyond private hospitals.
"""

from __future__ import annotations

from src.logging_setup import get_logger

log = get_logger(__name__)


def fetch_kemkes_hospitals() -> list[dict]:
    log.info(
        "kemkes_source_skipped",
        reason="only 42 national RSUP records, no ownership/class field, out of private-hospital scope",
    )
    return []
