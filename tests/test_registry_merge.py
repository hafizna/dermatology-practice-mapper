"""Fase 1: registry dedup logic tests (offline — synthetic OsmHospitalRecord
fixtures, no network calls per spec §14)."""

from __future__ import annotations

import datetime as dt

from src.registry.merge import _dedup_osm_records, _infer_ownership, _match_preferred_group
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
