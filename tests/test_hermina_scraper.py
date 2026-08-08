"""Fase 3: Hermina adapter tests — offline, fixture-based, no network calls
(spec §14). Exercises RSC extraction (src/scrapers/_rsc_extract.py) and
Jabodetabek branch filtering against fixture data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scrapers.hermina import HerminaScraper, _is_jabodetabek_branch

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def scraper(monkeypatch) -> HerminaScraper:
    s = HerminaScraper(use_cache=False)
    listing_html = (FIXTURES / "hermina_dermatology_listing.html").read_text(encoding="utf-8")
    schedule = json.loads((FIXTURES / "hermina_doctor_schedule.json").read_text(encoding="utf-8"))

    def fake_get_html(self, url, *, hospital_slug, cache_key, params=None):
        assert "doctors/specialist" in url
        return listing_html

    def fake_get_json(self, url, *, hospital_slug, cache_key, params=None):
        assert "schedules" in url
        return schedule

    monkeypatch.setattr(HerminaScraper, "_get_html", fake_get_html)
    monkeypatch.setattr(HerminaScraper, "_get_json", fake_get_json)
    return s


def test_fetch_all_unfiltered_returns_all_three(scraper: HerminaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    assert len(records) == 3


def test_jabodetabek_filter_keeps_depok_jatinegara_and_bekasi(scraper: HerminaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Sembilan, Sp.DVE" in names  # Depok + Jatinegara
    assert "dr. Anonim Contoh Sebelas, Sp.KK" in names  # Bekasi


def test_jabodetabek_filter_excludes_arcamanik(scraper: HerminaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Sepuluh, SpKK" not in names  # Arcamanik = Bandung


def test_schedule_fetched_per_doctor(scraper: HerminaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Sembilan, Sp.DVE")
    schedule = doc.raw_payload["schedule"]
    assert "Hermina Depok" in schedule
    assert "Hermina Jatinegara" in schedule


def test_source_url_uses_doctor_slug(scraper: HerminaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Sembilan, Sp.DVE")
    assert doc.source_url == "https://herminahospitals.com/id/doctors/dr-anonim-contoh-sembilan-spdve"


def test_is_jabodetabek_branch_matching():
    assert _is_jabodetabek_branch("Hermina Depok") is True
    assert _is_jabodetabek_branch("Hermina Bekasi") is True
    assert _is_jabodetabek_branch("Hermina Arcamanik") is False
    assert _is_jabodetabek_branch("Hermina Pekalongan") is False
