"""Schedule text parser — placeholder for Fase 4.2.

Layered parser: (1) exact known patterns, (2) normalized patterns,
(3) cautious fallback. Unparseable text keeps raw_text with
parse_confidence=low and must NOT be used to compute gaps. "selesai" means
end_time=None, never guessed. See PROJECT_SPEC.md §9 Fase 4.2.
"""

from __future__ import annotations


def parse_schedule_text(raw_text: str) -> list[dict]:
    raise NotImplementedError("Fase 4.2 belum diimplementasikan. Lihat PROJECT_SPEC.md §9 Fase 4.2.")
