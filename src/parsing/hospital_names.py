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

# English "Hospital(s)" appears not only as a prefix but ANYWHERE in the
# name across sources — e.g. registry name "RS SILOAM KEBON JERUK" (OSM,
# Indonesian-only) vs. scraper-reported "Siloam Hospitals Kebon Jeruk"
# (Fase 2/3, English word inserted mid-name). Confirmed during Fase 4.5
# pipeline testing: without stripping this as a stopword anywhere in the
# string, that exact pair scores 78 (token_sort_ratio), just under the
# pipeline's 80 match threshold — a real cross-hospital-group name
# consistently caused a false "unmatched" for every Siloam branch whose
# name includes "Hospitals". Removed as a whole-word stopword (not a
# prefix-only strip) rather than raising the threshold, since lowering
# the bar for everyone risks new false-positive merges elsewhere.
_ENGLISH_HOSPITAL_WORD_RE = re.compile(r"\bhospitals?\b", re.IGNORECASE)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_hospital_name(raw_name: str) -> str:
    """Lowercase, strip RS/RSU/RSUD/Rumah Sakit prefix (and the English
    "Hospital(s)" word anywhere in the string), remove punctuation,
    collapse whitespace. Spec §9 Fase 1 "Merge & dedup".

    Only used for *matching* — the original raw_name is always retained
    separately (Hospital.name) per spec §3.1/§3.2.
    """
    text = unicodedata.normalize("NFKC", raw_name).strip().lower()
    text = _PREFIX_RE.sub("", text)
    text = _ENGLISH_HOSPITAL_WORD_RE.sub("", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text
