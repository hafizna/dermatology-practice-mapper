"""Dermatologist credential detection — Fase 4.1.

Detects whether a text string (typically a doctor's raw_name /
raw_credentials_text, e.g. "dr. Budi Santoso, Sp.KK, FINSDV") indicates a
dermatology/venereology specialist ("Kulit dan Kelamin" / "Dermatologi
dan Venereologi").

CRITICAL correctness constraint (spec §9 Fase 4.1): a bare `Sp\\.?\\s*KK`
regex must NOT be used alone, because it also matches unrelated
specialities whose abbreviation happens to start with "KK"-adjacent
letters or whose full abbreviation CONTAINS "KK" as a substring:

    Sp.KKLP  — Kedokteran Keluarga Layanan Primer (Family Medicine)
    Sp.KJ    — Kedokteran Jiwa / Psikiatri (Psychiatry)
    Sp.KFR   — Kedokteran Fisik dan Rehabilitasi (Physical Medicine & Rehab)
    Sp.KL    — Kedokteran Laut / Kelautan (Maritime/Aviation Medicine)
    Sp.KO    — Kedokteran Olahraga (Sports Medicine)
    Sp.KN    — (various, e.g. Kedokteran Nuklir)
    Sp.KG    — Kedokteran Gigi (Dentistry, when abbreviated this way)
    Sp.KKV   — Bedah Toraks Kardiak dan Vaskular (Thoracic/Cardiac/
               Vascular Surgery) — this one is the trickiest false
               positive because it visually contains "KK" as a substring
               (Sp.K-K-V), not just a shared prefix.

Real-world data collected during Fase 2/3 scraping (280+ distinct doctor
name strings across 10 hospital groups) showed the valid forms are far
more varied than the spec's starting list: "SpKK", "Sp KK" (space, no
dot), "Sp.KK.", "SpKK(K)"/"Sp.KK (K)"/"Sp.KK-K" (konsultan subspecialist
suffix), "Sp.D.V.E" (every letter dotted), "SpDVE", "Sp DVE", "SpDV",
"Sp.DV", "S.DV" (typo, missing the "p"), and combinations with trailing
credentials (", FINSDV, FAADV", ", Subsp. OBK", ", M.Kes") that must not
break detection of the primary credential itself. Non-breaking spaces
(U+00A0) also appeared in place of regular spaces in a few scraped names.
"""

from __future__ import annotations

import re
import unicodedata

# --- Building blocks -------------------------------------------------
#
# "Sp" followed by an optional dot, matched case-insensitively. Doctors'
# own self-reported strings are almost always "Sp"/"SP" but we normalize
# case anyway rather than assume.
_SP = r"[Ss][Pp]\.?"

# Flexible separator between "Sp" and the specialty letters: zero or more
# spaces (including non-breaking space), with or without a dot.
_SEP = r"[\s ]*"


def _letters(*parts: str) -> str:
    """Build a pattern matching each letter in `parts` optionally
    followed by a dot and/or whitespace, e.g. _letters("K", "K") matches
    "KK", "K.K", "K. K.", "K K", etc. — handles the "every letter dotted"
    style (Sp.D.V.E) seen in real scraped data.
    """
    return _SEP.join(f"{ch}\\.?" for ch in parts)


# --- Valid dermatology/venereology credential patterns ----------------
#
# Kulit dan Kelamin / Dermatologi Venereologi is abbreviated in practice
# as SpKK, SpDV, or SpDVE (the modern "Sp.D.V.E" standardized form — see
# the real-world care-plus article several adapters' data referenced:
# "Mengenal Gelar Sp.DV, Sp.DVE, dan Sp.KK"). All three (and their
# dotted/spaced variants) are treated as valid.
#
# Order matters: try the longer/more specific patterns (DVE, then DV)
# before the shorter KK pattern where there's any ambiguity, though in
# practice these don't overlap.
_VALID_PATTERNS = [
    # SpDVE / Sp.DVE / Sp.D.V.E / Sp DVE
    rf"{_SP}{_SEP}{_letters('D', 'V', 'E')}",
    # SpDV / Sp.DV / Sp.D.V (must not also match the DVE prefix — handled
    # by trying DVE first in _VALID_PATTERNS order and using a negative
    # lookahead so "DV" doesn't match just the first two letters of "DVE").
    rf"{_SP}{_SEP}{_letters('D', 'V')}(?!\.?{_SEP}[Ee])",
    # SpKK / Sp.KK / Sp KK / Sp K K — the exact pattern from spec §9
    # Fase 4.1, ported to use our flexible separator/letter builder.
    # Negative lookbehind/lookahead exclude KKLP and KKV (the two "KK"
    # substring false positives); the plain word-boundary check via
    # \b before "Sp" and the explicit exclusions after handle the rest
    # (KJ/KFR/KL/KO/KN/KG don't contain "KK" at all, so the base pattern
    # never matches them in the first place — only KKLP/KKV need
    # explicit exclusion).
    rf"{_SP}{_SEP}{_letters('K', 'K')}(?!{_SEP}[Ll][Pp])(?!{_SEP}[Vv](?![a-zA-Z]))",
]

