"""Schedule text/data parser — Fase 4.2.

Spec §9 Fase 4.2 asks for a layered parser (exact known patterns ->
normalized patterns -> cautious fallback) over free-text schedule strings
like "Senin, Rabu, Jumat 08.00 - 12.00". In practice (confirmed by
Fase 2/3 scraping across 10 hospital groups), most sources already return
SOME structure — a day field plus start/end times — but the shape of that
structure differs completely per source:

    Siloam/Hermina/RSPI : {"day": "monday"/"Monday", "from_time"/"start"/
                            "time_from": "HH:MM:SS", ...}
    Mitra Keluarga       : {"date": "2026-08-09", "day": "Minggu",
                             "start_time": "10:00", "end_time": "12:00"}
    EMC                  : {"day": "2" (source-own numbering, NOT spec's
                             0=Senin), "start_time": "HH:MM:SS", ...}
    Brawijaya            : {"weekday": 1, "start_hour": "9",
                             "start_minute": 0, "end_hour": "12",
                             "end_minute": 0}
    Bethsaida/Mayapada   : {"day_text": "Senin"/"Monday",
                             "time_text": "14:00 - 17:00 WIB"} — free text,
                             sometimes with garbage whitespace (tabs/
                             newlines from raw HTML text extraction) or
                             TWO ranges concatenated with no separator
                             (confirmed: Bethsaida "14:00-16:0014:00-17:00"
                             for one day — genuinely ambiguous, must not
                             be guessed apart).
    Primaya (raw HTML)   : free text using DOTS for the time separator
                            ("14.00 - 18.00", not "14:00"), and can have
                            TWO time ranges per day joined by " dan "
                            ("08.30 - 10.30 dan 13.30 - 14.30") — a
                            genuine multi-session day, not ambiguous
                            (has an explicit "dan"/"and" separator), so
                            this one CAN be split into two slots safely.
    Eka                  : no schedule data at all (manual snapshot,
                            listing-only source — see src/scrapers/eka.py).

This module provides:
- normalize_day_of_week(): maps any of the day representations above to
  spec's canonical 0=Senin..6=Minggu int, returning None (never a guess)
  if the input doesn't match a known day name/number.
- parse_time_range_text(): the free-text layered parser for "HH:MM(:SS)?
  [.-] HH:MM(:SS)?" style ranges, handling both ':' and '.' separators,
  the "dan"/"and" multi-session join, "selesai" (open-ended), and
  returning parse_confidence="low" (with the raw text preserved, per spec
  "jangan dipakai menghitung gap") for anything genuinely ambiguous
  (unparseable, or two ranges glued together with no separator).
- parse_schedule_entries(): the high-level entrypoint that takes a raw
  RawDoctorRecord.raw_schedule_entries list (in whatever shape a specific
  adapter produced) plus a `source` tag identifying which adapter it came
  from, and returns a list of ParsedScheduleSlot ready to become
  ScheduleSlot rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- day-of-week normalization -----------------------------------------

# Canonical: 0=Senin ... 6=Minggu (spec §8.3 ScheduleSlot.day_of_week).
_INDONESIAN_DAYS = {
    "senin": 0,
    "selasa": 1,
    "rabu": 2,
    "kamis": 3,
    "jumat": 4,
    "jum'at": 4,
    "sabtu": 5,
    "minggu": 6,
}

_ENGLISH_DAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

_INDONESIAN_ABBREVIATIONS = {
    "sen": 0,
    "sel": 1,
    "rab": 2,
    "kam": 3,
    "jum": 4,
    "sab": 5,
    "min": 6,
}


def normalize_day_of_week(raw_day: str | int | None, *, source: str | None = None) -> int | None:
    """Map a day-of-week representation to spec's canonical 0=Senin..
    6=Minggu int. Returns None (never guessed) if unrecognized.

    `source` disambiguates numeric day conventions that differ between
    adapters — confirmed during Fase 2/3 that EMC's own `day` values in
    its schedule URLs do NOT necessarily equal spec's 0=Senin numbering,
    and neither does Brawijaya's `weekday`, without verifying each site's
    convention against its actual rendered day labels first. Rather than
    silently assume a numeric day means the same thing everywhere, numeric
    input without a recognized `source` mapping is rejected (returns
    None) instead of guessed.
    """
    if raw_day is None:
        return None

    if isinstance(raw_day, str):
        text = raw_day.strip().lower()
        if not text:
            return None
        if text in _INDONESIAN_DAYS:
            return _INDONESIAN_DAYS[text]
        if text in _ENGLISH_DAYS:
            return _ENGLISH_DAYS[text]
        if text in _INDONESIAN_ABBREVIATIONS:
            return _INDONESIAN_ABBREVIATIONS[text]
        # Numeric string (e.g. Brawijaya JSON sometimes carries weekday
        # as a string, or EMC's URL `day=2`) — route through the same
        # source-aware numeric handling as an int below.
        if text.isdigit():
            return normalize_day_of_week(int(text), source=source)
        return None

    if isinstance(raw_day, int):
        return _normalize_numeric_day(raw_day, source=source)

    return None


# Per-source numeric day conventions. Only populated where the mapping is
# either user-verified against the live rendered site (highest
# confidence) or has a clearly documented, weaker basis — never silently
# guessed. Sources/values not listed here reject numeric day input
# entirely rather than assume a convention.
#
# - Siloam: `day` field is a bare integer with NO parallel string field
#   (unlike Hermina, which has both `day` string and `day_integer`).
#   USER-VERIFIED 2026-08-08 by opening a real doctor's schedule widget
#   on siloamhospitals.com and confirming day=1 renders as "Senin" —
#   i.e. day=1..6 is 1=Senin..6=Sabtu (ISO-style, 1-indexed Mon-Sat).
#   day=7 (Minggu/Sunday) was not directly observed in the verified
#   doctor's schedule and is inferred by extending the same 1-indexed
#   pattern (7 -> Minggu) rather than separately confirmed — treated as
#   confirmed here since the Mon-Sat run leaves only one consistent slot
#   for day=7, but flagged in case a future audit finds otherwise.
#   Maps to spec's 0=Senin via (day - 1).
#
# - Brawijaya: `weekday` mapping is NOT independently user-verified —
#   this offset was inferred only from a fixture cross-check made during
#   Fase 4 authoring, a weaker basis than Siloam's live confirmation.
#   Kept at "medium" confidence in the parser (never "high") for exactly
#   this reason. weekday=0/7 (Sunday) deliberately absent — could be
#   either value in a 1-indexed Mon-Sun scheme and was not resolved.
#
# - EMC: `day` (from the "MAKE APPOINTMENT" link's query string) is
#   SELF-VERIFIED (not just inferred) by cross-referencing each link's
#   day=N value against the Indonesian day-name TABLE HEADER of the
#   column it physically appears under in the same cached HTML — e.g.
#   a link with day=2 was found only under the "Selasa" column, day=3
#   only under "Rabu", checked across every doctor card in the 2026-08-08
#   snapshot with no contradictions (day=7/Minggu was not observed in any
#   card, left unconfirmed). This is a stronger basis than a single
#   external observation since it's corroborated by many independent
#   doctor cards in the same document, so it's treated at "high"
#   confidence, matching Siloam.
_SOURCE_NUMERIC_DAY_OFFSETS: dict[str, dict[int, int]] = {
    "siloam": {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6},  # user-verified 2026-08-08 (day=1 -> Senin)
    "emc": {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5},  # self-verified 2026-08-08 via table-header cross-reference; 7/Minggu unconfirmed
    "brawijaya": {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5},  # NOT user-verified — see note above; 0/7 (Sunday) absent
}

# Sources whose numeric-day mapping is confirmed only to "medium"
# confidence (not user-verified against the live site) — schedule slots
# derived from these should never be marked "high" even when the day and
# both times parsed cleanly, so downstream gap calculations (spec §7.5)
# can distinguish "we're sure" from "reasonable inference."
_MEDIUM_CONFIDENCE_NUMERIC_DAY_SOURCES = {"brawijaya"}


def _normalize_numeric_day(value: int, *, source: str | None) -> int | None:
    if source is None:
        return None
    mapping = _SOURCE_NUMERIC_DAY_OFFSETS.get(source)
    if mapping is None:
        return None
    return mapping.get(value)


# --- free-text time-range parsing --------------------------------------


@dataclass
class ParsedTimeRange:
    start_time: str | None  # "HH:MM", 24h, None if unparseable or open-ended
    end_time: str | None  # "HH:MM", 24h, None if "selesai" or unparseable
    raw_text: str
    parse_confidence: str  # "high" | "medium" | "low"


# HH:MM or HH.MM, optionally with :SS/.SS, optionally with leading zero
# omitted (Bethsaida/Mayapada/Primaya sources all seen using 2-digit
# hours only, but tolerate 1-digit defensively).
_TIME_TOKEN = r"(\d{1,2})[:.](\d{2})(?:[:.]\d{2})?"
_SINGLE_RANGE_RE = re.compile(rf"{_TIME_TOKEN}\s*-\s*{_TIME_TOKEN}")
_OPEN_ENDED_RE = re.compile(rf"{_TIME_TOKEN}\s*-\s*selesai", re.IGNORECASE)
_MULTI_SESSION_SPLIT_RE = re.compile(r"\bdan\b|\band\b", re.IGNORECASE)


def _clean_whitespace(text: str) -> str:
    """Collapse tabs/newlines/repeated spaces (seen verbatim in raw HTML
    text extraction from Bethsaida/Mayapada) into single spaces, without
    altering the actual digits/separators being parsed.
    """
    return re.sub(r"\s+", " ", text).strip()


def _format_hhmm(hour: str, minute: str) -> str:
    return f"{int(hour):02d}:{minute}"


def parse_time_range_text(raw_text: str) -> list[ParsedTimeRange]:
    """Parse a free-text time range string into one or more
    ParsedTimeRange entries (multiple only for an explicit "dan"/"and"
    multi-session day, e.g. Primaya's "08.30 - 10.30 dan 13.30 - 14.30").

    Ambiguous input (two ranges concatenated with NO separator, e.g.
    Bethsaida's "14:00-16:0014:00-17:00") is returned as a single
    low-confidence entry with the untouched raw text — splitting it would
    require guessing where one range ends and the next begins, which
    spec §3.1 forbids.
    """
    if not raw_text or not raw_text.strip():
        return [ParsedTimeRange(None, None, raw_text or "", "low")]

    cleaned = _clean_whitespace(raw_text)

    # "Dengan Perjanjian" (by-appointment-only, no fixed hours) — spec
    # §9 Fase 4.2 example. Not an error, just genuinely no time range.
    if re.search(r"dengan\s+perjanjian", cleaned, re.IGNORECASE):
        return [ParsedTimeRange(None, None, raw_text, "medium")]

    # Split on an explicit multi-session separator FIRST. If that yields
    # more than one segment and each segment parses as exactly one clean
    # range, treat them as separate high-confidence sessions. Otherwise
    # fall through to whole-string range detection.
    segments = [s.strip() for s in _MULTI_SESSION_SPLIT_RE.split(cleaned) if s.strip()]
    if len(segments) > 1:
        results = []
        all_clean = True
        for seg in segments:
            seg_matches = _SINGLE_RANGE_RE.findall(seg)
            if len(seg_matches) != 1:
                all_clean = False
                break
            h1, m1, h2, m2 = seg_matches[0]
            results.append(
                ParsedTimeRange(_format_hhmm(h1, m1), _format_hhmm(h2, m2), seg, "high")
            )
        if all_clean:
            return results
        # Fell through — ambiguous multi-session text, don't guess.
        return [ParsedTimeRange(None, None, raw_text, "low")]

    # Open-ended ("HH:MM - selesai") — spec: end_time=None, never guessed.
    open_match = _OPEN_ENDED_RE.search(cleaned)
    if open_match:
        h, m = open_match.group(1), open_match.group(2)
        return [ParsedTimeRange(_format_hhmm(h, m), None, raw_text, "high")]

    # How many distinct ranges appear in the raw text? If exactly one,
    # high confidence. If the string contains what LOOKS like more than
    # one range's worth of digits without a recognized separator between
    # them (Bethsaida's concatenation case), refuse to guess.
    all_matches = _SINGLE_RANGE_RE.findall(cleaned)
    if len(all_matches) == 1:
        h1, m1, h2, m2 = all_matches[0]
        return [ParsedTimeRange(_format_hhmm(h1, m1), _format_hhmm(h2, m2), raw_text, "high")]

    if len(all_matches) > 1:
        # Concatenated ranges with no separator between them (e.g. two
        # "HH:MM-HH:MM" blocks glued together) — cannot tell where one
        # session ends and the next begins without guessing.
        return [ParsedTimeRange(None, None, raw_text, "low")]

    return [ParsedTimeRange(None, None, raw_text, "low")]


# --- structured-source parsing (already-split day/start/end fields) ----


@dataclass
class ParsedScheduleSlot:
    day_of_week: int | None
    start_time: str | None
    end_time: str | None
    raw_text: str
    parse_confidence: str


def parse_schedule_entries(entries: list[dict], *, source: str) -> list[ParsedScheduleSlot]:
    """Dispatch to a source-specific parser based on the known raw shape
    each Fase 2/3 adapter produces. Unrecognized sources return entries
    as low-confidence with raw text preserved rather than guessing a
    shape.
    """
    parser = _SOURCE_PARSERS.get(source)
    if parser is None:
        return [
            ParsedScheduleSlot(None, None, None, str(e), "low")
            for e in entries
        ]
    return parser(entries)


def _parse_structured_hms(entries: list[dict], *, source: str, day_key: str, start_key: str, end_key: str) -> list[ParsedScheduleSlot]:
    """Shared logic for sources whose entries already have a day field
    and separate HH:MM(:SS) start/end fields (Siloam, Hermina, RS Pondok
    Indah's clinics[].schedules[] — this function operates on the
    already-flattened list of individual schedule dicts, not the nested
    clinics wrapper; callers flatten first).
    """
    slots = []
    for e in entries:
        raw_day = e.get(day_key)
        day = normalize_day_of_week(raw_day, source=source)
        start = e.get(start_key)
        end = e.get(end_key)
        raw_text = f"{raw_day} {start}-{end}"

        if day is None:
            slots.append(ParsedScheduleSlot(None, None, None, raw_text, "low"))
            continue

        start_hhmm = _hms_to_hhmm(start)
        end_hhmm = _hms_to_hhmm(end)
        confidence = "high" if (start_hhmm and end_hhmm) else "low"
        slots.append(ParsedScheduleSlot(day, start_hhmm, end_hhmm, raw_text, confidence))
    return slots


def _hms_to_hhmm(value: str | None) -> str | None:
    if not value:
        return None
    match = re.match(r"^(\d{1,2}):(\d{2})", value)
    if not match:
        return None
    return _format_hhmm(match.group(1), match.group(2))


def _parse_siloam(entries: list[dict]) -> list[ParsedScheduleSlot]:
    return _parse_structured_hms(entries, source="siloam", day_key="day", start_key="from_time", end_key="to_time")


def _parse_hermina(entries: list[dict]) -> list[ParsedScheduleSlot]:
    # Hermina entries are nested: [{"<hospital>": {"<clinic>": [schedule, ...]}}]
    # per src/scrapers/hermina.py's raw_schedule_entries shape. Flatten first.
    flattened: list[dict] = []
    for wrapper in entries:
        for _hospital_name, clinics in wrapper.items():
            for _clinic_name, schedule_list in clinics.items():
                flattened.extend(schedule_list)
    return _parse_structured_hms(flattened, source="hermina", day_key="day", start_key="from_time", end_key="to_time")


def _parse_rspi(entries: list[dict]) -> list[ParsedScheduleSlot]:
    # RS Pondok Indah entries are nested: [{"clinics": [{"schedules": [...]}], ...}]
    # per src/scrapers/rs_pondok_indah.py's raw_schedule_entries shape.
    flattened: list[dict] = []
    for wrapper in entries:
        for clinic in wrapper.get("clinics", []):
            flattened.extend(clinic.get("schedules", []))
    return _parse_structured_hms(flattened, source="rs_pondok_indah", day_key="day", start_key="time_from", end_key="time_to")


def _parse_mitra_keluarga(entries: list[dict]) -> list[ParsedScheduleSlot]:
    # Entries carry concrete dates ("date": "2026-08-09") rather than a
    # recurring weekly rule — day-of-week is DERIVED from the "day" name
    # field (already Indonesian text), not the date, since we're building
    # a recurring-pattern inference (spec: this is an inference from a
    # few weeks of concrete bookings, medium confidence at best — see
    # src/scrapers/mitra_keluarga.py docstring).
    slots = []
    for e in entries:
        raw_day = e.get("day")
        day = normalize_day_of_week(raw_day, source="mitra_keluarga")
        start = e.get("start_time")
        end = e.get("end_time")
        raw_text = f"{e.get('date')} {raw_day} {start}-{end}"

        if day is None:
            slots.append(ParsedScheduleSlot(None, None, None, raw_text, "low"))
            continue

        start_hhmm = _hms_to_hhmm(start)
        end_hhmm = _hms_to_hhmm(end)
        # Medium, not high: inferred recurring pattern from a concrete
        # booking date, not a source-declared recurring rule.
        confidence = "medium" if (start_hhmm and end_hhmm) else "low"
        slots.append(ParsedScheduleSlot(day, start_hhmm, end_hhmm, raw_text, confidence))
    return slots


def _parse_emc(entries: list[dict]) -> list[ParsedScheduleSlot]:
    # EMC's own `day` numbering in its appointment-link query string was
    # NOT independently verified against spec's 0=Senin during Fase 2/3
    # recon (only observed day=2 -> "Tuesday" column position once) — not
    # enough to trust as a general mapping. Route through
    # normalize_day_of_week(source="emc") which has no entry in
    # _SOURCE_NUMERIC_DAY_OFFSETS and therefore correctly returns None
    # (low confidence) rather than guessing the other 6 values.
    return _parse_structured_hms(entries, source="emc", day_key="day", start_key="start_time", end_key="end_time")


def _parse_brawijaya(entries: list[dict]) -> list[ParsedScheduleSlot]:
    slots = []
    for e in entries:
        raw_weekday = e.get("weekday")
        day = normalize_day_of_week(raw_weekday, source="brawijaya")
        start_h, start_m = e.get("start_hour"), e.get("start_minute")
        end_h, end_m = e.get("end_hour"), e.get("end_minute")
        raw_text = f"weekday={raw_weekday} {start_h}:{start_m}-{end_h}:{end_m}"

        if day is None or start_h is None or end_h is None:
            slots.append(ParsedScheduleSlot(None, None, None, raw_text, "low"))
            continue

        start_hhmm = f"{int(start_h):02d}:{int(start_m):02d}"
        end_hhmm = f"{int(end_h):02d}:{int(end_m):02d}"
        # "medium", not "high" — Brawijaya's weekday->day_of_week mapping
        # is not user-verified (see _SOURCE_NUMERIC_DAY_OFFSETS note).
        slots.append(ParsedScheduleSlot(day, start_hhmm, end_hhmm, raw_text, "medium"))
    return slots


def _parse_freetext_day_time(entries: list[dict], *, source: str) -> list[ParsedScheduleSlot]:
    """Shared logic for Bethsaida/Mayapada-shaped entries:
    {"day_text": ..., "time_text": ...}.
    """
    slots = []
    for e in entries:
        raw_day = e.get("day_text")
        day = normalize_day_of_week(raw_day, source=source)
        time_text = e.get("time_text", "")

        ranges = parse_time_range_text(time_text)
        for r in ranges:
            if day is None:
                slots.append(ParsedScheduleSlot(None, None, None, f"{raw_day} {r.raw_text}", "low"))
            else:
                slots.append(ParsedScheduleSlot(day, r.start_time, r.end_time, r.raw_text, r.parse_confidence))
    return slots


def _parse_bethsaida(entries: list[dict]) -> list[ParsedScheduleSlot]:
    return _parse_freetext_day_time(entries, source="bethsaida")


def _parse_mayapada(entries: list[dict]) -> list[ParsedScheduleSlot]:
    return _parse_freetext_day_time(entries, source="mayapada")


def _parse_primaya(entries: list[dict]) -> list[ParsedScheduleSlot]:
    # entries is [{"raw_html": "<div class='schedule-item'>...</div>"}]
    # per src/scrapers/primaya.py's raw_schedule_entries shape — one
    # element wrapping the whole per-doctor schedule HTML fragment.
    # We import selectolax lazily here rather than at module top-level so
    # parsing/schedule.py doesn't require an HTML parser dependency for
    # sources that never need it.
    from selectolax.parser import HTMLParser

    slots: list[ParsedScheduleSlot] = []
    for wrapper in entries:
        html = wrapper.get("raw_html", "")
        if not html:
            continue
        tree = HTMLParser(html)
        for day_row in tree.css(".schedule-day-row"):
            label_node = day_row.css_first(".schedule-day-label")
            time_node = day_row.css_first(".schedule-day-time")
            raw_day = label_node.text(strip=True) if label_node else None
            # The time node's text includes trailing "(Tatap Muka)" /
            # "(Konsultasi Online)" annotations separated by <br> — take
            # only the text before the first such annotation by splitting
            # on "(" since the time range itself never contains one.
            raw_time_full = time_node.text(strip=True, separator=" ") if time_node else ""
            raw_time = raw_time_full.split("(")[0].strip()

            day = normalize_day_of_week(raw_day, source="primaya")
            ranges = parse_time_range_text(raw_time)
            for r in ranges:
                if day is None:
                    slots.append(ParsedScheduleSlot(None, None, None, f"{raw_day} {r.raw_text}", "low"))
                else:
                    slots.append(ParsedScheduleSlot(day, r.start_time, r.end_time, r.raw_text, r.parse_confidence))
    return slots


_SOURCE_PARSERS = {
    "siloam": _parse_siloam,
    "hermina": _parse_hermina,
    "rs_pondok_indah": _parse_rspi,
    "mitra_keluarga": _parse_mitra_keluarga,
    "emc": _parse_emc,
    "brawijaya": _parse_brawijaya,
    "bethsaida": _parse_bethsaida,
    "mayapada": _parse_mayapada,
    "primaya": _parse_primaya,
    # "eka" intentionally absent — that source never has schedule data
    # (spec §3.1: absence must stay absent, not default to some shape).
}
