"""RS Premier Bintaro + Jatinegara doctor/schedule adapter.

Reconnaissance (2026-08-09):

- Both domains allow crawling (``robots.txt`` has an empty Disallow).
- RS Premier Jatinegara's doctor page has speciality/day/sort filters but
  no branch selector.  Bintaro is served from a separate domain with the
  same server-rendered doctor-card and schedule-XHR structure.
- The official RS Premier appointment portal lists only Bintaro,
  Jatinegara, and Surabaya.  Bintaro and Jatinegara are in Jabodetabek;
  Surabaya is deliberately outside this adapter's target set.
- Doctor cards are static HTML.  Weekly schedules load from the public
  ``/xhr/doctor-schedule?id=...`` endpoint and contain a desktop and mobile
  rendering of identical data; only the desktop table is parsed to avoid
  double-counting.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from src.logging_setup import get_logger
from src.parsing.credentials import is_dermatologist_credential
from src.scrapers.base import (
    BaseScraper,
    BlockedError,
    HospitalRef,
    NetworkError,
    RawDoctorRecord,
    StructureChangedError,
)

log = get_logger(__name__)

_BRANCHES = (
    HospitalRef(
        name="RS Premier Jatinegara",
        url="https://www.rspremierjatinegara.com/rspj/dokter?keyword=&speciality=1514&sort=",
        slug="jatinegara",
    ),
    HospitalRef(
        name="RS Premier Bintaro",
        url="https://www.rspremierbintaro.com/spesialisasi/kulit-dan-kelamin-rspb",
        slug="bintaro",
    ),
)

_TIME_RANGE_RE = re.compile(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}")


def _parse_doctor_cards(html: str, *, branch: HospitalRef) -> list[RawDoctorRecord]:
    tree = HTMLParser(html)
    records: list[RawDoctorRecord] = []
    for card in tree.css(".doctor-item"):
        name_node = card.css_first("h6")
        schedule_link = card.css_first(".toggle-shcedule[data-id]")
        if name_node is None or schedule_link is None:
            continue
        raw_name = name_node.text(strip=True)
        if not raw_name or not is_dermatologist_credential(raw_name):
            log.warning("rs_premier_card_failed_credential_check", raw_name=raw_name, branch=branch.name)
            continue

        doctor_id = (schedule_link.attributes.get("data-id") or "").strip()
        profile_link = card.css_first("a.button-outline")
        profile_href = profile_link.attributes.get("href") if profile_link is not None else ""
        records.append(
            RawDoctorRecord(
                raw_name=raw_name,
                raw_credentials_text=raw_name,
                raw_schedule_entries=[],
                source_url=urljoin(branch.url, profile_href or ""),
                raw_payload={
                    "branch_name": branch.name,
                    "branch_slug": branch.slug,
                    "doctor_id": doctor_id,
                },
            )
        )
    return records


def _parse_schedule_table(html: str) -> list[dict]:
    """Parse the desktop schedule table into day/time raw entries.

    Explicit HTML cell boundaries make multiple ranges within one day safe
    to split.  Unrecognized non-empty cell text is preserved for the layered
    schedule parser to mark low-confidence rather than silently discarded.
    """
    tree = HTMLParser(html)
    table = tree.css_first("table.table-dekstop")
    if table is None:
        return []
    headers = [node.text(separator=" ", strip=True) for node in table.css("thead th")]
    entries: list[dict] = []
    for row in table.css("tbody tr"):
        cells = row.css("td")
        for index, cell in enumerate(cells):
            if index >= len(headers):
                break
            raw_cell = cell.text(separator=" ", strip=True)
            if not raw_cell or raw_cell.strip() == "-":
                continue
            ranges = _TIME_RANGE_RE.findall(raw_cell)
            if ranges:
                entries.extend(
                    {"day_text": headers[index], "time_text": value}
                    for value in ranges
                )
            else:
                entries.append({"day_text": headers[index], "time_text": raw_cell})
    return entries


class RsPremierScraper(BaseScraper):
    group_name = "rs_premier"
    base_urls = [
        "https://www.rspremierjatinegara.com",
        "https://www.rspremierbintaro.com",
    ]
    requires_js = False
    scraper_version = "0.1.0"

    def discover_hospitals(self) -> list[HospitalRef]:
        # Branches are separate official domains/pages; there is no branch
        # parameter or dropdown on either listing page (see module docstring).
        return list(_BRANCHES)

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        listing_html = self._get_html(
            hospital.url,
            hospital_slug=hospital.slug or "_unknown",
            cache_key="dermatology_listing",
        )
        records = _parse_doctor_cards(listing_html, branch=hospital)
        origin = f"{urlparse(hospital.url).scheme}://{urlparse(hospital.url).netloc}"

        for record in records:
            doctor_id = record.raw_payload.get("doctor_id", "")
            if not doctor_id:
                continue
            schedule_url = f"{origin}/xhr/doctor-schedule"
            try:
                schedule_html = self._get_html(
                    schedule_url,
                    hospital_slug=hospital.slug or "_unknown",
                    cache_key=f"doctor_schedule_{doctor_id}",
                    params={"id": doctor_id},
                )
            except (NetworkError, BlockedError, StructureChangedError) as exc:
                log.warning(
                    "rs_premier_doctor_schedule_fetch_failed",
                    branch=hospital.name,
                    doctor_id=doctor_id,
                    raw_name=record.raw_name,
                    error=str(exc),
                )
                continue
            record.raw_schedule_entries = _parse_schedule_table(schedule_html)
            record.raw_payload["schedule_source_url"] = f"{schedule_url}?id={doctor_id}"

        return records

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        # Both discovered branches are in Jabodetabek; Surabaya is not in
        # _BRANCHES.  Keep the argument for the common adapter interface.
        records: list[RawDoctorRecord] = []
        failed_branches: list[str] = []
        for hospital in self.discover_hospitals():
            try:
                records.extend(self.fetch_doctors(hospital))
            except (NetworkError, BlockedError, StructureChangedError) as exc:
                log.warning(
                    "rs_premier_branch_fetch_failed",
                    branch=hospital.name,
                    error=str(exc),
                )
                failed_branches.append(hospital.name)

        log.info(
            "rs_premier_dermatology_doctors_final",
            branches=len(_BRANCHES),
            failed_branches=failed_branches,
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
