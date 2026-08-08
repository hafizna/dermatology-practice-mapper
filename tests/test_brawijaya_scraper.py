"""Fase 3: Brawijaya adapter tests — offline, fixture-based, no network
calls (spec §14). This adapter uses _curl_get_json (not _get_json) since
Brawijaya's Cloudflare protection blocks httpx but not curl — tests mock
_curl_get_json accordingly. Also exercises per-branch fetch failure
handling (a branch erroring must not abort the whole run) and the dual
dermatology-specialist-name filter ("Dermat" vs "Kulit", confirmed to be
disjoint doctor sets on the live site).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scrapers.base import NetworkError
from src.scrapers.brawijaya import BrawijayaScraper, _is_dermatology_specialist

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def scraper(monkeypatch) -> BrawijayaScraper:
    s = BrawijayaScraper(use_cache=False)
    branches = json.loads((FIXTURES / "brawijaya_branches.json").read_text(encoding="utf-8"))
    sched7 = json.loads((FIXTURES / "brawijaya_schedule_rsid7.json").read_text(encoding="utf-8"))
    sched19 = json.loads((FIXTURES / "brawijaya_schedule_rsid19.json").read_text(encoding="utf-8"))

    def fake_curl_get_json(self, url, *, hospital_slug, cache_key, params=None):
        if "items/branch" in (params or {}).get("path", ""):
            return branches
        rsid = (params or {}).get("rsid")
        if rsid == "7":
            return sched7
        if rsid == "19":
            return sched19
        raise AssertionError(f"unexpected rsid: {rsid}")

    monkeypatch.setattr(BrawijayaScraper, "_curl_get_json", fake_curl_get_json)
    return s


def test_fetch_all_combines_both_specialist_phrasings(scraper: BrawijayaScraper):
    records = scraper.fetch_all_dermatology_doctors()
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duapuluhdelapan, Sp.DVE" in names  # "Dermatovenereologi" group
    assert "dr. Anonim Contoh Tigapuluh, Sp.KK" in names  # "Penyakit Kulit dan Kelamin" group


def test_non_dermatology_specialist_excluded(scraper: BrawijayaScraper):
    records = scraper.fetch_all_dermatology_doctors()
    names = {r.raw_name for r in records}
    assert "dr. Anonim Contoh Duapuluhsembilan, Sp.A" not in names  # Spesialis Anak


def test_schedule_entries_preserved_structured(scraper: BrawijayaScraper):
    records = scraper.fetch_all_dermatology_doctors()
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Duapuluhdelapan, Sp.DVE")
    assert doc.raw_schedule_entries == [
        {"dsid": 1, "weekday": 1, "start_hour": "9", "start_minute": 0, "end_hour": "12", "end_minute": 0}
    ]


def test_branch_and_specialist_group_recorded_in_payload(scraper: BrawijayaScraper):
    records = scraper.fetch_all_dermatology_doctors()
    doc = next(r for r in records if r.raw_name == "dr. Anonim Contoh Tigapuluh, Sp.KK")
    assert doc.raw_payload["branch_name"] == "Brawijaya Hospital - Saharjo"
    assert doc.raw_payload["specialist_group"] == "Spesialis Penyakit Kulit dan Kelamin"


def test_one_branch_failure_does_not_abort_the_run(monkeypatch):
    s = BrawijayaScraper(use_cache=False)
    branches = json.loads((FIXTURES / "brawijaya_branches.json").read_text(encoding="utf-8"))
    sched7 = json.loads((FIXTURES / "brawijaya_schedule_rsid7.json").read_text(encoding="utf-8"))

    def fake_curl_get_json(self, url, *, hospital_slug, cache_key, params=None):
        if "items/branch" in (params or {}).get("path", ""):
            return branches
        rsid = (params or {}).get("rsid")
        if rsid == "7":
            return sched7
        if rsid == "19":
            raise NetworkError("simulated HTTP 500 from Brawijaya's own server")
        raise AssertionError(f"unexpected rsid: {rsid}")

    monkeypatch.setattr(BrawijayaScraper, "_curl_get_json", fake_curl_get_json)

    records = s.fetch_all_dermatology_doctors()
    names = {r.raw_name for r in records}
    # rsid=7's doctor is still present despite rsid=19 failing.
    assert "dr. Anonim Contoh Duapuluhdelapan, Sp.DVE" in names
    assert "dr. Anonim Contoh Tigapuluh, Sp.KK" not in names


def test_is_dermatology_specialist_matches_both_phrasings():
    assert _is_dermatology_specialist("Spesialis Dermatovenereologi") is True
    assert _is_dermatology_specialist("Spesialis Penyakit Kulit dan Kelamin") is True
    assert _is_dermatology_specialist("Spesialis Anak") is False
    assert _is_dermatology_specialist("Spesialis Bedah Orthopaedi dan Traumatologi") is False
