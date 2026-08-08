"""Doctor name normalization / cross-hospital identity resolution —
Fase 4.3.

Two distinct jobs, kept separate per spec §4.3:

1. normalize_person_key(): produce a stable, comparable key from a raw
   doctor name by stripping titles/credentials, lowercasing, and
   collapsing whitespace — used so the SAME doctor scraped from two
   different hospital sites (different formatting: "Dr. dr. Betty
   Ekawati Suryaningsih, Sp.DV" vs "dr Betty Ekawati Suryaningsih SpDV")
   normalizes to the same key.

2. match_doctor_identity(): decide whether two RAW names likely refer to
   the same physical person, for cross-hospital overlap detection (spec
   §8.2 normalized_person_key, §8/Fase 6 doctors_with_external_overlap).

CRITICAL correctness constraint (spec §9 Fase 4.3): "Jangan merge dua
dokter hanya berdasarkan nama belakang atau token pendek." Verified this
matters with real Fase 2/3 data — a crude surname-only grouping over 300+
scraped names produced matches like:

    "dr. Inda Astri Aryani, SpKK (K)" (Siloam)
    "dr. Christilla Citra Aryani, Sp.KK" (Hermina)

...which are clearly two different physicians who merely share a common
Indonesian surname ("Aryani"). A first-name-initial or short-token match
is equally unsafe (e.g. many doctors share a single given name). This
module's matching therefore requires agreement across MULTIPLE full name
tokens (not just the last one) before ever returning anything above
"low" confidence, and never merges automatically at low confidence —
callers must treat low-confidence matches as "flag for manual review",
not "same person."

The same real-data audit also found genuine likely-same-doctor pairs with
IDENTICAL full names (credentials aside) across hospitals, e.g.:

    "dr. Danny Gunawan, SpDVE, FINSDV" (Siloam)
    "dr. Danny Gunawan, Sp.DVE, FINSDV" (Mitra Keluarga)

    "dr. Armita Asri A, SpDV" (Siloam)
    "dr. Armita Asri A, Sp.DV" (Hermina)

These normalize to an identical person_key and are the "high confidence"
case this module is designed to catch — but even here, spec's caution
applies: a shared full name is still not biometric proof of the same
person (common Indonesian names exist), so this remains a confidence
level, not a certainty, and is surfaced as such in ScheduleSlot/Doctor
records rather than silently deduplicated.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --- title/credential stripping -----------------------------------------

# Leading academic/professional titles, longest-first so "Prof. Dr. dr."
# doesn't leave a dangling "dr." unstripped by matching only "Prof."
_LEADING_TITLE_PATTERNS = [
    r"prof\.?\s+dr\.?\s+dr\.?",
    r"prof\.?\s+dr\.?",
    r"dr\.?\s+dr\.?",
    r"prof\.?",
    r"dr\.?",
    r"drg\.?",  # dentist, appears in some cross-referenced source data
    r"rd\.?",  # Raden (Javanese/Sundanese honorific), seen in real scraped names
]
_LEADING_TITLE_RE = re.compile(r"^(?:" + "|".join(_LEADING_TITLE_PATTERNS) + r")\s*", re.IGNORECASE)

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_credentials_suffix(name: str) -> str:
    """Drop everything from the first comma onward — credentials
    (Sp.KK, FINSDV, M.Kes, etc.) and post-nominal letters always appear
    after a comma in every source scraped in Fase 2/3. A name with no
    comma is returned unchanged (nothing to strip).
    """
    return name.split(",", 1)[0]


def _strip_leading_titles(name: str) -> str:
    """Strip leading titles iteratively — some real scraped names stack
    multiple titles ("Prof. Dr. dr. Kabulrachman") and a single-pass
    regex could leave a partial title behind depending on match order.
    """
    previous = None
    current = name.strip()
    while previous != current:
        previous = current
        current = _LEADING_TITLE_RE.sub("", current).strip()
    return current


def normalize_person_key(raw_name: str) -> str:
    """Produce a normalized comparison key: strip credentials (after the
    first comma) and leading titles, lowercase, strip punctuation,
    collapse whitespace. The original raw_name is never modified by
    callers — this is a matching key only (spec §8.2).

    Names structured as "Name, dr., Credential" (a title token sandwiched
    between commas, e.g. real scraped data "Lia Marlia Rudi, dr., SpKK")
    are handled correctly by the same first-comma split used for the more
    common "dr. Name, Credential" shape — the segment before the first
    comma is always the name itself in every source seen in Fase 2/3,
    regardless of where a title token appears relative to it.
    """
    if not raw_name:
        return ""

    text = unicodedata.normalize("NFKC", raw_name)
    name_part = _strip_credentials_suffix(text)
    name_part = _strip_leading_titles(name_part)
    name_part = name_part.replace(" ", " ")  # normalize non-breaking spaces
    name_part = _PUNCT_RE.sub(" ", name_part)
    name_part = _WHITESPACE_RE.sub(" ", name_part).strip().lower()
    return name_part


# --- cross-hospital identity matching ------------------------------------


@dataclass
class NameMatchResult:
    is_match: bool
    confidence: str  # "high" | "medium" | "low" | "none"
    reason: str


def _tokenize(key: str) -> list[str]:
    return [t for t in key.split(" ") if t]


def match_doctor_identity(raw_name_a: str, raw_name_b: str) -> NameMatchResult:
    """Compare two raw doctor names (typically from different hospital
    sources) and estimate whether they refer to the same person.

    Deliberately conservative per spec §9 Fase 4.3 — the return value is
    always a confidence assessment for human/downstream review, never an
    automatic merge decision. Short-token-only or surname-only agreement
    is explicitly insufficient (see module docstring for the real-data
    false-positive example that motivates this).
    """
    key_a = normalize_person_key(raw_name_a)
    key_b = normalize_person_key(raw_name_b)

    if not key_a or not key_b:
        return NameMatchResult(False, "none", "empty name after normalization")

    if key_a == key_b:
        return NameMatchResult(True, "high", "identical normalized full name")

    tokens_a = _tokenize(key_a)
    tokens_b = _tokenize(key_b)

    # Reject outright if either side is a single short token (e.g. just
    # a surname or a single initial) — spec explicitly forbids merging
    # on "nama belakang atau token pendek" alone, and a single-token name
    # gives us nothing else to corroborate against.
    if len(tokens_a) < 2 or len(tokens_b) < 2:
        return NameMatchResult(False, "none", "one or both names have fewer than 2 tokens after normalization")

    shared_tokens = set(tokens_a) & set(tokens_b)
    # Require at least 2 shared tokens of length >= 3 (excludes initials
    # like single-letter "A", "K" seen in real data, e.g. "Armita Asri A")
    # counted as corroborating evidence — a lone shared surname is
    # exactly the false-positive case documented above and must not pass.
    substantial_shared = {t for t in shared_tokens if len(t) >= 3}

    if len(substantial_shared) >= 2:
        # Multiple substantial tokens in common (e.g. given name AND
        # surname both match) but the full strings differ (extra
        # middle name, different token order, etc.) — plausible same
        # person, but not certain: flag for review, don't auto-merge.
        return NameMatchResult(
            True, "medium", f"{len(substantial_shared)} shared substantial name tokens: {sorted(substantial_shared)}"
        )

    if len(substantial_shared) == 1:
        # Exactly one substantial shared token — could be a shared
        # surname (the documented false-positive case) or a shared given
        # name; either way, not enough corroboration on its own.
        return NameMatchResult(
            False, "low", f"only 1 shared substantial name token ({next(iter(substantial_shared))!r}) — insufficient, spec forbids surname-only matching"
        )

    return NameMatchResult(False, "none", "no substantial shared name tokens")
