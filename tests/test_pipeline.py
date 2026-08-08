"""Fase 4.5: scraper -> parsing -> persistence pipeline tests.

Offline, in-memory SQLite (spec §14: no network calls). Exercises
per-source hospital-name extraction, cross-source fuzzy hospital
matching (a genuinely different problem from Fase 1's within-source OSM
dedup), and the credential-validation gate at persistence time.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.models import Hospital
from src.scrapers.base import RawDoctorRecord
from src.scrapers.pipeline import (
    extract_hospital_names,
    match_hospital_by_name,
    persist_doctor_record,
    persist_raw_doctor_records,
)


@pytest.fixture()
def db_session(in_memory_engine):
    from sqlalchemy.orm import Session

    with Session(in_memory_engine) as session:
        yield session


def _make_hospital(session, name: str, group: str | None = None) -> Hospital:
    from src.parsing.hospital_names import normalize_hospital_name

    h = Hospital(
        name=name,
        name_normalized=normalize_hospital_name(name),
        aliases_json="[]",
        preferred_rank_group=group,
    )
    session.add(h)
    session.flush()
    return h


# --- extract_hospital_names: per-source shapes --------------------------


def test_extract_siloam_hospital_names_from_availability():
    record = RawDoctorRecord(
        raw_name="dr. Contoh",
        raw_credentials_text="dr. Contoh, Sp.KK",
        raw_schedule_entries=[],
        source_url="",
        raw_payload={"availability": [{"hospital_name": "Siloam Hospitals Lippo Village"}]},
    )
    assert extract_hospital_names(record, source="siloam") == ["Siloam Hospitals Lippo Village"]


def test_extract_mitra_keluarga_hospital_name_from_clinic():
    record = RawDoctorRecord(
        raw_name="dr. Contoh",
        raw_credentials_text="dr. Contoh, Sp.KK",
        raw_schedule_entries=[],
        source_url="",
        raw_payload={"clinic": {"name": "Mitra Keluarga Bekasi"}},
    )
    assert extract_hospital_names(record, source="mitra_keluarga") == ["Mitra Keluarga Bekasi"]


def test_extract_hermina_hospital_names_from_schedule_keys():
    record = RawDoctorRecord(
        raw_name="dr. Contoh",
        raw_credentials_text="dr. Contoh, Sp.KK",
        raw_schedule_entries=[],
        source_url="",
        raw_payload={"schedule": {"Hermina Depok": {}, "Hermina Jatinegara": {}}},
    )
    names = extract_hospital_names(record, source="hermina")
    assert set(names) == {"Hermina Depok", "Hermina Jatinegara"}


def test_extract_eka_splits_comma_joined_branches():
    record = RawDoctorRecord(
        raw_name="dr. Contoh",
        raw_credentials_text="dr. Contoh, Sp.KK",
        raw_schedule_entries=[],
        source_url="",
        raw_payload={"card": {"location": "RSIA Eka Hospital PIK, RSIA Eka Hospital Pluit"}},
    )
    names = extract_hospital_names(record, source="eka")
    assert names == ["RSIA Eka Hospital PIK", "RSIA Eka Hospital Pluit"]


def test_extract_rspi_multiple_branches_from_doctor_schedule():
    record = RawDoctorRecord(
        raw_name="dr. Contoh",
        raw_credentials_text="dr. Contoh, Sp.KK",
        raw_schedule_entries=[],
        source_url="",
        raw_payload={
            "doctor": {
                "doctor_schedule": [
                    {"hospital": "RS Pondok Indah - Puri Indah"},
                    {"hospital": "RS Pondok Indah - Bintaro Jaya"},
                ]
            }
        },
    )
    names = extract_hospital_names(record, source="rs_pondok_indah")
    assert names == ["RS Pondok Indah - Puri Indah", "RS Pondok Indah - Bintaro Jaya"]


def test_extract_unrecognized_source_returns_empty():
    record = RawDoctorRecord(
        raw_name="dr. Contoh", raw_credentials_text="dr. Contoh, Sp.KK", raw_schedule_entries=[], source_url="", raw_payload={}
    )
    assert extract_hospital_names(record, source="some_new_source") == []


def test_extract_malformed_payload_does_not_raise():
    record = RawDoctorRecord(
        raw_name="dr. Contoh",
        raw_credentials_text="dr. Contoh, Sp.KK",
        raw_schedule_entries=[],
        source_url="",
        raw_payload={"availability": "not a list"},  # wrong shape
    )
    assert extract_hospital_names(record, source="siloam") == []


# --- match_hospital_by_name -----------------------------------------------


def test_match_exact_after_normalization(db_session):
    hospital = _make_hospital(db_session, "RS SILOAM KEBON JERUK", group="Siloam")
    result = match_hospital_by_name(db_session, "Siloam Hospitals Kebon Jeruk", preferred_group="Siloam")
    assert result is not None
    assert result.id == hospital.id


def test_match_returns_none_below_threshold(db_session):
    _make_hospital(db_session, "RS SILOAM ASRI", group="Siloam")
    result = match_hospital_by_name(db_session, "Completely Different Hospital Name Xyz", preferred_group="Siloam")
    assert result is None


def test_match_scoped_to_preferred_group(db_session):
    # Two hospitals with confusingly similar names in DIFFERENT groups —
    # scoping to preferred_group must prevent a cross-group false match.
    _make_hospital(db_session, "RS Harapan Kita", group="GroupA")
    target = _make_hospital(db_session, "RS Harapan Bunda", group="GroupB")
    result = match_hospital_by_name(db_session, "RS Harapan Bunda", preferred_group="GroupB")
    assert result is not None
    assert result.id == target.id


def test_match_empty_name_returns_none(db_session):
    _make_hospital(db_session, "RS Contoh")
    assert match_hospital_by_name(db_session, "") is None


def test_match_uses_real_alias_override_for_swapped_word_order_pair(db_session):
    # Regression guard: "RS Pondok Indah - Puri Indah" vs OSM's
    # "RSU PURI INDAH PONDOK INDAH" score min(token_sort_ratio=100,
    # ratio=65.2) = 65.2, below the 80 threshold, purely because the two
    # compound place-name tokens appear in swapped order. Confirmed by
    # a real config/manual_overrides.csv hospital_name_alias row (not a
    # synthetic fixture) — this test breaks if that row is ever removed
    # or edited incorrectly.
    hospital = _make_hospital(db_session, "RSU PURI INDAH PONDOK INDAH", group="RS Pondok Indah")
    result = match_hospital_by_name(db_session, "RS Pondok Indah - Puri Indah", preferred_group="RS Pondok Indah")
    assert result is not None
    assert result.id == hospital.id


def test_match_uses_coordinate_qualified_alias_when_name_is_ambiguous(db_session):
    # Real case: OSM has TWO different hospitals both named "Rumah Sakit
    # Siloam" (MRCCC Siloam Semanggi at Jalan Karet Pasar, and an
    # unrelated Siloam Kebon Jeruk duplicate ~7km away). A bare-name
    # hospital_name_alias override can't say which one "MRCCC Siloam
    # Hospitals Semanggi" (the scraper-reported name) refers to — this
    # needs the coordinate-qualified "name|lat|lon" override form.
    from src.parsing.hospital_names import normalize_hospital_name

    right_one = Hospital(
        name="Rumah Sakit Siloam",
        name_normalized=normalize_hospital_name("Rumah Sakit Siloam"),
        aliases_json="[]",
        preferred_rank_group="Siloam",
        lat=-6.21909,
        lon=106.8171913,
    )
    wrong_one = Hospital(
        name="Rumah Sakit Siloam",
        name_normalized=normalize_hospital_name("Rumah Sakit Siloam"),
        aliases_json="[]",
        preferred_rank_group="Siloam",
        lat=-6.1911419,
        lon=106.7638204,
    )
    db_session.add_all([right_one, wrong_one])
    db_session.flush()

    result = match_hospital_by_name(db_session, "MRCCC Siloam Hospitals Semanggi", preferred_group="Siloam")
    assert result is not None
    assert result.id == right_one.id


def test_match_uses_coordinate_qualified_alias_for_mayapada_tangerang(db_session):
    # Same coordinate-qualified alias mechanism, different real case
    # (dashboard review 2026-08-09): OSM has two "Mayapada Hospital" rows
    # (one confirmed Tangerang by address, one unconfirmed near Kuningan)
    # — "Mayapada Hospital Tangerang" from the scraper must resolve to
    # the address-confirmed one, not whichever "Mayapada Hospital" row
    # match_hospital_by_name's plain name lookup happens to find first.
    from src.parsing.hospital_names import normalize_hospital_name

    tangerang = Hospital(
        name="Mayapada Hospital",
        name_normalized=normalize_hospital_name("Mayapada Hospital"),
        aliases_json="[]",
        preferred_rank_group="Mayapada",
        lat=-6.2050819,
        lon=106.6416332,
    )
    unconfirmed = Hospital(
        name="Mayapada Hospital",
        name_normalized=normalize_hospital_name("Mayapada Hospital"),
        aliases_json="[]",
        preferred_rank_group="Mayapada",
        lat=-6.2981069,
        lon=106.7859833,
    )
    db_session.add_all([tangerang, unconfirmed])
    db_session.flush()

    result = match_hospital_by_name(db_session, "Mayapada Hospital Tangerang", preferred_group="Mayapada")
    assert result is not None
    assert result.id == tangerang.id


def test_manual_name_alias_bypasses_adapter_preferred_group_filter(db_session):
    # Primaya's own output can list practice at the separate UKRIDA
    # Hospital.  The user explicitly chose to keep UKRIDA outside every
    # preferred group while still linking its two known dermatologists.
    # The explicit alias must therefore win over preferred_group="Primaya".
    from src.parsing.hospital_names import normalize_hospital_name

    ukrida = Hospital(
        name="RS Ukrida Duri Kepa",
        name_normalized=normalize_hospital_name("RS Ukrida Duri Kepa"),
        aliases_json="[]",
        preferred_rank_group=None,
    )
    db_session.add(ukrida)
    db_session.flush()

    result = match_hospital_by_name(
        db_session,
        "UKRIDA Hospital (Jakarta Barat)",
        preferred_group="Primaya",
    )
    assert result is not None
    assert result.id == ukrida.id


# --- persist_doctor_record: credential gate -------------------------------


def test_persist_rejects_non_dermatologist(db_session):
    hospital = _make_hospital(db_session, "RS Contoh")
    record = RawDoctorRecord(
        raw_name="dr. Budi Santoso, Sp.OG",  # obstetrics, not dermatology
        raw_credentials_text="dr. Budi Santoso, Sp.OG",
        raw_schedule_entries=[],
        source_url="",
        raw_payload={},
    )
    result = persist_doctor_record(db_session, record, hospital=hospital, source="siloam")
    assert result is None


def test_persist_creates_doctor_and_schedule_slots(db_session):
    hospital = _make_hospital(db_session, "RS Contoh")
    record = RawDoctorRecord(
        raw_name="dr. Budi Santoso, Sp.KK",
        raw_credentials_text="dr. Budi Santoso, Sp.KK",
        raw_schedule_entries=[{"day": 1, "from_time": "14:00:00", "to_time": "17:00:00"}],
        source_url="https://example.com/doctor/budi",
        raw_payload={},
    )
    doctor = persist_doctor_record(db_session, record, hospital=hospital, source="siloam")
    assert doctor is not None
    assert doctor.hospital_id == hospital.id
    assert doctor.normalized_person_key == "budi santoso"
    assert len(doctor.schedule_slots) == 1
    assert doctor.schedule_slots[0].day_of_week == 0  # Senin, from siloam's verified day=1 mapping


# --- persist_raw_doctor_records: full batch pipeline ----------------------


def test_full_pipeline_summary_counts(db_session):
    _make_hospital(db_session, "RS SILOAM ASRI", group="Siloam")
    records = [
        RawDoctorRecord(  # matches
            raw_name="dr. Budi Santoso, Sp.KK",
            raw_credentials_text="dr. Budi Santoso, Sp.KK",
            raw_schedule_entries=[],
            source_url="",
            raw_payload={"availability": [{"hospital_name": "Siloam Hospitals ASRI"}]},
        ),
        RawDoctorRecord(  # not a dermatologist
            raw_name="dr. Siti Aisyah, Sp.OG",
            raw_credentials_text="dr. Siti Aisyah, Sp.OG",
            raw_schedule_entries=[],
            source_url="",
            raw_payload={"availability": [{"hospital_name": "Siloam Hospitals ASRI"}]},
        ),
        RawDoctorRecord(  # hospital not in registry
            raw_name="dr. Dewi Lestari, Sp.DV",
            raw_credentials_text="dr. Dewi Lestari, Sp.DV",
            raw_schedule_entries=[],
            source_url="",
            raw_payload={"availability": [{"hospital_name": "Siloam Hospitals Nowhereville"}]},
        ),
    ]
    summary = persist_raw_doctor_records(db_session, records, source="siloam", preferred_group="Siloam")
    assert summary["total_records"] == 3
    assert summary["not_dermatologist"] == 1
    assert summary["doctors_created"] == 1
    assert summary["hospital_unmatched"] == 1


def test_same_hospital_reported_under_two_names_does_not_duplicate_doctor(db_session):
    # Regression guard for a real bug (2026-08-09): Siloam's own API
    # reported one doctor's availability at the SAME physical hospital
    # under two different raw names ("Siloam Hospitals Lippo Village"
    # and "Rumah Sakit Umum Siloam Lippo Village") within one doctor
    # record — both resolve to the same registry Hospital row, so a
    # single doctor was silently persisted TWICE, inflating that
    # hospital's doctor count (one real hospital showed 52
    # "dermatologists" that were actually 13 real ones counted 4x after
    # repeated pipeline re-runs compounded this per-run duplication).
    hospital = _make_hospital(db_session, "RS SILOAM LIPPO VILLAGE", group="Siloam")
    records = [
        RawDoctorRecord(
            raw_name="dr. Hannah Damar, Sp.DVE",
            raw_credentials_text="dr. Hannah Damar, Sp.DVE",
            raw_schedule_entries=[],
            source_url="",
            raw_payload={
                "availability": [
                    {"hospital_name": "Siloam Hospitals Lippo Village"},
                    {"hospital_name": "Rumah Sakit Umum Siloam Lippo Village"},
                ]
            },
        )
    ]
    summary = persist_raw_doctor_records(db_session, records, source="siloam", preferred_group="Siloam")
    assert summary["doctors_created"] == 1  # not 2

    from src.models import Doctor

    doctors = db_session.query(Doctor).filter(Doctor.hospital_id == hospital.id).all()
    assert len(doctors) == 1


def test_full_pipeline_multi_branch_doctor_creates_multiple_doctor_rows(db_session):
    # Fictitious branch names ("Contoh A"/"Contoh B") deliberately used
    # here rather than a real RS Pondok Indah branch name, since real
    # branch names may be subject to config/manual_overrides.csv
    # hospital_name_alias entries (see test_match_uses_real_alias_
    # override_for_swapped_word_order_pair) that would make this
    # fixture's synthetic Hospital rows not the ones actually matched.
    h1 = _make_hospital(db_session, "RS Pondok Indah Contoh A", group="RS Pondok Indah")
    h2 = _make_hospital(db_session, "RS Pondok Indah Contoh B", group="RS Pondok Indah")
    records = [
        RawDoctorRecord(
            raw_name="dr. Budi Santoso, Sp.DVE",
            raw_credentials_text="dr. Budi Santoso, Sp.DVE",
            raw_schedule_entries=[],
            source_url="",
            raw_payload={
                "doctor": {
                    "doctor_schedule": [
                        {"hospital": "RS Pondok Indah - Contoh A"},
                        {"hospital": "RS Pondok Indah - Contoh B"},
                    ]
                }
            },
        )
    ]
    summary = persist_raw_doctor_records(
        db_session, records, source="rs_pondok_indah", preferred_group="RS Pondok Indah"
    )
    assert summary["doctors_created"] == 2  # one Doctor row per matched branch


def test_full_pipeline_multi_branch_doctor_gets_only_own_branch_schedule(db_session):
    # Regression guard: before parse_schedule_entries_by_hospital()
    # existed, EVERY matched branch's Doctor row received the FULL
    # pooled schedule (all branches' slots merged), not just its own.
    # (See fictitious-name note in the test above.)
    h1 = _make_hospital(db_session, "RS Pondok Indah Contoh A", group="RS Pondok Indah")
    h2 = _make_hospital(db_session, "RS Pondok Indah Contoh B", group="RS Pondok Indah")
    records = [
        RawDoctorRecord(
            raw_name="dr. Budi Santoso, Sp.DVE",
            raw_credentials_text="dr. Budi Santoso, Sp.DVE",
            raw_schedule_entries=[
                {
                    "hospital": "RS Pondok Indah - Contoh A",
                    "clinics": [{"schedules": [{"day": "Monday", "time_from": "09:00:00", "time_to": "15:00:00"}]}],
                },
                {
                    "hospital": "RS Pondok Indah - Contoh B",
                    "clinics": [
                        {"schedules": [{"day": "Tuesday", "time_from": "10:00:00", "time_to": "12:00:00"}]},
                        {"schedules": [{"day": "Wednesday", "time_from": "10:00:00", "time_to": "12:00:00"}]},
                    ],
                },
            ],
            source_url="",
            raw_payload={
                "doctor": {
                    "doctor_schedule": [
                        {"hospital": "RS Pondok Indah - Contoh A"},
                        {"hospital": "RS Pondok Indah - Contoh B"},
                    ]
                }
            },
        )
    ]
    summary = persist_raw_doctor_records(
        db_session, records, source="rs_pondok_indah", preferred_group="RS Pondok Indah"
    )
    assert summary["doctors_created"] == 2
    assert summary["schedule_slots_created"] == 3  # 1 (Puri Indah) + 2 (Bintaro Jaya), not 3+3 pooled

    from src.models import Doctor

    doctors = db_session.query(Doctor).order_by(Doctor.hospital_id).all()
    by_hospital_id = {d.hospital_id: d for d in doctors}
    assert len(by_hospital_id[h1.id].schedule_slots) == 1
    assert by_hospital_id[h1.id].schedule_slots[0].day_of_week == 0  # Monday
    assert len(by_hospital_id[h2.id].schedule_slots) == 2
    assert {s.day_of_week for s in by_hospital_id[h2.id].schedule_slots} == {1, 2}  # Tuesday, Wednesday
