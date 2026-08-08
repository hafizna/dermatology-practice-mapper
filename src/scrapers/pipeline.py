"""Scraper -> parsing -> persistence pipeline — bridges Fase 2/3 (raw
scraping) and Fase 4 (parsing) into the `doctors`/`schedule_slots` tables,
so Fase 5 (geocoding) and Fase 6 (coverage metrics) have something to
read.

This is not an explicitly numbered phase in PROJECT_SPEC.md, but is
required by the product architecture in spec §4 ("Registry -> Doctor &
Schedule Collection -> Parsing/Normalization/Dedup -> Core Practice
Opportunity") — parsed doctor/schedule data has to land somewhere before
metrics can be computed on it.

Key design decisions:
- Matching a scraper's hospital name (e.g. "Siloam Hospitals ASRI") to
  the Fase 1 registry's Hospital row (e.g. "RS SILOAM ASRI", sourced from
  OSM tags with much less consistent formatting) needs its own fuzzy
  matching pass — a DIFFERENT matching problem than Fase 1's
  within-source OSM dedup (we're matching ACROSS sources here), so it
  gets its own function (match_hospital_by_name) even though both build
  on the same normalize_hospital_name() building block.
- Each adapter's RawDoctorRecord.raw_payload has an adapter-specific
  shape (documented per-adapter in src/scrapers/*.py) — there is no
  single generic way to pull "the hospital name" out of it. Rather than
  one function that tries to guess the shape, each source gets its own
  small extractor in _HOSPITAL_NAME_EXTRACTORS, mirroring the
  per-source dispatch pattern already used in src/parsing/schedule.py's
  _SOURCE_PARSERS.

Known registry gaps (last reviewed 2026-08-09 via
scripts/run_pipeline_all.py against all 12 adapters): a number of
scraper-reported branch names have NO plausible match in the Fase 1
OSM-derived registry at all — these are genuine gaps, not matching bugs,
confirmed by searching the registry for every plausible substring
(city/area name, brand fragment) and finding nothing at that location.
Left as documented hospital_unmatched counts rather than guessed:
Siloam Hospitals Agora Cempaka Putih/Bogor, Siloam Specialist Center
Senayan, Siloam Heart Hospital, Hermina Ciledug, Mitra Keluarga Grand
Wisata, Brawijaya Hospital - Antasari/Tangerang, RS Sari Asih Bintaro,
RSIA Eka Hospital PIK/Pluit, EKA Hospital Bekasi/Depok/MT Haryono/
Permata Hijau, several Primaya branches, and Mayapada Hospital Jakarta
Selatan/Jakarta Timur. Confirmed non-obvious links are handled narrowly
in config/manual_overrides.csv: MRCCC Semanggi, EMC Grha Kedoya,
Bethsaida Gading Serpong, Mayapada Tangerang, Brawijaya Depok, and the
non-preferred UKRIDA Hospital records reported by Primaya.
"""

from __future__ import annotations

import datetime as dt

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.logging_setup import get_logger
from src.models import ConfidenceLevel, Doctor, Hospital, ParseConfidence, ScheduleSlot, SourceTier
from src.parsing.credentials import is_dermatologist_credential
from src.parsing.hospital_names import normalize_hospital_name
from src.parsing.names import normalize_person_key
from src.parsing.schedule import parse_schedule_entries, parse_schedule_entries_by_hospital
from src.scrapers.base import RawDoctorRecord

log = get_logger(__name__)

HOSPITAL_MATCH_THRESHOLD = 80.0

_CONFIDENCE_MAP = {
    "high": ParseConfidence.HIGH,
    "medium": ParseConfidence.MEDIUM,
    "low": ParseConfidence.LOW,
}


# --- per-source hospital-name extraction ---------------------------------
#
# Each function pulls the raw hospital name string(s) a doctor practices
# at out of that adapter's raw_payload shape. Some sources report exactly
# one hospital per record (most adapters); a few report several (a
# doctor practicing at multiple branches of the same group) — those
# return a list so the caller can create one Doctor+ScheduleSlot set per
# branch.


