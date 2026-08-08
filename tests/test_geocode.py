"""Fase 5: geocoding fallback + spatial-integrity audit tests.

Offline only (spec §14) — geocode_address()'s Nominatim HTTP call is
mocked via monkeypatching httpx.get, never a real network request.
"""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy.orm import Session

from src.enrich.geocode import (
    GeocodeResult,
    _in_jabodetabek_bbox,
    geocode_address,
    geocode_missing_hospitals,
    run_spatial_integrity_audit,
)
from src.models import ConfidenceLevel, Hospital


@pytest.fixture()
def db_session(in_memory_engine):
    with Session(in_memory_engine) as session:
        yield session


def _make_hospital(session, name, *, lat=None, lon=None, confidence=None, address=None) -> Hospital:
    h = Hospital(
        name=name,
        name_normalized=name.lower(),
        aliases_json="[]",
        lat=lat,
        lon=lon,
        geocode_confidence=confidence,
        address=address,
    )
    session.add(h)
    session.flush()
    return h


class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json_body = json_body
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_body


# --- geocode_address -------------------------------------------------


def test_geocode_address_empty_returns_unknown_no_guess():
    result = geocode_address("")
    assert result.lat is None
    assert result.lon is None
    assert result.confidence == ConfidenceLevel.UNKNOWN


def test_geocode_address_no_results_returns_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr("src.enrich.geocode._CACHE_DIR", tmp_path)
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse([]))
    result = geocode_address("Jalan Tidak Ada, Nowhere", use_cache=False)
    assert result.lat is None
    assert result.confidence == ConfidenceLevel.UNKNOWN


def test_geocode_address_precise_result_is_medium_confidence(monkeypatch, tmp_path):
    monkeypatch.setattr("src.enrich.geocode._CACHE_DIR", tmp_path)
    fake_result = [
        {
            "lat": "-6.2088",
            "lon": "106.8456",
            "display_name": "RS Contoh, Jalan Sudirman, Jakarta",
            "addresstype": "hospital",
        }
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(fake_result))
    result = geocode_address("RS Contoh, Jalan Sudirman, Jakarta", use_cache=False)
    assert result.lat == pytest.approx(-6.2088)
    assert result.lon == pytest.approx(106.8456)
    assert result.confidence == ConfidenceLevel.MEDIUM


def test_geocode_address_administrative_area_result_is_low_confidence(monkeypatch, tmp_path):
    # Real spec concern: "jangan menggunakan koordinat centroid kecamatan
    # seolah koordinat RS presisi" — a result that resolved to a
    # city/suburb/administrative level must be explicitly flagged LOW,
    # not silently treated as building-precise.
    monkeypatch.setattr("src.enrich.geocode._CACHE_DIR", tmp_path)
    fake_result = [
        {
            "lat": "-6.2",
            "lon": "106.8",
            "display_name": "Kecamatan Menteng, Jakarta Pusat",
            "addresstype": "suburb",
        }
    ]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(fake_result))
    result = geocode_address("some vague address in Menteng", use_cache=False)
    assert result.lat is not None  # coordinate IS returned...
    assert result.confidence == ConfidenceLevel.LOW  # ...but explicitly flagged low-precision
    assert result.note is not None


def test_geocode_address_uses_cache_on_second_call(monkeypatch, tmp_path):
    monkeypatch.setattr("src.enrich.geocode._CACHE_DIR", tmp_path)
    call_count = {"n": 0}

    def fake_get(*a, **k):
        call_count["n"] += 1
        return _FakeResponse([{"lat": "-6.2", "lon": "106.8", "display_name": "x", "addresstype": "hospital"}])

    monkeypatch.setattr(httpx, "get", fake_get)
    geocode_address("RS Contoh Cache", use_cache=True)
    geocode_address("RS Contoh Cache", use_cache=True)
    assert call_count["n"] == 1  # second call served from cache, no new HTTP request


