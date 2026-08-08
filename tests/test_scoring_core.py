"""Fase 7: Core Opportunity Score tests.

Uses the in-memory SQLite fixture (offline, spec §14) with hand-built
HospitalPracticeMetrics rows rather than running the full Fase 6 pipeline
— isolates Fase 7's own logic (eligibility gate, universe filtering,
percentile normalization, weighted combination) from Fase 6's.
"""

from __future__ import annotations

import contextlib

import pytest
from sqlalchemy.orm import Session

from src.models import (
    DermatologistCountStatus,
    Hospital,
    HospitalPracticeMetrics,
    ScoreStatus,
)
from src.scoring.core import (
    _is_score_eligible,
    _percentile_rank,
    _raw_components_for_confirmed_zero,
    compute_core_opportunity_scores,
)


@pytest.fixture()
def db_session(in_memory_engine):
    with Session(in_memory_engine) as session:
        yield session


@contextlib.contextmanager
def _session_scope_ctx(session):
    yield session
    session.flush()


def _make_hospital(session, name, **kwargs) -> Hospital:
    h = Hospital(name=name, name_normalized=name.lower(), aliases_json="[]", **kwargs)
    session.add(h)
    session.flush()
    return h


def _make_metrics(session, hospital_id, **kwargs) -> HospitalPracticeMetrics:
    m = HospitalPracticeMetrics(hospital_id=hospital_id, **kwargs)
    session.add(m)
    session.flush()
    return m


# --- _is_score_eligible ----------------------------------------------------


def test_unknown_status_is_ineligible():
    m = HospitalPracticeMetrics(dermatologist_count_status=DermatologistCountStatus.UNKNOWN)
    ok, reason = _is_score_eligible(m, minimum_schedule_completeness=0.70)
    assert ok is False
    assert "tidak diketahui" in reason


def test_confirmed_zero_is_eligible_even_without_schedule_completeness():
    m = HospitalPracticeMetrics(
        dermatologist_count_status=DermatologistCountStatus.CONFIRMED_ZERO, schedule_completeness=None
    )
    ok, reason = _is_score_eligible(m, minimum_schedule_completeness=0.70)
    assert ok is True
    assert reason is None


def test_has_doctors_below_completeness_threshold_is_ineligible():
    m = HospitalPracticeMetrics(
        dermatologist_count_status=DermatologistCountStatus.HAS_DOCTORS, schedule_completeness=0.5
    )
    ok, reason = _is_score_eligible(m, minimum_schedule_completeness=0.70)
    assert ok is False
    assert "50%" in reason


def test_has_doctors_above_completeness_threshold_is_eligible():
    m = HospitalPracticeMetrics(
        dermatologist_count_status=DermatologistCountStatus.HAS_DOCTORS, schedule_completeness=0.85
    )
    ok, reason = _is_score_eligible(m, minimum_schedule_completeness=0.70)
    assert ok is True


def test_has_doctors_none_completeness_is_ineligible():
    m = HospitalPracticeMetrics(
        dermatologist_count_status=DermatologistCountStatus.HAS_DOCTORS, schedule_completeness=None
    )
    ok, reason = _is_score_eligible(m, minimum_schedule_completeness=0.70)
    assert ok is False


def test_no_derm_service_is_ineligible_not_insufficient_wording():
    m = HospitalPracticeMetrics(dermatologist_count_status=DermatologistCountStatus.NO_DERM_SERVICE)
    ok, reason = _is_score_eligible(m, minimum_schedule_completeness=0.70)
    assert ok is False
    assert "tidak menyediakan" in reason


# --- _percentile_rank -------------------------------------------------


def test_percentile_rank_basic():
    values = [0.1, 0.2, 0.3, 0.4, 0.5]
    assert _percentile_rank(0.5, values) == 1.0  # max -> 100th percentile
    assert _percentile_rank(0.1, values) == 0.2  # min -> 1/5


def test_percentile_rank_empty_list_is_zero():
    assert _percentile_rank(0.5, []) == 0.0


def test_percentile_rank_single_value_peer_set_is_one():
    assert _percentile_rank(0.5, [0.5]) == 1.0


# --- confirmed_zero raw components -------------------------------------


def test_confirmed_zero_raw_components_are_maximum_scarcity():
    raw = _raw_components_for_confirmed_zero()
    assert raw.dermatologist_count_scarcity == 1.0
    assert raw.doctor_hours_scarcity == 1.0
    assert raw.prime_time_gap == 1.0
    assert raw.weekend_gap == 1.0


# --- compute_core_opportunity_scores: full orchestrator -----------------


def test_ineligible_hospital_gets_no_score(db_session, monkeypatch):
    monkeypatch.setattr("src.scoring.core.session_scope", lambda: _session_scope_ctx(db_session))
    h = _make_hospital(db_session, "RS Belum Discrape", is_preferred_group=True)
    _make_metrics(db_session, h.id, dermatologist_count_status=DermatologistCountStatus.UNKNOWN)

    compute_core_opportunity_scores(universe="preferred_private")

    m = db_session.get(HospitalPracticeMetrics, h.id)
    assert m.score_status == ScoreStatus.INSUFFICIENT_DATA
    assert m.opportunity_score is None
    assert m.score_status_reason is not None