def _hospital_names_siloam(record: RawDoctorRecord) -> list[str]:
    availability = record.raw_payload.get("availability", [])
    return [a.get("hospital_name", "") for a in availability if a.get("hospital_name")]


def _hospital_names_mitra_keluarga(record: RawDoctorRecord) -> list[str]:
    clinic = record.raw_payload.get("clinic") or {}
    name = clinic.get("name", "")
    return [name] if name else []


def _hospital_names_hermina(record: RawDoctorRecord) -> list[str]:
    schedule = record.raw_payload.get("schedule", {})
    names = [name for name in schedule.keys() if name]
    if names:
        return names

    # Some doctors are present in the official speciality listing while
    # the separate schedule endpoint returns an empty dict. The listing
    # still carries structured practice locations, so use those exact
    # official names rather than losing the hospital association.
    attrs = record.raw_payload.get("listing_entry", {}).get("attributes", {})
    hospital_names = [
        hospital.get("name", "")
        for hospital in attrs.get("hospitals", [])
        if isinstance(hospital, dict) and hospital.get("name")
    ]
    if hospital_names:
        return list(dict.fromkeys(hospital_names))
    return list(dict.fromkeys(name for name in attrs.get("practic_locations", []) if name))


def _hospital_names_emc(record: RawDoctorRecord) -> list[str]:
    branch = record.raw_payload.get("card", {}).get("branch", "")
    return [branch] if branch else []


def _hospital_names_mayapada(record: RawDoctorRecord) -> list[str]:
    card = record.raw_payload.get("card", {})
    hospital = card.get("hospital", "")
    return [hospital] if hospital else []


def _hospital_names_bethsaida(record: RawDoctorRecord) -> list[str]:
    card = record.raw_payload.get("card", {})
    branch = card.get("branch", "")
    return [branch] if branch else []


def _hospital_names_rs_pondok_indah(record: RawDoctorRecord) -> list[str]:
    doc = record.raw_payload.get("doctor", {})
    names = []
    for sched in doc.get("doctor_schedule", []):
        h = sched.get("hospital", "")
        if h:
            names.append(h)
    return names


def _hospital_names_brawijaya(record: RawDoctorRecord) -> list[str]:
    name = record.raw_payload.get("branch_name", "")
    return [name] if name else []


def _hospital_names_sari_asih(record: RawDoctorRecord) -> list[str]:
    name = record.raw_payload.get("card", {}).get("branch", "")
    return [name] if name else []


def _hospital_names_rs_premier(record: RawDoctorRecord) -> list[str]:
    name = record.raw_payload.get("branch_name", "")
    return [name] if name else []


def _hospital_names_columbia_asia(record: RawDoctorRecord) -> list[str]:
    name = record.raw_payload.get("branch_name", "")
    return [name] if name else []


def _hospital_names_radjak(record: RawDoctorRecord) -> list[str]:
    name = record.raw_payload.get("branch_name", "")
    return [name] if name else []


def _hospital_names_primaya(record: RawDoctorRecord) -> list[str]:
    # card.location is a bare city/area name (e.g. "Bekasi"), NOT a full
    # hospital name — unusable for hospital-row matching. The real branch
    # name(s) live inside each ".schedule-item" block's
    # ".schedule-hospital" div in the doctor's schedule HTML (e.g.
    # "Primaya Hospital Bekasi Timur") — confirmed in Fase 4.5 pipeline
    # testing this is where the actual matchable name is. Reuse the
    # schedule parser's hospital-scoped extraction (its dict keys ARE the
    # branch names) so this stays in sync with however that HTML is
    # walked, rather than re-implementing the selectolax traversal here.
    by_hospital = parse_schedule_entries_by_hospital(record.raw_schedule_entries, source="primaya") or {}
    if by_hospital:
        return list(by_hospital.keys())

    # Doctor-scoped verified fallback for an empty schedule response. This
    # is deliberately keyed by source URL: globally mapping a bare card
    # location such as "Bekasi" would conflate several Primaya branches.
    if record.source_url in _DOCTOR_HOSPITAL_OVERRIDES:
        return [_DOCTOR_HOSPITAL_OVERRIDES[record.source_url]]

    # Fallback: no schedule HTML at all (confirmed real case — some
    # Primaya doctors' schedule_html is empty). Bare city name is all
    # that's left; keep it rather than dropping the record, since
    # unmatched-by-city is still a documented, visible outcome (spec
    # §3.1) rather than silently discarding the doctor.
    card = record.raw_payload.get("card", {})
    location = card.get("location", "")
    return [location] if location else []