def test_geocode_address_request_failure_returns_unknown_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr("src.enrich.geocode._CACHE_DIR", tmp_path)

    def raise_error(*a, **k):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(httpx, "get", raise_error)
    result = geocode_address("RS Yang Gagal", use_cache=False)
    assert result.lat is None
    assert result.confidence == ConfidenceLevel.UNKNOWN


# --- geocode_missing_hospitals -----------------------------------------


def test_geocode_missing_hospitals_skips_when_none_missing(db_session):
    _make_hospital(db_session, "RS Sudah Ada Koordinat", lat=-6.2, lon=106.8)
    summary = geocode_missing_hospitals(db_session)
    assert summary["total_missing"] == 0
    assert summary["geocoded"] == 0


def test_geocode_missing_hospitals_skips_no_address_without_guessing(db_session):
    _make_hospital(db_session, "RS Tanpa Alamat", lat=None, lon=None, address=None)
    summary = geocode_missing_hospitals(db_session)
    assert summary["total_missing"] == 1
    assert summary["still_missing"] == 1
    assert summary["geocoded"] == 0


def test_geocode_missing_hospitals_fills_coordinate_and_marks_source(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr("src.enrich.geocode._CACHE_DIR", tmp_path)
    fake_result = [{"lat": "-6.3", "lon": "106.9", "display_name": "x", "addresstype": "hospital"}]
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResponse(fake_result))

    h = _make_hospital(db_session, "RS Baru", lat=None, lon=None, address="Jalan Baru No. 1, Bekasi")
    summary = geocode_missing_hospitals(db_session, use_cache=False)
    assert summary["geocoded"] == 1
    assert h.lat == pytest.approx(-6.3)
    assert h.geocode_source == "nominatim_fallback"
    assert h.geocode_confidence == ConfidenceLevel.MEDIUM


# --- spatial integrity audit --------------------------------------------


def test_in_jabodetabek_bbox():
    assert _in_jabodetabek_bbox(-6.2, 106.8) is True  # Jakarta
    assert _in_jabodetabek_bbox(-7.8, 110.4) is False  # Yogyakarta, clearly outside


def test_audit_flags_out_of_bbox_coordinate(db_session):
    _make_hospital(db_session, "RS Salah Kota", lat=-7.8, lon=110.4)  # Yogyakarta coords
    report = run_spatial_integrity_audit(db_session)
    assert len(report["out_of_bbox"]) == 1
    assert report["out_of_bbox"][0]["name"] == "RS Salah Kota"


def test_audit_flags_missing_coordinate(db_session):
    _make_hospital(db_session, "RS Tanpa Koordinat", lat=None, lon=None)
    report = run_spatial_integrity_audit(db_session)
    assert "RS Tanpa Koordinat" in report["missing_coordinate"]


def test_audit_flags_exact_duplicate_coordinates(db_session):
    _make_hospital(db_session, "RS A", lat=-6.2, lon=106.8)
    _make_hospital(db_session, "RS B", lat=-6.2, lon=106.8)
    report = run_spatial_integrity_audit(db_session)
    assert len(report["exact_duplicate_coordinate_groups"]) == 1
    group = report["exact_duplicate_coordinate_groups"][0]
    assert set(group["hospitals"]) == {"RS A", "RS B"}


def test_audit_clean_coordinate_keeps_medium_confidence(db_session):
    _make_hospital(db_session, "RS Normal", lat=-6.2, lon=106.8)
    report = run_spatial_integrity_audit(db_session)
    assert report["out_of_bbox"] == []
    assert report["exact_duplicate_coordinate_groups"] == []
    assert report["confidence_counts"].get("medium") == 1


def test_audit_total_hospitals_matches_count(db_session):
    _make_hospital(db_session, "RS 1", lat=-6.2, lon=106.8)
    _make_hospital(db_session, "RS 2", lat=None, lon=None)
    report = run_spatial_integrity_audit(db_session)
    assert report["total_hospitals"] == 2
