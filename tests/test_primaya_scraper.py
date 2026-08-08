"""Fase 3: Primaya adapter tests — offline, fixture-based, no network calls
(spec §14). Exercises the not_in-based cumulative pagination (with an
explicit regression test for the "full_results_count is remaining, not
total" bug found during the first live run) and HTML-fragment doctor-card
parsing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scrapers.primaya import PrimayaScraper, _is_jabodetabek_location, _parse_doctor_cards

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def scraper(monkeypatch) -> PrimayaScraper:
    s = PrimayaScraper(use_cache=False)
    call0 = json.loads((FIXTURES / "primaya_listing_call0.json").read_text(encoding="utf-8"))
    call1 = json.loads((FIXTURES / "primaya_listing_call1.json").read_text(encoding="utf-8"))
    schedule_html = (FIXTURES / "primaya_doctor_schedule.html").read_text(encoding="utf-8")

    calls_made = []

    def fake_post_json(self, url, *, hospital_slug, cache_key, data):
        if data.get("action") == "ajaxsearchpro_search":
            calls_made.append(data)
            call_index = len(calls_made) - 1
            return call1 if call_index >= 1 else call0
        if data.get("action") == "ph_doctor_get_doctor":
            return {"status": "success", "message": "", "data": schedule_html}
        raise AssertionError(f"unexpected action: {data.get('action')}")

    monkeypatch.setattr(PrimayaScraper, "_post_json", fake_post_json)
    s._test_calls_made = calls_made  # type: ignore[attr-defined]
    return s


def test_pagination_walks_until_full_count_reaches_zero(scraper: PrimayaScraper):
    # Regression test: call0 has full_results_count=3 (2 returned so far,
    # 1 remaining), call1 has full_results_count=0 (done). A cumulative
    # "len(all) >= full_count" check would have wrongly stopped after
    # call0 (2 >= 3 is False, so it wouldn't even in this exact case --
    # but the real bug was len(all_cards) exceeding a SHRINKING full_count
    # on a later call). The correct behavior walks until full_count <= 0.
    cards = scraper._fetch_dermatology_listing()
    assert len(cards) == 3


def test_not_in_parameter_accumulates_across_calls(scraper: PrimayaScraper):
    scraper._fetch_dermatology_listing()
    calls = scraper._test_calls_made  # type: ignore[attr-defined]
    assert len(calls) == 2
    assert "not_in" not in calls[0]["options"]
    assert "not_in[pagepost][0]=50001" in calls[1]["options"]
    assert "not_in[pagepost][1]=50002" in calls[1]["options"]
    assert "not_in_count=2" in calls[1]["options"]


def test_jabodetabek_filter_keeps_bekasi_and_tangerang(scraper: PrimayaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Limabelas, Sp.KK" in names  # Bekasi
    assert "dr. Anonim Contoh Tujuhbelas, Sp.DVE" in names  # Tangerang


def test_jabodetabek_filter_excludes_semarang(scraper: PrimayaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Enambelas, Sp.DV" not in names


def test_schedule_html_fragment_preserved_raw(scraper: PrimayaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Limabelas, Sp.KK")
    assert "Primaya Hospital Bekasi Timur" in doc.raw_schedule_entries[0]["raw_html"]


def test_parse_doctor_cards_extracts_post_id_name_location():
    html = (FIXTURES / "primaya_listing_call0.json").read_text(encoding="utf-8")
    payload = json.loads(html)
    cards = _parse_doctor_cards(payload["html"])
    assert len(cards) == 2
    assert cards[0]["post_id"] == "50001"
    assert cards[0]["name"] == "dr. Anonim Contoh Limabelas, Sp.KK"
    assert cards[0]["location"] == "Bekasi"


def test_is_jabodetabek_location_matching():
    assert _is_jabodetabek_location("Bekasi") is True
    assert _is_jabodetabek_location("Jakarta Barat") is True
    assert _is_jabodetabek_location("Tangerang") is True
    assert _is_jabodetabek_location("Semarang") is False
    assert _is_jabodetabek_location("Makassar") is False