def _load_doctor_hospital_overrides() -> dict[str, str]:
    from src.scrapers.manual import load_manual_overrides

    return {
        override.entity_key: override.override_value
        for override in load_manual_overrides()
        if override.entity_type == "doctor" and override.field == "hospital_name"
    }


_DOCTOR_HOSPITAL_OVERRIDES = _load_doctor_hospital_overrides()


def _hospital_names_eka(record: RawDoctorRecord) -> list[str]:
    card = record.raw_payload.get("card", {})
    location = card.get("location", "")
    # Eka's location can be a comma-joined multi-branch string (e.g.
    # "RSIA Eka Hospital PIK, RSIA Eka Hospital Pluit") — split it.
    return [part.strip() for part in location.split(",") if part.strip()]


_HOSPITAL_NAME_EXTRACTORS = {
    "siloam": _hospital_names_siloam,
    "mitra_keluarga": _hospital_names_mitra_keluarga,
    "hermina": _hospital_names_hermina,
    "emc": _hospital_names_emc,
    "mayapada": _hospital_names_mayapada,
    "bethsaida": _hospital_names_bethsaida,
    "rs_pondok_indah": _hospital_names_rs_pondok_indah,
    "brawijaya": _hospital_names_brawijaya,
    "sari_asih": _hospital_names_sari_asih,
    "rs_premier": _hospital_names_rs_premier,
    "columbia_asia": _hospital_names_columbia_asia,
    "radjak": _hospital_names_radjak,
    "primaya": _hospital_names_primaya,
    "eka": _hospital_names_eka,
}


def extract_hospital_names(record: RawDoctorRecord, *, source: str) -> list[str]:
    """Return every raw hospital-name string this record's doctor
    practices at, per that source's raw_payload shape. Empty list (never
    a guess) if the source has no registered extractor or the payload
    doesn't contain what's expected.
    """
    extractor = _HOSPITAL_NAME_EXTRACTORS.get(source)
    if extractor is None:
        return []
    try:
        return extractor(record)
    except (AttributeError, TypeError, KeyError):
        log.warning("pipeline_hospital_name_extraction_failed", source=source, raw_name=record.raw_name)
        return []


# --- hospital matching -----------------------------------------------------


def _load_hospital_name_alias_overrides() -> dict[str, str]:
    """Manual overrides mapping a scraper-reported hospital name straight
    to a registry Hospital.name_normalized value, bypassing fuzzy
    matching entirely. For confirmed-by-a-human name pairs that fuzzy
    matching structurally cannot get right — e.g. compound place names
    reported in swapped word order ("RS Pondok Indah - Puri Indah" vs
    OSM's "RSU Puri Indah Pondok Indah"): token_sort_ratio scores these
    100 (order-insensitive) but plain ratio only ~65 (order-sensitive),
    and match_hospital_by_name deliberately takes min(the two) to guard
    against a DIFFERENT failure mode (unrelated same-token-bag names) —
    loosening that threshold generally would risk new false positives
    elsewhere, so a targeted alias override is the correct fix for this
    specific confirmed pair (spec's Tier-3 manual override mechanism).

    Keyed by entity_type="hospital", field="hospital_name_alias" rows in
    config/manual_overrides.csv, entity_key = the scraper-reported raw
    name, override_value = the target Hospital's raw name (normalized
    here for the lookup). Rows whose override_value contains "|" (the
    coordinate-qualified "name|lat|lon" shape — see
    _load_hospital_coord_alias_overrides()) are skipped here; those go
    through the coordinate-aware lookup instead since a bare name can't
    disambiguate when OSM has more than one Hospital row sharing that
    exact name string.
    """
    from src.scrapers.manual import load_manual_overrides

    aliases = {}
    for o in load_manual_overrides():
        if o.entity_type == "hospital" and o.field == "hospital_name_alias" and "|" not in o.override_value:
            aliases[o.entity_key] = normalize_hospital_name(o.override_value)
    return aliases


