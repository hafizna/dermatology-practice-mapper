"""Offline tests for Columbia Asia BSD/Pulomas adapter."""

from pathlib import Path

from src.parsing.schedule import parse_schedule_entries
from src.scrapers.columbia_asia import (
    ColumbiaAsiaScraper,
    _parse_doctor_detail,
    _parse_listing_cards,
    _parse_location_options,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _listing_html() -> str:
    return (FIXTURES / "columbia_asia_dermatology_listing.html").read_text(encoding="utf-8")


def _detail_html() -> str:
    return (FIXTURES / "columbia_asia_doctor_detail.html").read_text(encoding="utf-8")


def test_location_filter_discovers_only_jabodetabek_branches():
    hospitals = _parse_location_options(_listing_html())
    assert [(h.name, h.hospital_id_upstream) for h in hospitals] == [
        ("RS Columbia Asia BSD", "164"),
        ("RS Columbia Asia Pulomas", "158"),
    ]


def test_listing_parser_keeps_exact_dermatology_specialty_only():
    branch = _parse_location_options(_listing_html())[0]
    records = _parse_listing_cards(_listing_html(), branch=branch)
    assert len(records) == 1
    assert records[0].raw_name == "dr. Anonim Columbia"
    assert records[0].raw_credentials_text == "DERMATOLOGY"
    assert records[0].raw_payload["branch_name"] == "RS Columbia Asia BSD"
    assert records[0].source_url.endswith("/doctor-appointment/anonim-columbia/")


def test_detail_parser_splits_multiple_time_ranges():
    detail = _parse_doctor_detail(_detail_html())
    assert detail["specialty"] == "DERMATOLOGY"
    assert detail["source_branch"] == "RSCA BSD"
    assert detail["schedule_entries"] == [
        {"day_text": "Senin", "time_text": "10:10 - 10:30"},
        {"day_text": "Senin", "time_text": "10:40 - 12:00"},
        {"day_text": "Jum'at", "time_text": "10:00 - 13:00"},
    ]
    slots = parse_schedule_entries(detail["schedule_entries"], source="columbia_asia")
    assert len(slots) == 3
    assert (slots[0].day_of_week, slots[0].start_time, slots[0].end_time) == (
        0,
        "10:10",
        "10:30",
    )
    assert slots[0].parse_confidence == "high"


def test_fetch_all_combines_bsd_and_pulomas(monkeypatch):
    scraper = ColumbiaAsiaScraper(use_cache=False)

    def fake_get_html(self, url, *, hospital_slug, cache_key, params=None):
        if cache_key.startswith("doctor_detail_"):
            html = _detail_html()
            if hospital_slug == "pulomas":
                html = html.replace("RSCA BSD", "RSCA Pulomas")
            return html
        return _listing_html()

    monkeypatch.setattr(ColumbiaAsiaScraper, "_get_html", fake_get_html)
    records = scraper.fetch_all_dermatology_doctors()
    assert len(records) == 2
    assert {r.raw_payload["branch_name"] for r in records} == {
        "RS Columbia Asia BSD",
        "RS Columbia Asia Pulomas",
    }
    assert all(len(r.raw_schedule_entries) == 3 for r in records)
