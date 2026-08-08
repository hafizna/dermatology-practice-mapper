"""Fase 3: Mitra Keluarga adapter tests — offline, fixture-based, no
network calls (spec §14).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scrapers.mitra_keluarga import MitraKeluargaScraper, _is_jabodetabek_clinic

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def scraper(monkeypatch) -> MitraKeluargaScraper:
    s = MitraKeluargaScraper(use_cache=False)
    listing = json.loads((FIXTURES / "mitra_keluarga_doctors_listing.json").read_text(encoding="utf-8"))

    def fake_get_json(self, url, *, hospital_slug, cache_key, params=None):
        assert "doctor/data" in url
        return listing

    monkeypatch.setattr(MitraKeluargaScraper, "_get_json", fake_get_json)
    return s


def test_fetch_all_returns_all_five_when_unfiltered(scraper: MitraKeluargaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    # 5 total in fixture, minus 1 with clinic=null (skipped, no clinic to
    # even evaluate) = 4 returned regardless of Jabodetabek filter.
    assert len(records) == 4


def test_jabodetabek_filter_keeps_kemayoran_and_bekasi(scraper: MitraKeluargaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Empat, SpDVE" in names  # Kemayoran
    assert "dr. Anonim Contoh Lima, Sp.KK" in names  # Bekasi Timur


def test_jabodetabek_filter_excludes_surabaya_and_tegal(scraper: MitraKeluargaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Enam, SpDV" not in names  # Surabaya
    assert "dr. Anonim Contoh Tujuh, SpDVE" not in names  # Tegal


def test_null_clinic_entry_is_skipped_not_crashed_on(scraper: MitraKeluargaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Delapan, Sp.KK" not in names


def test_schedule_entries_preserved_raw(scraper: MitraKeluargaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    kemayoran_doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Empat, SpDVE")
    assert len(kemayoran_doc.raw_schedule_entries) == 3
    assert kemayoran_doc.raw_schedule_entries[0]["day"] == "Minggu"


def test_source_url_uses_doctor_slug(scraper: MitraKeluargaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    kemayoran_doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Empat, SpDVE")
    assert kemayoran_doc.source_url == "https://www.mitrakeluarga.com/dokter/dr-anonim-contoh-empat-spdve"


def test_is_jabodetabek_clinic_name_matching():
    assert _is_jabodetabek_clinic("Mitra Keluarga Kemayoran") is True
    assert _is_jabodetabek_clinic("Mitra Keluarga Bekasi Timur") is True
    assert _is_jabodetabek_clinic("Mitra Keluarga Surabaya") is False
    assert _is_jabodetabek_clinic("Mitra Keluarga Tegal") is False