def _load_hospital_coord_alias_overrides() -> dict[str, tuple[str, float, float]]:
    """Coordinate-qualified variant of _load_hospital_name_alias_overrides()
    — needed when OSM has TWO (or more) Hospital rows sharing the exact
    same name string, so a bare-name alias can't say which physical row
    a scraper-reported name refers to. Real case: OSM tags two entirely
    different hospitals both "Rumah Sakit Siloam" (one is MRCCC Siloam
    Semanggi in South Jakarta, the other a duplicate of RS Siloam Kebon
    Jeruk in West Jakarta, ~7km apart) — a name-only override_value
    would match whichever "Rumah Sakit Siloam" row match_hospital_by_name
    happens to see first, not necessarily the right one.

    override_value shape: "name|lat|lon" (pipe-joined, same convention as
    _load_duplicate_overrides() in src/registry/merge.py).
    """
    from src.scrapers.manual import load_manual_overrides

    aliases = {}
    for o in load_manual_overrides():
        if o.entity_type == "hospital" and o.field == "hospital_name_alias" and "|" in o.override_value:
            try:
                name, lat, lon = o.override_value.split("|")
                aliases[o.entity_key] = (name, float(lat), float(lon))
            except ValueError:
                log.warning("hospital_coord_alias_malformed", entity_key=o.entity_key, override_value=o.override_value)
    return aliases


_HOSPITAL_NAME_ALIAS_OVERRIDES = _load_hospital_name_alias_overrides()
_HOSPITAL_COORD_ALIAS_OVERRIDES = _load_hospital_coord_alias_overrides()

_COORD_ALIAS_TOLERANCE = 0.0005  # ~50m, matches src/registry/merge.py's duplicate-override tolerance


def _resolve_coord_alias(session: Session, raw_hospital_name: str) -> Hospital | None:
    if raw_hospital_name not in _HOSPITAL_COORD_ALIAS_OVERRIDES:
        return None
    name, lat, lon = _HOSPITAL_COORD_ALIAS_OVERRIDES[raw_hospital_name]
    candidates = session.execute(select(Hospital).where(Hospital.name == name)).scalars().all()
    for c in candidates:
        if c.lat is None or c.lon is None:
            continue
        if abs(c.lat - lat) < _COORD_ALIAS_TOLERANCE and abs(c.lon - lon) < _COORD_ALIAS_TOLERANCE:
            return c
    return None


