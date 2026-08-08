"""Core Opportunity Score (Layer A) — placeholder for Fase 7.

Reads weights from config/scoring.yaml (never hardcoded), applies
peer-relative normalization within the active universe, and enforces the
score eligibility gate (score_status = insufficient_data below
minimum_schedule_completeness). See PROJECT_SPEC.md §9 Fase 7.
"""

from __future__ import annotations


def compute_core_opportunity_scores(universe: str = "preferred_private") -> None:
    raise NotImplementedError("Fase 7 belum diimplementasikan. Lihat PROJECT_SPEC.md §9 Fase 7.")
