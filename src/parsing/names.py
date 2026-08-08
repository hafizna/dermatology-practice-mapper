"""Doctor name normalization / cross-hospital identity resolution —
placeholder for Fase 4.3.

Strip leading/trailing titles for matching, lowercase, normalize
whitespace, keep raw_name, fuzzy-match only when needed, and always store
match confidence. Never merge two doctors based on surname or a short
token alone. See PROJECT_SPEC.md §9 Fase 4.3.
"""

from __future__ import annotations


def normalize_person_key(raw_name: str) -> str:
    raise NotImplementedError("Fase 4.3 belum diimplementasikan. Lihat PROJECT_SPEC.md §9 Fase 4.3.")
