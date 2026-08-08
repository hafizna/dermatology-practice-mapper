"""Offline tests for the V1.5 hospital-only competitive pilot."""

from sqlalchemy.orm import Session

from src.config import CompetitivePilotCluster, CompetitivePilotConfig
from src.enrich.competition import compute_competitive_pilot, haversine_km
from src.models import (
    ConfidenceLevel,
    DermatologistCountStatus,
    Doctor,
    Hospital,
    HospitalPracticeMetrics,
)


def _hospital(session: Session, name: str, lat: float, lon: float) -> Hospital:
    row = Hospital(
        name=name,
        name_normalized=name.casefold(),
        aliases_json="[]",
        lat=lat,
        lon=lon,
    )
    session.add(row)
    session.flush()
    return row


def _metrics(
    session: Session,
    hospital: Hospital,
    status: DermatologistCountStatus,
    doctors: int | None,
    hours: float | None,
) -> None:
    session.add(
        HospitalPracticeMetrics(
            hospital_id=hospital.id,
            dermatologist_count_status=status,
            n_dermatologists_unique=doctors,
            doctor_hours_week=hours,
            schedule_completeness=1.0 if status == DermatologistCountStatus.HAS_DOCTORS else None,
        )
    )


def _doctor(session: Session, hospital: Hospital, person_key: str) -> None:
    session.add(
        Doctor(
            hospital_id=hospital.id,
            raw_name=person_key,
            normalized_person_key=person_key,
            credentials_json="[]",
            is_dermatologist=True,
        )
    )


def test_haversine_returns_expected_short_distance():
    distance = haversine_km(-6.2, 106.8, -6.2, 106.81)
    assert 1.0 < distance < 1.2


def test_pilot_excludes_clinics_tracks_unknown_and_deduplicates_doctors(in_memory_engine):
    config = CompetitivePilotConfig(
        radii_km=[5.0],
        default_radius_km=5.0,
        high_confidence_min_coverage=0.80,
        medium_confidence_min_coverage=0.50,
        excluded_name_fragments=["clinic", "klinik", "dental"],
        clusters={
            "test": CompetitivePilotCluster(label="Test", anchor_hospital="RS Anchor")
        },
    )

    with Session(in_memory_engine) as session:
        anchor = _hospital(session, "RS Anchor", -6.20, 106.80)
        _metrics(session, anchor, DermatologistCountStatus.HAS_DOCTORS, 1, 4.0)

        first = _hospital(session, "RS Nearby One", -6.20, 106.81)
        _metrics(session, first, DermatologistCountStatus.HAS_DOCTORS, 2, 10.0)
        _doctor(session, first, "dokter-sama")
        _doctor(session, first, "dokter-satu")

        second = _hospital(session, "Hospital Nearby Two", -6.21, 106.80)
        _metrics(session, second, DermatologistCountStatus.HAS_DOCTORS, 1, 5.0)
        _doctor(session, second, "dokter-sama")

        confirmed_zero = _hospital(session, "RS Nearby Zero", -6.21, 106.81)
        _metrics(session, confirmed_zero, DermatologistCountStatus.CONFIRMED_ZERO, 0, 0.0)

        unknown = _hospital(session, "RS Nearby Unknown", -6.19, 106.80)
        _metrics(session, unknown, DermatologistCountStatus.UNKNOWN, None, None)

        clinic = _hospital(session, "Beauty Clinic Nearby", -6.20, 106.805)
        _metrics(session, clinic, DermatologistCountStatus.HAS_DOCTORS, 8, 80.0)
        _doctor(session, clinic, "dokter-klinik")

        medical_center = _hospital(session, "Medical Center Nearby", -6.20, 106.806)
        _metrics(session, medical_center, DermatologistCountStatus.HAS_DOCTORS, 7, 70.0)
        _doctor(session, medical_center, "dokter-medical-center")

        verified_group = _hospital(session, "Verified Bare Brand", -6.20, 106.807)
        verified_group.preferred_rank_group = "Verified Group"
        _metrics(session, verified_group, DermatologistCountStatus.HAS_DOCTORS, 1, 2.0)
        _doctor(session, verified_group, "dokter-brand")

        outside = _hospital(session, "RS Outside", -6.30, 106.80)
        _metrics(session, outside, DermatologistCountStatus.HAS_DOCTORS, 9, 90.0)
        _doctor(session, outside, "dokter-luar")
        session.flush()

        result = compute_competitive_pilot(session, config)["test"][0]

    assert result.nearby_hospitals_count == 5
    assert result.nearby_known_status_count == 4
    assert result.nearby_unknown_hospitals_count == 1
    assert result.nearby_derm_hospitals_count == 3
    assert result.nearby_confirmed_zero_count == 1
    assert result.nearby_dermatologists_unique == 3
    assert result.nearby_derm_doctor_hours_week == 17.0
    assert result.known_status_coverage_ratio == 0.8
    assert result.data_quality == ConfidenceLevel.HIGH
    assert all("Clinic" not in detail.hospital_name for detail in result.hospitals)
    assert all("Medical Center" not in detail.hospital_name for detail in result.hospitals)
