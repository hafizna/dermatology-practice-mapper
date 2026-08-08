"""EMC Healthcare (RS EMC) adapter — Fase 3.

Reconnaissance (2026-08-08, plain curl + honest UA per spec §3.7 — no
Next.js/Nuxt/WordPress framework detected; server-rendered HTML, no JS
needed at runtime):

- Single listing page has EVERYTHING needed — name, credentials (embedded
  in name string), hospital branch, AND full weekly schedule table, all
  in one server-rendered response. No separate per-doctor request needed
  (unlike Siloam/Hermina/Primaya):
    GET https://www.emc.id/id/specialities/kulit-dan-kelamin
  (also available at /en/specialities/kulit-dan-kelamin, same doctors,
  English day labels — Indonesian version used since day names map more
  directly to spec's canonical Senin..Minggu labeling.)
- Confirmed 2026-08-08: 22 unique dermatologists, 23 `.doctor-list-
  schedule` card containers (one doctor's name appeared with a duplicate
  CSS id `EMCDR148` on two cards — the site's own markup quirk, not a
  parsing bug here; card boundaries are still correctly delimited by the
  `.doctor-list-schedule` container regardless).
- No pagination signal found and none needed to fabricate one for:
  scrolling to bottom (Playwright) did not change the doctor count,
  `?page=2` had no effect, and there is no "load more" button — unlike
  Hermina's page, this was cross-checked two ways before being trusted.
- All 8 branches found (Alam Sutera, Cibitung, Cikarang, Grha Kedoya,
  Pekayon, Pulomas, Sentul, Tangerang) are Jabodetabek — EMC does not
  appear to have any dermatologist-staffed branch outside the region as
  of this snapshot, so no location filtering is strictly required, but
  _is_jabodetabek_branch() is still applied for consistency/robustness in
  case a non-Jabodetabek branch is added later.
- Schedule is read directly from each "MAKE APPOINTMENT" / "BUAT JANJI"
  link's query string, NOT parsed from the visible time-range text —
  the link already encodes day/start_time/end_time unambiguously
  (e.g. `?hospital=alam-sutera&doctor=...&day=2&start_time=17:30:00&
  end_time=20:00:00`), which sidesteps free-text time-range parsing
  ambiguity entirely for this source. `day` here is EMC's own numbering
  (empirically 2=Tuesday in the sample seen) — Fase 4 parsing must derive
  spec's canonical day_of_week from context, not assume EMC's `day`
  value equals spec's 0=Senin numbering directly.

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from selectolax.parser import HTMLParser

from src.logging_setup import get_logger
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

SITE_BASE = "https://www.emc.id"
DERMATOLOGY_LISTING_PATH = "/id/specialities/kulit-dan-kelamin"

# All branches observed serve Jabodetabek as of the 2026-08-08 snapshot —
# kept here for robustness/documentation rather than because it currently
# excludes anything.
_JABODETABEK_BRANCH_HINTS = [
    "alam sutera",
    "cibitung",
    "cikarang",
    "grha kedoya",
    "pekayon",
    "pulomas",
    "sentul",
    "tangerang",
]


def _is_jabodetabek_branch(branch_name: str) -> bool:
    name_lower = branch_name.lower()
    return any(hint in name_lower for hint in _JABODETABEK_BRANCH_HINTS)


def _parse_appointment_link(href: str) -> dict | None:
    """Extract day/start_time/end_time/hospital from a MAKE APPOINTMENT
    link's query string. Returns None if the link doesn't have the
    expected shape (e.g. a doctor with no schedule at all for a given
    day cell — those cells have no link).
    """
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    if "day" not in qs or "start_time" not in qs or "end_time" not in qs:
        return None
    return {
        "hospital_slug": qs.get("hospital", [None])[0],
        "day": qs["day"][0],
        "start_time": qs["start_time"][0],
        "end_time": qs["end_time"][0],
        "time_text": qs.get("time_text", [None])[0],
    }


def _parse_doctor_cards(html: str) -> list[dict]:
    tree = HTMLParser(html)
    cards: list[dict] = []
    for card in tree.css(".doctor-list-schedule"):
        name_node = card.css_first(".media-content b")
        branch_node = card.css_first(".media-figure__caption")
        detail_link_node = card.css_first('a[href*="/doctors/"]')

        name = name_node.text(strip=True) if name_node else ""
        branch = (branch_node.text(deep=True, separator=" ", strip=True) if branch_node else "").strip()
        detail_url = detail_link_node.attributes.get("href") if detail_link_node else None

        schedule_entries = []
        for link in card.css('a.anchor-button[href*="/doctor-schedule"]'):
            entry = _parse_appointment_link(link.attributes.get("href", ""))
            if entry:
                schedule_entries.append(entry)

        if not name:
            continue

        cards.append(
            {
                "name": name,
                "branch": branch,
                "detail_url": detail_url,
                "schedule_entries": schedule_entries,
            }
        )
    return cards


class EmcScraper(BaseScraper):
    group_name = "emc"
    base_urls = [SITE_BASE]
    requires_js = False  # fully server-rendered HTML, confirmed via plain curl

    def discover_hospitals(self) -> list[HospitalRef]:
        raise NotImplementedError(
            "EMC discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN dokter "
            "DAN jadwal sekaligus dari satu halaman listing."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "EMC fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors()."
        )

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        """Single HTTP request covers everything — no per-doctor schedule
        fetch needed, unlike Siloam/Hermina/Primaya.
        """
        html = self._get_html(
            f"{SITE_BASE}{DERMATOLOGY_LISTING_PATH}",
            hospital_slug="_group",
            cache_key="dermatology_listing",
        )
        cards = _parse_doctor_cards(html)
        log.info("emc_dermatology_doctors_found_network_wide", count=len(cards))

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
            "emc_dermatology_doctors_final",
            total_network_wide=len(cards),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
