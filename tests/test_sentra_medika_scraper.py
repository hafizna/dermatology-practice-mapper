"""Sentra Medika adapter tests — offline, fixture-based, no network calls
(spec §14). Exercises specialization-id discovery (two ids both qualify as
dermatology), doctor-id de-duplication across those two ids, and the
Jabodetabek branch filter (keeps Cikarang/Cibinong, drops Minahasa
Utara/Gempol).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scrapers.sentra_medika import SentraMedikaScraper, _is_jabodetabek_hospital

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def scraper(monkeypatch) -> SentraMedikaScraper:
    s = SentraMedikaScraper(use_cache=False)
    specializations = json.loads((FIXTURES / "sentra_medika_specializations.json").read_text(encoding="utf-8"))
    spec60 = json.loads((FIXTURES / "sentra_medika_doctors_spec60_page1.json").read_text(encoding="utf-8"))
    spec66 = json.loads((FIXTURES / "sentra_medika_doctors_spec66_page1.json").read_text(encoding="utf-8"))
    schedule = json.loads((FIXTURES / "sentra_medika_doctor_schedule.json").read_text(encoding="utf-8"))

    def fake_get_json(self, url, *, hospital_slug, cache_key, params=None):
        if url.endswith("/specializations"):
            return specializations
        if url.endswith("/doctors"):
            spec_id = (params or {}).get("specialization_ids[]")
            if spec_id == "60":
                return spec60
            if spec_id == "66":
                return spec66
            raise AssertionError(f"unexpected specialization_ids[] value: {spec_id}")
        if "/schedules" in url:
            return schedule
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(SentraMedikaScraper, "_get_json", fake_get_json)
    return s


def test_discover_dermatology_specialization_ids_finds_both(scraper: SentraMedikaScraper):
    ids = scraper.discover_dermatology_specialization_ids()
    assert set(ids) == {"60", "66"}


def test_fetch_all_dermatology_doctors_dedupes_across_specialization_ids(scraper: SentraMedikaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=False)
    # 3 doctors under spec 60 + 3 under spec 66, but dr. Evy Aryanti (id 799)
    # appears in both lists — must be counted once, not twice.
    names = [r.raw_name for r in records]
    assert names.count("dr. Evy Aryanti, Sp.DVE") == 1
    assert len(records) == 5  # 6 raw entries - 1 duplicate


def test_jabodetabek_filter_keeps_cikarang_and_cibinong_drops_minahasa_and_gempol(scraper: SentraMedikaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    names = {r.raw_name for r in records}
    assert "dr. Evy Aryanti, Sp.DVE" in names
    assert "dr. Lady Cecillia Caraldy Koesoema, Sp.DV" in names
    assert "dr. Lucky Pratama, Sp.DVE" in names
    assert "dr. Shienty Gaspersz, Sp.KK" not in names  # Minahasa Utara
    assert "dr. Annisa Marsha Evanti, M.Kes., Sp. DVE" not in names  # Gempol


def test_is_jabodetabek_hospital_matching():
    assert _is_jabodetabek_hospital("Sentra Medika Hospital Cikarang")
    assert _is_jabodetabek_hospital("Sentra Medika Hospital Cibinong")
    assert _is_jabodetabek_hospital("Harapan Bunda Hospital")
    assert not _is_jabodetabek_hospital("Sentra Medika Hospital Minahasa Utara")
    assert not _is_jabodetabek_hospital("Sentra Medika Hospital Gempol")


def test_schedule_payload_preserved_raw(scraper: SentraMedikaScraper):
    records = scraper.fetch_all_dermatology_doctors(jabodetabek_only=True)
    record = next(r for r in records if r.raw_name == "dr. Evy Aryanti, Sp.DVE")
    assert record.raw_schedule_entries == [{"raw_schedule": record.raw_payload["schedule"]}]
    assert record.raw_payload["schedule"]["data"][0]["polyclinic_name"] == "POLIKLINIK KULIT DAN KELAMIN"
