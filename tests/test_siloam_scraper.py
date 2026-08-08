"""Fase 2: Siloam pilot adapter tests — offline, fixture-based, no network
calls (spec §14). BaseScraper._get_json is monkeypatched to serve fixture
JSON instead of hitting the network, so these tests exercise the adapter's
own logic (dermatology speciality resolution, Jabodetabek filtering,
schedule fetch wiring) in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scrapers.siloam import (
    DERMATOLOGY_SPECIALITY_ID,
    SiloamScraper,
    _is_jabodetabek_hospital,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def scraper(monkeypatch) -> SiloamScraper:
    s = SiloamScraper(use_cache=False)

    specialities = _load_fixture("siloam_specialities.json")
    listing = _load_fixture("siloam_doctors_listing.json")
    schedules_by_doctor_id = {
        "aaaa1111-0000-0000-0000-000000000001": _load_fixture("siloam_doctor_schedule.json"),
        "aaaa1111-0000-0000-0000-000000000002": _load_fixture("siloam_doctor_schedule_heart_hospital.json"),
        "aaaa1111-0000-0000-0000-000000000003": _load_fixture("siloam_doctor_schedule_surabaya.json"),
    }

    def fake_get_json(self, url, *, hospital_slug, cache_key, params=None):
        if "specialities" in url:
            return specialities
        if "doctors/withavailability" in url:
            return listing
        if "schedules/withavailability/doctor" in url:
            doctor_id = url.rsplit("/", 1)[-1]
            return schedules_by_doctor_id[doctor_id]
        raise AssertionError(f"unexpected URL in test: {url}")

    monkeypatch.setattr(SiloamScraper, "_get_json", fake_get_json)
    return s


def test_discover_dermatology_speciality_id_matches_fixture(scraper: SiloamScraper):
    found_id = scraper.discover_dermatology_speciality_id()
    assert found_id == DERMATOLOGY_SPECIALITY_ID


def test_fetch_all_dermatology_doctors_returns_raw_records(scraper: SiloamScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    assert len(records) == 3
    assert all(r.raw_name.startswith("dr. Anonim") for r in records)
    assert all(r.raw_schedule_entries for r in records)  # schedule was fetched per doctor


def test_jabodetabek_filter_keeps_kebon_jeruk_and_heart_hospital(scraper: SiloamScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    # "Siloam Hospitals Kebon Jeruk" matches by name; "Siloam Heart Hospital"
    # has no city in its name but its schedule's hospital_address (fixture:
    # Kebon Jeruk, Jakarta Barat) should still qualify it via address match.
    assert "dr. Anonim Contoh Satu, SpDVE" in names
    assert "dr. Anonim Contoh Dua, Sp.KK" in names


def test_jabodetabek_filter_excludes_surabaya(scraper: SiloamScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Tiga, SpDVE" not in names


def test_raw_payload_preserved_for_audit(scraper: SiloamScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    for r in records:
        assert "listing" in r.raw_payload
        assert "availability" in r.raw_payload
        assert r.raw_payload["listing"]["doctor_id"]


def test_is_jabodetabek_hospital_name_match():
    assert _is_jabodetabek_hospital("Siloam Hospitals Kebon Jeruk", None) is True
    assert _is_jabodetabek_hospital("Siloam Hospitals Surabaya", None) is False


def test_is_jabodetabek_hospital_address_fallback():
    # No name hint, but address clearly Jabodetabek — this is the exact
    # "Siloam Heart Hospital" case found during reconnaissance.
    assert _is_jabodetabek_hospital("Siloam Heart Hospital", "Jl. Cinere Raya No. 19, Depok") is True
    assert _is_jabodetabek_hospital("Siloam Heart Hospital", "Jl. Somewhere, Manado") is False


def test_is_jabodetabek_hospital_neither_name_nor_address_matches():
    assert _is_jabodetabek_hospital("Siloam Hospitals Denpasar", "Jl. Sunset Road, Bali") is False
