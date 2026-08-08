"""Fase 3: Mayapada adapter tests — offline, fixture-based, no network
calls (spec §14). Exercises conventional page-number pagination
(cross-checked directly against a real page=5-of-5 during recon, unlike
Hermina's implicit/broken pagination) and per-doctor schedule-table
parsing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.scrapers.mayapada import (
    MayapadaScraper,
    _find_max_page,
    _is_jabodetabek_branch,
    _parse_listing_cards,
    _parse_schedule_table,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def scraper(monkeypatch) -> MayapadaScraper:
    s = MayapadaScraper(use_cache=False)
    page1 = (FIXTURES / "mayapada_listing_page1.html").read_text(encoding="utf-8")
    page2 = (FIXTURES / "mayapada_listing_page2.html").read_text(encoding="utf-8")
    detail = (FIXTURES / "mayapada_doctor_detail.html").read_text(encoding="utf-8")

    def fake_get_html(self, url, *, hospital_slug, cache_key, params=None):
        if "find-doctor/show" in url:
            page = params.get("page", 1) if params else 1
            return page1 if page == 1 else page2
        if "find-doctor/detail" in url:
            return detail
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(MayapadaScraper, "_get_html", fake_get_html)
    return s


def test_pagination_walks_both_pages(scraper: MayapadaScraper):
    cards = scraper._fetch_dermatology_listing()
    assert len(cards) == 3


def test_jabodetabek_filter_keeps_jakarta_and_tangerang(scraper: MayapadaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duapuluh, Sp.DV" in names  # Jakarta Selatan
    assert "dr. Anonim Contoh Duapuluhdua, Sp.DVE" in names  # Tangerang


def test_jabodetabek_filter_excludes_surabaya(scraper: MayapadaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duapuluhsatu, Sp.KK" not in names


def test_schedule_fetched_per_doctor(scraper: MayapadaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Duapuluh, Sp.DV")
    assert len(doc.raw_schedule_entries) == 2
    assert doc.raw_schedule_entries[0]["day_text"] == "Monday"
    assert doc.raw_schedule_entries[0]["time_text"] == "13:00 - 19:00 WIB"


def test_find_max_page_reads_pagination_links():
    page1 = (FIXTURES / "mayapada_listing_page1.html").read_text(encoding="utf-8")
    assert _find_max_page(page1) == 2


def test_find_max_page_defaults_to_one_without_pagination():
    assert _find_max_page("<html><body>no pagination here</body></html>") == 1


def test_parse_listing_cards_extracts_name_hospital_url():
    page1 = (FIXTURES / "mayapada_listing_page1.html").read_text(encoding="utf-8")
    cards = _parse_listing_cards(page1)
    assert len(cards) == 2
    assert cards[0]["name"] == "dr. Anonim Contoh Duapuluh, Sp.DV"
    assert cards[0]["hospital"] == "Mayapada Hospital Jakarta Selatan"
    assert "find-doctor/detail" in cards[0]["detail_url"]


def test_parse_listing_cards_dedups_responsive_desktop_mobile_duplicate():
    # Real pages render each card twice (desktop d-md-block + mobile
    # d-md-none wrappers) — confirmed 2026-08-08 by finding a doctor slug
    # 4x (2 doctors x 2 wrappers) in one real listing page's raw HTML.
    html = """
    <section id="doctor_list">
      <div class="col-lg-6 d-none d-md-block">
        <a href="/find-doctor/detail/dr-dup-test"><p>dr. Dup Test, Sp.KK</p></a>
      </div>
      <div class="col-12 d-block d-md-none">
        <a href="/find-doctor/detail/dr-dup-test"><p>dr. Dup Test, Sp.KK</p></a>
      </div>
    </section>
    """
    cards = _parse_listing_cards(html)
    assert len(cards) == 1


def test_parse_schedule_table_extracts_day_and_time():
    detail = (FIXTURES / "mayapada_doctor_detail.html").read_text(encoding="utf-8")
    entries = _parse_schedule_table(detail)
    assert len(entries) == 2
    assert entries[0]["day_text"] == "Monday"
    assert "Mayapada Hospital Jakarta Selatan" in entries[0]["hospital_name"]


def test_is_jabodetabek_branch_matching():
    assert _is_jabodetabek_branch("Mayapada Hospital Jakarta Selatan") is True
    assert _is_jabodetabek_branch("Mayapada Hospital Tangerang") is True
    assert _is_jabodetabek_branch("Mayapada Hospital Bogor") is True
    assert _is_jabodetabek_branch("Mayapada Hospital Surabaya") is False