def match_hospital_by_name(
    session: Session, raw_hospital_name: str, *, preferred_group: str | None = None
) -> Hospital | None:
    """Find the best-matching Hospital row for a scraper-reported hospital
    name string. Returns None (never a low-confidence guess persisted
    silently) if nothing clears HOSPITAL_MATCH_THRESHOLD — callers must
    treat that as "unmatched", not "matched to something".

    Restricting the candidate pool to `preferred_group` when given
    (almost always available, since every adapter is written for one
    specific hospital group) keeps this fast and avoids cross-group
    false positives — spec §9 Fase 1 dedup already established these
    hospital names ARE ambiguous/similar across unrelated groups (e.g.
    generic "RS Harapan"-style names), so narrowing the search space
    first is not just a performance optimization but a correctness one.
    """
    coord_alias_match = _resolve_coord_alias(session, raw_hospital_name)
    if coord_alias_match is not None:
        return coord_alias_match

    if raw_hospital_name in _HOSPITAL_NAME_ALIAS_OVERRIDES:
        normalized_target = _HOSPITAL_NAME_ALIAS_OVERRIDES[raw_hospital_name]
        # A manual alias is an explicit human-confirmed cross-source link,
        # so it must bypass the adapter's preferred-group candidate filter.
        # Real case: Primaya's official output includes two doctors whose
        # practice location is the separate, non-preferred "UKRIDA Hospital
        # (Jakarta Barat)".  Its confirmed registry target intentionally has
        # preferred_rank_group=None; applying preferred_group="Primaya"
        # after resolving the alias made the override impossible to match.
        # Coordinate-qualified aliases already bypass the group restriction
        # via _resolve_coord_alias() above.  Bare aliases are required to do
        # the same; duplicate raw target names must use the coordinate form.
        exact_candidates = session.execute(
            select(Hospital).where(Hospital.name_normalized == normalized_target)
        ).scalars().all()
        if len(exact_candidates) == 1:
            return exact_candidates[0]
        if len(exact_candidates) > 1:
            log.warning(
                "hospital_name_alias_target_ambiguous",
                raw_hospital_name=raw_hospital_name,
                normalized_target=normalized_target,
                candidate_count=len(exact_candidates),
            )
            return None
    else:
        normalized_target = normalize_hospital_name(raw_hospital_name)
    if not normalized_target:
        return None

    query = select(Hospital)
    if preferred_group:
        query = query.where(Hospital.preferred_rank_group == preferred_group)
    candidates = session.execute(query).scalars().all()

    best_hospital: Hospital | None = None
    best_score = 0.0
    for hospital in candidates:
        candidate_norm = hospital.name_normalized
        score = min(
            fuzz.token_sort_ratio(normalized_target, candidate_norm),
            fuzz.ratio(normalized_target, candidate_norm),
        )
        if score > best_score:
            best_score = score
            best_hospital = hospital

    if best_score >= HOSPITAL_MATCH_THRESHOLD:
        return best_hospital
    return None


# --- persistence -----------------------------------------------------------


def persist_doctor_record(
    session: Session,
    record: RawDoctorRecord,
    *,
    hospital: Hospital,
    source: str,
    source_tier: SourceTier = SourceTier.TIER_1_OFFICIAL,
    raw_hospital_name: str | None = None,
) -> Doctor | None:
    """Persist one already-hospital-resolved RawDoctorRecord as a Doctor
    row (+ its ScheduleSlot rows), after Fase 4.1 credential validation.
    Returns None (creates nothing) if the record doesn't validate as a
    dermatologist — this is the credential parser acting as a
    validator/cross-check even for Tier 1 sources already filtered by
    speciality (spec Appendix A "Implikasi desain penting").

    raw_hospital_name: the specific branch name this call is persisting
    for (as extracted by extract_hospital_names(), BEFORE fuzzy-matching
    to `hospital`). Required to get branch-scoped schedule slots for
    multi-branch sources (Hermina/RSPI/Primaya) via
    parse_schedule_entries_by_hospital() — without it, or for sources not
    in that dispatch table, falls back to parse_schedule_entries()'s
    flat/pooled result, which is already correct for single-branch-per-
    record sources.
    """
    credential_text = record.raw_credentials_text or record.raw_name
    if not is_dermatologist_credential(credential_text):
        log.warning(
            "pipeline_doctor_failed_credential_check",
            source=source,
            raw_name=record.raw_name,
            hospital=hospital.name,
        )
        return None

    now = dt.datetime.now(dt.timezone.utc)
    person_key = normalize_person_key(record.raw_name)

    doctor = Doctor(
        hospital_id=hospital.id,
        raw_name=record.raw_name,
        clean_name=person_key.title() if person_key else None,
        normalized_person_key=person_key or None,
        credentials_json="[]",  # structured credential extraction is a future refinement; raw_name retains the full string
        is_dermatologist=True,
        source_url=record.source_url or None,
        source_tier=source_tier,
        scraped_at=now,
    )
    session.add(doctor)
    session.flush()  # populate doctor.id for the ScheduleSlot rows below

    by_hospital = parse_schedule_entries_by_hospital(record.raw_schedule_entries, source=source)
    if by_hospital is not None and raw_hospital_name is not None:
        # Branch-aware source: only this branch's own slots, not every
        # branch's slots pooled together (see docstring).
        parsed_slots = by_hospital.get(raw_hospital_name, [])
    else:
        parsed_slots = parse_schedule_entries(record.raw_schedule_entries, source=source)

    for slot in parsed_slots:
        session.add(
            ScheduleSlot(
                doctor_id=doctor.id,
                hospital_id=hospital.id,
                day_of_week=slot.day_of_week,
                start_time=slot.start_time,
                end_time=slot.end_time,
                raw_text=slot.raw_text,
                parse_confidence=_CONFIDENCE_MAP[slot.parse_confidence],
                source_url=record.source_url or None,
                scraped_at=now,
            )
        )

    log.info(
        "pipeline_doctor_persisted",
        source=source,
        raw_name=record.raw_name,
        hospital=hospital.name,
        n_schedule_slots=len(parsed_slots),
    )
    return doctor


