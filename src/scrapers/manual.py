"""Manual override loader — reads config/manual_overrides.csv.

Spec §3 (input manual selalu tersedia) and Fase 3 ("Tier 3 = manual
override" has highest priority for explicitly overridden fields, but the
scraper-derived value must still be kept for audit).

This module only loads/validates the CSV; applying overrides on top of
scraped records happens in src/registry/merge.py (Fase 1) and per-adapter
pipelines (Fase 3), so overrides are visible at every stage rather than
silently baked into raw data.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from src.config import CONFIG_DIR


@dataclass
class ManualOverride:
    entity_type: str  # e.g. "hospital", "doctor", "schedule_slot"
    entity_key: str  # e.g. hospital name_normalized, or doctor normalized_person_key
    field: str
    override_value: str
    reason: str | None
    source_note: str | None
    overridden_by: str | None
    overridden_at: dt.datetime | None


def load_manual_overrides(path: Path | None = None) -> list[ManualOverride]:
    csv_path = path or CONFIG_DIR / "manual_overrides.csv"
    if not csv_path.exists():
        return []

    overrides: list[ManualOverride] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if not row.get("entity_type"):
                continue
            overridden_at = None
            raw_ts = row.get("overridden_at")
            if raw_ts:
                try:
                    overridden_at = dt.datetime.fromisoformat(raw_ts)
                except ValueError:
                    overridden_at = None
            overrides.append(
                ManualOverride(
                    entity_type=row["entity_type"],
                    entity_key=row["entity_key"],
                    field=row["field"],
                    override_value=row["override_value"],
                    reason=row.get("reason") or None,
                    source_note=row.get("source_note") or None,
                    overridden_by=row.get("overridden_by") or None,
                    overridden_at=overridden_at,
                )
            )
    return overrides
