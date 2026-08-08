"""Offline tests for RS Premier Bintaro/Jatinegara adapter."""

from pathlib import Path

from src.parsing.schedule import parse_schedule_entries
from src.scrapers.base import HospitalRef
from src.scrapers.rs_premier import (
    RsPremierScraper,
    _parse_doctor_cards,
    _parse_schedule_table,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _listing_html() -> str:
    return (FIXTURES / "rs_premier_dermatology_listing.html").read_text(encoding="utf-8")


def _schedule_html() -> str:
    return (FIXTURES / "rs_premier_doctor_schedule.html").read_text(encoding="utf-8")


def _branch() -> HospitalRef:
    return HospitalRef(
        name="RS Premier Jatinegara",
        url="https://www.rspremierjatinegara.com/rspj/dokter?speciality=1514",
        slug="jatinegara",
    )


def test_parse_cards_filters_non_dermatologist_and_preserves_branch():
    records = _parse_doctor_cards(_listing_html(), branch=_branch())
    assert len(records) == 1
    assert records[0].raw_name == "dr. Anonim Premier, Sp.DVE"
    assert records[0].raw_payload["doctor_id"] == "1001"
    assert records[0].raw_payload["branch_name"] == "RS Premier Jatinegara"


def test_schedule_parser_uses_desktop_table_once_and_splits_explicit_ranges():
    entries = _parse_schedule_table(_schedule_html())
    assert entries == [
        {"day_text": "Senin", "time_text": "08:00 - 10:00"},
        {"day_text": "Senin", "time_text": "13:00 - 15:00"},
        {"day_text": "Rabu", "time_text": "09:00 - 11:00"},
    ]
    slots = parse_schedule_entries(entries, source="rs_premier")
    assert [(s.day_of_week, s.start_time, s.end_time) for s in slots] == [
        (0, "08:00", "10:00"),
        (0, "13:00", "15:00"),
        (2, "09:00", "11:00"),
    ]


def test_discover_hospitals_contains_only_two_jabodetabek_branches():
    hospitals = RsPremierScraper(use_cache=False).discover_hospitals()
    assert [h.name for h in hospitals] == ["RS Premier Jatinegara", "RS Premier Bintaro"]
    assert all("surabaya" not in h.name.lower() for h in hospitals)


def test_fetch_all_combines_both_domains(monkeypatch):
    scraper = RsPremierScraper(use_cache=False)

    def fake_get_html(self, url, *, hospital_slug, cache_key, params=None):
        return _schedule_html() if "doctor-schedule" in url else _listing_html()

    monkeypatch.setattr(RsPremierScraper, "_get_html", fake_get_html)
    records = scraper.fetch_all_dermatology_doctors()
    assert len(records) == 2
    assert {r.raw_payload["branch_name"] for r in records} == {
        "RS Premier Jatinegara",
        "RS Premier Bintaro",
    }
    assert all(len(r.raw_schedule_entries) == 3 for r in records)
