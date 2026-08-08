"""Columbia Asia BSD + Pulomas dermatology doctor/schedule adapter.

Reconnaissance (2026-08-09):

- ``robots.txt`` has no disallowed paths.
- The official doctor listing is server-rendered WordPress HTML.  Specialty
  119 is DERMATOLOGY; the location filter is discovered from the page rather
  than duplicating the upstream numeric IDs in code.
- Only BSD and Pulomas are Columbia Asia branches in Jabodetabek.  Their
  individual doctor pages contain a server-rendered weekly schedule table.
- The source labels the two branches ``RSCA BSD`` and ``RSCA Pulomas``;
  records use the corresponding official public hospital names so the
  cross-source registry match remains human-readable and auditable.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse

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

SITE_BASE = "https://columbiaasia.co.id"
LISTING_URL = f"{SITE_BASE}/dokter-kami/"
DERMATOLOGY_SPECIALTY_ID = "119"

_JABODETABEK_BRANCH_NAMES = {
    "RSCA BSD": "RS Columbia Asia BSD",
    "RSCA Pulomas": "RS Columbia Asia Pulomas",
}
_TIME_RANGE_RE = re.compile(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}")


def _parse_location_options(html: str) -> list[HospitalRef]:
    """Discover the two Jabodetabek location IDs from the official filter."""
    tree = HTMLParser(html)
    result: list[HospitalRef] = []
    for link in tree.css('.dropdown-menu[aria-labelledby="dropdownLocation"] a'):
        source_name = link.text(separator=" ", strip=True)
        official_name = _JABODETABEK_BRANCH_NAMES.get(source_name)
        if not official_name:
            continue
        href = urljoin(LISTING_URL, link.attributes.get("href") or "")
        location_values = parse_qs(urlparse(href).query).get("location", [])
        if not location_values or not location_values[0]:
            continue
        result.append(
            HospitalRef(
                name=official_name,
                url=LISTING_URL,
                slug=source_name.removeprefix("RSCA ").casefold(),
                hospital_id_upstream=location_values[0],
            )
        )
    return result


def _parse_listing_cards(html: str, *, branch: HospitalRef) -> list[RawDoctorRecord]:
    tree = HTMLParser(html)
    records: list[RawDoctorRecord] = []
    for card in tree.css(".find-doctor-item"):
        name_node = card.css_first("h4")
        profile_link = card.css_first('a[href*="/doctor-appointment/"]')
        if name_node is None or profile_link is None:
            continue
        raw_name = name_node.text(separator=" ", strip=True)
        # The source emits a slash-less path that responds 301. BaseScraper
        # intentionally does not follow redirects, so request WordPress's
        # canonical trailing-slash URL directly and cache the actual page.
        profile_url = urljoin(SITE_BASE, profile_link.attributes.get("href") or "").rstrip("/") + "/"

        specialty = ""
        for node in card.css(".find-doctor-info div"):
            candidate = node.text(separator=" ", strip=True)
            if candidate.casefold() == "dermatology":
                specialty = candidate
                break
        if not raw_name or not is_dermatologist_credential(specialty):
            log.warning(
                "columbia_asia_card_failed_specialty_check",
                raw_name=raw_name,
                specialty=specialty,
                branch=branch.name,
            )
            continue

        records.append(
            RawDoctorRecord(
                raw_name=raw_name,
                raw_credentials_text=specialty,
                source_url=profile_url,
                raw_payload={
                    "branch_name": branch.name,
                    "branch_slug": branch.slug,
                    "location_id": branch.hospital_id_upstream,
                    "listing_specialty": specialty,
                },
            )
        )
    return records


def _parse_doctor_detail(html: str) -> dict:
    """Extract source-declared branch/specialty and weekly time ranges."""
    tree = HTMLParser(html)
    info_values = [node.text(separator=" ", strip=True) for node in tree.css(".pt-doctor-info strong")]
    specialty = info_values[0] if info_values else ""
    source_branch = info_values[1] if len(info_values) > 1 else ""

    entries: list[dict] = []
    table = tree.css_first("table.table-schedule")
    if table is not None:
        for row in table.css("tbody tr"):
            cells = row.css("td")
            if len(cells) < 2:
                continue
            day_text = cells[0].text(separator=" ", strip=True)
            raw_times = cells[1].text(separator="\n", strip=True)
            if not day_text or not raw_times or raw_times == "-":
                continue
            ranges = _TIME_RANGE_RE.findall(raw_times)
            if ranges:
                entries.extend({"day_text": day_text, "time_text": value} for value in ranges)
            else:
                # Preserve unexpected non-empty source text for the common
                # parser to classify as low-confidence instead of dropping it.
                entries.append({"day_text": day_text, "time_text": raw_times})

    return {
        "specialty": specialty,
        "source_branch": source_branch,
        "schedule_entries": entries,
    }


class ColumbiaAsiaScraper(BaseScraper):
    group_name = "columbia_asia"
    base_urls = [SITE_BASE]
    requires_js = False
    scraper_version = "0.1.0"

    def _fetch_discovery_page(self) -> str:
        return self._get_html(
            LISTING_URL,
            hospital_slug="_group",
            cache_key="dermatology_locations",
            params={"specialty": DERMATOLOGY_SPECIALTY_ID, "location": "", "keyword": ""},
        )

    def discover_hospitals(self) -> list[HospitalRef]:
        hospitals = _parse_location_options(self._fetch_discovery_page())
        if len(hospitals) != len(_JABODETABEK_BRANCH_NAMES):
            raise StructureChangedError(
                "Columbia Asia location filter no longer exposes both BSD and Pulomas"
            )
        return hospitals

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        listing_html = self._get_html(
            LISTING_URL,
            hospital_slug=hospital.slug or "_unknown",
            cache_key="dermatology_listing",
            params={
                "specialty": DERMATOLOGY_SPECIALTY_ID,
                "location": hospital.hospital_id_upstream or "",
                "keyword": "",
            },
        )
        records = _parse_listing_cards(listing_html, branch=hospital)
        source_branch_expected = next(
            source_name
            for source_name, official_name in _JABODETABEK_BRANCH_NAMES.items()
            if official_name == hospital.name
        )

        for record in records:
            try:
                detail_html = self._get_html(
                    record.source_url,
                    hospital_slug=hospital.slug or "_unknown",
                    cache_key=f"doctor_detail_{record.source_url.rstrip('/').rsplit('/', 1)[-1]}",
                )
            except (NetworkError, BlockedError, StructureChangedError) as exc:
                log.warning(
                    "columbia_asia_doctor_detail_fetch_failed",
                    raw_name=record.raw_name,
                    branch=hospital.name,
                    error=str(exc),
                )
                continue

            detail = _parse_doctor_detail(detail_html)
            record.raw_payload["detail_specialty"] = detail["specialty"]
            record.raw_payload["source_branch"] = detail["source_branch"]
            if detail["source_branch"] != source_branch_expected:
                log.warning(
                    "columbia_asia_doctor_branch_mismatch",
                    raw_name=record.raw_name,
                    listing_branch=source_branch_expected,
                    detail_branch=detail["source_branch"],
                )
                continue
            if not is_dermatologist_credential(detail["specialty"]):
                log.warning(
                    "columbia_asia_doctor_specialty_mismatch",
                    raw_name=record.raw_name,
                    detail_specialty=detail["specialty"],
                )
                continue
            record.raw_schedule_entries = detail["schedule_entries"]

        return records

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        # Discovery deliberately selects only BSD and Pulomas. The remaining
        # official branches (Medan, Semarang, Aksara) are outside Jabodetabek.
        records: list[RawDoctorRecord] = []
        failed_branches: list[str] = []
        for hospital in self.discover_hospitals():
            try:
                records.extend(self.fetch_doctors(hospital))
            except (NetworkError, BlockedError, StructureChangedError) as exc:
                failed_branches.append(hospital.name)
                log.warning(
                    "columbia_asia_branch_fetch_failed",
                    branch=hospital.name,
                    error=str(exc),
                )

        log.info(
            "columbia_asia_dermatology_doctors_final",
            failed_branches=failed_branches,
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
