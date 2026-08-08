"""Human-verified hospital rows that are missing from the OSM registry.

``config/manual_hospitals.csv`` is deliberately separate from
``manual_overrides.csv``: an override links or corrects an existing row,
whereas this file supplies a whole physical facility that the current OSM
snapshot does not contain.  Coordinates are mandatory so a branch is never
created from a city centroid or an unverified name-only guess.
"""

from __future__ import annotations

import csv
import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from src.config import CONFIG_DIR
from src.models import ConfidenceLevel


@dataclass(frozen=True)
class ManualHospitalRecord:
    name: str
    group: str
    ownership: str | None
    hospital_type: str | None
    address: str
    kota_kab: str | None
    lat: float
    lon: float
    geocode_source: str
    geocode_confidence: ConfidenceLevel
    website: str | None
    source_url: str
    geocode_source_url: str
    source_note: str | None
    verified_at: dt.datetime


def load_manual_hospitals(path: Path | None = None) -> list[ManualHospitalRecord]:
    """Load and validate complete manual hospital rows.

    Invalid or incomplete rows raise ``ValueError`` instead of being silently
    skipped.  This is curated configuration, so failing loudly is safer than
    running a registry that only contains an arbitrary subset of the file.
    """
    csv_path = path or CONFIG_DIR / "manual_hospitals.csv"
    if not csv_path.exists():
        return []

    records: list[ManualHospitalRecord] = []
    seen_names: set[str] = set()
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        for line_number, row in enumerate(csv.DictReader(fh), start=2):
            name = (row.get("name") or "").strip()
            group = (row.get("group") or "").strip()
            address = (row.get("address") or "").strip()
            source_url = (row.get("source_url") or "").strip()
            geocode_source_url = (row.get("geocode_source_url") or "").strip()
            if not all((name, group, address, source_url, geocode_source_url)):
                raise ValueError(
                    f"{csv_path}:{line_number}: name/group/address/source URLs are required"
                )
            if name.casefold() in seen_names:
                raise ValueError(f"{csv_path}:{line_number}: duplicate hospital name {name!r}")

            try:
                lat = float(row["lat"])
                lon = float(row["lon"])
                confidence = ConfidenceLevel((row.get("geocode_confidence") or "").strip())
                verified_at = dt.datetime.fromisoformat((row.get("verified_at") or "").strip())
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{csv_path}:{line_number}: invalid coordinate, confidence, or verified_at"
                ) from exc

            # Same deliberately generous Jabodetabek bounds used by the
            # spatial-integrity audit.  This catches swapped/malformed values,
            # not whether a point is the correct building (that is human QA).
            if not (-6.80 <= lat <= -5.90 and 106.30 <= lon <= 107.30):
                raise ValueError(
                    f"{csv_path}:{line_number}: coordinate outside Jabodetabek bounds"
                )

            records.append(
                ManualHospitalRecord(
                    name=name,
                    group=group,
                    ownership=(row.get("ownership") or "").strip() or None,
                    hospital_type=(row.get("hospital_type") or "").strip() or None,
                    address=address,
                    kota_kab=(row.get("kota_kab") or "").strip() or None,
                    lat=lat,
                    lon=lon,
                    geocode_source=(row.get("geocode_source") or "").strip() or "manual_verified",
                    geocode_confidence=confidence,
                    website=(row.get("website") or "").strip() or None,
                    source_url=source_url,
                    geocode_source_url=geocode_source_url,
                    source_note=(row.get("source_note") or "").strip() or None,
                    verified_at=verified_at,
                )
            )
            seen_names.add(name.casefold())

    return records
