"""Radjak Hospital dermatology doctor/schedule adapter.

Reconnaissance (2026-08-09):

- The domain has no published robots.txt restrictions (``/robots.txt``
  returns the site's normal 404 page).
- The official ``/dokter`` page is server-rendered and specialty 17 is
  ``Spesialis Kulit dan Kelamin``. Doctor cards already contain the branch
  and full weekly schedule, so no JavaScript or secondary request is needed.
- The source currently misclassifies one pathology doctor (Sp.PK) under the
  dermatology filter. Credential validation deliberately rejects that card.
- Purwakarta is outside Jabodetabek and is filtered out; the other source-
  declared Radjak branches are within the project's geographic scope.
"""

from __future__ import annotations

from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from src.logging_setup import get_logger
from src.parsing.credentials import is_dermatologist_credential
from src.parsing.schedule import normalize_day_of_week
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

SITE_BASE = "https://www.radjakhospital.com"
DOCTOR_URL = f"{SITE_BASE}/dokter"
DERMATOLOGY_SPECIALTY_ID = "17"
_OUT_OF_SCOPE_BRANCHES = {"Radjak Hospital Purwakarta"}


def _parse_unit_options(html: str) -> list[HospitalRef]:
    """Discover branch names/slugs from the official unit filter."""
    tree = HTMLParser(html)
    select = tree.css_first('select[name="unit"]')
    if select is None:
        return []
    hospitals: list[HospitalRef] = []
    for option in select.css("option"):
        slug = (option.attributes.get("value") or "").strip()
        name = option.text(separator=" ", strip=True)
        if not slug or slug.casefold() == "all" or not name:
            continue
        hospitals.append(HospitalRef(name=name, url=DOCTOR_URL, slug=slug))
    return hospitals


def _parse_doctor_cards(html: str) -> list[RawDoctorRecord]:
    tree = HTMLParser(html)
    branch_names = [hospital.name for hospital in _parse_unit_options(html)]
    records: list[RawDoctorRecord] = []

    for card in tree.css(".col-md-4.mb-4"):
        profile = card.css_first('a[href*="/dokter/detail/"]')
        image = card.css_first("img[alt]")
        if profile is None or image is None:
            continue

        raw_name = (image.attributes.get("alt") or "").strip()
        card_text = card.text(separator=" ", strip=True)
        branch_name = next((name for name in branch_names if name in card_text), "")
        specialty = "Spesialis Kulit dan Kelamin" if "Spesialis Kulit dan Kelamin" in card_text else ""

        # The filter itself is not trustworthy enough: the live page carries
        # a Sp.PK pathology doctor under specialty=17. Require both the source
        # specialty label and a dermatology credential in the doctor's name.
        if not specialty or not is_dermatologist_credential(raw_name):
            log.warning(
                "radjak_card_failed_credential_check",
                raw_name=raw_name,
                specialty=specialty,
                branch=branch_name,
            )
            continue
        if not branch_name:
            log.warning("radjak_card_missing_branch", raw_name=raw_name)
            continue

        schedule_entries: list[dict] = []
        for row in card.css(".row"):
            day_node = row.css_first(".col-md-4")
            time_node = row.css_first(".col-md-8")
            if day_node is None or time_node is None:
                continue
            day_text = day_node.text(separator=" ", strip=True)
            if normalize_day_of_week(day_text, source="radjak") is None:
                continue
            time_text = time_node.text(separator=" ", strip=True)
            if not time_text or time_text == "-":
                continue
            schedule_entries.append({"day_text": day_text, "time_text": time_text})

        profile_url = urljoin(DOCTOR_URL, profile.attributes.get("href") or "")
        records.append(
            RawDoctorRecord(
                raw_name=raw_name,
                raw_credentials_text=raw_name,
                raw_schedule_entries=schedule_entries,
                source_url=profile_url,
                raw_payload={
                    "branch_name": branch_name,
                    "specialty": specialty,
                },
            )
        )
    return records


class RadjakScraper(BaseScraper):
    group_name = "radjak"
    base_urls = [SITE_BASE]
    requires_js = False
    scraper_version = "0.1.0"

    def _fetch_listing(self, *, unit: str = "All") -> str:
        return self._get_html(
            DOCTOR_URL,
            hospital_slug=unit.casefold(),
            cache_key="dermatology_listing",
            params={"unit": unit, "spesialis": DERMATOLOGY_SPECIALTY_ID, "hari": "All"},
        )

    def discover_hospitals(self) -> list[HospitalRef]:
        return _parse_unit_options(self._fetch_listing())

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        return [
            record
            for record in _parse_doctor_cards(self._fetch_listing(unit=hospital.slug or "All"))
            if record.raw_payload.get("branch_name") == hospital.name
        ]

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        records = _parse_doctor_cards(self._fetch_listing())
        if jabodetabek_only:
            records = [
                record
                for record in records
                if record.raw_payload.get("branch_name") not in _OUT_OF_SCOPE_BRANCHES
            ]

        seen: set[tuple[str, str]] = set()
        deduplicated: list[RawDoctorRecord] = []
        for record in records:
            key = (
                record.source_url.casefold(),
                str(record.raw_payload.get("branch_name", "")).casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduplicated.append(record)

        log.info(
            "radjak_dermatology_doctors_final",
            jabodetabek_only=jabodetabek_only,
            kept=len(deduplicated),
        )
        return deduplicated
