"""SQLAlchemy ORM schema — spec §8 (Data Model).

Prinsip yang mengikat model ini (lihat PROJECT_SPEC.md §3):

- Setiap record yang berasal dari scraping wajib punya provenance
  (`source_url`, `source_tier`, `scraped_at`) sehingga dapat diverifikasi
  manual (§3.2).
- Field yang tidak diketahui disimpan sebagai NULL, bukan ditebak (§3.1,
  §3.5). "Unknown" adalah status yang sah, bukan nol.
- Skor (`opportunity_score` dst.) tidak pernah dinamai `probability` (§3.3).

Semua tabel dibuat lewat SQLite (zero-config) via SQLAlchemy Core/ORM,
sesuai stack §6.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# Shared enums
# ---------------------------------------------------------------------------


class DataStatus(str, enum.Enum):
    """Hospital.data_status — spec §8.1."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    SCRAPE_FAILED = "scrape_failed"
    MANUAL = "manual"


class SourceTier(str, enum.Enum):
    """Tier 1 = official site, Tier 2 = aggregator, Tier 3 = manual override.

    Spec §3.7 / Fase 3 "Source priority".
    """

    TIER_1_OFFICIAL = "tier_1_official"
    TIER_2_AGGREGATOR = "tier_2_aggregator"
    TIER_3_MANUAL = "tier_3_manual"


