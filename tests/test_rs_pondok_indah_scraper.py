"""Fase 3: RS Pondok Indah adapter tests — offline, fixture-based, no
network calls (spec §14). This source has the cleanest API of all
adapters in the project: explicit pagination.count/total_page (no
Hermina/Primaya-style ambiguity) and fully structured schedule data in
one response.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scrapers.rs_pondok_indah import RsPondokIndahScraper, _is_jabodetabek_branch

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def scraper(monkeypatch) -> RsPondokIndahScraper:
    s = RsPondokIndahScraper(use_cache=False)
    payload = json.loads((FIXTURES / "rspi_doctors_master.json").read_text(encoding="utf-8"))

    def fake_get_json(self, url, *, hospital_slug, cache_key, params=None):
        assert "doctors/master" in url
        return payload

    monkeypatch.setattr(RsPondokIndahScraper, "_get_json", fake_get_json)
    return s


def test_content_language_header_set_on_init():
    s = RsPondokIndahScraper(use_cache=False)
    assert s._headers.get("content-language") == "id"


def test_fetch_all_unfiltered_returns_all_three(scraper: RsPondokIndahScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    assert len(records) == 3


def test_jabodetabek_filter_keeps_puri_indah(scraper: RsPondokIndahScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duapuluhlima, Sp. D.V.E, FINSDV" in names


def test_jabodetabek_filter_excludes_surabaya_only_doctor(scraper: RsPondokIndahScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duapuluhenam, Sp. D.V.E" not in names


def test_multi_branch_doctor_kept_if_any_branch_is_jabodetabek(scraper: RsPondokIndahScraper):
    # Doctor 3 practices at Bintaro Jaya (Jabodetabek) AND Pondok Indah
    # (also Jabodetabek in this fixture) -- kept either way, but this
    # also documents the "any branch matches" semantics for a doctor
    # with genuinely mixed branches.
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duapuluhtujuh, Sp.DVE" in names


def test_schedule_entries_preserved_structured(scraper: RsPondokIndahScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Duapuluhlima, Sp. D.V.E, FINSDV")
    schedules = doc.raw_schedule_entries[0]["clinics"][0]["schedules"]
    assert len(schedules) == 2
    assert schedules[0]["day"] == "Monday"
    assert schedules[0]["time_from"] == "14:00:00"
    assert schedules[0]["time_to"] == "20:00:00"


def test_source_url_uses_doctor_code(scraper: RsPondokIndahScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Duapuluhlima, Sp. D.V.E, FINSDV")
    assert doc.source_url == "https://www.rspondokindah.co.id/id/doctor/D9001"


def test_is_jabodetabek_branch_matching():
    assert _is_jabodetabek_branch("RS Pondok Indah - Puri Indah") is True
    assert _is_jabodetabek_branch("RS Pondok Indah - Bintaro Jaya") is True
    assert _is_jabodetabek_branch("RS Pondok Indah - Pondok Indah") is True
    assert _is_jabodetabek_branch("RS Pondok Indah - Surabaya") is False