def persist_raw_doctor_records(
    session: Session,
    records: list[RawDoctorRecord],
    *,
    source: str,
    preferred_group: str | None,
    source_tier: SourceTier = SourceTier.TIER_1_OFFICIAL,
) -> dict:
    """Full pipeline for a batch of RawDoctorRecord from one adapter's
    fetch_all_dermatology_doctors(): extract hospital name(s) per record,
    fuzzy-match each to a registry Hospital, and persist. A record naming
    multiple hospitals (a doctor practicing at several branches) produces
    one Doctor+ScheduleSlot set per matched branch — spec §8.2 treats
    Doctor rows as hospital-scoped, so "the same person at 2 hospitals" is
    2 Doctor rows sharing one normalized_person_key, not 1 row.

    Never raises on a single bad/unmatched record — one malformed or
    unmatchable record must not abort an entire scrape's worth of
    otherwise-good data (spec §3.1's "don't fake data" principle extends
    to the pipeline's own robustness: a partial failure must be visible
    in the summary counts, not silently swallowed OR allowed to crash
    everything else).
    """
    summary = {
        "total_records": len(records),
        "not_dermatologist": 0,
        "hospital_unmatched": 0,
        "doctors_created": 0,
        "schedule_slots_created": 0,
        "unmatched_hospital_names": [],
    }

    for record in records:
        if not is_dermatologist_credential(record.raw_credentials_text or record.raw_name):
            summary["not_dermatologist"] += 1
            continue

        hospital_names = extract_hospital_names(record, source=source)
        if not hospital_names:
            summary["hospital_unmatched"] += 1
            summary["unmatched_hospital_names"].append(f"{record.raw_name} (no hospital name extracted)")
            continue

        any_matched = False
        hospital_ids_already_persisted: set[int] = set()
        for raw_hospital_name in hospital_names:
            hospital = match_hospital_by_name(session, raw_hospital_name, preferred_group=preferred_group)
            if hospital is None:
                summary["unmatched_hospital_names"].append(raw_hospital_name)
                continue

            if hospital.id in hospital_ids_already_persisted:
                # Same physical hospital reported under 2+ different raw
                # names within ONE doctor record (real case, confirmed
                # 2026-08-09: Siloam's own API reports one doctor's
                # availability twice for the same Lippo Village branch,
                # once as "Siloam Hospitals Lippo Village" and once as
                # "Rumah Sakit Umum Siloam Lippo Village" — both resolve
                # to the same registry Hospital row). Persisting a second
                # Doctor row here would silently double the hospital's
                # doctor count and doctor-hours — skip, not a new branch.
                any_matched = True  # still counts as a successful match, just not a NEW doctor row
                continue
            hospital_ids_already_persisted.add(hospital.id)

            doctor = persist_doctor_record(
                session,
                record,
                hospital=hospital,
                source=source,
                source_tier=source_tier,
                raw_hospital_name=raw_hospital_name,
            )
            if doctor is not None:
                any_matched = True
                summary["doctors_created"] += 1
                summary["schedule_slots_created"] += len(doctor.schedule_slots)

        if not any_matched and hospital_names:
            summary["hospital_unmatched"] += 1

    return summary
