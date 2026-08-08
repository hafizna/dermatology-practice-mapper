"""Fase 3: Bethsaida adapter tests — offline, fixture-based, no network
calls (spec §14). Exercises the "doctor-list-schedule" card+schedule
container parsing (same structural pattern found in EMC — likely a shared
CMS template).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scrapers.bethsaida import BethsaidaScraper, _is_jabodetabek_branch, _parse_doctor_cards

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def scraper(monkeypatch) -> BethsaidaScraper:
    s = BethsaidaScraper(use_cache=False)
    html = (FIXTURES / "bethsaida_dermatology_search.html").read_text(encoding="utf-8")

    def fake_get_html(self, url, *, hospital_slug, cache_key, params=None):
        assert params.get("hospital") == "_alllocation_"
        assert params.get("speciality") == "258"
        return html

    monkeypatch.setattr(BethsaidaScraper, "_get_html", fake_get_html)
    return s


def test_fetch_all_unfiltered_returns_both(scraper: BethsaidaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    assert len(records) == 2


def test_jabodetabek_filter_keeps_gading_serpong(scraper: BethsaidaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duapuluhtiga, Sp. KK" in names


def test_jabodetabek_filter_excludes_serang(scraper: BethsaidaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duapuluhempat, Sp. DV" not in names


def test_schedule_entries_extracted(scraper: BethsaidaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Duapuluhtiga, Sp. KK")
    assert len(doc.raw_schedule_entries) == 2
    assert doc.raw_schedule_entries[0]["day_text"] == "Senin"
    assert "14:00" in doc.raw_schedule_entries[0]["time_text"]


def test_parse_doctor_cards_extracts_name_branch_url():
    html = (FIXTURES / "bethsaida_dermatology_search.html").read_text(encoding="utf-8")
    cards = _parse_doctor_cards(html)
    assert len(cards) == 2
    assert cards[0]["name"] == "dr. Anonim Contoh Duapuluhtiga, Sp. KK"
    assert cards[0]["branch"] == "Bethsaida Gading Serpong"
    assert "detaildokter" in cards[0]["detail_url"]


def test_is_jabodetabek_branch_matching():
    assert _is_jabodetabek_branch("Bethsaida Gading Serpong") is True
    assert _is_jabodetabek_branch("Bethsaida Serang") is False
