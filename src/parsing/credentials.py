"""Dermatologist credential detection — placeholder for Fase 4.1.

Must not use a bare `Sp\\.?\\s*KK` regex alone (false positives: Sp.KKLP,
Sp.KJ, Sp.KFR, Sp.KL, Sp.KO, Sp.KN, Sp.KG, Sp.KKV). Minimum 30 unit tests
required. See PROJECT_SPEC.md §9 Fase 4.1 for the full valid/invalid list
and starting regex.
"""

from __future__ import annotations


def is_dermatologist_credential(text: str) -> bool:
    raise NotImplementedError("Fase 4.1 belum diimplementasikan. Lihat PROJECT_SPEC.md §9 Fase 4.1.")
