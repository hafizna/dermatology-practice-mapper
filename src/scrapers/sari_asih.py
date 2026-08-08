"""RS Sari Asih doctor/schedule adapter.

Reconnaissance (2026-08-09):

- ``robots.txt`` permits crawling and declares ``Crawl-delay: 10``.  This
  adapter therefore overrides the project-wide two-second limiter with a
  ten-second per-domain limiter.
- The dermatology listing is server-rendered HTML.  Doctor cards, branch
  names, and complete weekly schedule tables are present without JavaScript.
- The listing is paginated (12 cards on page one and additional cards on
  page two at reconnaissance time), so the adapter discovers the last page
  from the rendered pagination links rather than assuming a single page.
- Serang is outside Jabodetabek and is excluded.  Sangiang is a different
  Sari Asih branch in Tangerang and remains in scope, including when the same
  doctor card contains both Serang and Sangiang schedules.

The source page is already speciality-filtered, but credentials remain a
validator/cross-check as required by PROJECT_SPEC_revised.md Appendix A.
"""

from __future__ import annotations

from collections import defaultdict
from urllib.parse import parse_qs, urljoin, urlparse

from selectolax.parser import HTMLParser

from src.logging_setup import get_logger
from src.parsing.credentials import is_dermatologist_credential
from src.scrapers.base import BaseScraper, HospitalRef, RateLimiter, RawDoctorRecord

log = get_logger(__name__)

SITE_BASE = "https://www.sariasih.id"
DERMATOLOGY_PATH = "/dokter/poliklinik-kulit-dan-kelamin"
DERMATOLOGY_URL = f"{SITE_BASE}{DERMATOLOGY_PATH}"

_OUT_OF_SCOPE_BRANCH_SLUGS = {"rs-sari-asih-serang"}


def _branch_options(tree: HTMLParser) -> dict[str, str]:
    """Return ``{upstream slug: official branch display name}`` from the
    listing's hospital filter.  Branches are discovered from source HTML,
    not maintained as a parallel hardcoded list.
    """
    select = tree.css_first('select[form-data="hospital"]')
    if select is None:
        return {}
    result: dict[str, str] = {}
    for option in select.css("option"):
        slug = (option.attributes.get("value") or "").strip()
        name = option.text(strip=True)
        if slug and name and name.lower() != "semua cabang":
            result[slug] = name
    return result


def _pagination_pages(html: str) -> list[int]:
    tree = HTMLParser(html)
    pages = {1}
    for link in tree.css('a[href*="page="]'):
        href = link.attributes.get("href") or ""
        raw_page = parse_qs(urlparse(href).query).get("page", [])
        if raw_page and raw_page[0].isdigit():
            pages.add(int(raw_page[0]))
    return sorted(pages)


