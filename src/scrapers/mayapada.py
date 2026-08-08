"""Mayapada Hospital adapter — Fase 3.

Reconnaissance (2026-08-08, plain curl + honest UA per spec §3.7 — no
JS framework detected in the way that mattered; conventional server-side
pagination with real page-number links, unlike Hermina/Primaya):

- Doctor listing (server-rendered, standard `?page=N` pagination with
  visible page-link `<a>` tags, confirmed page 5 of 5 has no `page=6`
  link — cross-checked directly rather than assumed):
    GET https://mayapadahospital.com/find-doctor/show?speciality=Kulit%20%26%20Kelamin&page={n}
  Each card gives doctor name, hospital branch, and specialty, but NOT
  schedule — that requires visiting the per-doctor detail page.
- Per-doctor detail page (schedule table, one table per hospital branch
  the doctor practices at — most have just one):
    GET https://mayapadahospital.com/find-doctor/detail/{doctor-slug}
  Schedule table rows are `<th>Monday</th><th>13:00 - 19:00 WIB</th>`
  style — day name + a combined "HH:MM - HH:MM WIB" time-range string
  (needs range parsing in Fase 4, not query-string params like EMC).
  The slug used for the detail URL is embedded in the listing card's
  `<a href="/find-doctor/detail/{slug}">` link — no separate ID mapping
  step needed.

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from selectolax.parser import HTMLParser

from src.logging_setup import get_logger
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

SITE_BASE = "https://mayapadahospital.com"
LISTING_PATH = "/find-doctor/show"
DERMATOLOGY_SPECIALITY_PARAM = "Kulit & Kelamin"

# Jabodetabek branch name fragments, matched against the listing card's
# "Hospital" table cell text.
#
# REAL BUG (found via dashboard review 2026-08-09): "kuningan" was
# missing from this list. "Mayapada Hospital Kuningan" is a real,
# distinct Jabodetabek branch (Kuningan/Mega Kuningan area, South
# Jakarta -- NOT Kuningan, West Java) confirmed present in ALL 5 cached
# listing pages with dermatologists, but every one of those doctors was
# silently filtered out here because the branch name doesn't contain
# "jakarta"/"tangerang"/"bogor" -- it was only ever called "Kuningan" on
# the site, never "Jakarta Kuningan" or similar. This made the
# corresponding registry hospital (Rumah Sakit Mayapada Kuningan)
# wrongly show confirmed_zero (opportunity_score=1.0) despite being a
# well-known hospital that obviously has a dermatology clinic. User
# confirmed https://mayapadahospital.com/find-doctor/show?location=4
# (Kuningan's location filter) returns 4 dermatologists. NOTE: "Mayapada
# Medical Center Kuningan" is a DIFFERENT, separate facility (a clinic,
# not the hospital) -- user explicitly said not to assume dermatologists
# practice there too, so it is NOT added as a hint here; only the actual
# "Mayapada Hospital Kuningan" branch name match is affected by this fix.
_JABODETABEK_BRANCH_HINTS = [
    "jakarta",
    "tangerang",
    "bogor",
    "kuningan",
]

_KNOWN_NON_JABODETABEK_BRANCHES = [
    "surabaya",
    "bandung",
    "nusantara",  # IKN
]


def _is_jabodetabek_branch(branch_name: str) -> bool:
    name_lower = branch_name.lower()
    if any(hint in name_lower for hint in _KNOWN_NON_JABODETABEK_BRANCHES):
        return False
    return any(hint in name_lower for hint in _JABODETABEK_BRANCH_HINTS)


def _parse_listing_cards(html: str) -> list[dict]:
    """Parse doctor cards from a listing page.

    IMPORTANT: the page renders each doctor card TWICE — once inside a
    `d-none d-md-block` (desktop) wrapper and once inside a `d-block
    d-md-none` (mobile) wrapper, both present in the same server-rendered
    HTML and switched via CSS media query, not JS. Confirmed 2026-08-08 by
    finding the doctor's slug literally 4x in one page's raw HTML (2
    doctors x 2 responsive wrappers). Deduplicated here by `detail_url`
    (the doctor's unique profile URL) rather than by name, since two
    different doctors could coincidentally share a display name but never
    a detail URL.
    """
    tree = HTMLParser(html)
    cards: list[dict] = []
    seen_urls: set[str] = set()
    for name_link in tree.css('#doctor_list a[href*="/find-doctor/detail/"]'):
        name_p = name_link.css_first("p")
        if not name_p:
            continue
        name = name_p.text(strip=True)
        detail_url = name_link.attributes.get("href", "")

        if not name or not detail_url or detail_url in seen_urls:
            continue

        # Walk up to the enclosing card block to find the "Hospital" table row.
        container = name_link.parent
        hospital = ""
        while container is not None and container.tag != "body":
            table = container.css_first("table.table")
            if table is not None:
                for row in table.css("tr"):
                    cells = row.css("td")
                    if len(cells) >= 2 and "hospital" in cells[0].text(strip=True).lower():
                        hospital = cells[1].text(strip=True)
                break
            container = container.parent

        seen_urls.add(detail_url)
        cards.append({"name": name, "hospital": hospital, "detail_url": detail_url})
    return cards


def _find_max_page(html: str) -> int:
    """Return the highest page number linked in the pagination nav (1 if
    no pagination links found — i.e. single page).
    """
    tree = HTMLParser(html)
    max_page = 1
    for link in tree.css("ul.pagination a.page-link"):
        href = link.attributes.get("href", "") or ""
        if "page=" in href:
            try:
                page_num = int(href.rsplit("page=", 1)[1].split("&")[0])
                max_page = max(max_page, page_num)
            except ValueError:
                continue
    return max_page


def _parse_schedule_table(html: str) -> list[dict]:
    """Parse ALL schedule tables on a doctor detail page (one per
    hospital branch the doctor practices at).
    """
    tree = HTMLParser(html)
    entries: list[dict] = []
    for table in tree.css("table"):
        header = table.css_first("thead th")
        if not header:
            continue
        hospital_name = header.text(strip=True)
        if not hospital_name or "schedule" in hospital_name.lower():
            # The hospital name is on the *previous* header cell in some
            # layouts (span class="...">Schedule</span> then hospital
            # name span) — fall back to searching thead text broadly.
            thead = table.css_first("thead")
            hospital_name = thead.text(strip=True).replace("Schedule", "").strip() if thead else ""

        for row in table.css("tbody tr"):
            cells = row.css("th")
            if len(cells) < 2:
                continue
            day_text = cells[0].text(strip=True)
            time_text = cells[1].text(strip=True)
            if not day_text or not time_text:
                continue
            entries.append(
                {
                    "hospital_name": hospital_name,
                    "day_text": day_text,
                    "time_text": time_text,
                }
            )
    return entries


class MayapadaScraper(BaseScraper):
    group_name = "mayapada"
    base_urls = [SITE_BASE]
    requires_js = False  # fully server-rendered HTML, confirmed via plain curl

    def discover_hospitals(self) -> list[HospitalRef]:
        raise NotImplementedError(
            "Mayapada discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN dokter "
            "sekaligus dari listing yang ter-filter speciality."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "Mayapada fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors()."
        )

    def _fetch_dermatology_listing(self) -> list[dict]:
        all_cards: list[dict] = []
        page = 1
        max_page = 1

        while page <= max_page:
            html = self._get_html(
                f"{SITE_BASE}{LISTING_PATH}",
                hospital_slug="_group",
                cache_key=f"listing_page_{page}",
                params={"speciality": DERMATOLOGY_SPECIALITY_PARAM, "page": page},
            )
            if page == 1:
                max_page = _find_max_page(html)

            cards = _parse_listing_cards(html)
            log.info("mayapada_listing_page_fetched", page=page, max_page=max_page, n_cards=len(cards))
            all_cards.extend(cards)
            page += 1

        return all_cards

    def fetch_doctor_schedule(self, detail_url: str, doctor_slug: str) -> list[dict]:
        html = self._get_html(
            detail_url,
            hospital_slug=doctor_slug,
            cache_key=f"detail_{doctor_slug}",
        )
        return _parse_schedule_table(html)

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        cards = self._fetch_dermatology_listing()
        log.info("mayapada_dermatology_doctors_found_network_wide", count=len(cards))

        records: list[RawDoctorRecord] = []
        for card in cards:
            if jabodetabek_only and not _is_jabodetabek_branch(card["hospital"]):
                continue

            detail_url = card["detail_url"]
            if detail_url.startswith("/"):
                detail_url = f"{SITE_BASE}{detail_url}"
            doctor_slug = detail_url.rstrip("/").rsplit("/", 1)[-1]

            schedule_entries = self.fetch_doctor_schedule(detail_url, doctor_slug)

            records.append(
                RawDoctorRecord(
                    raw_name=card["name"],
                    raw_credentials_text=card["name"],  # credentials embedded in name string
                    raw_schedule_entries=schedule_entries,
                    source_url=detail_url,
                    raw_payload={"card": card, "schedule_entries": schedule_entries},
                )
            )

        log.info(
            "mayapada_dermatology_doctors_final",
            total_network_wide=len(cards),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
