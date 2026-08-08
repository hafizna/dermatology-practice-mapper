"""Config loader — spec §6 (pydantic-settings + YAML) and §7 (config/ tree).

All thresholds, weights, and preferred-hospital lists must live in
`config/*.yaml`, never hardcoded in scraper/scoring code (spec §5, §7.2,
§10 Fase 7.2). This module is the single place that reads those files and
validates their shape.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
DATA_DIR = REPO_ROOT / "data"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}. Expected config/ files are documented "
            "in PROJECT_SPEC.md §7."
        )
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------------
# config/hospital_preferences.yaml
# ---------------------------------------------------------------------------


class HospitalPreferencesConfig(BaseModel):
    preferred_groups: list[str] = Field(default_factory=list)
    include_ownership: list[str] = Field(default_factory=list)
    exclude_hospital_types: list[str] = Field(default_factory=list)
    manual_preference: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "HospitalPreferencesConfig":
        return cls.model_validate(_load_yaml(path or CONFIG_DIR / "hospital_preferences.yaml"))


# ---------------------------------------------------------------------------
# config/prime_time.yaml
# ---------------------------------------------------------------------------


class TimeWindow(BaseModel):
    days: list[int]
    start: str
    end: str


class PrimeTimeConfig(BaseModel):
    weekday_evening: TimeWindow
    saturday: TimeWindow
    weekend_full: TimeWindow | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "PrimeTimeConfig":
        return cls.model_validate(_load_yaml(path or CONFIG_DIR / "prime_time.yaml"))


# ---------------------------------------------------------------------------
# config/scoring.yaml
# ---------------------------------------------------------------------------


class CoreOpportunityWeights(BaseModel):
    dermatologist_count_scarcity: float
    doctor_hours_scarcity: float
    prime_time_gap: float
    weekend_gap: float

    def validate_sums_to_one(self, tol: float = 1e-6) -> None:
        total = (
            self.dermatologist_count_scarcity
            + self.doctor_hours_scarcity
            + self.prime_time_gap
            + self.weekend_gap
        )
        if abs(total - 1.0) > tol:
            raise ValueError(
                f"core_opportunity weights must sum to 1.0, got {total} "
                "(config/scoring.yaml)"
            )


class ScoringConfig(BaseModel):
    core_opportunity: CoreOpportunityWeights
    normalization_method: str = "percentile"
    winsorize_limits: list[float] = Field(default_factory=lambda: [0.05, 0.05])
    low_dermatologist_count_flag_threshold: int = 4
    minimum_schedule_completeness: float = 0.70

    @classmethod
    def load(cls, path: Path | None = None) -> "ScoringConfig":
        cfg = cls.model_validate(_load_yaml(path or CONFIG_DIR / "scoring.yaml"))
        cfg.core_opportunity.validate_sums_to_one()
        return cfg


# ---------------------------------------------------------------------------
# config/sources.yaml
# ---------------------------------------------------------------------------


class CrawlPolicy(BaseModel):
    respect_robots_txt: bool = True
    rate_limit_seconds_per_domain: float = 2.0
    user_agent: str
    max_retries: int = 3
    backoff_factor: float = 2.0
    allow_playwright_if_js_required: bool = True
    allow_evasion_techniques: bool = False

    def model_post_init(self, __context: Any) -> None:
        if self.allow_evasion_techniques:
            # Hard stop, not just a default — spec §3.6 forbids this outright.
            raise ValueError(
                "allow_evasion_techniques=true violates PROJECT_SPEC.md §3.6 "
                "and is not permitted."
            )


class RegistrySourceEntry(BaseModel):
    tier: str
    base_url: str | None = None
    query_tag: str | None = None
    bbox_jabodetabek: str | None = None
    notes: str | None = None


class DoctorScheduleSourceEntry(BaseModel):
    tier: str
    display_name: str
    base_urls: list[str] = Field(default_factory=list)
    requires_js: bool | None = None
    # "accessible" | "blocked" | null (not yet checked) — recon-level
    # signal only, not a guarantee the doctor/schedule structure has been
    # mapped (that happens per-adapter in Fase 3).
    access_status: str | None = None
    notes: str | None = None


class AggregatorSourceEntry(BaseModel):
    tier: str
    base_urls: list[str] = Field(default_factory=list)
    notes: str | None = None


class SourcesConfig(BaseModel):
    crawl_policy: CrawlPolicy
    source_tiers: dict[str, str] = Field(default_factory=dict)
    registry_sources: dict[str, RegistrySourceEntry] = Field(default_factory=dict)
    doctor_schedule_sources: dict[str, DoctorScheduleSourceEntry] = Field(default_factory=dict)
    aggregator_sources: dict[str, AggregatorSourceEntry] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> "SourcesConfig":
        return cls.model_validate(_load_yaml(path or CONFIG_DIR / "sources.yaml"))


# ---------------------------------------------------------------------------
# App-level settings (env-overridable) — spec §6
# ---------------------------------------------------------------------------


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DERM_MAPPER_", env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{(DATA_DIR / 'processed' / 'derm_mapper.sqlite').as_posix()}"
    raw_data_dir: Path = DATA_DIR / "raw"
    processed_data_dir: Path = DATA_DIR / "processed"
    reference_data_dir: Path = DATA_DIR / "reference"
    log_level: str = "INFO"
    log_json: bool = False


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    return AppSettings()


@lru_cache(maxsize=1)
def get_hospital_preferences() -> HospitalPreferencesConfig:
    return HospitalPreferencesConfig.load()


@lru_cache(maxsize=1)
def get_prime_time_config() -> PrimeTimeConfig:
    return PrimeTimeConfig.load()


@lru_cache(maxsize=1)
def get_scoring_config() -> ScoringConfig:
    return ScoringConfig.load()


@lru_cache(maxsize=1)
def get_sources_config() -> SourcesConfig:
    return SourcesConfig.load()
