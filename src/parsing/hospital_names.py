"""Hospital name normalization — shared by registry dedup (Fase 1) and
future cross-source matching (Fase 3 manual overrides, aggregator merge).

Kept separate from src/parsing/names.py because that module is reserved
for *doctor* name normalization (Fase 4.3) with different rules (title
stripping, person-identity fuzzy matching). Hospital name normalization is
simpler and institution-specific.
"""

from __future__ import annotations

import re
import unicodedata

# Order matters: longer/more specific prefixes first so "Rumah Sakit Umum
# Daerah" doesn't leave a dangling "Daerah" after a naive "Rumah Sakit" strip.
_PREFIX_PATTERNS = [
    r"rumah\s+sakit\s+umum\s+daerah",
    r"rumah\s+sakit\s+umum\s+pusat",
    r"rumah\s+sakit\s+umum",
    r"rumah\s+sakit\s+khusus",
    r"rumah\s+sakit\s+ibu\s+dan\s+anak",
    r"rumah\s+sakit",
    r"rsud",
    r"rsup",
    r"rsia",
    r"rsu",
    r"rs",
]
_PREFIX_RE = re.compile(r"^(?:" + "|".join(_PREFIX_PATTERNS) + r")\b\.?\s*", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_hospital_name(raw_name: str) -> str:
    """Lowercase, strip RS/RSU/RSUD/Rumah Sakit prefix, remove punctuation,
    collapse whitespace. Spec §9 Fase 1 "Merge & dedup".

    Only used for *matching* — the original raw_name is always retained
    separately (Hospital.name) per spec §3.1/§3.2.
    """
    text = unicodedata.normalize("NFKC", raw_name).strip().lower()
    text = _PREFIX_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text
