"""Fase 7: Core Opportunity Score (Layer A) — spec §10 Fase 7.

Purpose is narrowly scoped (spec, verbatim): "mengurutkan RS berdasarkan
indikasi ruang praktik internal yang belum terisi penuh" — rank hospitals
by internal-practice-capacity signal ONLY. Population, income proxies,
office density, and brand preference are explicitly OUT of scope here
(those live in src/scoring/market.py / fit.py for later phases, never
merged into this score).

Pipeline:
1. Select the peer universe (Preferred Private / All Private / All
   Hospitals — spec §5) — only hospitals matching the universe filter
   are candidates.
2. Within that universe, split into ELIGIBLE (score_status=ok) and
   INELIGIBLE (score_status=insufficient_data) per the gate in
   _is_score_eligible() below. Peer-relative normalization (percentile)
   is computed ONLY across the eligible subset — normalizing a real
   hospital's percentile rank against hundreds of never-scraped
   "unknown" peers would be meaningless (their raw component values
   are None, not a real 0th percentile).
3. For each eligible hospital, compute the 4 raw component values
   (dermatologist_count_scarcity, doctor_hours_scarcity,
   prime_time_gap, weekend_gap), percentile-normalize each within the
   eligible peer set, then combine via config/scoring.yaml weights into
   opportunity_score. Raw AND normalized values are both stored (spec
   §7.1 "simpan nilai mentah dan nilai normalized").
4. Ineligible hospitals get score_status=insufficient_data,
   opportunity_score=None, and a human-readable score_status_reason —
   never a fabricated/defaulted score.

Confirmed-zero special case (spec §7.6): a hospital with
dermatologist_count_status=CONFIRMED_ZERO has no schedule data at all
(there's no doctor to have a schedule), so schedule_completeness is None
for it — but "zero dermatologists, confirmed" is itself a maximally
informative signal (the exact opposite of "insufficient data"), not a
reason to exclude it from scoring. It's treated as eligible with
dermatologist_count_scarcity/doctor_hours_scarcity/prime_time_gap/
weekend_gap raw values representing maximum scarcity/gap (worst-case,
i.e. most "opportunity"), all at HIGH confidence (we're certain there
are zero doctors, not guessing).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_scoring_config
from src.db import session_scope
from src.logging_setup import get_logger
from src.models import DermatologistCountStatus, Hospital, HospitalPracticeMetrics, ScoreStatus

log = get_logger(__name__)

UNIVERSE_FILTERS = ("preferred_private", "all_private", "all_hospitals")


def _universe_query(universe: str):
    # Hospitals manually confirmed as a duplicate of another row (see
    # Hospital.duplicate_of_hospital_id docstring — typically a brand-
    # only-named OSM entry a few dozen meters from a fully-named branch
    # that already has its own scored row) never become their own
    # scoring candidate in ANY universe — they'd otherwise show up as a
    # phantom "confirmed_zero" duplicate of a hospital that actually has
    # real data under its properly-named row.
    query = select(Hospital).where(Hospital.duplicate_of_hospital_id.is_(None))
    if universe == "preferred_private":
        query = query.where(Hospital.is_preferred_group.is_(True))
    elif universe == "all_private":
        query = query.where(Hospital.ownership == "swasta")
    elif universe == "all_hospitals":
        pass  # no filter
    else:
        raise ValueError(f"Universe tidak dikenal: {universe!r}. Pilihan: {UNIVERSE_FILTERS}")
    return query


@dataclass
class RawComponents:
    dermatologist_count_scarcity: float  # higher = fewer doctors than peers (more scarce)
    doctor_hours_scarcity: float  # higher = fewer doctor-hours than peers
    prime_time_gap: float  # proportion of prime-time slots with no dermatologist
    weekend_gap: float  # proportion of weekend slots with no dermatologist


def _is_score_eligible(metrics: HospitalPracticeMetrics, *, minimum_schedule_completeness: float) -> tuple[bool, str | None]:
    """spec §7.5 gate. Returns (eligible, reason_if_not)."""
    if metrics.dermatologist_count_status == DermatologistCountStatus.UNKNOWN:
        return False, "Status layanan dermatologi tidak diketahui (RS belum pernah discrape)."

    if metrics.dermatologist_count_status == DermatologistCountStatus.CONFIRMED_ZERO:
        # Special case (module docstring): no schedule exists BECAUSE
        # there are confirmed zero doctors — this is complete
        # information, not missing information, so the schedule-
        # completeness gate doesn't apply here.
        return True, None

    if metrics.dermatologist_count_status == DermatologistCountStatus.HAS_DOCTORS:
        if metrics.schedule_completeness is None or metrics.schedule_completeness < minimum_schedule_completeness:
            pct = f"{(metrics.schedule_completeness or 0) * 100:.0f}%"
            return False, (
                f"Schedule completeness {pct} di bawah ambang minimum "
                f"{minimum_schedule_completeness * 100:.0f}% (config/scoring.yaml)."
            )
        return True, None

    # NO_DERM_SERVICE: currently never assigned (see coverage.py), but
    # handled defensively — a hospital confirmed to NOT offer
    # dermatology is out of scope for an opportunity score entirely
    # (there's no "practice space" to rank), not insufficient data.
    return False, "RS tidak menyediakan layanan dermatologi (bukan target opportunity)."


def _raw_components_for_confirmed_zero() -> RawComponents:
    # Maximum scarcity/gap across the board — certain, not guessed.
    return RawComponents(
        dermatologist_count_scarcity=1.0,
        doctor_hours_scarcity=1.0,
        prime_time_gap=1.0,
        weekend_gap=1.0,
    )


def _raw_components_for_has_doctors(metrics: HospitalPracticeMetrics, peer_metrics: list[HospitalPracticeMetrics]) -> RawComponents:
    """Scarcity is computed relative to the peer set's observed range so
    it's a 0..1 raw value BEFORE percentile normalization — "fewer
    doctors/hours than the peer max" rather than an arbitrary absolute
    scale. (The subsequent percentile step in compute_core_opportunity_
    scores is the actual peer-relative ranking spec §7.1 asks for; this
    raw value only needs to be monotonic, not itself a percentile.)
    """
    peer_counts = [m.n_dermatologists_unique for m in peer_metrics if m.n_dermatologists_unique is not None]
    peer_hours = [m.doctor_hours_week for m in peer_metrics if m.doctor_hours_week is not None]

    max_count = max(peer_counts) if peer_counts else (metrics.n_dermatologists_unique or 1)
    max_hours = max(peer_hours) if peer_hours else (metrics.doctor_hours_week or 1.0)

    count = metrics.n_dermatologists_unique or 0
    hours = metrics.doctor_hours_week or 0.0

    count_scarcity = 1.0 - (count / max_count) if max_count > 0 else 0.0
    hours_scarcity = 1.0 - (hours / max_hours) if max_hours > 0 else 0.0

    return RawComponents(
        dermatologist_count_scarcity=max(0.0, min(1.0, count_scarcity)),
        doctor_hours_scarcity=max(0.0, min(1.0, hours_scarcity)),
        prime_time_gap=metrics.prime_gap_ratio if metrics.prime_gap_ratio is not None else 0.0,
        weekend_gap=metrics.weekend_gap_ratio if metrics.weekend_gap_ratio is not None else 0.0,
    )


def _percentile_rank(value: float, all_values: list[float]) -> float:
    """Fraction of all_values <= value — spec §7.1 percentile
    normalization. A single-hospital peer set trivially percentile-ranks
    to 1.0 (nothing to compare against, but not an error).
    """
    if not all_values:
        return 0.0
    n_le = sum(1 for v in all_values if v <= value)
    return round(n_le / len(all_values), 4)


def compute_core_opportunity_scores(universe: str = "preferred_private") -> dict:
    """Fase 7 orchestrator. Computes and persists opportunity_score +
    component breakdown for every Hospital in the given universe.
    Hospitals outside the universe are left untouched (their existing
    HospitalPracticeMetrics row, if any, keeps whatever score_status it
    already had from a previous run under a different universe — this
    function only writes rows it actually scores).

    Returns a summary dict for CLI reporting.
    """
    scoring_cfg = get_scoring_config()
    weights = scoring_cfg.core_opportunity

    summary = {"universe": universe, "eligible": 0, "insufficient_data": 0, "total_candidates": 0}

    with session_scope() as session:
        hospital_ids = [row[0] for row in session.execute(_universe_query(universe).with_only_columns(Hospital.id)).all()]
        summary["total_candidates"] = len(hospital_ids)

        metrics_rows = (
            session.execute(select(HospitalPracticeMetrics).where(HospitalPracticeMetrics.hospital_id.in_(hospital_ids)))
            .scalars()
            .all()
        )
        metrics_by_hospital = {m.hospital_id: m for m in metrics_rows}

        # First pass: determine eligibility for every candidate, and
        # collect raw components for the eligible ones. Two-pass because
        # HAS_DOCTORS raw scarcity needs the peer set's max count/hours,
        # which isn't known until we've looked at every candidate.
        eligibility: dict[int, tuple[bool, str | None]] = {}
        for hospital_id in hospital_ids:
            metrics = metrics_by_hospital.get(hospital_id)
            if metrics is None:
                eligibility[hospital_id] = (False, "Belum ada data coverage (jalankan Fase 6 dahulu).")
                continue
            eligibility[hospital_id] = _is_score_eligible(
                metrics, minimum_schedule_completeness=scoring_cfg.minimum_schedule_completeness
            )

        eligible_ids = [hid for hid, (ok, _) in eligibility.items() if ok]
        peer_metrics = [metrics_by_hospital[hid] for hid in eligible_ids if metrics_by_hospital[hid].dermatologist_count_status == DermatologistCountStatus.HAS_DOCTORS]

        raw_components: dict[int, RawComponents] = {}
        for hospital_id in eligible_ids:
            metrics = metrics_by_hospital[hospital_id]
            if metrics.dermatologist_count_status == DermatologistCountStatus.CONFIRMED_ZERO:
                raw_components[hospital_id] = _raw_components_for_confirmed_zero()
            else:
                raw_components[hospital_id] = _raw_components_for_has_doctors(metrics, peer_metrics)

        # Percentile-normalize each component across the eligible set.
        all_count_scarcity = [c.dermatologist_count_scarcity for c in raw_components.values()]
        all_hours_scarcity = [c.doctor_hours_scarcity for c in raw_components.values()]
        all_prime_gap = [c.prime_time_gap for c in raw_components.values()]
        all_weekend_gap = [c.weekend_gap for c in raw_components.values()]

        for hospital_id in hospital_ids:
            metrics = metrics_by_hospital.get(hospital_id)
            if metrics is None:
                continue  # no Fase 6 row at all -- nothing to write a score onto
            ok, reason = eligibility[hospital_id]

            if not ok:
                metrics.score_status = ScoreStatus.INSUFFICIENT_DATA
                metrics.score_status_reason = reason
                metrics.opportunity_score = None
                summary["insufficient_data"] += 1
                continue

            raw = raw_components[hospital_id]
            count_norm = _percentile_rank(raw.dermatologist_count_scarcity, all_count_scarcity)
            hours_norm = _percentile_rank(raw.doctor_hours_scarcity, all_hours_scarcity)
            prime_norm = _percentile_rank(raw.prime_time_gap, all_prime_gap)
            weekend_norm = _percentile_rank(raw.weekend_gap, all_weekend_gap)

            score = (
                weights.dermatologist_count_scarcity * count_norm
                + weights.doctor_hours_scarcity * hours_norm
                + weights.prime_time_gap * prime_norm
                + weights.weekend_gap * weekend_norm
            )

            metrics.dermatologist_count_scarcity_raw = raw.dermatologist_count_scarcity
            metrics.doctor_hours_scarcity_raw = raw.doctor_hours_scarcity
            metrics.prime_time_gap_raw = raw.prime_time_gap
            metrics.weekend_gap_raw = raw.weekend_gap

            metrics.dermatologist_count_scarcity_norm = count_norm
            metrics.doctor_hours_scarcity_norm = hours_norm
            metrics.prime_time_gap_norm = prime_norm
            metrics.weekend_gap_norm = weekend_norm

            metrics.opportunity_score = round(score, 4)
            metrics.score_status = ScoreStatus.OK
            metrics.score_status_reason = None
            summary["eligible"] += 1

    log.info("core_opportunity_scores_computed", **summary)
    return summary
