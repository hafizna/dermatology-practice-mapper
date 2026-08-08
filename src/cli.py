"""CLI entrypoint — spec §9 Fase 0.

    python -m src.cli init-db
    python -m src.cli fetch-registry
    python -m src.cli scrape --group eka
    python -m src.cli scrape --all
    python -m src.cli compute-core
    python -m src.cli serve

Commands for phases not yet built raise a clear NotImplementedError-style
message rather than silently doing nothing (spec §3.1 — no faking results).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from src.config import get_settings
from src.db import init_db
from src.logging_setup import configure_logging, get_logger

log = get_logger(__name__)


@click.group()
def cli() -> None:
    """Dermatology Practice Opportunity Mapper — CLI."""
    configure_logging()


@cli.command("init-db")
def init_db_command() -> None:
    """Create SQLite schema from src/models.py (idempotent)."""
    init_db()
    settings = get_settings()
    click.echo(f"Database initialized at: {settings.database_url}")


@cli.command("fetch-registry")
@click.option(
    "--source",
    type=click.Choice(["overpass", "kemkes", "all"]),
    default="all",
    show_default=True,
    help="Which registry source to pull before merge/dedup (Fase 1).",
)
def fetch_registry_command(source: str) -> None:
    """Fase 1 — Hospital Master Registry (Overpass + Kemkes + dedup)."""
    from src.registry.merge import run_registry_pipeline

    run_registry_pipeline(source=source)


@cli.command("scrape")
@click.option("--group", type=str, default=None, help="Adapter group name, e.g. 'eka'.")
@click.option("--all", "scrape_all", is_flag=True, default=False, help="Scrape all configured groups.")
def scrape_command(group: str | None, scrape_all: bool) -> None:
    """Fase 2/3 — run one or all doctor/schedule scraper adapters."""
    if not group and not scrape_all:
        raise click.UsageError("Pass --group <name> or --all.")

    from src.scrapers.registry import run_scrape

    run_scrape(group=group, scrape_all=scrape_all)


@cli.command("geocode")
def geocode_command() -> None:
    """Fase 5 — Nominatim fallback for any Hospital missing lat/lon, then
    spatial-integrity audit (bbox check, duplicate-coordinate detection,
    geocode_confidence re-assessment) + geocode-quality report."""
    from src.db import session_scope
    from src.enrich.geocode import (
        geocode_missing_hospitals,
        run_spatial_integrity_audit,
        write_geocode_quality_report,
    )

    with session_scope() as session:
        fallback_summary = geocode_missing_hospitals(session)
        log.info("geocode_fallback_summary", **fallback_summary)

        report = run_spatial_integrity_audit(session)

    report["fallback_summary"] = fallback_summary
    out_path = write_geocode_quality_report(report)

    print(f"Total hospitals: {report['total_hospitals']}")
    print(f"Geocode fallback: {fallback_summary['geocoded']} diisi, {fallback_summary['still_missing']} masih kosong.")
    print(f"Di luar bbox Jabodetabek: {len(report['out_of_bbox'])}")
    print(f"Grup koordinat identik (perlu review): {len(report['exact_duplicate_coordinate_groups'])}")
    print(f"Distribusi geocode_confidence: {report['confidence_counts']}")
    print(f"Laporan lengkap: {out_path}")


@cli.command("compute-core")
@click.option(
    "--universe",
    type=click.Choice(["preferred_private", "all_private", "all_hospitals"]),
    default="preferred_private",
    show_default=True,
    help="Peer group used for percentile normalization (spec §7.1).",
)
def compute_core_command(universe: str) -> None:
    """Fase 6/7 — coverage matrix, supply metrics, and opportunity_score."""
    from src.metrics.coverage import build_coverage_matrix
    from src.scoring.core import compute_core_opportunity_scores

    build_coverage_matrix()
    compute_core_opportunity_scores(universe=universe)


@cli.command("serve")
@click.option("--port", type=int, default=8501, show_default=True)
def serve_command(port: int) -> None:
    """Fase 8 — launch the Streamlit dashboard."""
    app_path = Path(__file__).resolve().parent / "app.py"
    cmd = [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port)]
    log.info("launching_streamlit", cmd=" ".join(cmd))
    subprocess.run(cmd, check=False)


@cli.command("check-sources")
def check_sources_command() -> None:
    """Run scripts/check_sources.py — source-drift detection (spec §14)."""
    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "scripts" / "check_sources.py"
    subprocess.run([sys.executable, str(script_path)], check=False, cwd=repo_root)


if __name__ == "__main__":
    cli()
