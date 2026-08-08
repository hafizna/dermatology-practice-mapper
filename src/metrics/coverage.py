"""Fase 6: Coverage Matrix dan Supply Metrics (spec §10 Fase 6).

Builds, per hospital, a 7-day x 30-minute-slot (07:00-21:00) coverage
matrix from ScheduleSlot rows, and derives the required metrics:

    n_dermatologists_unique, n_sessions_week, doctor_hours_week,
    prime_time_doctor_hours, weekend_doctor_hours, coverage_ratio_all,
    coverage_ratio_prime, coverage_ratio_weekend, prime_gap_ratio,
    weekend_gap_ratio, longest_prime_gap_minutes,
    doctors_with_external_overlap, mean_external_hospital_count

Design notes:
- LOW parse_confidence slots (day_of_week is None, or the schedule text
  was genuinely ambiguous) are EXCLUDED from every metric — spec's
  schedule.py already documents "jangan dipakai menghitung gap" for
  exactly this reason: a low-confidence slot's start/end time may not
  even be set. Only day_of_week is not None AND start_time is not None
  slots participate in the matrix. This exclusion rate feeds directly
  into schedule_completeness (spec §7.5's score-eligibility gate).
- A slot with end_time=None ("selesai"/open-ended, or genuinely
  unparseable) has an unknown duration — it's still counted as "a
  dermatologist is present" for the single 30-minute slot its start_time
  falls in (coverage matrix cells are boolean presence, not duration),
  but contributes 0 to doctor_hours_week since spec never asks the tool
  to guess how long "selesai" means in hours. This keeps coverage_ratio_*
  (presence-based) and doctor_hours_week (duration-based) internally
  consistent with what's actually known rather than silently estimating.
- "Doctor count isn't enough" (spec's RS A/RS B example) is why BOTH
  n_dermatologists_unique and doctor_hours_week are computed and stored
  side by side, never collapsed into one number here — that tension is
  resolved later by scoring weights (Fase 7), not hidden at this layer.
- Cross-hospital overlap (doctors_with_external_overlap,
  mean_external_hospital_count) is a CONTEXT metric only (spec §10 Fase
  6) — computed and stored, never used to penalize/reward automatically.
  Relies on Doctor.normalized_person_key (Fase 4.3 identity resolution);
  a doctor whose key is None/empty never participates (can't confirm
  identity across hospitals without a key).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_prime_time_config
from src.db import session_scope
from src.logging_setup import get_logger
from src.models import (
    DermatologistCountStatus,
    Doctor,
    Hospital,
    HospitalPracticeMetrics,
    ParseConfidence,
    ScheduleSlot,
)

log = get_logger(__name__)

# A zero-doctor branch can only become CONFIRMED_ZERO when its group
# source is exhaustive at branch level. Primaya's official search is a
# documented lower-bound feed: pagination breaks while results remain,
# so absence from that scrape cannot prove a real zero.
_GROUPS_WITH_INCOMPLETE_DOCTOR_LISTS = frozenset({"Primaya"})

SLOT_MINUTES = 30
DAY_START_MINUTES = 7 * 60  # 07:00
DAY_END_MINUTES = 21 * 60  # 21:00
N_SLOTS_PER_DAY = (DAY_END_MINUTES - DAY_START_MINUTES) // SLOT_MINUTES  # 28
N_DAYS = 7


@dataclass
class UsableSlot:
    """A ScheduleSlot filtered down to the fields the matrix needs, after
    the low-confidence/unusable exclusion described in the module
    docstring. Kept separate from the ORM row so the pure-computation
    functions below never need a live Session (testable without a DB).
    """

    doctor_id: int
    day_of_week: int  # 0=Senin..6=Minggu
    start_minutes: int  # minutes since midnight
    end_minutes: int | None  # None if open-ended/unknown duration


def _time_to_minutes(hhmm: str) -> int | None:
    try:
        h, m = hhmm.split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def usable_slots_for_hospital(slots: list[ScheduleSlot]) -> list[UsableSlot]:
    """Filter+convert raw ScheduleSlot rows to UsableSlot, dropping
    anything not confidently placeable on the matrix (see module
    docstring's LOW-confidence exclusion rule).
    """
    usable = []
    for s in slots:
        if s.parse_confidence == ParseConfidence.LOW:
            continue
        if s.day_of_week is None or s.start_time is None:
            continue
        start_min = _time_to_minutes(s.start_time)
        if start_min is None:
            continue
        end_min = _time_to_minutes(s.end_time) if s.end_time else None
        usable.append(UsableSlot(s.doctor_id, s.day_of_week, start_min, end_min))
    return usable


def build_matrix_cells(slots: list[UsableSlot]) -> dict[tuple[int, int], set[int]]:
    """Return {(day_of_week, slot_index): {doctor_id, ...}} — slot_index
    is 0..N_SLOTS_PER_DAY-1, the 30-minute bucket within 07:00-21:00.
    A slot outside 07:00-21:00 still contributes to doctor_hours_week
    (spec doesn't say practice can't happen outside that window) but is
    NOT placed in the matrix (matrix is explicitly scoped to 07:00-21:00
    per spec's literal window) — see doctor_hours_week's separate
    full-duration computation below, which does not use this matrix.
    """
    cells: dict[tuple[int, int], set[int]] = defaultdict(set)
    for slot in slots:
        # A slot may span multiple 30-min buckets (e.g. 14:00-18:00) —
        # mark every bucket it overlaps, using end_minutes when known;
        # an open-ended slot (end_minutes=None) marks only its single
        # start bucket (can't know how many buckets it spans).
        end = slot.end_minutes if slot.end_minutes is not None else slot.start_minutes + SLOT_MINUTES
        bucket_start = max(slot.start_minutes, DAY_START_MINUTES)
        bucket_end = min(end, DAY_END_MINUTES)
        t = bucket_start
        while t < bucket_end:
            idx = (t - DAY_START_MINUTES) // SLOT_MINUTES
            if 0 <= idx < N_SLOTS_PER_DAY:
                cells[(slot.day_of_week, idx)].add(slot.doctor_id)
            t += SLOT_MINUTES
    return cells


def _in_window(day: int, minute_of_day: int, window) -> bool:
    if day not in window.days:
        return False
    start = _time_to_minutes(window.start)
    end = _time_to_minutes(window.end)
    return start <= minute_of_day < end


@dataclass
class SupplyMetrics:
    n_dermatologists_unique: int = 0
    n_sessions_week: int = 0
    doctor_hours_week: float = 0.0
    prime_time_doctor_hours: float = 0.0
    weekend_doctor_hours: float = 0.0
    coverage_ratio_all: float | None = None
    coverage_ratio_prime: float | None = None
    coverage_ratio_weekend: float | None = None
    prime_gap_ratio: float | None = None
    weekend_gap_ratio: float | None = None
    longest_prime_gap_minutes: int | None = None
    schedule_completeness: float | None = None


def compute_supply_metrics(
    all_slots_for_hospital: list[ScheduleSlot], *, doctor_ids_at_hospital: list[int] | None = None
) -> SupplyMetrics:
    """Pure computation: given every ScheduleSlot row for one hospital
    (any confidence level — this function does the filtering itself so
    it can also compute schedule_completeness), return SupplyMetrics.

    doctor_ids_at_hospital: the AUTHORITATIVE set of confirmed
    dermatologist Doctor.id values at this hospital, independent of
    whether any of them have schedule data at all. Required for sources
    where a listed doctor can have no visible/parseable schedule (a real
    case in the Eka snapshot) — deriving doctor count purely from usable
    ScheduleSlot rows would wrongly report 0 dermatologists for a
    hospital with real, confirmed doctors just because none of them
    have a parseable schedule. When omitted (e.g. ad-hoc/test calls),
    falls back to the schedule-derived count, which undercounts for
    schedule-less sources — production code (build_coverage_matrix)
    always passes this.
    """
    prime_cfg = get_prime_time_config()
    metrics = SupplyMetrics()

    total_slot_rows = len(all_slots_for_hospital)
    usable = usable_slots_for_hospital(all_slots_for_hospital)
    row_parse_completeness = (len(usable) / total_slot_rows) if total_slot_rows > 0 else None

    # Completeness must also account for listed doctors with no visible
    # schedule rows. Looking only at parsed/raw slot rows made a branch
    # with 1 scheduled doctor + 1 schedule-less doctor appear 100%
    # complete. This is especially visible in the Eka manual snapshot.
    if doctor_ids_at_hospital:
        usable_doctor_ids = {slot.doctor_id for slot in usable}
        doctor_schedule_completeness = len(usable_doctor_ids) / len(doctor_ids_at_hospital)
        metrics.schedule_completeness = (
            min(row_parse_completeness, doctor_schedule_completeness)
            if row_parse_completeness is not None
            else 0.0
        )
    else:
        metrics.schedule_completeness = row_parse_completeness

    if doctor_ids_at_hospital is not None:
        metrics.n_dermatologists_unique = len(doctor_ids_at_hospital)
    elif usable:
        metrics.n_dermatologists_unique = len({s.doctor_id for s in usable})

    if not usable:
        return metrics

    metrics.n_sessions_week = len(usable)

    total_hours = 0.0
    prime_hours = 0.0
    weekend_hours = 0.0
    for s in usable:
        if s.end_minutes is None:
            continue  # unknown duration — never guessed (module docstring)
        duration_min = s.end_minutes - s.start_minutes
        if duration_min <= 0:
            continue  # malformed range, skip rather than subtract hours
        hours = duration_min / 60.0
        total_hours += hours

        # A session can straddle a prime-time window boundary; count the
        # actual overlapping minutes with each configured window rather
        # than an all-or-nothing classification of the whole session.
        prime_hours += _overlap_hours(s, prime_cfg.weekday_evening) + _overlap_hours(s, prime_cfg.saturday)
        if prime_cfg.weekend_full:
            weekend_hours += _overlap_hours(s, prime_cfg.weekend_full)

    metrics.doctor_hours_week = round(total_hours, 2)
    metrics.prime_time_doctor_hours = round(prime_hours, 2)
    metrics.weekend_doctor_hours = round(weekend_hours, 2)

    cells = build_matrix_cells(usable)
    total_cells = N_DAYS * N_SLOTS_PER_DAY
    covered_cells = len({k for k, doctors in cells.items() if doctors})
    metrics.coverage_ratio_all = round(covered_cells / total_cells, 4)

    prime_ratio, prime_gap, longest_gap = _window_coverage(cells, prime_cfg.weekday_evening, prime_cfg.saturday)
    metrics.coverage_ratio_prime = prime_ratio
    metrics.prime_gap_ratio = prime_gap
    metrics.longest_prime_gap_minutes = longest_gap

    if prime_cfg.weekend_full:
        weekend_ratio, weekend_gap, _ = _window_coverage(cells, prime_cfg.weekend_full)
        metrics.coverage_ratio_weekend = weekend_ratio
        metrics.weekend_gap_ratio = weekend_gap

    return metrics


def _overlap_hours(slot: UsableSlot, window) -> float:
    if slot.day_of_week not in window.days:
        return 0.0
    win_start = _time_to_minutes(window.start)
    win_end = _time_to_minutes(window.end)
    end = slot.end_minutes if slot.end_minutes is not None else slot.start_minutes
    overlap_start = max(slot.start_minutes, win_start)
    overlap_end = min(end, win_end)
    return max(0, overlap_end - overlap_start) / 60.0


def _window_coverage(cells: dict[tuple[int, int], set[int]], *windows) -> tuple[float, float, int | None]:
    """Return (coverage_ratio, gap_ratio, longest_gap_minutes) across the
    30-min matrix slots that fall inside any of the given windows.
    """
    window_slot_indices: list[tuple[int, int]] = []
    for day in range(N_DAYS):
        for idx in range(N_SLOTS_PER_DAY):
            minute_of_day = DAY_START_MINUTES + idx * SLOT_MINUTES
            if any(_in_window(day, minute_of_day, w) for w in windows):
                window_slot_indices.append((day, idx))

    if not window_slot_indices:
        return (None, None, None)

    total = len(window_slot_indices)
    covered = sum(1 for k in window_slot_indices if cells.get(k))
    coverage_ratio = round(covered / total, 4)
    gap_ratio = round(1.0 - coverage_ratio, 4)

    # Longest consecutive-gap run in minutes, per day (gaps don't span
    # across days — a hospital closed overnight isn't "one long gap").
    longest_gap_slots = 0
    by_day: dict[int, list[int]] = defaultdict(list)
    for day, idx in window_slot_indices:
        by_day[day].append(idx)
    for day, indices in by_day.items():
        indices.sort()
        run = 0
        for idx in indices:
            if cells.get((day, idx)):
                run = 0
            else:
                run += 1
                longest_gap_slots = max(longest_gap_slots, run)

    longest_gap_minutes = longest_gap_slots * SLOT_MINUTES
    return (coverage_ratio, gap_ratio, longest_gap_minutes)


@dataclass
class OverlapMetrics:
    doctors_with_external_overlap: int = 0
    mean_external_hospital_count: float | None = None


def compute_overlap_metrics(
    session: Session, hospital_id: int, doctor_ids_at_hospital: list[int]
) -> OverlapMetrics:
    """Context metric only (spec §10 Fase 6) — never used to
    automatically penalize/reward a hospital's score. Counts, among this
    hospital's dermatologists, how many share a normalized_person_key
    with a Doctor row at a DIFFERENT hospital (cross-hospital identity
    match from Fase 4.3), and the mean number of distinct external
    hospitals those doctors are found at.
    """
    result = OverlapMetrics()
    if not doctor_ids_at_hospital:
        return result

    doctors = session.execute(select(Doctor).where(Doctor.id.in_(doctor_ids_at_hospital))).scalars().all()
    keys = {d.normalized_person_key for d in doctors if d.normalized_person_key}
    if not keys:
        return result

    all_matches = (
        session.execute(select(Doctor).where(Doctor.normalized_person_key.in_(keys))).scalars().all()
    )
    by_key: dict[str, set[int]] = defaultdict(set)
    for d in all_matches:
        if d.normalized_person_key:
            by_key[d.normalized_person_key].add(d.hospital_id)

    external_counts = []
    for key in keys:
        external_hospitals = by_key.get(key, set()) - {hospital_id}
        if external_hospitals:
            external_counts.append(len(external_hospitals))

    result.doctors_with_external_overlap = len(external_counts)
    result.mean_external_hospital_count = round(sum(external_counts) / len(external_counts), 2) if external_counts else None
    return result


def _dermatologist_count_status(
    hospital: Hospital, n_dermatologists: int, group_has_any_doctors: bool
) -> DermatologistCountStatus:
    """spec §7.6 — the three-way distinction for n_dermatologists_unique==0.

    NO_DERM_SERVICE is never assigned here: no scraper currently captures
    a positive "this hospital explicitly does not offer dermatology"
    signal (e.g. a full specialities list that omits it) — only absence
    of derm doctors in the scrape, which is exactly the
    confirmed_zero/unknown ambiguity this function resolves the OTHER
    way. Leaving NO_DERM_SERVICE unused rather than guessed.
    """
    if n_dermatologists > 0:
        return DermatologistCountStatus.HAS_DOCTORS
    if hospital.preferred_rank_group in _GROUPS_WITH_INCOMPLETE_DOCTOR_LISTS:
        return DermatologistCountStatus.UNKNOWN
    if hospital.preferred_rank_group and group_has_any_doctors:
        # We scraped this hospital's group specifically for dermatology
        # and the group's scrape overall succeeded (found doctors
        # elsewhere) — a zero here is a confirmed absence for THIS
        # branch, not a scraper failure.
        return DermatologistCountStatus.CONFIRMED_ZERO
    return DermatologistCountStatus.UNKNOWN


def build_coverage_matrix() -> dict:
    """Fase 6 orchestrator: compute + persist HospitalPracticeMetrics for
    every Hospital row, from currently-persisted ScheduleSlot data.
    Returns a summary dict for CLI reporting.

    Every hospital in the registry gets a HospitalPracticeMetrics row
    (even ones with zero doctors) so the dashboard (Fase 8) can show
    "no dermatologist found" as a real, visible state rather than a
    missing row — spec §3.5 "unknown != zero" applies to hospital
    coverage too: a hospital absent from this table would be
    indistinguishable from one simply not loaded yet.
    """
    summary = {"hospitals_processed": 0, "with_doctors": 0, "confirmed_zero": 0, "unknown": 0}

    with session_scope() as session:
        hospitals = session.execute(select(Hospital)).scalars().all()

        # Precompute which preferred_rank_groups actually produced ANY
        # dermatologist anywhere, to distinguish confirmed_zero from
        # unknown per hospital below without one query per hospital.
        groups_with_doctors = {
            row[0]
            for row in session.execute(
                select(Hospital.preferred_rank_group)
                .join(Doctor, Doctor.hospital_id == Hospital.id)
                .where(Hospital.preferred_rank_group.isnot(None))
                .distinct()
            ).all()
        }

        for hospital in hospitals:
            slots = (
                session.execute(select(ScheduleSlot).where(ScheduleSlot.hospital_id == hospital.id))
                .scalars()
                .all()
            )
            doctor_ids = [
                row[0]
                for row in session.execute(select(Doctor.id).where(Doctor.hospital_id == hospital.id)).all()
            ]

            supply = compute_supply_metrics(slots, doctor_ids_at_hospital=doctor_ids)
            overlap = compute_overlap_metrics(session, hospital.id, doctor_ids)
            group_has_any = bool(hospital.preferred_rank_group) and hospital.preferred_rank_group in groups_with_doctors
            derm_status = _dermatologist_count_status(hospital, supply.n_dermatologists_unique, group_has_any)

            existing = session.get(HospitalPracticeMetrics, hospital.id)
            row = existing or HospitalPracticeMetrics(hospital_id=hospital.id)

            # NOTE: `x or None` is WRONG here for CONFIRMED_ZERO/HAS_DOCTORS
            # hospitals — 0 is a CONFIRMED, meaningful value for those two
            # statuses (real bug, caught via dashboard review 2026-08-09:
            # every confirmed_zero hospital showed n_dermatologists_unique
            # =None, which the dashboard's "Data quality" logic then read
            # as "score computed from unknown doctor count" — a
            # contradiction, since confirmed_zero literally means we ARE
            # certain the count is zero). Only UNKNOWN hospitals (never
            # scraped at all) should show None/"tidak diketahui" here.
            known_status = derm_status in (DermatologistCountStatus.HAS_DOCTORS, DermatologistCountStatus.CONFIRMED_ZERO)
            row.dermatologist_count_status = derm_status
            row.n_dermatologists_unique = supply.n_dermatologists_unique if known_status else None
            row.n_sessions_week = supply.n_sessions_week if known_status else None
            row.doctor_hours_week = supply.doctor_hours_week if known_status else None
            row.prime_time_doctor_hours = supply.prime_time_doctor_hours if known_status else None
            row.weekend_doctor_hours = supply.weekend_doctor_hours if known_status else None
            row.coverage_ratio_all = supply.coverage_ratio_all
            row.coverage_ratio_prime = supply.coverage_ratio_prime
            row.coverage_ratio_weekend = supply.coverage_ratio_weekend
            row.prime_gap_ratio = supply.prime_gap_ratio
            row.weekend_gap_ratio = supply.weekend_gap_ratio
            row.longest_prime_gap_minutes = supply.longest_prime_gap_minutes
            row.schedule_completeness = supply.schedule_completeness
            row.doctors_with_external_overlap = overlap.doctors_with_external_overlap or None
            row.mean_external_hospital_count = overlap.mean_external_hospital_count

            if existing is None:
                session.add(row)

            summary["hospitals_processed"] += 1
            if derm_status == DermatologistCountStatus.HAS_DOCTORS:
                summary["with_doctors"] += 1
            elif derm_status == DermatologistCountStatus.CONFIRMED_ZERO:
                summary["confirmed_zero"] += 1
            else:
                summary["unknown"] += 1

    log.info("coverage_matrix_built", **summary)
    return summary
