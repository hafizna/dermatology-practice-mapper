"""Fase 3: Eka manual-snapshot loader tests — offline, fixture-based, no
network calls (spec §14). Eka is not a live scraper (booking.ekahospital.com
is CAPTCHA-protected — see src/scrapers/eka.py docstring); this exercises
the local-file parsing and Jabodetabek filtering logic only.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.scrapers.eka import (
    EkaSnapshotInfo,
    _is_jabodetabek_location,
    _parse_doctor_cards,
    fetch_all_dermatology_doctors,
    find_latest_snapshot,
)

FIXTURE_HTML_PATH = None  # set in fixture


@pytest.fixture()
def eka_manual_uploads(tmp_path, monkeypatch):
    """Point MANUAL_UPLOADS_ROOT at a temp dir seeded with the anonymized
    fixture, so tests never touch the real (gitignored) manual upload.
    """
    import src.scrapers.eka as eka_module

    fixture_src = (
        __import__("pathlib").Path(__file__).parent / "fixtures" / "eka_dermatology_listing.html"
    )
    snapshot_dir = tmp_path / "eka" / "2026-08-08"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "dermatology_listing.html").write_text(
        fixture_src.read_text(encoding="utf-8"), encoding="utf-8"
    )

    monkeypatch.setattr(eka_module, "MANUAL_UPLOADS_ROOT", tmp_path / "eka")
    return tmp_path / "eka"


def test_find_latest_snapshot_returns_dated_folder(eka_manual_uploads):
    snapshot = find_latest_snapshot()
    assert snapshot is not None
    assert snapshot.snapshot_date == dt.date(2026, 8, 8)


def test_find_latest_snapshot_none_when_no_uploads(tmp_path, monkeypatch):
    import src.scrapers.eka as eka_module

    monkeypatch.setattr(eka_module, "MANUAL_UPLOADS_ROOT", tmp_path / "nonexistent")
    assert find_latest_snapshot() is None


def test_find_latest_snapshot_picks_most_recent_date(eka_manual_uploads):
    # Add a newer dated snapshot alongside the fixture one.
    newer_dir = eka_manual_uploads / "2026-09-01"
    newer_dir.mkdir()
    (newer_dir / "dermatology_listing.html").write_text("<html></html>", encoding="utf-8")

    snapshot = find_latest_snapshot()
    assert snapshot.snapshot_date == dt.date(2026, 9, 1)


def test_fetch_all_unfiltered_returns_all_three(eka_manual_uploads):
    records = fetch_all_dermatology_doctors(jabodetabek_only=False)
    assert len(records) == 3


def test_jabodetabek_filter_keeps_mt_haryono_and_pik(eka_manual_uploads):
    records = fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duabelas, Sp.DVE" in names  # MT Haryono
    assert "dr. Anonim Contoh Empatbelas, Sp.DVE" in names  # PIK/Pluit


def test_jabodetabek_filter_excludes_pekanbaru(eka_manual_uploads):
    records = fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Tigabelas, Sp.DV" not in names


def test_visible_schedule_is_extracted_from_each_doctor_card(eka_manual_uploads):
    records = fetch_all_dermatology_doctors(jabodetabek_only=True)
    mt_haryono = next(r for r in records if "Duabelas" in r.raw_name)
    assert mt_haryono.raw_schedule_entries == [
        {
            "hospital": "EKA Hospital MT Haryono",
            "day_text": "Selasa",
            "time_text": "16:30 - 20:00",
        },
        {
            "hospital": "EKA Hospital MT Haryono",
            "day_text": "Selasa",
            "time_text": "20:15 - 21:00",
        },
    ]


def test_multibranch_card_tags_schedule_only_with_selected_branch(eka_manual_uploads):
    records = fetch_all_dermatology_doctors(jabodetabek_only=True)
    pik_pluit = next(r for r in records if "Empatbelas" in r.raw_name)
    assert pik_pluit.raw_payload["card"]["location"] == (
        "RSIA Eka Hospital PIK, RSIA Eka Hospital Pluit"
    )
    assert pik_pluit.raw_schedule_entries == [
        {
            "hospital": "RSIA Eka Hospital PIK",
            "day_text": "Sabtu",
            "time_text": "08:00 - 10:00",
        }
    ]


def test_provenance_includes_snapshot_date(eka_manual_uploads):
    records = fetch_all_dermatology_doctors(jabodetabek_only=True)
    for r in records:
        assert r.raw_payload["manual_snapshot_date"] == "2026-08-08"


def test_is_jabodetabek_location_matching():
    assert _is_jabodetabek_location("EKA Hospital MT Haryono") is True
    assert _is_jabodetabek_location("EKA Hospital Cibubur") is True
    assert _is_jabodetabek_location("RSIA Eka Hospital PIK, RSIA Eka Hospital Pluit") is True
    assert _is_jabodetabek_location("EKA Hospital Pekanbaru") is False
