"""Registry merge/dedup pipeline — Fase 1.

Pipeline:
1. Pull from Overpass API (src/registry/osm.py). Kemkes is intentionally
   skipped — see src/registry/kemkes.py docstring for the documented
   reasoning (42 national RSUP records, no ownership/class field, out of
   the user's private-hospital scope).
2. Normalize names (src/parsing/hospital_names.py) and dedup with
   rapidfuzz, threshold ~85. Borderline matches are NOT auto-merged —
   they're written to an `unresolved_duplicates` report instead (spec
   §9 Fase 1 "jangan auto-merge kasus borderline tanpa audit").
3. Infer `ownership` only where OSM `operator:type` is present (private /
   private_non_profit -> "swasta"; government / public / community ->
   "pemerintah"). Missing tag -> ownership=None (unknown), never guessed
   (spec §3.1, §3.5).
4. Join with config/hospital_preferences.yaml -> is_preferred_group /
   preferred_rank_group. No hospital is removed from the registry for
   being non-preferred (spec §9 Fase 1 point 4).
5. Persist to `hospitals` table and print the Fase 1 deliverable report
   (spec §9 Fase 1 "Deliverable").
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select

from src.config import DATA_DIR, get_hospital_preferences
from src.db import init_db, session_scope
from src.logging_setup import get_logger
from src.models import DataStatus, Hospital, SourceTier
from src.parsing.hospital_names import normalize_hospital_name
from src.registry.kemkes import fetch_kemkes_hospitals
from src.registry.manual import load_manual_hospitals
from src.registry.osm import OsmHospitalRecord, fetch_osm_hospitals

log = get_logger(__name__)

DEDUP_THRESHOLD = 85.0

# OSM operator:type -> Hospital.ownership. Anything not in this map (or
# missing entirely) stays None/unknown rather than guessed (spec §3.1).
_OWNERSHIP_MAP = {
    "private": "swasta",
    "private_non_profit": "swasta",
    "government": "pemerintah",
    "public": "pemerintah",
    "community": "pemerintah",  # community/village-run facilities grouped with public sector
}


@dataclass
class DedupCandidate:
    kept_name: str
    dropped_name: str
    score: float
    kept_source_url: str
    dropped_source_url: str


@dataclass
class UnresolvedDuplicate:
    name_a: str
    name_b: str
    score: float
    source_url_a: str
    source_url_b: str


def _infer_ownership(tags: dict) -> str | None:
    return _OWNERSHIP_MAP.get(tags.get("operator:type"))


_EARTH_RADIUS_KM = 6371.0
# Same institution is very unlikely to be represented twice more than this
# far apart in OSM (covers a large hospital campus + geocoding slop). Used
# to suppress name-only false positives like "RS Harapan" vs "RSUD Tarakan"
# that happen to share generic tokens but are clearly different places.
_MAX_DUPLICATE_DISTANCE_KM = 1.5


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * _EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _name_similarity(norm_a: str, norm_b: str) -> float:
    """Combine token-order-independent and raw character similarity so
    short/generic names (e.g. "RS Harapan", "Puskesmas Parung") sharing one
    common word don't score as high as a genuine near-duplicate. Using the
    minimum of the two metrics is intentionally conservative.
    """
    return min(fuzz.token_sort_ratio(norm_a, norm_b), fuzz.ratio(norm_a, norm_b))


def _dedup_osm_records(
    records: list[OsmHospitalRecord],
) -> tuple[list[OsmHospitalRecord], list[UnresolvedDuplicate]]:
    """Greedy dedup: for each record, compare its normalized name (and, when
    both records have coordinates, geographic distance) against already-kept
    records. Above DEDUP_THRESHOLD *and* within _MAX_DUPLICATE_DISTANCE_KM
    (or coordinates missing on either side) -> merge as alias of the
    first-seen record. Between a lower "review" band and the threshold ->
    flagged unresolved, both kept as separate records (spec: no silent
    auto-merge on borderline cases). Name similarity alone is never enough
    to merge when both records have coordinates far apart — that is a
    strong signal of two distinct hospitals with generic/similar names.
    """
    review_floor = DEDUP_THRESHOLD - 15  # 70..85 = "borderline, needs human review"

    kept: list[OsmHospitalRecord] = []
    kept_normalized: list[str] = []
    aliases: dict[int, list[str]] = {}  # index into `kept` -> alias names
    unresolved: list[UnresolvedDuplicate] = []

    named_records = [r for r in records if r.name]
    unnamed_records = [r for r in records if not r.name]
    if unnamed_records:
        log.warning("osm_unnamed_records_dropped", count=len(unnamed_records))

    for rec in named_records:
        norm = normalize_hospital_name(rec.name)
        if not norm:
            continue

        best_idx = -1
        best_score = 0.0
        for idx, existing_norm in enumerate(kept_normalized):
            score = _name_similarity(norm, existing_norm)
            if score <= best_score:
                continue

            other = kept[idx]
            if rec.lat is not None and rec.lon is not None and other.lat is not None and other.lon is not None:
                distance_km = _haversine_km(rec.lat, rec.lon, other.lat, other.lon)
                if distance_km > _MAX_DUPLICATE_DISTANCE_KM:
                    # Clearly two different places despite similar names —
                    # don't let this suppress a real match against another
                    # candidate later in the loop.
                    continue

            best_score = score
            best_idx = idx

        if best_idx >= 0 and best_score >= DEDUP_THRESHOLD:
            aliases.setdefault(best_idx, []).append(rec.name)
            continue

        if best_idx >= 0 and review_floor <= best_score < DEDUP_THRESHOLD:
            unresolved.append(
                UnresolvedDuplicate(
                    name_a=kept[best_idx].name or "",
                    name_b=rec.name,
                    score=best_score,
                    source_url_a=kept[best_idx].source_url,
                    source_url_b=rec.source_url,
                )
            )
            # Still kept as a separate record — not auto-merged.

        kept.append(rec)
        kept_normalized.append(norm)

    for idx, alias_list in aliases.items():
        kept[idx].tags["_aliases"] = alias_list  # stashed for merge step below

    return kept, unresolved


def _match_preferred_group(name: str, preferred_groups: list[str]) -> str | None:
    name_lower = name.lower()
    for group in preferred_groups:
        if group.lower() in name_lower:
            return group
    return None


def _load_preferred_group_overrides() -> dict[str, str]:
    """Manual overrides for preferred_rank_group, keyed by
    normalize_hospital_name(hospital name). Fase 4.5 pipeline testing
    surfaced real cases where a hospital's OSM `name` tag doesn't contain
    its brand's substring at all (e.g. "RS GRHA KEDOYA" is EMC's Kedoya
    branch, but has no "emc" substring for _match_preferred_group to
    find) — the substring heuristic above cannot and should not try to
    guess these; config/manual_overrides.csv (spec's Tier-3 manual
    override mechanism) is the sanctioned place to record a human-
    confirmed correction instead.

    Only entity_type="hospital", field="preferred_rank_group" rows are
    used here; other override rows (if any get added later) are ignored
    by this loader, not an error.
    """
    from src.scrapers.manual import load_manual_overrides

    overrides = {}
    for o in load_manual_overrides():
        if o.entity_type == "hospital" and o.field == "preferred_rank_group":
            overrides[o.entity_key] = o.override_value
    return overrides


def _load_duplicate_overrides() -> list[tuple[str, float, float, str, float, float]]:
    """Manual dedup markers (config/manual_overrides.csv, field=
    "duplicate_of") — see Hospital.duplicate_of_hospital_id's docstring
    for why this exists (OSM entries named only after a brand, sitting
    a few dozen meters from a fully-named branch, that survive Fase 1's
    name-similarity-first automated dedup).

    Keyed by (name, lat, lon) rather than name/name_normalized alone —
    confirmed during this investigation that TWO different real
    hospitals can share the exact string "Rumah Sakit Siloam" in OSM
    (different branches, ~6.7km apart), so name alone cannot
    disambiguate which physical row a human-reviewed pair refers to.
    entity_key/override_value are "name|lat|lon" pipe-joined strings.

    Returns a list of (dup_name, dup_lat, dup_lon, target_name,
    target_lat, target_lon) tuples for the caller to resolve into
    Hospital.id values after insert (IDs aren't known until then).
    """
    from src.scrapers.manual import load_manual_overrides

    pairs = []
    for o in load_manual_overrides():
        if o.entity_type != "hospital" or o.field != "duplicate_of":
            continue
        try:
            dup_name, dup_lat, dup_lon = o.entity_key.split("|")
            target_name, target_lat, target_lon = o.override_value.split("|")
            pairs.append(
                (dup_name, float(dup_lat), float(dup_lon), target_name, float(target_lat), float(target_lon))
            )
        except ValueError:
            log.warning("duplicate_override_malformed", entity_key=o.entity_key, override_value=o.override_value)
    return pairs


def _apply_duplicate_overrides(session) -> int:
    """Resolve _load_duplicate_overrides() pairs to Hospital.id values
    (matched by name AND coordinate, within a small tolerance for float
    round-tripping) and set duplicate_of_hospital_id. Returns the count
    successfully applied. A pair that doesn't resolve to an exact match
    on both sides is skipped with a warning, never guessed.
    """
    _COORD_TOLERANCE = 0.0005  # ~50m, enough for float round-trip slop, tight enough to avoid ambiguity

    def _find(name: str, lat: float, lon: float) -> Hospital | None:
        candidates = session.query(Hospital).filter(Hospital.name == name).all()
        for c in candidates:
            if c.lat is None or c.lon is None:
                continue
            if abs(c.lat - lat) < _COORD_TOLERANCE and abs(c.lon - lon) < _COORD_TOLERANCE:
                return c
        return None

    applied = 0
    for dup_name, dup_lat, dup_lon, target_name, target_lat, target_lon in _load_duplicate_overrides():
        dup = _find(dup_name, dup_lat, dup_lon)
        target = _find(target_name, target_lat, target_lon)
        if dup is None or target is None:
            log.warning(
                "duplicate_override_unresolved",
                dup_name=dup_name,
                dup_found=dup is not None,
                target_name=target_name,
                target_found=target is not None,
            )
            continue
        dup.duplicate_of_hospital_id = target.id
        applied += 1
    return applied


def _load_display_alias_overrides() -> list[tuple[str, float, float, str]]:
    """Manual overrides (config/manual_overrides.csv, field=
    "display_alias") for Hospital.display_alias — see that column's
    docstring in src/models.py. entity_key shape is "name|lat|lon" (same
    coordinate-qualified convention as duplicate_of, needed for the same
    reason: OSM can have more than one Hospital row sharing a name
    string), override_value is the plain alias text to show.
    """
    from src.scrapers.manual import load_manual_overrides

    entries = []
    for o in load_manual_overrides():
        if o.entity_type != "hospital" or o.field != "display_alias":
            continue
        try:
            name, lat, lon = o.entity_key.split("|")
            entries.append((name, float(lat), float(lon), o.override_value))
        except ValueError:
            log.warning("display_alias_override_malformed", entity_key=o.entity_key)
    return entries


def _apply_display_alias_overrides(session) -> int:
    """Resolve _load_display_alias_overrides() entries to Hospital rows
    (matched by name AND coordinate, same tolerance/logic as
    _apply_duplicate_overrides) and set display_alias. Returns the count
    successfully applied.
    """
    _COORD_TOLERANCE = 0.0005

    applied = 0
    for name, lat, lon, alias in _load_display_alias_overrides():
        candidates = session.query(Hospital).filter(Hospital.name == name).all()
        match = None
        for c in candidates:
            if c.lat is None or c.lon is None:
                continue
            if abs(c.lat - lat) < _COORD_TOLERANCE and abs(c.lon - lon) < _COORD_TOLERANCE:
                match = c
                break
        if match is None:
            log.warning("display_alias_override_unresolved", name=name, lat=lat, lon=lon, alias=alias)
            continue
        match.display_alias = alias
        applied += 1
    return applied


def _replace_manual_hospitals(session, preferred_groups: list[str]) -> int:
    """Replace the curated non-OSM facility rows idempotently.

    These rows represent complete facilities missing from the current OSM
    snapshot, not aliases for an existing row.  Deleting through the ORM (as
    opposed to a bulk delete) preserves relationship cascades when a registry
    rebuild is run after a previous doctor pipeline.
    """
    for existing in session.query(Hospital).filter(
        Hospital.source_tier == SourceTier.TIER_3_MANUAL
    ).all():
        session.delete(existing)
    session.flush()

    records = load_manual_hospitals()
    for rec in records:
        is_preferred = rec.group in preferred_groups
        session.add(
            Hospital(
                name=rec.name,
                name_normalized=normalize_hospital_name(rec.name),
                aliases_json="[]",
                group=rec.group,
                ownership=rec.ownership,
                hospital_class=None,
                hospital_type=rec.hospital_type,
                address=rec.address,
                kota_kab=rec.kota_kab,
                lat=rec.lat,
                lon=rec.lon,
                geocode_source=rec.geocode_source,
                geocode_confidence=rec.geocode_confidence,
                website=rec.website,
                source_url=rec.source_url,
                source_tier=SourceTier.TIER_3_MANUAL,
                scraped_at=rec.verified_at,
                data_status=DataStatus.MANUAL,
                is_preferred_group=is_preferred,
                preferred_rank_group=rec.group if is_preferred else None,
                has_dermatology_service=None,
            )
        )
    session.flush()
    return len(records)


def run_registry_pipeline(source: str = "all") -> None:
    init_db()
    prefs = get_hospital_preferences()

    osm_records: list[OsmHospitalRecord] = []
    if source in ("overpass", "all"):
        osm_records = fetch_osm_hospitals()
    if source in ("kemkes", "all"):
        fetch_kemkes_hospitals()  # intentionally returns [] — see module docstring

    if not osm_records:
        log.error("registry_pipeline_no_data", source=source)
        print("Tidak ada data registry yang berhasil diambil. Lihat log di atas.")
        return

    deduped, unresolved = _dedup_osm_records(osm_records)
    group_overrides = _load_preferred_group_overrides()

    n_with_coords = 0
    n_preferred = 0
    n_private = 0
    n_public = 0
    n_ownership_unknown = 0
    n_manual = 0

    with session_scope() as session:
        # Idempotent re-run: clear previously-loaded OSM-sourced rows so
        # re-running fetch-registry doesn't create duplicate hospitals.
        session.query(Hospital).filter(Hospital.source_tier == SourceTier.TIER_1_OFFICIAL).filter(
            Hospital.geocode_source == "osm_overpass"
        ).delete(synchronize_session=False)

        for rec in deduped:
            name_normalized = normalize_hospital_name(rec.name or "")
            ownership = _infer_ownership(rec.tags)
            preferred_group = _match_preferred_group(rec.name or "", prefs.preferred_groups)
            if name_normalized in group_overrides:
                # Tier-3 manual override wins over the substring heuristic
                # — see _load_preferred_group_overrides() docstring.
                preferred_group = group_overrides[name_normalized]
            addr = rec.address_raw

            address_parts = [p for p in [addr.get("full")] if p]
            if not address_parts:
                street_bits = [addr.get("street"), addr.get("housenumber")]
                street = " ".join(b for b in street_bits if b)
                address_parts = [p for p in [street, addr.get("kota_kab")] if p]
            address = ", ".join(address_parts) if address_parts else None

            has_coords = rec.lat is not None and rec.lon is not None
            if has_coords:
                n_with_coords += 1
            if preferred_group:
                n_preferred += 1
            if ownership == "swasta":
                n_private += 1
            elif ownership == "pemerintah":
                n_public += 1
            else:
                n_ownership_unknown += 1

            data_status = DataStatus.PARTIAL if has_coords else DataStatus.UNKNOWN

            hospital = Hospital(
                name=rec.name or "(unnamed)",
                name_normalized=name_normalized,
                aliases_json=json.dumps(rec.tags.get("_aliases", []), ensure_ascii=False),
                group=preferred_group,
                ownership=ownership,
                hospital_class=None,  # not available from OSM; Fase 2/3 or manual override may fill this
                hospital_type=None,
                address=address,
                kelurahan=addr.get("kelurahan"),
                kecamatan=addr.get("kecamatan"),
                kota_kab=addr.get("kota_kab"),
                lat=rec.lat,
                lon=rec.lon,
                geocode_source="osm_overpass" if has_coords else None,
                geocode_confidence="medium" if has_coords else None,
                website=rec.website,
                source_url=rec.source_url,
                source_tier=SourceTier.TIER_1_OFFICIAL,
                scraped_at=rec.scraped_at,
                data_status=data_status,
                is_preferred_group=preferred_group is not None,
                preferred_rank_group=preferred_group,
                has_dermatology_service=None,  # unknown until Fase 2/3 scraping
            )
            session.add(hospital)

        n_manual = _replace_manual_hospitals(session, prefs.preferred_groups)
        for rec in load_manual_hospitals():
            n_with_coords += 1
            n_preferred += int(rec.group in prefs.preferred_groups)
            if rec.ownership == "swasta":
                n_private += 1
            elif rec.ownership == "pemerintah":
                n_public += 1
            else:
                n_ownership_unknown += 1

        session.flush()  # populate .id for every newly-inserted Hospital before resolving overrides below
        n_duplicates_marked = _apply_duplicate_overrides(session)
        n_aliases_applied = _apply_display_alias_overrides(session)

        total = len(deduped) + n_manual

    # --- Deliverable report (spec §9 Fase 1) ---
    report_lines = [
        "=== Fase 1 — Hospital Master Registry: Deliverable ===",
        f"Total RS di master registry: {total}",
        f"  - swasta (ownership diketahui): {n_private}",
        f"  - pemerintah (ownership diketahui): {n_public}",
        f"  - ownership unknown (tidak ada tag operator:type di OSM): {n_ownership_unknown}",
        f"Preferred-private (cocok dengan config/hospital_preferences.yaml): {n_preferred}",
        f"Dengan koordinat: {n_with_coords} / {total}",
        f"Tanpa koordinat: {total - n_with_coords} / {total}",
        f"Kandidat duplicate unresolved (skor {DEDUP_THRESHOLD - 15:.0f}-{DEDUP_THRESHOLD:.0f}, tidak di-auto-merge): {len(unresolved)}",
        f"Ditandai duplicate_of via manual override (config/manual_overrides.csv): {n_duplicates_marked}",
        f"Diberi display_alias via manual override (config/manual_overrides.csv): {n_aliases_applied}",
        f"RS terverifikasi yang ditambahkan karena tidak ada di snapshot OSM: {n_manual}",
        "",
        "CATATAN PENTING:",
        "- Untuk row OSM, ownership='swasta' HANYA dari tag operator:type, TIDAK LENGKAP.",
        "  Row manual terverifikasi dapat membawa ownership dari sumber resminya.",
        "  Sebagian besar RS swasta target lain (Eka, Siloam, dst.) kemungkinan besar",
        "  masih 'unknown' sampai dikonfirmasi manual atau lewat scraping situs RS",
        "  langsung di Fase 2/3. Filter 'RS swasta' saat ini TIDAK BOLEH dipakai",
        "  sebagai daftar final -- gunakan is_preferred_group + config manual",
        "  sebagai pelengkap.",
        "- hospital_class tidak tersedia dari sumber ini (None untuk semua record).",
        "- rs.kemkes.go.id di-skip (lihat src/registry/kemkes.py untuk alasan).",
    ]
    report = "\n".join(report_lines)
    print(report)

    if unresolved:
        print(f"\n--- {len(unresolved)} kandidat duplicate unresolved (contoh, maks 20) ---")
        for u in unresolved[:20]:
            print(f"  [{u.score:.0f}] {u.name_a!r}  <->  {u.name_b!r}")

    _write_reports(deduped, unresolved, report)


def _write_reports(
    deduped: list[OsmHospitalRecord],
    unresolved: list[UnresolvedDuplicate],
    report_text: str,
) -> None:
    out_dir = DATA_DIR / "processed" / "registry_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()

    (out_dir / f"{stamp}_summary.txt").write_text(report_text, encoding="utf-8")

    unresolved_path = out_dir / f"{stamp}_unresolved_duplicates.json"
    unresolved_path.write_text(
        json.dumps([u.__dict__ for u in unresolved], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sample = deduped[:20]
    sample_path = out_dir / f"{stamp}_sample_20.json"
    sample_path.write_text(
        json.dumps(
            [
                {
                    "name": r.name,
                    "lat": r.lat,
                    "lon": r.lon,
                    "address": r.address_raw,
                    "operator_type": r.tags.get("operator:type"),
                    "website": r.website,
                    "source_url": r.source_url,
                }
                for r in sample
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log.info(
        "registry_reports_written",
        summary=str(out_dir / f"{stamp}_summary.txt"),
        unresolved=str(unresolved_path),
        sample=str(sample_path),
    )
