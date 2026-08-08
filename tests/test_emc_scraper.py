"""Fase 3: EMC adapter tests — offline, fixture-based, no network calls
(spec §14). EMC is unusual among the adapters in this project: schedule
data is embedded directly in the listing page's appointment-link query
strings, no per-doctor request needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scrapers.emc import EmcScraper, _is_jabodetabek_branch, _parse_appointment_link, _parse_doctor_cards

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def scraper(monkeypatch) -> EmcScraper:
    s = EmcScraper(use_cache=False)
    html = (FIXTURES / "emc_dermatology_listing.html").read_text(encoding="utf-8")

    def fake_get_html(self, url, *, hospital_slug, cache_key, params=None):
        assert "specialities/kulit-dan-kelamin" in url
        return html

    monkeypatch.setattr(EmcScraper, "_get_html", fake_get_html)
    return s


def test_fetch_all_unfiltered_returns_both(scraper: EmcScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    assert len(records) == 2


def test_jabodetabek_filter_keeps_alam_sutera(scraper: EmcScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Delapanbelas, Sp.KK" in names


def test_jabodetabek_filter_excludes_semarang(scraper: EmcScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Sembilanbelas, Sp.DVE" not in names


def test_schedule_parsed_from_appointment_links_not_text(scraper: EmcScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Delapanbelas, Sp.KK")
    assert len(doc.raw_schedule_entries) == 2
    entry = doc.raw_schedule_entries[0]
    assert entry["day"] == "2"
    assert entry["start_time"] == "17:30:00"
    assert entry["end_time"] == "20:00:00"
    assert entry["hospital_slug"] == "alam-sutera"


def test_parse_doctor_cards_extracts_name_and_branch():
    html = (FIXTURES / "emc_dermatology_listing.html").read_text(encoding="utf-8")
    cards = _parse_doctor_cards(html)
    assert len(cards) == 2
    assert cards[0]["name"] == "dr. Anonim Contoh Delapanbelas, Sp.KK"
    assert "Alam Sutera" in cards[0]["branch"]


def test_parse_appointment_link_extracts_day_time():
    href = (
        "https://www.emc.id/id/doctor-schedule?hospital=alam-sutera"
        "&doctor=x&day=2&start_time=17%3A30%3A00&end_time=20%3A00%3A00"
        "&time_text=17%3A30%20-%2020%3A00"
    )
    result = _parse_appointment_link(href)
    assert result == {
        "hospital_slug": "alam-sutera",
        "day": "2",
        "start_time": "17:30:00",
        "end_time": "20:00:00",
        "time_text": "17:30 - 20:00",
    }


def test_parse_appointment_link_returns_none_for_malformed_link():
    assert _parse_appointment_link("https://www.emc.id/id/some-other-page") is None


def test_is_jabodetabek_branch_matching():
    assert _is_jabodetabek_branch("RS EMC Alam Sutera") is True
    assert _is_jabodetabek_branch("RS EMC Cibitung") is True
    assert _is_jabodetabek_branch("RS EMC Semarang") is False