def test_eligible_hospital_gets_a_score_and_raw_plus_normalized_values(db_session, monkeypatch):
    monkeypatch.setattr("src.scoring.core.session_scope", lambda: _session_scope_ctx(db_session))
    h1 = _make_hospital(db_session, "RS Banyak Dokter", is_preferred_group=True)
    _make_metrics(
        db_session,
        h1.id,
        dermatologist_count_status=DermatologistCountStatus.HAS_DOCTORS,
        n_dermatologists_unique=10,
        doctor_hours_week=100.0,
        schedule_completeness=1.0,
        prime_gap_ratio=0.1,
        weekend_gap_ratio=0.1,
    )
    h2 = _make_hospital(db_session, "RS Sedikit Dokter", is_preferred_group=True)
    _make_metrics(
        db_session,
        h2.id,
        dermatologist_count_status=DermatologistCountStatus.HAS_DOCTORS,
        n_dermatologists_unique=1,
        doctor_hours_week=5.0,
        schedule_completeness=1.0,
        prime_gap_ratio=0.9,
        weekend_gap_ratio=0.9,
    )

    compute_core_opportunity_scores(universe="preferred_private")

    m1 = db_session.get(HospitalPracticeMetrics, h1.id)
    m2 = db_session.get(HospitalPracticeMetrics, h2.id)
    assert m1.score_status == ScoreStatus.OK
    assert m2.score_status == ScoreStatus.OK
    # RS Sedikit Dokter (fewer doctors, fewer hours, bigger gaps) must
    # rank as MORE opportunity than RS Banyak Dokter.
    assert m2.opportunity_score > m1.opportunity_score
    # Raw values preserved alongside normalized (spec §7.1).
    assert m1.dermatologist_count_scarcity_raw is not None
    assert m1.dermatologist_count_scarcity_norm is not None


def test_confirmed_zero_scores_maximally_within_peer_set(db_session, monkeypatch):
    monkeypatch.setattr("src.scoring.core.session_scope", lambda: _session_scope_ctx(db_session))
    h1 = _make_hospital(db_session, "RS Ada Dokter", is_preferred_group=True)
    _make_metrics(
        db_session,
        h1.id,
        dermatologist_count_status=DermatologistCountStatus.HAS_DOCTORS,
        n_dermatologists_unique=5,
        doctor_hours_week=50.0,
        schedule_completeness=1.0,
        prime_gap_ratio=0.3,
        weekend_gap_ratio=0.3,
    )
    h2 = _make_hospital(db_session, "RS Nol Dokter", is_preferred_group=True)
    _make_metrics(db_session, h2.id, dermatologist_count_status=DermatologistCountStatus.CONFIRMED_ZERO)

    compute_core_opportunity_scores(universe="preferred_private")

    m1 = db_session.get(HospitalPracticeMetrics, h1.id)
    m2 = db_session.get(HospitalPracticeMetrics, h2.id)
    assert m2.opportunity_score == 1.0  # top of every percentile
    assert m2.opportunity_score > m1.opportunity_score


def test_universe_filter_excludes_non_preferred_hospitals(db_session, monkeypatch):
    monkeypatch.setattr("src.scoring.core.session_scope", lambda: _session_scope_ctx(db_session))
    h_pref = _make_hospital(db_session, "RS Preferred", is_preferred_group=True)
    _make_metrics(db_session, h_pref.id, dermatologist_count_status=DermatologistCountStatus.CONFIRMED_ZERO)
    h_other = _make_hospital(db_session, "RS Bukan Preferred", is_preferred_group=False)
    _make_metrics(db_session, h_other.id, dermatologist_count_status=DermatologistCountStatus.CONFIRMED_ZERO)

    summary = compute_core_opportunity_scores(universe="preferred_private")

    assert summary["total_candidates"] == 1
    m_other = db_session.get(HospitalPracticeMetrics, h_other.id)
    # Untouched: score_status stays whatever it was before (default),
    # not overwritten just because it exists in the DB.
    assert m_other.opportunity_score is None


def test_all_private_universe_uses_ownership_not_preferred_flag(db_session, monkeypatch):
    monkeypatch.setattr("src.scoring.core.session_scope", lambda: _session_scope_ctx(db_session))
    h_swasta = _make_hospital(db_session, "RS Swasta Non-Preferred", is_preferred_group=False, ownership="swasta")
    _make_metrics(db_session, h_swasta.id, dermatologist_count_status=DermatologistCountStatus.CONFIRMED_ZERO)
    h_public = _make_hospital(db_session, "RSUD Pemerintah", is_preferred_group=False, ownership="pemerintah")
    _make_metrics(db_session, h_public.id, dermatologist_count_status=DermatologistCountStatus.CONFIRMED_ZERO)

    summary = compute_core_opportunity_scores(universe="all_private")

    assert summary["total_candidates"] == 1
    m_swasta = db_session.get(HospitalPracticeMetrics, h_swasta.id)
    assert m_swasta.score_status == ScoreStatus.OK


def test_all_hospitals_universe_includes_everything(db_session, monkeypatch):
    monkeypatch.setattr("src.scoring.core.session_scope", lambda: _session_scope_ctx(db_session))
    h1 = _make_hospital(db_session, "RS 1", is_preferred_group=False, ownership=None)
    _make_metrics(db_session, h1.id, dermatologist_count_status=DermatologistCountStatus.CONFIRMED_ZERO)

    summary = compute_core_opportunity_scores(universe="all_hospitals")
    assert summary["total_candidates"] == 1


def test_unknown_universe_raises():
    with pytest.raises(ValueError):
        compute_core_opportunity_scores(universe="not_a_real_universe")


def test_hospital_with_no_fase6_row_is_skipped_not_crashed(db_session, monkeypatch):
    monkeypatch.setattr("src.scoring.core.session_scope", lambda: _session_scope_ctx(db_session))
    _make_hospital(db_session, "RS Belum Ada Metrics Row", is_preferred_group=True)
    # deliberately no HospitalPracticeMetrics row at all

    summary = compute_core_opportunity_scores(universe="preferred_private")
    assert summary["total_candidates"] == 1
    assert summary["eligible"] == 0
    assert summary["insufficient_data"] == 0  # skipped, not counted as either