_COMBINED_VALID_RE = re.compile("(?:" + "|".join(_VALID_PATTERNS) + ")")

# Full-word Indonesian/English phrasings sometimes used instead of an
# abbreviation (spec §9 Fase 4.1 valid examples).
_FULL_WORD_PATTERNS = [
    r"dermatologi\s+dan\s+venereologi",
    r"dermatovenereologi",
    r"kulit\s+dan\s+kelamin",
    r"dermatology\s+and\s+venereology",
]
_COMBINED_FULL_WORD_RE = re.compile("(?:" + "|".join(_FULL_WORD_PATTERNS) + ")", re.IGNORECASE)

# Explicit false-positive credentials that must NEVER be classified as
# dermatology, even though some share letters with valid patterns.
# Checked first so any accidental future pattern overlap fails safe.
_FALSE_POSITIVE_PATTERNS = [
    rf"{_SP}{_SEP}{_letters('K', 'K', 'L', 'P')}",  # Sp.KKLP — Family Medicine
    rf"{_SP}{_SEP}{_letters('K', 'J')}",  # Sp.KJ — Psychiatry
    rf"{_SP}{_SEP}{_letters('K', 'F', 'R')}",  # Sp.KFR — Physical Medicine & Rehab
    rf"{_SP}{_SEP}{_letters('K', 'L')}",  # Sp.KL — Maritime/Aviation Medicine
    rf"{_SP}{_SEP}{_letters('K', 'O')}",  # Sp.KO — Sports Medicine
    rf"{_SP}{_SEP}{_letters('K', 'N')}",  # Sp.KN — e.g. Nuclear Medicine
    rf"{_SP}{_SEP}{_letters('K', 'G')}",  # Sp.KG — Dentistry
    rf"{_SP}{_SEP}{_letters('K', 'K', 'V')}",  # Sp.KKV — Thoracic/Cardiac/Vascular Surgery
]
_COMBINED_FALSE_POSITIVE_RE = re.compile("(?:" + "|".join(_FALSE_POSITIVE_PATTERNS) + ")")


def _normalize(text: str) -> str:
    """NFKC-normalize (collapses some unicode variants) and replace
    non-breaking spaces with regular spaces, without altering casing —
    the regexes above already handle case themselves.
    """
    text = unicodedata.normalize("NFKC", text)
    return text.replace(" ", " ")


def is_dermatologist_credential(text: str) -> bool:
    """Return True if `text` contains a dermatology/venereology
    credential (Sp.KK, Sp.DV, Sp.DVE, or a spelled-out equivalent),
    False otherwise — including for known false-positive-prone
    credentials like Sp.KKLP or Sp.KKV that merely share letters.

    This is a text-level check, not a full name parser: callers pass the
    doctor's raw name/credentials string and get a boolean. It does not
    extract WHICH credential matched or normalize the doctor's name —
    that's out of scope here (name normalization is src/parsing/names.py).
    """
    if not text:
        return False

    normalized = _normalize(text)

    # Some official hospital sources expose only this exact English
    # specialty label rather than a credential suffix. Keep the allowance
    # exact so phrases such as "dermatology assistant" are not mistaken
    # for a specialist credential.
    if normalized.strip().casefold() == "dermatology":
        return True

    if _COMBINED_FULL_WORD_RE.search(normalized):
        return True

    # Scan for every "Sp..." occurrence and classify each independently,
    # rather than short-circuiting on the first false-positive match —
    # a doctor can hold multiple credentials (e.g. "Sp.KJ, Sp.KK" for a
    # dual-boarded physician), so one non-dermatology credential must not
    # mask a real dermatology one elsewhere in the same string.
    for match in re.finditer(rf"{_SP}", normalized):
        remainder = normalized[match.start() :]
        if _COMBINED_FALSE_POSITIVE_RE.match(remainder):
            continue
        if _COMBINED_VALID_RE.match(remainder):
            return True

    return False
