"""BaseScraper — Fase 2.

Provides what every adapter needs per spec §9 Fase 2:

- retry + exponential backoff (tenacity)
- per-domain rate limiter (simple last-request-time tracking)
- raw response cache (data/raw/{group}/{YYYY-MM-DD}/{hospital_slug}/...)
- structured logging
- provenance metadata (source_url, source_tier, scraped_at, scraper_version)
- cache replay for development (use_cache=True re-reads from disk instead
  of hitting the network)
- error classification (network vs HTTP-status vs parse-shape failures)

Adapters (src/scrapers/eka.py, siloam.py, ...) subclass BaseScraper and
implement discover_hospitals()/fetch_doctors(); HTTP mechanics live here so
each adapter only encodes source-specific knowledge.
"""

from __future__ import annotations

import datetime as dt
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config import DATA_DIR, get_sources_config
from src.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class HospitalRef:
    """Minimal reference to a hospital as discovered by a scraper."""

    name: str
    url: str
    slug: str | None = None
    hospital_id_upstream: str | None = None  # source's own ID, if any (e.g. Siloam hospital_id UUID)


@dataclass
class RawDoctorRecord:
    """Raw (unparsed) doctor/schedule payload as scraped, pre-normalization.

    `raw_payload` keeps the full source JSON/HTML fragment for audit
    (spec §3.2) — parsing (Fase 4) reads from this, never re-fetches.
    """

    raw_name: str
    raw_credentials_text: str | None
    raw_schedule_entries: list[dict] = field(default_factory=list)
    source_url: str = ""
    raw_payload: dict = field(default_factory=dict)


class ScraperError(Exception):
    """Base class for classified scraper errors."""


class NetworkError(ScraperError):
    """Connection/timeout failure — retryable, likely transient."""


class BlockedError(ScraperError):
    """403/429/bot-protection response — NOT retryable; must stop and report
    per spec §3.6/§16, never bypassed with evasion techniques.
    """


class StructureChangedError(ScraperError):
    """2xx response but expected fields/shape are missing — likely a site
    redesign, distinct from a network failure (spec §14 source drift).
    """


class RateLimiter:
    """Simple per-domain rate limiter: sleeps as needed so consecutive
    requests to the same domain are spaced at least `seconds` apart.
    """

    def __init__(self, seconds: float) -> None:
        self._seconds = seconds
        self._last_request_at: dict[str, float] = {}

    def wait(self, url: str) -> None:
        domain = urlparse(url).netloc
        last = self._last_request_at.get(domain)
        if last is not None:
            elapsed = time.monotonic() - last
            remaining = self._seconds - elapsed
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_at[domain] = time.monotonic()


class BaseScraper(ABC):
    group_name: str
    base_urls: list[str]
    requires_js: bool = False
    scraper_version: str = "0.1.0"

    def __init__(self, *, use_cache: bool = True) -> None:
        self.use_cache = use_cache
        self._sources_cfg = get_sources_config()
        self._rate_limiter = RateLimiter(self._sources_cfg.crawl_policy.rate_limit_seconds_per_domain)
        self._headers = {"User-Agent": self._sources_cfg.crawl_policy.user_agent, "Accept": "application/json"}

    # -- abstract interface (implemented per-adapter) -----------------

    @abstractmethod
    def discover_hospitals(self) -> list[HospitalRef]:
        """Enumerate hospitals belonging to this group from structured
        navigation or an API — never hardcode a slug list (spec Appendix A).
        """

    @abstractmethod
    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        """Fetch raw (unparsed) doctor/schedule records for one hospital."""

    # -- shared HTTP + cache machinery ---------------------------------

    def _cache_path(self, hospital_slug: str, cache_key: str) -> Path:
        today = dt.date.today().isoformat()
        safe_key = cache_key.replace("/", "_").replace("?", "_").replace("&", "_")[:150]
        return DATA_DIR / "raw" / self.group_name / today / hospital_slug / f"{safe_key}.json"

    @retry(
        retry=retry_if_exception_type(NetworkError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    def _get_json(self, url: str, *, hospital_slug: str, cache_key: str, params: dict | None = None) -> dict:
        """GET a JSON endpoint with caching, rate limiting, retry, and error
        classification. Returns the parsed JSON body.
        """
        cache_path = self._cache_path(hospital_slug, cache_key)
        if self.use_cache and cache_path.exists():
            log.debug("scraper_cache_hit", group=self.group_name, cache_path=str(cache_path))
            return json.loads(cache_path.read_text(encoding="utf-8"))

        self._rate_limiter.wait(url)
        try:
            with httpx.Client(timeout=30.0, headers=self._headers) as client:
                resp = client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise NetworkError(f"timeout fetching {url}") from exc
        except httpx.ConnectError as exc:
            raise NetworkError(f"connect error fetching {url}") from exc

        if resp.status_code in (403, 429):
            raise BlockedError(
                f"blocked (HTTP {resp.status_code}) fetching {url} — stopping per spec §3.6, no evasion."
            )
        if resp.status_code >= 500:
            raise NetworkError(f"server error (HTTP {resp.status_code}) fetching {url}")
        if resp.status_code >= 400:
            raise StructureChangedError(f"unexpected HTTP {resp.status_code} fetching {url}")

        try:
            payload = resp.json()
        except ValueError as exc:
            raise StructureChangedError(f"non-JSON response from {url}") from exc

        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("scraper_fetch_ok", group=self.group_name, url=url, cache_path=str(cache_path))
        return payload

    def provenance(self, source_url: str) -> dict:
        return {
            "source_url": source_url,
            "source_tier": "tier_1_official",
            "scraped_at": dt.datetime.now(dt.timezone.utc),
            "scraper_version": self.scraper_version,
        }
