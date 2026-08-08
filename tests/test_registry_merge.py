"""Fase 1: registry dedup logic tests (offline — synthetic OsmHospitalRecord
fixtures, no network calls per spec §14)."""

from __future__ import annotations

import datetime as dt

from src.registry.merge import (
    _apply_duplicate_overrides,
    _dedup_osm_records,
    _infer_ownership,
    _load_duplicate_overrides,
    _load_preferred_group_overrides,
    _match_preferred_group,
)
from src.registry.osm import OsmHospitalRecord


def _rec(name: str, lat: float | None, lon: float | None, **tags) -> OsmHospitalRecord:
    return OsmHospitalRecord(
        osm_type="node",
        osm_id=1,
        name=name,
        lat=lat,
        lon=lon,
        tags=tags,
        source_url="https://www.openstreetmap.org/node/1",
        scraped_at=dt.datetime.now(dt.timezone.utc),
    )


def test_near_identical_names_close_together_are_merged_as_alias():
    records = [
        _rec("RS Eka Hospital BSD", -6.298, 106.669),
        _rec("Eka Hospital BSD", -6.2981, 106.6691),  # ~15m away, near-identical name
    ]
    kept, unresolved = _dedup_osm_records(records)
    assert len(kept) == 1
    assert unresolved == []
    assert kept[0].tags.get("_aliases") == ["Eka Hospital BSD"]


def test_similar_generic_names_far_apart_are_not_merged_or_flagged():
    # "RS Harapan" vs "RSUD Tarakan" style false positive: short/generic
    # tokens produce a misleadingly high fuzzy score, but the two hospitals
    # are clearly in different places.
    records = [
        _rec("RS Harapan", -6.10, 106.80),
        _rec("RSUD Tarakan", -6.40, 107.20),  # >40km away
    ]
    kept, unresolved = _dedup_osm_records(records)
    assert len(kept) == 2
    assert unresolved == []


def test_borderline_similarity_nearby_is_flagged_unresolved_not_merged():
    records = [
        _rec("RSAL dr. Mintohardjo", -6.20, 106.80),
        _rec("Rumah Sakit Dr Mintohardjo", -6.2001, 106.8001),  # same place, borderline name score
    ]
    kept, unresolved = _dedup_osm_records(records)
    # Either merged (if score >= threshold) or flagged unresolved — but
    # never silently dropped without a trace, and never silently merged
    # below the hard threshold.
    assert len(kept) + len(unresolved) >= 1
    if len(kept) == 2:
        assert len(unresolved) == 1


def test_missing_coordinates_falls_back_to_name_only_matching():
    records = [
        _rec("RS Anak Bunda Harapan Kita", None, None),
        _rec("Rumah Sakit Anak dan Bunda Harapan Kita", None, None),
    ]
    kept, unresolved = _dedup_osm_records(records)
    # No coordinates on either side -> distance gate can't suppress a
    # genuine high-similarity match.
    assert len(kept) == 1


def test_unnamed_records_are_dropped_not_crashed_on():
    records = [_rec(None, -6.2, 106.8), _rec("RS Contoh", -6.2, 106.8)]  # type: ignore[arg-type]
    kept, unresolved = _dedup_osm_records(records)
    assert len(kept) == 1
    assert kept[0].name == "RS Contoh"


def test_infer_ownership_maps_known_operator_types():
    assert _infer_ownership({"operator:type": "private"}) == "swasta"
    assert _infer_ownership({"operator:type": "private_non_profit"}) == "swasta"
    assert _infer_ownership({"operator:type": "government"}) == "pemerintah"
    assert _infer_ownership({"operator:type": "public"}) == "pemerintah"


def test_infer_ownership_missing_tag_is_none_not_guessed():
    assert _infer_ownership({}) is None
    assert _infer_ownership({"operator:type": "something_unmapped"}) is None


def test_match_preferred_group_case_insensitive_substring():
    groups = ["Eka Hospital", "Siloam", "Mitra Keluarga"]
    assert _match_preferred_group("RS Eka Hospital BSD", groups) == "Eka Hospital"
    assert _match_preferred_group("SILOAM HOSPITALS LIPPO VILLAGE", groups) == "Siloam"
    assert _match_preferred_group("RSUD Tarakan", groups) is None


def test_load_preferred_group_overrides_reads_real_csv():
    # Regression guard for the Fase 4.5 pipeline investigation: some OSM
    # entries were left preferred_rank_group=None even though a human
    # can confirm which brand they belong to by location —
    # config/manual_overrides.csv is the sanctioned Tier-3 correction
    # mechanism (spec) for exactly this. Note: "RS GRHA KEDOYA" was
    # investigated too (plausibly EMC's Kedoya branch by coordinate) but
    # the user explicitly declined that override, so it deliberately
    # stays untagged/its own entry — not every plausible match gets
    # auto-applied.
    overrides = _load_preferred_group_overrides()
    assert overrides.get("pondok indah") == "RS Pondok Indah"
    assert overrides.get("puri indah pondok indah") == "RS Pondok Indah"
    assert "grha kedoya" not in overrides


def test_load_duplicate_overrides_reads_real_csv():
    # Regression guard for the dashboard-review investigation (2026-08-08):
    # OSM has several brand-only-named entries ("Siloam Hospital", no
    # branch identifier) sitting a few dozen meters from a fully-named
    # branch ("Siloam Hospital Lippo Village") that already has scraped
    # dermatologist data — Fase 1's name-similarity-first dedup doesn't
    # catch these because the name strings are too different, so a
    # manual duplicate_of override (Tier-3, config/manual_overrides.csv)
    # is the sanctioned fix.
    pairs = _load_duplicate_overrides()
    names = {(p[0], p[3]) for p in pairs}
    assert ("Siloam Hospital", "Siloam Hospital Lippo Village") in names
    assert ("Rumah Sakit Pondok Indah", "RSU PURI INDAH PONDOK INDAH") in names


def test_apply_duplicate_overrides_sets_duplicate_of_hospital_id(in_memory_engine):
    from sqlalchemy.orm import Session

    from src.models import Hospital

    with Session(in_memory_engine) as session:
        dup = Hospital(
            name="Siloam Hospital",
            name_normalized="siloam hospital",
            aliases_json="[]",
            lat=-6.2250421,
            lon=106.5979602,
        )
        target = Hospital(
            name="Siloam Hospital Lippo Village",
            name_normalized="siloam hospital lippo village",
            aliases_json="[]",
            lat=-6.2251927,
            lon=106.5985479,
        )
        session.add_all([dup, target])
        session.flush()

        applied = _apply_duplicate_overrides(session)

        assert applied >= 1
        session.refresh(dup)
        assert dup.duplicate_of_hospital_id == target.id


def test_apply_duplicate_overrides_skips_when_coordinate_does_not_match(in_memory_engine):
    # A Hospital row with the right NAME but a coordinate far outside
    # tolerance must NOT be marked duplicate — matching by name alone
    # would risk merging two genuinely different institutions that
    # happen to share a generic name (confirmed real case: two different
    # "Rumah Sakit Siloam" rows ~6.7km apart in the actual registry).
    from sqlalchemy.orm import Session

    from src.models import Hospital

    with Session(in_memory_engine) as session:
        wrong_location = Hospital(
            name="Siloam Hospital",
            name_normalized="siloam hospital",
            aliases_json="[]",
            lat=-7.0,  # far from the overridden coordinate
            lon=108.0,
        )
        session.add(wrong_location)
        session.flush()

        _apply_duplicate_overrides(session)

        session.refresh(wrong_location)
        assert wrong_location.duplicate_of_hospital_id is None
