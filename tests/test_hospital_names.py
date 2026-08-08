"""Fase 1: hospital name normalization tests."""

from __future__ import annotations

from src.parsing.hospital_names import normalize_hospital_name


def test_strips_common_prefixes():
    assert normalize_hospital_name("RS Eka Hospital BSD") == "eka bsd"
    assert normalize_hospital_name("RSUD Kebayoran Lama") == "kebayoran lama"
    assert normalize_hospital_name("Rumah Sakit Umum Daerah Tarakan") == "tarakan"
    assert normalize_hospital_name("RSIA Brawijaya") == "brawijaya"
    assert normalize_hospital_name("Rumah Sakit Siloam") == "siloam"


def test_lowercases_and_strips_punctuation():
    assert normalize_hospital_name("RS. Pondok Indah") == "pondok indah"
    assert normalize_hospital_name("RS Mitra Keluarga - Kelapa Gading") == "mitra keluarga kelapa gading"


def test_collapses_whitespace():
    assert normalize_hospital_name("RS   Hermina   Bekasi") == "hermina bekasi"


def test_no_prefix_present_still_normalizes():
    assert normalize_hospital_name("Eka Hospital Cibubur") == "eka cibubur"


def test_english_hospital_word_stripped_anywhere_in_string():
    # Real cross-source case (Fase 4.5 pipeline): registry name "RS
    # SILOAM KEBON JERUK" (OSM, Indonesian-only) must match scraper name
    # "Siloam Hospitals Kebon Jeruk" (English "Hospitals" inserted
    # mid-name, not just as a prefix).
    assert normalize_hospital_name("Siloam Hospitals Kebon Jeruk") == normalize_hospital_name(
        "RS Siloam Kebon Jeruk"
    )
    assert normalize_hospital_name("Siloam Hospitals Kebon Jeruk") == "siloam kebon jeruk"


def test_empty_or_prefix_only_returns_empty_or_short():
    # Degenerate input shouldn't crash; downstream dedup treats empty as
    # "skip" rather than matching everything.
    assert normalize_hospital_name("RS") == ""
    assert normalize_hospital_name("") == ""
