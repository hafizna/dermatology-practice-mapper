"""Fase 6: coverage matrix & supply metrics tests.

Pure-computation functions (usable_slots_for_hospital, build_matrix_cells,
compute_supply_metrics) are tested with lightweight fake ScheduleSlot-like
objects — no DB needed. compute_overlap_metrics and build_coverage_matrix
(the DB-touching orchestrator) use the in-memory SQLite fixture.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session

from src.metrics.coverage import (
    DAY_END_MINUTES,
    DAY_START_MINUTES,
    N_SLOTS_PER_DAY,
    SLOT_MINUTES,
    build_coverage_matrix,
    build_matrix_cells,
    compute_overlap_metrics,
    compute_supply_metrics,
    usable_slots_for_hospital,
)
from src.models import (
    DermatologistCountStatus,
    Doctor,
    Hospital,
    HospitalPracticeMetrics,
    ParseConfidence,
    ScheduleSlot,
)


@dataclass
class FakeSlot:
    doctor_id: int
    day_of_week: int | None
    start_time: str | None
    end_time: str | None
    parse_confidence: ParseConfidence = ParseConfidence.HIGH


# --- usable_slots_for_hospital: exclusion rules ---------------------------


def test_low_confidence_slot_excluded():
    slots = [FakeSlot(1, 0, "17:00", "19:00", ParseConfidence.LOW)]
    assert usable_slots_for_hospital(slots) == []


def test_missing_day_excluded():
    slots = [FakeSlot(1, None, "17:00", "19:00")]
    assert usable_slots_for_hospital(slots) == []


def test_missing_start_time_excluded():
    slots = [FakeSlot(1, 0, None, "19:00")]
    assert usable_slots_for_hospital(slots) == []


def test_open_ended_slot_kept_with_none_end():
    slots = [FakeSlot(1, 0, "17:00", None)]  # "selesai" / open-ended
    usable = usable_slots_for_hospital(slots)
    assert len(usable) == 1
    assert usable[0].end_minutes is None


def test_high_and_medium_confidence_both_usable():
    slots = [
        FakeSlot(1, 0, "17:00", "19:00", ParseConfidence.HIGH),
        FakeSlot(2, 1, "09:00", "11:00", ParseConfidence.MEDIUM),
    ]
    assert len(usable_slots_for_hospital(slots)) == 2


# --- build_matrix_cells ----------------------------------------------------


def test_matrix_cells_marks_every_overlapping_bucket():
    slots = usable_slots_for_hospital([FakeSlot(1, 0, "14:00", "15:30")])
    cells = build_matrix_cells(slots)
    # 14:00-15:30 spans 3 30-min buckets: 14:00, 14:30, 15:00
    day0_buckets = {idx for (day, idx) in cells if day == 0}
    assert len(day0_buckets) == 3


def test_matrix_cells_open_ended_marks_only_start_bucket():
    slots = usable_slots_for_hospital([FakeSlot(1, 0, "17:00", None)])
    cells = build_matrix_cells(slots)
    assert len(cells) == 1


def test_matrix_cells_outside_0700_2100_not_placed():
    # A 05:00-06:00 slot is entirely before the matrix window.
    slots = usable_slots_for_hospital([FakeSlot(1, 0, "05:00", "06:00")])
    cells = build_matrix_cells(slots)
    assert cells == {}


# --- compute_supply_metrics: doctor count vs doctor-hours (spec's own example) --


def test_fewer_doctors_more_hours_is_supported_not_conflated():
    # Spec's own example: "RS A: 3 dokter tapi 6 doctor-hours/week. RS B:
    # 2 dokter tapi 30 doctor-hours/week." Both numbers must be visible
    # and NOT collapsed into a ranking here (that's Fase 7's job).
    rs_a_slots = [
        FakeSlot(1, 0, "09:00", "11:00"),
        FakeSlot(2, 1, "09:00", "11:00"),
        FakeSlot(3, 2, "09:00", "11:00"),
    ]
    rs_b_slots = [
        FakeSlot(4, 0, "09:00", "19:00"),  # 10h
        FakeSlot(5, 1, "09:00", "19:00"),  # 10h
        FakeSlot(4, 2, "09:00", "19:00"),  # 10h (same doctor, another day)
    ]
    a = compute_supply_metrics(rs_a_slots)
    b = compute_supply_metrics(rs_b_slots)
    assert a.n_dermatologists_unique == 3
    assert a.doctor_hours_week == 6.0
    assert b.n_dermatologists_unique == 2
    assert b.doctor_hours_week == 30.0


def test_open_ended_slot_contributes_zero_hours_not_guessed():
    slots = [FakeSlot(1, 0, "17:00", None)]
    m = compute_supply_metrics(slots)
    assert m.n_sessions_week == 1
    assert m.doctor_hours_week == 0.0  # unknown duration never guessed


def test_no_slots_returns_zeroed_metrics_not_crash():
    m = compute_supply_metrics([])
    assert m.n_dermatologists_unique == 0
    assert m.coverage_ratio_all is None
    assert m.schedule_completeness is None  # no rows at all, not 0% or 100%


def test_doctors_with_zero_schedule_slots_still_counted_when_doctor_ids_given():
    # Regression guard for a real bug: a listed doctor with no visible
    # schedule was wrongly reported as
    # n_dermatologists_unique=0 / dermatologist_count_status=CONFIRMED_
    # ZERO despite having real, confirmed Doctor rows — because the
    # count was derived purely from usable ScheduleSlot rows. Passing
    # the authoritative doctor_ids_at_hospital must fix this even when
    # there are zero (or all-LOW-confidence) slots.
    m = compute_supply_metrics([], doctor_ids_at_hospital=[101, 102, 103])
    assert m.n_dermatologists_unique == 3
    assert m.n_sessions_week == 0
    assert m.doctor_hours_week == 0.0
    assert m.schedule_completeness == 0.0


def test_schedule_completeness_counts_listed_doctors_without_schedule_rows():
    slots = [FakeSlot(101, 0, "09:00", "11:00")]
    m = compute_supply_metrics(slots, doctor_ids_at_hospital=[101, 102])
    assert m.n_dermatologists_unique == 2
    assert m.schedule_completeness == 0.5


def test_doctor_ids_omitted_falls_back_to_schedule_derived_count():
    # Without doctor_ids_at_hospital (e.g. an ad-hoc/exploratory call),
    # the old schedule-derived behavior is preserved for backward
    # compatibility rather than silently reporting 0.
    slots = [FakeSlot(1, 0, "09:00", "11:00"), FakeSlot(2, 1, "10:00", "12:00")]
    m = compute_supply_metrics(slots)
    assert m.n_dermatologists_unique == 2


def test_all_low_confidence_gives_zero_completeness_not_none():
    slots = [FakeSlot(1, 0, "17:00", "19:00", ParseConfidence.LOW)]
    m = compute_supply_metrics(slots)
    assert m.schedule_completeness == 0.0
    assert m.n_dermatologists_unique == 0


def test_schedule_completeness_partial():
    slots = [
        FakeSlot(1, 0, "17:00", "19:00", ParseConfidence.HIGH),
        FakeSlot(2, 1, "09:00", "11:00", ParseConfidence.LOW),
    ]
    m = compute_supply_metrics(slots)
    assert m.schedule_completeness == 0.5


def test_prime_time_hours_only_counts_configured_windows():
    # Tuesday (day=1) 10:00-12:00 is NOT in weekday_evening (17-21) or
    # saturday — zero prime hours even though it's a weekday session.
    slots = [FakeSlot(1, 1, "10:00", "12:00")]
    m = compute_supply_metrics(slots)
    assert m.doctor_hours_week == 2.0
    assert m.prime_time_doctor_hours == 0.0


def test_session_straddling_prime_boundary_counts_partial_overlap():
    # Monday 16:00-18:00 straddles the 17:00 prime-time start — only the
    # 17:00-18:00 hour should count as prime.
    slots = [FakeSlot(1, 0, "16:00", "18:00")]
    m = compute_supply_metrics(slots)
    assert m.doctor_hours_week == 2.0
    assert m.prime_time_doctor_hours == 1.0


# --- compute_overlap_metrics (DB-backed, context metric only) -------------


@pytest.fixture()
def db_session(in_memory_engine):
    with Session(in_memory_engine) as session:
        yield session


def _make_hospital(session, name) -> Hospital:
    h = Hospital(name=name, name_normalized=name.lower(), aliases_json="[]")
    session.add(h)
    session.flush()
    return h


def _make_doctor(session, hospital_id, key) -> Doctor:
    d = Doctor(hospital_id=hospital_id, raw_name=key.title(), normalized_person_key=key, is_dermatologist=True)
    session.add(d)
    session.flush()
    return d


def test_overlap_metrics_no_shared_key_is_zero(db_session):
    h1 = _make_hospital(db_session, "RS A")
    d1 = _make_doctor(db_session, h1.id, "budi santoso")
    result = compute_overlap_metrics(db_session, h1.id, [d1.id])
    assert result.doctors_with_external_overlap == 0
    assert result.mean_external_hospital_count is None


def test_overlap_metrics_detects_doctor_at_two_hospitals(db_session):
    h1 = _make_hospital(db_session, "RS A")
    h2 = _make_hospital(db_session, "RS B")
    d1 = _make_doctor(db_session, h1.id, "budi santoso")
    _make_doctor(db_session, h2.id, "budi santoso")  # same person, other hospital

    result = compute_overlap_metrics(db_session, h1.id, [d1.id])
    assert result.doctors_with_external_overlap == 1
    assert result.mean_external_hospital_count == 1.0


def test_overlap_metrics_ignores_doctors_without_person_key(db_session):
    h1 = _make_hospital(db_session, "RS A")
    d1 = Doctor(hospital_id=h1.id, raw_name="dr. X", normalized_person_key=None, is_dermatologist=True)
    db_session.add(d1)
    db_session.flush()
    result = compute_overlap_metrics(db_session, h1.id, [d1.id])
    assert result.doctors_with_external_overlap == 0


# --- build_coverage_matrix: full orchestrator, dermatologist_count_status --


def test_hospital_with_doctors_gets_has_doctors_status(db_session, monkeypatch):
    monkeypatch.setattr("src.metrics.coverage.session_scope", lambda: _session_scope_ctx(db_session))
    h = _make_hospital(db_session, "RS Punya Dokter")
    h.preferred_rank_group = "TestGroup"
    d = _make_doctor(db_session, h.id, "budi santoso")
    db_session.add(
        ScheduleSlot(
            doctor_id=d.id,
            hospital_id=h.id,
            day_of_week=0,
            start_time="09:00",
            end_time="11:00",
            raw_text="Senin 09:00-11:00",
            parse_confidence=ParseConfidence.HIGH,
        )
    )
    db_session.flush()

    build_coverage_matrix()

    metrics = db_session.get(HospitalPracticeMetrics, h.id)
    assert metrics.dermatologist_count_status == DermatologistCountStatus.HAS_DOCTORS
    assert metrics.n_dermatologists_unique == 1


def test_doctors_with_no_schedule_data_at_all_are_has_doctors_not_confirmed_zero(db_session, monkeypatch):
    # Full orchestrator regression test for the Eka Hospital bug (see
    # test_doctors_with_zero_schedule_slots_still_counted_when_doctor_
    # ids_given for the unit-level version): a hospital whose doctors
    # have Doctor rows but ZERO ScheduleSlot rows at all must still be
    # HAS_DOCTORS, never CONFIRMED_ZERO.
    monkeypatch.setattr("src.metrics.coverage.session_scope", lambda: _session_scope_ctx(db_session))
    h = _make_hospital(db_session, "RS Tanpa Data Jadwal")
    h.preferred_rank_group = "TestGroup"
    _make_doctor(db_session, h.id, "budi santoso")
    _make_doctor(db_session, h.id, "siti aisyah")
    db_session.flush()
    # deliberately NO ScheduleSlot rows added

    build_coverage_matrix()

    metrics = db_session.get(HospitalPracticeMetrics, h.id)
    assert metrics.dermatologist_count_status == DermatologistCountStatus.HAS_DOCTORS
    assert metrics.n_dermatologists_unique == 2
    # 0, not None: we KNOW the count is 2 confirmed doctors with 0
    # scheduled sessions (a real, meaningful value for a HAS_DOCTORS
    # hospital) — not "unknown how many sessions" (bug fix 2026-08-09,
    # caught via dashboard review: `x or None` was silently turning
    # every confirmed 0 into None across n_dermatologists_unique/
    # n_sessions_week/doctor_hours_week for HAS_DOCTORS and
    # CONFIRMED_ZERO hospitals alike).
    assert metrics.n_sessions_week == 0
    assert metrics.doctor_hours_week == 0.0


def test_hospital_in_scraped_group_with_zero_doctors_is_confirmed_zero(db_session, monkeypatch):
    monkeypatch.setattr("src.metrics.coverage.session_scope", lambda: _session_scope_ctx(db_session))
    # Another hospital in the SAME group proves the group's scrape ran.
    h_with_doctors = _make_hospital(db_session, "RS Grup Punya Dokter")
    h_with_doctors.preferred_rank_group = "TestGroup"
    d = _make_doctor(db_session, h_with_doctors.id, "budi santoso")
    db_session.flush()

    h_zero = _make_hospital(db_session, "RS Grup Nol Dokter")
    h_zero.preferred_rank_group = "TestGroup"
    db_session.flush()

    build_coverage_matrix()

    metrics = db_session.get(HospitalPracticeMetrics, h_zero.id)
    assert metrics.dermatologist_count_status == DermatologistCountStatus.CONFIRMED_ZERO
    # Regression guard (dashboard review 2026-08-09): confirmed_zero
    # hospitals must show n_dermatologists_unique=0 (a CONFIRMED,
    # meaningful value), never None — `x or None` was silently
    # collapsing every confirmed 0 into None here, which made the
    # dashboard's "Data quality" column wrongly read confirmed_zero
    # hospitals as "score computed from an unknown doctor count".
    assert metrics.n_dermatologists_unique == 0
    assert metrics.n_sessions_week == 0
    assert metrics.doctor_hours_week == 0.0


def test_hospital_never_scraped_with_zero_doctors_is_unknown(db_session, monkeypatch):
    monkeypatch.setattr("src.metrics.coverage.session_scope", lambda: _session_scope_ctx(db_session))
    h = _make_hospital(db_session, "RS Belum Discrape")  # preferred_rank_group=None
    db_session.flush()

    build_coverage_matrix()

    metrics = db_session.get(HospitalPracticeMetrics, h.id)
    assert metrics.dermatologist_count_status == DermatologistCountStatus.UNKNOWN
    # Unlike CONFIRMED_ZERO/HAS_DOCTORS, an UNKNOWN hospital genuinely
    # has no known doctor count — None (not 0) is correct here.
    assert metrics.n_dermatologists_unique is None


def test_incomplete_primaya_listing_cannot_turn_missing_branch_into_confirmed_zero(
    db_session, monkeypatch
):
    monkeypatch.setattr("src.metrics.coverage.session_scope", lambda: _session_scope_ctx(db_session))
    h_with_doctors = _make_hospital(db_session, "Primaya Cabang Dengan Dokter")
    h_with_doctors.preferred_rank_group = "Primaya"
    _make_doctor(db_session, h_with_doctors.id, "budi santoso")

    h_missing = _make_hospital(db_session, "Primaya Cabang Tidak Muncul")
    h_missing.preferred_rank_group = "Primaya"
    db_session.flush()

    build_coverage_matrix()

    metrics = db_session.get(HospitalPracticeMetrics, h_missing.id)
    assert metrics.dermatologist_count_status == DermatologistCountStatus.UNKNOWN
    assert metrics.n_dermatologists_unique is None


def test_every_hospital_gets_a_metrics_row_even_with_no_data(db_session, monkeypatch):
    monkeypatch.setattr("src.metrics.coverage.session_scope", lambda: _session_scope_ctx(db_session))
    _make_hospital(db_session, "RS Kosong")
    build_coverage_matrix()
    count = db_session.query(HospitalPracticeMetrics).count()
    assert count == 1


@contextlib.contextmanager
def _session_scope_ctx(session):
    # build_coverage_matrix() commits/closes via session_scope() in
    # production; tests reuse the SAME session/transaction so assertions
    # can see the results without a second engine connection. flush()
    # (not commit()) is enough for query visibility within one session.
    yield session
    session.flush()
