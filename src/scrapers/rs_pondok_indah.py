"""RS Pondok Indah adapter — Fase 3.

Reconnaissance (2026-08-08, via Playwright network capture per spec §3.7):

- Public API host: api.rspondokindah.co.id. Cleanest/most complete
  response of all adapters in this project — one call returns doctor
  name, credentials-in-name, hospital branch, AND a fully structured
  weekly schedule (day name + time_from/time_to in HH:MM:SS), no
  per-doctor follow-up request needed:
    GET https://api.rspondokindah.co.id/v1/doctors/master
        ?is_active=true&day=&clinic_category=Kulit%20%26%20Kelamin%20%26%20Estetik
        &limit={n}&page={p}
  REQUIRES a `content-language` header (any value, e.g. "id") — omitting
  it returns HTTP 400 `{"stat_msg": "content-language is empty"}`. This
  is a genuine required header, not evasion — it's simply undocumented
  and easy to miss without inspecting a real browser request.
- `clinic_category=Kulit & Kelamin & Estetik` (URL-encoded) is the exact
  category string used by the site's own "find-a-doctor" page — found by
  following a real link from an official news article
  (rspondokindah.co.id/{lang}/news/jadwal-dokter-spesialis-kulit-dan-
  kelamin), not guessed.
- Pagination is explicit and unambiguous (unlike Hermina/Primaya):
  `pagination.count` is a real fixed total, `pagination.total_page` is
  accurate. Confirmed 2026-08-08: count=16 doctors nationwide with NO
  hospital_code filter (this group is fully Jabodetabek — see below), 1
  page at limit=20.
- IMPORTANT: an official article link used `hospital_code=H2,H3` (Puri
  Indah + Bintaro Jaya only) which under-counts — the flagship branch H1
  (RS Pondok Indah - Pondok Indah) has dermatologists too and was missing
  from that filtered URL. This adapter queries WITHOUT a hospital_code
  filter and relies on `doctor_schedule[].hospital_code`/`hospital` in
  each result to determine branch, rather than trusting any single
  official link's query string as exhaustive.
- All 3 branches found (H1 Pondok Indah, H2 Puri Indah, H3 Bintaro Jaya)
  are Jabodetabek — RS Pondok Indah does not appear to have any
  dermatologist-staffed branch outside the region as of this snapshot.

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from src.logging_setup import get_logger
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

API_BASE = "https://api.rspondokindah.co.id"
SITE_BASE = "https://www.rspondokindah.co.id"
DERMATOLOGY_CLINIC_CATEGORY = "Kulit & Kelamin & Estetik"

# All 3 branches found in the 2026-08-08 snapshot serve Jabodetabek. Every
# RS Pondok Indah branch nationwide shares the "RS Pondok Indah" prefix
# (confirmed: a fixture-derived bug initially matched "RS Pondok Indah -
# Surabaya" as Jabodetabek because "pondok indah" is a substring of the
# group's own name, not a location signal) — so matching must be on the
# CITY/AREA suffix after the dash, not the shared brand prefix.
_JABODETABEK_BRANCH_HINTS = [
    "puri indah",  # Jakarta Barat
    "bintaro",  # Tangerang Selatan
]

# The bare "RS Pondok Indah - Pondok Indah" flagship branch (no city
# suffix distinguishing it) is itself in Jakarta Selatan — matched
# explicitly rather than via a generic substring to avoid re-introducing
# the brand-prefix false-positive bug.
_FLAGSHIP_BRANCH_EXACT_SUFFIX = "- pondok indah"

_KNOWN_NON_JABODETABEK_BRANCHES = [
    "surabaya",
]


def _is_jabodetabek_branch(hospital_name: str) -> bool:
    name_lower = hospital_name.lower()
    if any(hint in name_lower for hint in _KNOWN_NON_JABODETABEK_BRANCHES):
        return False
    if name_lower.strip().endswith(_FLAGSHIP_BRANCH_EXACT_SUFFIX):
        return True
    return any(hint in name_lower for hint in _JABODETABEK_BRANCH_HINTS)


class RsPondokIndahScraper(BaseScraper):
    group_name = "rs_pondok_indah"
    base_urls = [API_BASE, SITE_BASE]
    requires_js = False  # plain JSON API once the required content-language header is set

    def __init__(self, *, use_cache: bool = True) -> None:
        super().__init__(use_cache=use_cache)
        # api.rspondokindah.co.id requires this header — omitting it
        # returns HTTP 400 {"stat_msg": "content-language is empty"}.
        # This is a genuine required header (not a UA/evasion trick).
        self._headers["content-language"] = "id"

    def discover_hospitals(self) -> list[HospitalRef]:
        raise NotImplementedError(
            "RS Pondok Indah discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN dokter "
            "DAN jadwal sekaligus dari satu endpoint."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "RS Pondok Indah fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors()."
        )

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        payload = self._get_json(
            f"{API_BASE}/v1/doctors/master",
            hospital_slug="_group",
            cache_key="dermatology_doctors",
            params={
                "is_active": "true",
                "day": "",
                "clinic_category": DERMATOLOGY_CLINIC_CATEGORY,
                "limit": 100,
                "page": 1,
            },
        )
        doctors = payload.get("data", [])
        pagination = payload.get("pagination", {})
        log.info(
            "rspi_dermatology_doctors_found_network_wide",
            count=len(doctors),
            reported_total=pagination.get("count"),
            total_page=pagination.get("total_page"),
        )

        records: list[RawDoctorRecord] = []
        for doc in doctors:
            schedule = doc.get("doctor_schedule", [])
            branches = [s.get("hospital", "") for s in schedule]

            if jabodetabek_only and not any(_is_jabodetabek_branch(b) for b in branches):
                continue

            doctor_code = doc.get("doctor_code", "")
            records.append(
                RawDoctorRecord(
                    raw_name=doc.get("fullname_doctor", ""),
                    raw_credentials_text=doc.get("fullname_doctor", ""),  # credentials embedded in name string
                    raw_schedule_entries=schedule,
                    source_url=f"{SITE_BASE}/id/doctor/{doctor_code}" if doctor_code else "",
                    raw_payload={"doctor": doc},
                )
            )

        log.info(
            "rspi_dermatology_doctors_final",
            total_network_wide=len(doctors),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
