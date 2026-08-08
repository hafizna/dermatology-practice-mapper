"""Overpass API registry source — Fase 1.

Queries `amenity=hospital` (nodes, ways, relations) over the Jabodetabek
bbox declared in config/sources.yaml. Uses `out center` so way/relation
results (hospital building polygons) resolve to a single representative
point, same as node results.

Source-priority note: this is registry data (existence + rough location),
not doctor/schedule data — it is always Tier 1 in the sense that OSM is a
primary structured source, but per spec §3.2 every record still carries
its own provenance rather than being treated as ground truth.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from src.config import DATA_DIR, get_sources_config
from src.logging_setup import get_logger

log = get_logger(__name__)

SCRAPER_VERSION = "0.1.0"


@dataclass
class OsmHospitalRecord:
    """Raw-ish hospital record straight from Overpass, pre-normalization."""

    osm_type: str  # "node" | "way" | "relation"
    osm_id: int
    name: str | None
    lat: float | None
    lon: float | None
    address_raw: dict = field(default_factory=dict)
    website: str | None = None
    tags: dict = field(default_factory=dict)
    source_url: str = ""
    source_tier: str = "tier_1_official"
    scraped_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))


def _build_query(bbox: str) -> str:
    # node/way/relation covers hospitals mapped as a point or as a building
    # polygon. `out center` gives way/relation a representative lat/lon
    # without pulling full geometry (we only need registry-level location).
    return (
        "[out:json][timeout:90];"
        "("
        f'node["amenity"="hospital"]({bbox});'
        f'way["amenity"="hospital"]({bbox});'
        f'relation["amenity"="hospital"]({bbox});'
        ");"
        "out center tags;"
    )


def _extract_address(tags: dict) -> dict:
    return {
        "housenumber": tags.get("addr:housenumber"),
        "street": tags.get("addr:street"),
        "kelurahan": tags.get("addr:suburb") or tags.get("addr:village"),
        "kecamatan": tags.get("addr:subdistrict"),
        "kota_kab": tags.get("addr:city") or tags.get("addr:county"),
        "postcode": tags.get("addr:postcode"),
        "full": tags.get("addr:full"),
    }


def _raw_cache_path() -> Path:
    today = dt.date.today().isoformat()
    return DATA_DIR / "raw" / "registry" / today / "overpass_hospitals.json"


def fetch_osm_hospitals(*, use_cache_if_present: bool = True) -> list[OsmHospitalRecord]:
    """Fetch hospitals from Overpass API for the configured Jabodetabek bbox.

    Caches the raw response to data/raw/registry/{date}/ so repeated runs
    during development don't hit the server again (spec §3.2 raw_payload_path
    / Fase 2 caching principle, applied here too since Fase 1 also touches
    a live network source).
    """
    sources = get_sources_config()
    overpass_cfg = sources.registry_sources.get("overpass")
    if overpass_cfg is None or not overpass_cfg.base_url or not overpass_cfg.bbox_jabodetabek:
        raise RuntimeError("config/sources.yaml: registry_sources.overpass tidak lengkap.")

    cache_path = _raw_cache_path()
    if use_cache_if_present and cache_path.exists():
        log.info("overpass_cache_hit", path=str(cache_path))
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    else:
        query = _build_query(overpass_cfg.bbox_jabodetabek)
        headers = {"User-Agent": sources.crawl_policy.user_agent}
        log.info(
            "overpass_fetch_start",
            url=overpass_cfg.base_url,
            bbox=overpass_cfg.bbox_jabodetabek,
        )
        with httpx.Client(timeout=120.0, headers=headers) as client:
            resp = client.post(overpass_cfg.base_url, data={"data": query})
            resp.raise_for_status()
            payload = resp.json()

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("overpass_fetch_done", n_elements=len(payload.get("elements", [])), cache_path=str(cache_path))

        # Per-domain rate limit — spec §3.6 default 1 request / 2 seconds.
        # Only one request is made per run here, but sleeping keeps the
        # behavior correct if this function is ever called in a loop.
        time.sleep(sources.crawl_policy.rate_limit_seconds_per_domain)

    records: list[OsmHospitalRecord] = []
    scraped_at = dt.datetime.now(dt.timezone.utc)
    for el in payload.get("elements", []):
        tags = el.get("tags", {}) or {}
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")

        records.append(
            OsmHospitalRecord(
                osm_type=el.get("type", "unknown"),
                osm_id=el.get("id"),
                name=tags.get("name"),
                lat=lat,
                lon=lon,
                address_raw=_extract_address(tags),
                website=tags.get("website") or tags.get("contact:website"),
                tags=tags,
                source_url=f"https://www.openstreetmap.org/{el.get('type')}/{el.get('id')}",
                scraped_at=scraped_at,
            )
        )

    n_unnamed = sum(1 for r in records if not r.name)
    log.info("overpass_parsed", n_total=len(records), n_unnamed=n_unnamed)
    return records
