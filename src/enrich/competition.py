"""Hospital-only nearby dermatology supply pilot for V1.5 (Fase 9).

The first pilot covers configured clusters and keeps known supply separate
from unknown hospital rows. Counts remain auditable lower bounds rather
than silently treating unknown as zero.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import CompetitivePilotConfig, get_competitive_pilot_config
from src.models import (
    ConfidenceLevel,
    DermatologistCountStatus,
    Doctor,
    Hospital,
    HospitalPracticeMetrics,
)

EARTH_RADIUS_KM = 6371.0


@dataclass(frozen=True)
class NearbyHospitalDetail:
    hospital_id: int
    hospital_name: str
    group: str | None
    distance_km: float
    dermatologist_status: str
    n_dermatologists: int | None
    doctor_hours_week: float | None
    schedule_completeness: float | None
    lat: float
    lon: float


@dataclass(frozen=True)
class ClusterCompetitiveMetrics:
    cluster_key: str
    cluster_label: str
    anchor_hospital_id: int
    anchor_hospital_name: str
    anchor_lat: float
    anchor_lon: float
    anchor_dermatologists: int | None
    anchor_doctor_hours_week: float | None
    radius_km: float
    nearby_hospitals_count: int
    nearby_known_status_count: int
    nearby_unknown_hospitals_count: int
    nearby_derm_hospitals_count: int
    nearby_confirmed_zero_count: int
    nearby_dermatologists_unique: int
    nearby_derm_doctor_hours_week: float
    known_status_coverage_ratio: float | None
    data_quality: ConfidenceLevel
    hospitals: tuple[NearbyHospitalDetail, ...]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Straight-line great-circle distance; not travel time."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _display_name(hospital: Hospital) -> str:
    return (
        f"{hospital.name} (a.k.a. {hospital.display_alias})"
        if hospital.display_alias
        else hospital.name
    )


def _is_hospital_only_row(hospital: Hospital, excluded_fragments: list[str]) -> bool:
    """Keep explicit hospital labels or manually verified hospital groups only."""
    label = f"{hospital.name} {hospital.display_alias or ''}".casefold()
    if any(fragment.casefold() in label for fragment in excluded_fragments):
        return False

    explicit_hospital_label = bool(
        re.search(r"\brs[a-z]*\b|rumah\s+sakit|\bhospital\b", label)
    )
    return explicit_hospital_label or bool(hospital.preferred_rank_group)


def _quality_for_coverage(
    ratio: float | None, config: CompetitivePilotConfig
) -> ConfidenceLevel:
    if ratio is None or ratio == 0:
        return ConfidenceLevel.UNKNOWN
    if ratio >= config.high_confidence_min_coverage:
        return ConfidenceLevel.HIGH
    if ratio >= config.medium_confidence_min_coverage:
        return ConfidenceLevel.MEDIUM
    return ConfidenceLevel.LOW


def compute_competitive_pilot(
    session: Session, config: CompetitivePilotConfig | None = None
) -> dict[str, list[ClusterCompetitiveMetrics]]:
    """Compute all configured clusters/radii from current local data."""
    config = config or get_competitive_pilot_config()
    rows = session.execute(
        select(Hospital, HospitalPracticeMetrics)
        .outerjoin(HospitalPracticeMetrics, HospitalPracticeMetrics.hospital_id == Hospital.id)
        .where(Hospital.duplicate_of_hospital_id.is_(None))
    ).all()
    hospital_rows = [(h, m) for h, m in rows if h.lat is not None and h.lon is not None]

    doctors_by_hospital: dict[int, set[str]] = defaultdict(set)
    for hospital_id, doctor_id, person_key in session.execute(
        select(Doctor.hospital_id, Doctor.id, Doctor.normalized_person_key).where(
            Doctor.is_dermatologist.is_(True)
        )
    ).all():
        doctors_by_hospital[hospital_id].add(person_key or f"doctor-id:{doctor_id}")

    results: dict[str, list[ClusterCompetitiveMetrics]] = {}
    for cluster_key, cluster_cfg in config.clusters.items():
        anchor_matches = [(h, m) for h, m in hospital_rows if h.name == cluster_cfg.anchor_hospital]
        if len(anchor_matches) != 1:
            raise ValueError(
                f"Competitive pilot anchor {cluster_cfg.anchor_hospital!r} must resolve exactly once; "
                f"found {len(anchor_matches)}"
            )
        anchor, anchor_metrics = anchor_matches[0]
        cluster_results: list[ClusterCompetitiveMetrics] = []

        for radius_km in sorted(config.radii_km):
            nearby: list[tuple[Hospital, HospitalPracticeMetrics | None, float]] = []
            for hospital, metrics in hospital_rows:
                if hospital.id == anchor.id or not _is_hospital_only_row(
                    hospital, config.excluded_name_fragments
                ):
                    continue
                distance = haversine_km(anchor.lat, anchor.lon, hospital.lat, hospital.lon)
                if distance <= radius_km:
                    nearby.append((hospital, metrics, distance))

            known = [
                (h, m, d)
                for h, m, d in nearby
                if m is not None
                and m.dermatologist_count_status != DermatologistCountStatus.UNKNOWN
            ]
            derm_hospitals = [
                (h, m, d)
                for h, m, d in nearby
                if m is not None
                and m.dermatologist_count_status == DermatologistCountStatus.HAS_DOCTORS
            ]
            unique_doctors: set[str] = set()
            for hospital, _metrics, _distance in derm_hospitals:
                unique_doctors.update(doctors_by_hospital.get(hospital.id, set()))

            total = len(nearby)
            known_count = len(known)
            coverage = known_count / total if total else None
            details = tuple(
                NearbyHospitalDetail(
                    hospital_id=h.id,
                    hospital_name=_display_name(h),
                    group=h.preferred_rank_group,
                    distance_km=round(distance, 2),
                    dermatologist_status=(
                        m.dermatologist_count_status.value
                        if m is not None
                        else DermatologistCountStatus.UNKNOWN.value
                    ),
                    n_dermatologists=m.n_dermatologists_unique if m is not None else None,
                    doctor_hours_week=m.doctor_hours_week if m is not None else None,
                    schedule_completeness=m.schedule_completeness if m is not None else None,
                    lat=h.lat,
                    lon=h.lon,
                )
                for h, m, distance in sorted(nearby, key=lambda item: item[2])
            )
            cluster_results.append(
                ClusterCompetitiveMetrics(
                    cluster_key=cluster_key,
                    cluster_label=cluster_cfg.label,
                    anchor_hospital_id=anchor.id,
                    anchor_hospital_name=_display_name(anchor),
                    anchor_lat=anchor.lat,
                    anchor_lon=anchor.lon,
                    anchor_dermatologists=(
                        anchor_metrics.n_dermatologists_unique if anchor_metrics is not None else None
                    ),
                    anchor_doctor_hours_week=(
                        anchor_metrics.doctor_hours_week if anchor_metrics is not None else None
                    ),
                    radius_km=radius_km,
                    nearby_hospitals_count=total,
                    nearby_known_status_count=known_count,
                    nearby_unknown_hospitals_count=total - known_count,
                    nearby_derm_hospitals_count=len(derm_hospitals),
                    nearby_confirmed_zero_count=sum(
                        m is not None
                        and m.dermatologist_count_status
                        == DermatologistCountStatus.CONFIRMED_ZERO
                        for _h, m, _d in nearby
                    ),
                    nearby_dermatologists_unique=len(unique_doctors),
                    nearby_derm_doctor_hours_week=round(
                        sum((m.doctor_hours_week or 0.0) for _h, m, _d in derm_hospitals), 2
                    ),
                    known_status_coverage_ratio=round(coverage, 4) if coverage is not None else None,
                    data_quality=_quality_for_coverage(coverage, config),
                    hospitals=details,
                )
            )
        results[cluster_key] = cluster_results
    return results
