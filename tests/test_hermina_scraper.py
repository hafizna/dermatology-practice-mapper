"""Fase 3: Hermina adapter tests — offline, fixture-based, no network calls
(spec §14). Uses the paginated `/api/v1/public/doctors?speciality_id=...`
API (the corrected source of truth after the first RSC-embedded listing
page was found to return an implausibly incomplete result set).
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
    page1 = json.loads((FIXTURES / "hermina_doctors_listing_page1.json").read_text(encoding="utf-8"))
    page2 = json.loads((FIXTURES / "hermina_doctors_listing_page2.json").read_text(encoding="utf-8"))
    schedule = json.loads((FIXTURES / "hermina_doctor_schedule.json").read_text(encoding="utf-8"))

    def fake_get_json(self, url, *, hospital_slug, cache_key, params=None):
        if "doctors/" in url and "schedules" in url:
            return schedule
        if url.endswith("/api/v1/public/doctors"):
            page = params.get("page", 1) if params else 1
            return page1 if page == 1 else page2
        raise AssertionError(f"unexpected URL in test: {url} params={params}")

    monkeypatch.setattr(HerminaScraper, "_get_json", fake_get_json)
    return s


def test_pagination_walks_all_pages(scraper: HerminaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    # page1 has 2 entries, page2 has 1 entry -> 3 total across both pages.
    assert len(records) == 3


def test_jabodetabek_filter_keeps_depok_jatinegara_and_ciledug(scraper: HerminaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Sembilan, Sp.DVE" in names  # Depok + Jatinegara
    assert "dr. Anonim Contoh Sebelas, Sp.KK" in names  # Ciledug (page 2!)


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
    assert _is_jabodetabek_branch("Hermina Ciledug") is True
    assert _is_jabodetabek_branch("Hermina Serpong") is True
    assert _is_jabodetabek_branch("RS Hermina Galaxy") is True
    assert _is_jabodetabek_branch("Hermina Arcamanik") is False
    assert _is_jabodetabek_branch("Hermina Pekalongan") is False
