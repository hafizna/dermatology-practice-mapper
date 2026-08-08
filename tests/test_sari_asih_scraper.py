"""Offline tests for the server-rendered RS Sari Asih adapter."""

from pathlib import Path

from src.parsing.schedule import parse_schedule_entries
from src.scrapers.sari_asih import SariAsihScraper, _pagination_pages, _parse_doctor_cards

FIXTURES = Path(__file__).parent / "fixtures"


def _listing_html() -> str:
    return (FIXTURES / "sari_asih_dermatology_listing.html").read_text(encoding="utf-8")


def test_parse_cards_splits_branches_and_filters_credentials():
    records = _parse_doctor_cards(_listing_html(), jabodetabek_only=False)
    assert len(records) == 2
    assert {r.raw_payload["card"]["branch"] for r in records} == {
        "RS Sari Asih Sangiang",
        "RS Sari Asih Serang",
    }
    assert {r.raw_name for r in records} == {"dr. Anonim Dermatologi, Sp.DVE"}


def test_jabodetabek_filter_excludes_serang_but_keeps_sangiang():
    records = _parse_doctor_cards(_listing_html(), jabodetabek_only=True)
    assert len(records) == 1
    assert records[0].raw_payload["card"]["branch"] == "RS Sari Asih Sangiang"


def test_schedule_table_shape_reaches_common_parser():
    record = _parse_doctor_cards(_listing_html(), jabodetabek_only=True)[0]
    slots = parse_schedule_entries(record.raw_schedule_entries, source="sari_asih")
    assert len(slots) == 1
    assert (slots[0].day_of_week, slots[0].start_time, slots[0].end_time) == (0, "08:00", "10:00")
    assert slots[0].parse_confidence == "high"


def test_pagination_is_discovered_from_source_links():
    assert _pagination_pages(_listing_html()) == [1, 2]


def test_fetch_all_follows_pagination_and_deduplicates(monkeypatch):
    scraper = SariAsihScraper(use_cache=False)
    calls: list[int] = []

    def fake_get_html(self, url, *, hospital_slug, cache_key, params=None):
        calls.append((params or {}).get("page", 1))
        return _listing_html()

    monkeypatch.setattr(SariAsihScraper, "_get_html", fake_get_html)
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    assert calls == [1, 2]
    assert len(records) == 1


def test_discover_hospitals_uses_filter_options(monkeypatch):
    scraper = SariAsihScraper(use_cache=False)
    monkeypatch.setattr(SariAsihScraper, "_get_html", lambda *args, **kwargs: _listing_html())
    hospitals = scraper.discover_hospitals()
    assert [(h.slug, h.name) for h in hospitals] == [
        ("rs-sari-asih-sangiang", "RS Sari Asih Sangiang"),
        ("rs-sari-asih-serang", "RS Sari Asih Serang"),
    ]
