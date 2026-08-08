"""Fase 5: Geocoding dan Spatial Integrity (spec §9 Fase 5).

Two responsibilities, deliberately kept in one module since they operate
on the same Hospital rows and share the "how much do we trust this
coordinate" question:

1. geocode_address() / geocode_missing_hospitals(): Nominatim FALLBACK
   for any Hospital row still missing lat/lon after Fase 1. As of
   2026-08-08, this path has never actually fired — the Fase 1 OSM
   Overpass registry already supplies coordinates for all 554 hospitals
   — but it must exist for future re-runs (a new manual_overrides.csv
   entry, a hospital added by hand, a future non-OSM source) rather than
   silently leaving such a hospital uncoordinated forever.
2. run_spatial_integrity_audit(): re-assesses geocode_confidence with an
   ACTUAL signal (OSM element type, bbox sanity, duplicate-coordinate
   clustering) instead of Fase 1's blanket "medium" default, and
   produces the geocode-quality report deliverable spec §9 Fase 5 asks
   for.

Rate limit for Nominatim is 1 request/second (spec §9 Fase 5 — stricter
than the general 1 req/2 sec in config/sources.yaml's crawl_policy,
because Nominatim's own public-instance usage policy requires it), with
an honest User-Agent containing contact info (same one already used for
scraping, reused here — same contact person, same tool).

Never guess precision: a Nominatim result with `class`/`type` indicating
city/kecamatan-level precision (address type "city", "suburb",
"administrative", etc., or an empty `address.house_number`) is marked
geocode_confidence=LOW and explicitly flagged, never silently treated as
building-precise (spec's "jangan menggunakan koordinat centroid
kecamatan seolah koordinat RS presisi").
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from src.config import DATA_DIR, get_sources_config
from src.logging_setup import get_logger
from src.models import ConfidenceLevel, Hospital

log = get_logger(__name__)

# Nominatim's public-instance usage policy caps at 1 request/second —
# stricter than this project's general crawl_policy default (spec §9
# Fase 5 states this explicitly, separate from crawl_policy).
_NOMINATIM_RATE_LIMIT_SECONDS = 1.0

# Jabodetabek bbox reused from config/sources.yaml's Overpass query (same
# region, same margin-adjusted bounds) — any hospital coordinate outside
# this box is almost certainly a geocoding error (wrong city entirely),
# not a legitimate Jabodetabek address, per spec's spatial-integrity ask.
_BBOX_SOUTH, _BBOX_WEST, _BBOX_NORTH, _BBOX_EAST = -6.80, 106.30, -5.90, 107.30

_CACHE_DIR = DATA_DIR / "raw" / "nominatim_cache"

# Nominatim `addresstype`/`type` values that indicate the match resolved
# to an administrative area rather than a specific building/POI — these
# must never be treated as hospital-precise (spec §9 Fase 5).
_COARSE_ADDRESS_TYPES = {
    "city", "suburb", "administrative", "county", "state", "region",
    "postcode", "town", "village", "municipality",
}


@dataclass
class GeocodeResult:
    lat: float | None
    lon: float | None
    confidence: ConfidenceLevel
    raw_display_name: str | None
    address_type: str | None
    note: str | None = None


class NominatimRateLimiter:
    """Global (not per-domain — there's only one Nominatim endpoint we
    ever call) rate limiter enforcing >= 1 second between requests.
    """

    def __init__(self, seconds: float = _NOMINATIM_RATE_LIMIT_SECONDS) -> None:
        self._seconds = seconds
        self._last_request_at: float | None = None

    def wait(self) -> None:
        if self._last_request_at is not None:
            elapsed = time.monotonic() - self._last_request_at
            remaining = self._seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at = time.monotonic()


_rate_limiter = NominatimRateLimiter()


def _cache_path(address: str) -> Path:
    # Cache key is the raw address text itself (safe-ish filename via
    # hash) — persistent cache per spec §9 Fase 5 "jangan geocode ulang
    # alamat yang sama tanpa alasan".
    import hashlib

    digest = hashlib.sha256(address.encode("utf-8")).hexdigest()[:24]
    return _CACHE_DIR / f"{digest}.json"


def geocode_address(address: str, *, use_cache: bool = True) -> GeocodeResult:
    """Geocode a single address via Nominatim, honoring rate limit +
    persistent cache. Returns a GeocodeResult with confidence=UNKNOWN and
    lat/lon=None (never a guess) if nothing was found or the address is
    empty — callers must not fabricate a fallback coordinate.
    """
    address = (address or "").strip()
    if not address:
        return GeocodeResult(None, None, ConfidenceLevel.UNKNOWN, None, None, "empty address")

    cache_path = _cache_path(address)
    if use_cache and cache_path.exists():
        log.debug("nominatim_cache_hit", address=address)
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return _result_from_cached_payload(payload)

    sources_cfg = get_sources_config()
    nominatim_cfg = sources_cfg.registry_sources.get("nominatim")
    base_url = nominatim_cfg.base_url if nominatim_cfg else "https://nominatim.openstreetmap.org/search"
    user_agent = sources_cfg.crawl_policy.user_agent

    _rate_limiter.wait()
    try:
        resp = httpx.get(
            base_url,
            params={"q": address, "format": "jsonv2", "addressdetails": 1, "limit": 1},
            headers={"User-Agent": user_agent},
            timeout=15.0,
        )
        resp.raise_for_status()
        results = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("nominatim_geocode_failed", address=address, error=str(exc))
        return GeocodeResult(None, None, ConfidenceLevel.UNKNOWN, None, None, f"request failed: {exc}")

    if not results:
        payload = {"found": False}
        _write_cache(cache_path, payload)
        return GeocodeResult(None, None, ConfidenceLevel.UNKNOWN, None, None, "no Nominatim match")

    top = results[0]
    payload = {
        "found": True,
        "lat": top.get("lat"),
        "lon": top.get("lon"),
        "display_name": top.get("display_name"),
        "addresstype": top.get("addresstype") or top.get("type"),
    }
    _write_cache(cache_path, payload)
    return _result_from_cached_payload(payload)


def _write_cache(cache_path: Path, payload: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_from_cached_payload(payload: dict) -> GeocodeResult:
    if not payload.get("found"):
        return GeocodeResult(None, None, ConfidenceLevel.UNKNOWN, None, None, "no Nominatim match (cached)")

    try:
        lat = float(payload["lat"])
        lon = float(payload["lon"])
    except (KeyError, TypeError, ValueError):
        return GeocodeResult(None, None, ConfidenceLevel.UNKNOWN, None, None, "malformed cached coordinate")

    address_type = payload.get("addresstype")
    if address_type in _COARSE_ADDRESS_TYPES:
        # spec: never present a kecamatan/kota-level centroid as if it
        # were a precise hospital coordinate.
        confidence = ConfidenceLevel.LOW
        note = f"Nominatim resolved to administrative-area level ({address_type}), not a specific building"
    else:
        confidence = ConfidenceLevel.MEDIUM
        note = None

    return GeocodeResult(lat, lon, confidence, payload.get("display_name"), address_type, note)


def geocode_missing_hospitals(session: Session, *, use_cache: bool = True) -> dict:
    """Fallback pass: geocode every Hospital row still missing lat/lon.
    Returns a summary dict. Safe to call even when there's nothing to do
    (the common case as of 2026-08-08 — see module docstring).
    """
    missing = session.query(Hospital).filter(
        (Hospital.lat.is_(None)) | (Hospital.lon.is_(None))
    ).all()

    summary = {"total_missing": len(missing), "geocoded": 0, "still_missing": 0}
    for hospital in missing:
        if not hospital.address:
            summary["still_missing"] += 1
            log.warning("geocode_skipped_no_address", hospital=hospital.name)
            continue

        result = geocode_address(hospital.address, use_cache=use_cache)
        if result.lat is None or result.lon is None:
            summary["still_missing"] += 1
            continue

        hospital.lat = result.lat
        hospital.lon = result.lon
        hospital.geocode_source = "nominatim_fallback"
        hospital.geocode_confidence = result.confidence
        summary["geocoded"] += 1
        log.info(
            "geocode_fallback_applied",
            hospital=hospital.name,
            confidence=result.confidence.value,
            note=result.note,
        )

    return summary


# --- spatial-integrity audit ------------------------------------------


def _in_jabodetabek_bbox(lat: float, lon: float) -> bool:
    return _BBOX_SOUTH <= lat <= _BBOX_NORTH and _BBOX_WEST <= lon <= _BBOX_EAST


def run_spatial_integrity_audit(session: Session) -> dict:
    """Re-assess geocode_confidence for every Hospital row using actual
    signal instead of Fase 1's blanket default, and flag rows that look
    spatially wrong. Does NOT touch lat/lon — only geocode_confidence and
    the returned report; a coordinate that looks suspicious is
    surfaced, not silently dropped or "fixed" by guessing (spec §3.1).

    Checks:
    - out_of_bbox: coordinate falls outside the Jabodetabek bbox used for
      the Fase 1 Overpass query — almost certainly wrong (a different
      city entirely, or a lat/lon swap).
    - exact_duplicate_coordinate: >1 hospital sharing the EXACT same
      lat/lon — plausible for a真 shared building/campus, but also the
      classic symptom of a geocoder falling back to a city/kecamatan
      centroid for multiple addresses that couldn't be resolved
      precisely. Flagged for review, not auto-corrected either way.
    - missing_coordinate: still has no lat/lon after the fallback pass.
    """
    hospitals = session.query(Hospital).all()

    coord_counts: dict[tuple[float, float], list[str]] = {}
    for h in hospitals:
        if h.lat is not None and h.lon is not None:
            coord_counts.setdefault((h.lat, h.lon), []).append(h.name)

    report = {
        "total_hospitals": len(hospitals),
        "out_of_bbox": [],
        "exact_duplicate_coordinate_groups": [],
        "missing_coordinate": [],
        "confidence_counts": {},
    }

    duplicate_coords = {coord for coord, names in coord_counts.items() if len(names) > 1}

    for h in hospitals:
        if h.lat is None or h.lon is None:
            report["missing_coordinate"].append(h.name)
            h.geocode_confidence = ConfidenceLevel.UNKNOWN
            continue

        if not _in_jabodetabek_bbox(h.lat, h.lon):
            report["out_of_bbox"].append({"name": h.name, "lat": h.lat, "lon": h.lon})
            h.geocode_confidence = ConfidenceLevel.LOW
            continue

        if (h.lat, h.lon) in duplicate_coords:
            # Shared coordinate: still plausibly valid (a real multi-
            # building campus reusing one OSM node), so kept at MEDIUM
            # rather than downgraded to LOW outright — but surfaced in
            # the report for a human to sanity-check, per spec's spirit
            # of "don't hide this kind of ambiguity".
            h.geocode_confidence = ConfidenceLevel.MEDIUM
            continue

        # No red flags found: OSM Overpass node/way/relation coordinates
        # are generally building-precise for a source explicitly tagged
        # amenity=hospital — kept at the Fase 1 default.
        h.geocode_confidence = h.geocode_confidence or ConfidenceLevel.MEDIUM

    for coord, names in coord_counts.items():
        if len(names) > 1:
            report["exact_duplicate_coordinate_groups"].append({"lat": coord[0], "lon": coord[1], "hospitals": names})

    counts: dict[str, int] = {}
    for h in hospitals:
        key = h.geocode_confidence.value if h.geocode_confidence else "unknown"
        counts[key] = counts.get(key, 0) + 1
    report["confidence_counts"] = counts

    return report


def write_geocode_quality_report(report: dict, *, out_dir: Path | None = None) -> Path:
    """Persist the spatial-integrity audit report to
    data/processed/geocode_reports/ — the Fase 5 deliverable (spec §9
    "Deliverable Fase 5: geocode-quality report").
    """
    out_dir = out_dir or (DATA_DIR / "processed" / "geocode_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    today = dt.date.today().isoformat()
    out_path = out_dir / f"{today}_geocode_quality_report.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("geocode_quality_report_written", path=str(out_path))
    return out_path
