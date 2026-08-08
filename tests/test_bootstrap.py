"""Fase 0 smoke tests: schema creation, config loading, CLI help."""

from __future__ import annotations

import subprocess
import sys

from click.testing import CliRunner
from sqlalchemy import inspect

from src.cli import cli
from src.config import (
    HospitalPreferencesConfig,
    PrimeTimeConfig,
    ScoringConfig,
    SourcesConfig,
)


def test_schema_creates_expected_tables(in_memory_engine) -> None:
    inspector = inspect(in_memory_engine)
    tables = set(inspector.get_table_names())
    expected = {
        "hospitals",
        "doctors",
        "schedule_slots",
        "hospital_practice_metrics",
        "competitive_context_metrics",
        "market_attractiveness_metrics",
        "scrape_logs",
    }
    assert expected.issubset(tables)


def test_hospital_preferences_config_loads() -> None:
    cfg = HospitalPreferencesConfig.load()
    assert "Eka Hospital" in cfg.preferred_groups
    assert "Columbia Asia" in cfg.preferred_groups
    assert "swasta" in cfg.include_ownership
    # manual_preference must never silently leak into Layer A weighting —
    # this test only checks the file parses, not that it's used correctly
    # (that guarantee lives in scoring tests from Fase 7 onward).
    assert isinstance(cfg.manual_preference, dict)


def test_prime_time_config_loads() -> None:
    cfg = PrimeTimeConfig.load()
    assert cfg.weekday_evening.days == [0, 1, 2, 3, 4]
    assert cfg.saturday.days == [5]


def test_scoring_config_weights_sum_to_one() -> None:
    cfg = ScoringConfig.load()
    total = (
        cfg.core_opportunity.dermatologist_count_scarcity
        + cfg.core_opportunity.doctor_hours_scarcity
        + cfg.core_opportunity.prime_time_gap
        + cfg.core_opportunity.weekend_gap
    )
    assert abs(total - 1.0) < 1e-6


def test_sources_config_forbids_evasion_by_default() -> None:
    cfg = SourcesConfig.load()
    assert cfg.crawl_policy.allow_evasion_techniques is False
    assert cfg.crawl_policy.respect_robots_txt is True


def test_cli_help_lists_all_required_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for command in ["init-db", "fetch-registry", "scrape", "compute-core", "serve"]:
        assert command in result.output


def test_cli_invocable_as_module() -> None:
    # Mirrors `python -m src.cli --help` from PROJECT_SPEC.md §9 Fase 0.
    proc = subprocess.run(
        [sys.executable, "-m", "src.cli", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    assert "fetch-registry" in proc.stdout
