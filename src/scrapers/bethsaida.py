"""Bethsaida Hospital adapter — Fase 3.

Reconnaissance (2026-08-08, Playwright network capture per spec §3.7 —
the "MENCARI" search button turned out to be a plain GET full-page
navigation, not an XHR/fetch call, so it was easy to miss until request
TYPE (not just xhr/fetch) was inspected):

- Doctor search is a plain server-rendered GET, works via curl + honest
  UA, no session/JS needed at runtime:
    GET https://www.bethsaidahospitals.com/dokter
        ?hospital={hospital_code}&speciality={speciality_id}&nama_dokter=&cariok=
  `speciality=258` is "Kulit dan Kelamin" — the numeric ID, found via the
  site's own `/Dokter/Getpoly_hope/{hospital_id}/0` endpoint response
  (an HTML `<option value=N>Name</option>` list), not guessed.
  `hospital=_alllocation_` works and returns doctors across all branches
  in one call (confirmed 2026-08-08: 3 dermatologists, all at Bethsaida
  Hospital Gading Serpong — the only Jabodetabek branch; Bethsaida
  Hospital Serang, the other branch, had none).
- Listing page includes BOTH doctor info (name/credentials/branch) AND a
  full weekly schedule table per doctor in the same response — no
  separate per-doctor request needed (same pattern as EMC).
- Schedule cells are `data-label="{Indonesian day name}"` containing a
  "HH:MM - HH:MM" text range (no query-string-encoded times like EMC —
  Fase 4 parsing will need to handle a free-text range here, not just
  read URL params).
- Only 2 hospital branches exist site-wide: BHI (Gading Serpong,
  Jabodetabek) and BHM (Serang, outside Jabodetabek) — confirmed via the
  `hospital` <select> dropdown's own option values.

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from selectolax.parser import HTMLParser

from src.logging_setup import get_logger
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

SITE_BASE = "https://www.bethsaidahospitals.com"
SEARCH_PATH = "/dokter"
DERMATOLOGY_SPECIALITY_ID = "258"  # "Kulit dan Kelamin", found via Getpoly_hope response

# Jabodetabek branch name fragments, matched against the doctor-card's
# location caption text.
_JABODETABEK_BRANCH_HINTS = [
    "gading serpong",
]

_KNOWN_NON_JABODETABEK_BRANCHES = [
    "serang",
]


def _is_jabodetabek_branch(branch_name: str) -> bool:
    name_lower = branch_name.lower()
    if any(hint in name_lower for hint in _KNOWN_NON_JABODETABEK_BRANCHES):
        return False
    return any(hint in name_lower for hint in _JABODETABEK_BRANCH_HINTS)


def _parse_doctor_cards(html: str) -> list[dict]:
    """Each doctor is a `.doctor-list-schedule` container (same top-level
    structure as EMC's adapter — likely the same CMS template/vendor)
    holding a `.doctor-profile.media` block (name, branch caption, detail
    link) followed by a `.doctor-schedule` block (weekly schedule table).
    """
    tree = HTMLParser(html)
    cards: list[dict] = []
    for card in tree.css(".doctor-list-schedule"):
        name_node = card.css_first(".media-content b")
        caption_node = card.css_first(".media-figure__caption")
        detail_link_node = card.css_first("a[href*='/dokter/detaildokter/']")

        name = name_node.text(strip=True) if name_node else ""
        branch = caption_node.text(strip=True) if caption_node else ""
        detail_url = detail_link_node.attributes.get("href") if detail_link_node else None

        if not name.lower().startswith("dr"):
            continue

        schedule_entries: list[dict] = []
        for row in card.css(".doctor-schedule table.doctor-schedule-table tbody tr"):
            for cell in row.css("td[data-label]"):
                day_text = cell.attributes.get("data-label", "")
                time_text = cell.text(strip=True)
                if day_text and time_text:
                    schedule_entries.append({"day_text": day_text, "time_text": time_text})

        cards.append(
            {
                "name": name,
                "branch": branch,
                "detail_url": detail_url,
                "schedule_entries": schedule_entries,
            }
        )
    return cards


class BethsaidaScraper(BaseScraper):
    group_name = "bethsaida"
    base_urls = [SITE_BASE]
    requires_js = False  # confirmed the search "MENCARI" action is a plain GET, not XHR

    def discover_hospitals(self) -> list[HospitalRef]:
        raise NotImplementedError(
            "Bethsaida discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN dokter "
            "DAN jadwal sekaligus dari satu pencarian."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "Bethsaida fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors()."
        )

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        html = self._get_html(
            f"{SITE_BASE}{SEARCH_PATH}",
            hospital_slug="_group",
            cache_key="dermatology_search_all_locations",
            params={
                "hospital": "_alllocation_",
                "speciality": DERMATOLOGY_SPECIALITY_ID,
                "nama_dokter": "",
                "cariok": "",
            },
        )
        cards = _parse_doctor_cards(html)
        log.info("bethsaida_dermatology_doctors_found_network_wide", count=len(cards))

        records: list[RawDoctorRecord] = []
        for card in cards:
            if jabodetabek_only and not _is_jabodetabek_branch(card["branch"]):
                continue

            records.append(
                RawDoctorRecord(
                    raw_name=card["name"],
                    raw_credentials_text=card["name"],  # credentials embedded in name string
                    raw_schedule_entries=card["schedule_entries"],
                    source_url=card["detail_url"] or "",
                    raw_payload={"card": card},
                )
            )

        log.info(
            "bethsaida_dermatology_doctors_final",
            total_network_wide=len(cards),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
