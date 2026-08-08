"""Offline tests for the server-rendered Radjak Hospital adapter."""

from pathlib import Path

from src.parsing.schedule import parse_schedule_entries
from src.scrapers.radjak import RadjakScraper, _parse_doctor_cards, _parse_unit_options

FIXTURES = Path(__file__).parent / "fixtures"


def _listing_html() -> str:
    return (FIXTURES / "radjak_dermatology_listing.html").read_text(encoding="utf-8")


def test_unit_filter_discovers_branches_from_source():
    hospitals = _parse_unit_options(_listing_html())
    assert [(h.slug, h.name) for h in hospitals] == [
        ("salemba", "Radjak Hospital Salemba"),
        ("purwakarta", "Radjak Hospital Purwakarta"),
        ("cikarang", "Radjak Hospital Jababeka"),
    ]


def test_cards_require_dermatology_credential_despite_source_filter():
    records = _parse_doctor_cards(_listing_html())
    assert {record.raw_name for record in records} == {
        "dr. Anonim Radjak, Sp.DVE",
        "dr. Anonim Luar Kota, Sp.KK",
    }
    assert all("Sp.PK" not in record.raw_name for record in records)


def test_schedule_reaches_common_parser_and_preserves_by_appointment():
    record = _parse_doctor_cards(_listing_html())[0]
    slots = parse_schedule_entries(record.raw_schedule_entries, source="radjak")
    assert (slots[0].day_of_week, slots[0].start_time, slots[0].end_time) == (
        0,
        "10:00",
        "12:00",
    )
    # The non-numeric source text is retained for audit but not invented as
    # a concrete time range.
    assert record.raw_schedule_entries[1] == {
        "day_text": "Rabu",
        "time_text": "Dengan Perjanjian",
    }


def test_fetch_all_excludes_purwakarta_and_rejects_pathology(monkeypatch):
    scraper = RadjakScraper(use_cache=False)
    monkeypatch.setattr(RadjakScraper, "_get_html", lambda *args, **kwargs: _listing_html())
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    assert len(records) == 1
    assert records[0].raw_payload["branch_name"] == "Radjak Hospital Salemba"