def _parse_doctor_cards(html: str, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
    """Parse one listing page into branch-scoped raw doctor records.

    One source card can contain schedules for more than one branch.  The
    pipeline's Doctor rows are hospital-scoped, so this function emits one
    RawDoctorRecord per card/branch rather than pooling branch schedules.
    """
    tree = HTMLParser(html)
    branches = _branch_options(tree)
    records: list[RawDoctorRecord] = []

    for card in tree.css('[id^="dokter_"]'):
        name_node = card.css_first("h4")
        if name_node is None:
            continue
        raw_name = name_node.text(strip=True)
        if not raw_name or not is_dermatologist_credential(raw_name):
            log.warning("sari_asih_card_failed_credential_check", raw_name=raw_name)
            continue

        profile = card.css_first("a.text-link")
        profile_href = profile.attributes.get("href") if profile is not None else ""
        source_url = urljoin(SITE_BASE, profile_href or DERMATOLOGY_PATH)

        schedules_by_branch: dict[str, list[dict]] = defaultdict(list)
        for table in card.css("table.schedule_doctor"):
            branch_link = table.css_first("[data-hospital]")
            branch_slug = (branch_link.attributes.get("data-hospital") or "").strip() if branch_link else ""
            if not branch_slug:
                # No source-declared branch identifier means we cannot safely
                # attach the table to a hospital.  Keep the card discoverable
                # through its badges below, but do not guess schedule scope.
                continue

            for row in table.css("tbody tr"):
                cells = row.css("td")
                if len(cells) < 2:
                    continue
                day_text = cells[0].text(separator=" ", strip=True)
                time_text = cells[1].text(separator=" ", strip=True)
                if not day_text or not time_text or time_text.strip() == "-":
                    continue
                schedules_by_branch[branch_slug].append(
                    {"day_text": day_text, "time_text": time_text}
                )

        # A doctor with no rendered schedule must still remain visible as a
        # listed specialist (unknown schedule, not zero supply).  Badges are
        # source-declared branch labels and are safe as a schedule-less fallback.
        if not schedules_by_branch:
            name_to_slug = {name.casefold(): slug for slug, name in branches.items()}
            for badge in card.css(".badge"):
                short_name = badge.text(separator=" ", strip=True)
                full_name = f"RS Sari Asih {short_name}".casefold()
                slug = name_to_slug.get(full_name)
                if slug:
                    schedules_by_branch.setdefault(slug, [])

        for branch_slug, schedule_entries in schedules_by_branch.items():
            if jabodetabek_only and branch_slug in _OUT_OF_SCOPE_BRANCH_SLUGS:
                continue
            branch_name = branches.get(branch_slug)
            if not branch_name:
                log.warning(
                    "sari_asih_unknown_branch_slug",
                    raw_name=raw_name,
                    branch_slug=branch_slug,
                )
                continue
            records.append(
                RawDoctorRecord(
                    raw_name=raw_name,
                    raw_credentials_text=raw_name,
                    raw_schedule_entries=schedule_entries,
                    source_url=source_url,
                    raw_payload={
                        "card": {
                            "branch": branch_name,
                            "branch_slug": branch_slug,
                        },
                        "speciality": "Kulit Dan Kelamin",
                    },
                )
            )

    return records


class SariAsihScraper(BaseScraper):
    group_name = "sari_asih"
    base_urls = [SITE_BASE]
    requires_js = False
    scraper_version = "0.1.0"

    def __init__(self, *, use_cache: bool = True) -> None:
        super().__init__(use_cache=use_cache)
        # Source-specific robots.txt requirement; deliberately stricter than
        # config/sources.yaml's project-wide two-second default.
        self._rate_limiter = RateLimiter(10.0)

    def _fetch_page(self, page: int) -> str:
        return self._get_html(
            DERMATOLOGY_URL,
            hospital_slug="_group",
            cache_key=f"dermatology_page_{page}",
            params={"page": page} if page > 1 else None,
        )

    def discover_hospitals(self) -> list[HospitalRef]:
        html = self._fetch_page(1)
        tree = HTMLParser(html)
        return [
            HospitalRef(name=name, url=DERMATOLOGY_URL, slug=slug)
            for slug, name in _branch_options(tree).items()
        ]

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        return [
            record
            for record in self.fetch_all_dermatology_doctors(jabodetabek_only=False)
            if record.raw_payload.get("card", {}).get("branch_slug") == hospital.slug
        ]

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        first_html = self._fetch_page(1)
        pages = _pagination_pages(first_html)
        page_html = {1: first_html}
        for page in pages:
            if page != 1:
                page_html[page] = self._fetch_page(page)

        records: list[RawDoctorRecord] = []
        seen: set[tuple[str, str]] = set()
        for page in pages:
            for record in _parse_doctor_cards(page_html[page], jabodetabek_only=jabodetabek_only):
                branch_slug = record.raw_payload.get("card", {}).get("branch_slug", "")
                key = (record.raw_name.casefold(), branch_slug)
                if key in seen:
                    continue
                seen.add(key)
                records.append(record)

        log.info(
            "sari_asih_dermatology_doctors_final",
            pages=pages,
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