class ConfidenceLevel(str, enum.Enum):
    """Generic High/Medium/Low/Unknown bucket for UI display — spec §15."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ScoreStatus(str, enum.Enum):
    """HospitalPracticeMetrics.score_status — spec §7.5."""

    OK = "ok"
    INSUFFICIENT_DATA = "insufficient_data"


class DermatologistCountStatus(str, enum.Enum):
    """Distinguishes the three meanings of n_dermatologists_unique == 0.

    Spec §7.6 — must not be conflated:
    1. confirmed_zero   — service exists, genuinely zero registered doctors.
    2. no_derm_service  — hospital does not appear to offer dermatology.
    3. unknown          — scraper/data insufficient to tell.
    """

    HAS_DOCTORS = "has_doctors"
    CONFIRMED_ZERO = "confirmed_zero"
    NO_DERM_SERVICE = "no_derm_service"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Hospital — spec §8.1
# ---------------------------------------------------------------------------


class Hospital(Base):
    __tablename__ = "hospitals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    name: Mapped[str] = mapped_column(String, nullable=False)
    name_normalized: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # JSON-encoded list[str]; kept as Text to stay dependency-light in SQLite.
    aliases_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    group: Mapped[str | None] = mapped_column(String, nullable=True)
    ownership: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. "swasta" / "pemerintah"
    hospital_class: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. "A", "B", "C", "D"
    hospital_type: Mapped[str | None] = mapped_column(String, nullable=True)  # e.g. "umum", "ibu_anak"

    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    kelurahan: Mapped[str | None] = mapped_column(String, nullable=True)
    kecamatan: Mapped[str | None] = mapped_column(String, nullable=True)
    kota_kab: Mapped[str | None] = mapped_column(String, nullable=True)

    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    geocode_source: Mapped[str | None] = mapped_column(String, nullable=True)
    geocode_confidence: Mapped[ConfidenceLevel | None] = mapped_column(
        Enum(ConfidenceLevel), nullable=True
    )

    website: Mapped[str | None] = mapped_column(String, nullable=True)

    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    source_tier: Mapped[SourceTier | None] = mapped_column(Enum(SourceTier), nullable=True)
    scraped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    data_status: Mapped[DataStatus] = mapped_column(
        Enum(DataStatus), nullable=False, default=DataStatus.UNKNOWN
    )

    # Preferred-hospital-universe flags — spec §5.
    is_preferred_group: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    preferred_rank_group: Mapped[str | None] = mapped_column(String, nullable=True)

    # Dermatology service existence — kept independent from doctor count so
    # n_dermatologists_unique == 0 can be disambiguated (spec §7.6).
    has_dermatology_service: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    doctors: Mapped[list["Doctor"]] = relationship(back_populates="hospital", cascade="all, delete-orphan")
    schedule_slots: Mapped[list["ScheduleSlot"]] = relationship(
        back_populates="hospital", cascade="all, delete-orphan"
    )
    practice_metrics: Mapped["HospitalPracticeMetrics | None"] = relationship(
        back_populates="hospital", uselist=False, cascade="all, delete-orphan"
    )
    competitive_metrics: Mapped["CompetitiveContextMetrics | None"] = relationship(
        back_populates="hospital", uselist=False, cascade="all, delete-orphan"
    )
    market_metrics: Mapped["MarketAttractivenessMetrics | None"] = relationship(
        back_populates="hospital", uselist=False, cascade="all, delete-orphan"
    )

    # No DB-level uniqueness constraint on (name_normalized, kota_kab):
    # kota_kab is too coarse (e.g. all of "DKI Jakarta") to safely dedup on,
    # and two distinct hospitals can legitimately share a normalized name
    # within the same city. Identity/dedup is handled in the application
    # layer by src/registry/merge.py using name similarity *and* geographic
    # distance (spec §9 Fase 1 — no auto-merge without a proper signal).

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Hospital id={self.id} name={self.name!r} status={self.data_status}>"


# ---------------------------------------------------------------------------
# Doctor — spec §8.2
# ---------------------------------------------------------------------------


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), nullable=False, index=True)

    raw_name: Mapped[str] = mapped_column(String, nullable=False)
    clean_name: Mapped[str | None] = mapped_column(String, nullable=True)
    normalized_person_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # JSON-encoded list[str], e.g. ["Sp.KK", "FINSDV"].
    credentials_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    is_dermatologist: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    subspecialty: Mapped[str | None] = mapped_column(String, nullable=True)

    # Cross-hospital identity resolution confidence — spec §8.2, §4.3.
    # Only meaningful when normalized_person_key was assigned via fuzzy
    # matching rather than an exact match.
    identity_match_confidence: Mapped[ConfidenceLevel | None] = mapped_column(
        Enum(ConfidenceLevel), nullable=True
    )

    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    source_tier: Mapped[SourceTier | None] = mapped_column(Enum(SourceTier), nullable=True)
    scraped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    hospital: Mapped[Hospital] = relationship(back_populates="doctors")
    schedule_slots: Mapped[list["ScheduleSlot"]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Doctor id={self.id} hospital_id={self.hospital_id} clean_name={self.clean_name!r}>"


# ---------------------------------------------------------------------------
# ScheduleSlot — spec §8.3
# ---------------------------------------------------------------------------


class ParseConfidence(str, enum.Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ScheduleSlot(Base):
    __tablename__ = "schedule_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), nullable=False, index=True)
    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), nullable=False, index=True)

    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Senin ... 6=Minggu
    start_time: Mapped[str | None] = mapped_column(String, nullable=True)  # "HH:MM", 24h
    end_time: Mapped[str | None] = mapped_column(String, nullable=True)  # None if "selesai" / unparseable

    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    parse_confidence: Mapped[ParseConfidence] = mapped_column(Enum(ParseConfidence), nullable=False)

    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    scraped_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    doctor: Mapped[Doctor] = relationship(back_populates="schedule_slots")
    hospital: Mapped[Hospital] = relationship(back_populates="schedule_slots")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ScheduleSlot id={self.id} day={self.day_of_week} {self.start_time}-{self.end_time}>"


# ---------------------------------------------------------------------------
# HospitalPracticeMetrics — spec §8.4 (Layer A / MVP)
# ---------------------------------------------------------------------------


class HospitalPracticeMetrics(Base):
    __tablename__ = "hospital_practice_metrics"

    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), primary_key=True)

    dermatologist_count_status: Mapped[DermatologistCountStatus] = mapped_column(
        Enum(DermatologistCountStatus), nullable=False, default=DermatologistCountStatus.UNKNOWN
    )
    n_dermatologists_unique: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_sessions_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doctor_hours_week: Mapped[float | None] = mapped_column(Float, nullable=True)
    prime_time_doctor_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    weekend_doctor_hours: Mapped[float | None] = mapped_column(Float, nullable=True)

    coverage_ratio_all: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_ratio_prime: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_ratio_weekend: Mapped[float | None] = mapped_column(Float, nullable=True)

    prime_gap_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    weekend_gap_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    longest_prime_gap_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    doctors_with_external_overlap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mean_external_hospital_count: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Fraction of this hospital's dermatologists whose schedule parsed with
    # confidence medium/high — gate for score eligibility (spec §7.5).
    schedule_completeness: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Raw component values (pre-normalization) kept alongside normalized
    # ones so nothing is hidden from the UI (spec §3.4).
    dermatologist_count_scarcity_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    doctor_hours_scarcity_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    prime_time_gap_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    weekend_gap_raw: Mapped[float | None] = mapped_column(Float, nullable=True)

    dermatologist_count_scarcity_norm: Mapped[float | None] = mapped_column(Float, nullable=True)
    doctor_hours_scarcity_norm: Mapped[float | None] = mapped_column(Float, nullable=True)
    prime_time_gap_norm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weekend_gap_norm: Mapped[float | None] = mapped_column(Float, nullable=True)

    opportunity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_status: Mapped[ScoreStatus] = mapped_column(
        Enum(ScoreStatus), nullable=False, default=ScoreStatus.INSUFFICIENT_DATA
    )
    score_status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    scoring_universe: Mapped[str | None] = mapped_column(String, nullable=True)  # peer group used for normalization

    metrics_version: Mapped[str] = mapped_column(String, nullable=False, default="0.1.0")
    calculated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    hospital: Mapped[Hospital] = relationship(back_populates="practice_metrics")


# ---------------------------------------------------------------------------
# CompetitiveContextMetrics — spec §8.5 (V1.5, schema reserved now)
# ---------------------------------------------------------------------------


class CompetitiveContextMetrics(Base):
    __tablename__ = "competitive_context_metrics"

    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), primary_key=True)
    radius_km: Mapped[float | None] = mapped_column(Float, primary_key=True)

    nearby_hospitals_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nearby_derm_hospitals_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nearby_dermatologists_unique: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nearby_derm_doctor_hours_week: Mapped[float | None] = mapped_column(Float, nullable=True)
    nearby_derm_clinics_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    local_supply_index: Mapped[float | None] = mapped_column(Float, nullable=True)

    data_quality: Mapped[ConfidenceLevel | None] = mapped_column(Enum(ConfidenceLevel), nullable=True)
    calculated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    hospital: Mapped[Hospital] = relationship(back_populates="competitive_metrics")


# ---------------------------------------------------------------------------
# MarketAttractivenessMetrics — spec §8.6 (V2, schema reserved now)
# ---------------------------------------------------------------------------


class MarketAttractivenessMetrics(Base):
    __tablename__ = "market_attractiveness_metrics"

    hospital_id: Mapped[int] = mapped_column(ForeignKey("hospitals.id"), primary_key=True)

    catchment_population: Mapped[float | None] = mapped_column(Float, nullable=True)
    population_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    residential_affluence_proxy: Mapped[float | None] = mapped_column(Float, nullable=True)
    office_density_proxy: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_residential_proxy: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_retail_proxy: Mapped[float | None] = mapped_column(Float, nullable=True)
    healthcare_ecosystem_proxy: Mapped[float | None] = mapped_column(Float, nullable=True)

    market_attractiveness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_quality: Mapped[ConfidenceLevel | None] = mapped_column(Enum(ConfidenceLevel), nullable=True)
    calculated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    hospital: Mapped[Hospital] = relationship(back_populates="market_metrics")


# ---------------------------------------------------------------------------
# ScrapeLog — not in spec §8 explicitly but required by §3.2 provenance /
# §14 source-drift detection (scripts/check_sources.py needs a place to
# record run history distinct from per-record scraped_at).
# ---------------------------------------------------------------------------


class ScrapeRunStatus(str, enum.Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED_NETWORK = "failed_network"
    FAILED_STRUCTURE = "failed_structure"
    BLOCKED = "blocked"


class ScrapeLog(Base):
    __tablename__ = "scrape_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    group_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    hospital_slug: Mapped[str | None] = mapped_column(String, nullable=True)
    target_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[ScrapeRunStatus] = mapped_column(Enum(ScrapeRunStatus), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload_path: Mapped[str | None] = mapped_column(String, nullable=True)
    scraper_version: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
