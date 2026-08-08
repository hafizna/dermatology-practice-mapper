"""BaseScraper — placeholder for Fase 2.

Per PROJECT_SPEC.md §9 ("Fase 2 — Scraper Framework + Satu Adapter Pilot"),
the full BaseScraper (retry/backoff, per-domain rate limiter, raw response
cache, structured logging, provenance metadata, cache replay, error
classification) is implemented in that phase, not Fase 0.

This stub only exists so `src/scrapers/` is an importable package with the
shape described in spec §7 before Fase 2 lands. Do not build adapters
against this stub yet.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class HospitalRef:
    """Minimal reference to a hospital as discovered by a scraper.

    Full field set (slug, url, source metadata) will be finalized in Fase 2
    once the pilot adapter (Eka Hospital) clarifies real-world requirements.
    """

    name: str
    url: str
    slug: str | None = None


@dataclass
class RawDoctorRecord:
    """Minimal raw doctor record as scraped, pre-parsing/normalization."""

    raw_name: str
    raw_credentials_text: str | None
    raw_schedule_text: str | None
    source_url: str


class BaseScraper(ABC):
    group_name: str
    base_urls: list[str]
    requires_js: bool = False

    @abstractmethod
    def discover_hospitals(self) -> list[HospitalRef]:
        """Enumerate hospitals belonging to this group from structured
        navigation or an API — never hardcode a slug list (spec Appendix A).
        """
        raise NotImplementedError("Implemented per-adapter starting Fase 2.")

    @abstractmethod
    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        """Fetch raw (unparsed) doctor/schedule records for one hospital."""
        raise NotImplementedError("Implemented per-adapter starting Fase 2.")
