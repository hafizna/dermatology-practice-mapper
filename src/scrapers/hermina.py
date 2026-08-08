"""Hermina Hospitals adapter — Fase 3.

Reconnaissance (2026-08-08, via Playwright network capture per spec §3.7 —
site confirmed slow, needed a generous wait before XHR calls appeared):

- CORRECT doctor listing endpoint (found by the user after the
  RSC-embedded `/id/doctors/specialist/{slug}` page turned out to return
  an incomplete/truncated result — see history below):
    GET https://api.herminahospitals.com/api/v1/public/doctors
        ?page={n}&per_page=20&lang=id
        &speciality_id=kulit-dan-kelamin-dermatologi-dan-venereologi
  Unlike the generic `/doctors` endpoint tried earlier (which silently
  ignored every filter parameter attempted), `speciality_id` on THIS path
  does filter correctly. Confirmed 2026-08-08: count=146 dermatologists
  nationwide, 8 pages at per_page=20 (`pagination.last`).
- Per-doctor schedule (unchanged from earlier recon): recurring weekly
  pattern via
    GET https://api.herminahospitals.com/api/v1/public/doctors/{slug}/schedules
        ?schedule_type=executive&type=table&lang=id
  Response: {"data": {"<hospital name>": {"<clinic name>": [{"day":
  "monday", "day_integer": 1, "from_time": "10:00", "to_time": "12:00",
  ...}]}}}. Fase 4 parsing should derive day_of_week from the `day`
  string, not assume day_integer's numbering without checking Sunday.

History (why this replaced an earlier, wrong approach): the first recon
pass used `/id/doctors/specialist/kulit-dan-kelamin-dermatologi-dan-
venereologi` (a page whose doctor list is embedded in a Next.js RSC
streaming payload, extracted via src/scrapers/_rsc_extract.py). That page
returned only 12 doctors with no pagination signal, which the user
correctly flagged as implausible for a group Hermina's size — known
branches (Ciledug, Ciputat, Serpong) were entirely absent. Root cause was
never fully confirmed, but the paginated API above is unambiguously more
complete (146 vs 12) and is now the source of truth. _rsc_extract.py is
kept for potential reuse elsewhere but no longer used by this adapter.

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from src.logging_setup import get_logger
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

API_BASE = "https://api.herminahospitals.com"
SITE_BASE = "https://herminahospitals.com"
DERMATOLOGY_SPECIALITY_ID = "kulit-dan-kelamin-dermatologi-dan-venereologi"
LISTING_PER_PAGE = 20

# Jabodetabek branch name fragments, matched against `practic_locations`
# entries (e.g. "Hermina Depok", "Hermina Daan Mogot", "RS Hermina
# Galaxy"). Hermina branch names are city/area-explicit, but some area
# names are ambiguous with other regions (documented below) so this list
# is deliberately specific.
_JABODETABEK_BRANCH_HINTS = [
    "tangerang",
    "daan mogot",  # Jakarta Barat
    "jatinegara",  # Jakarta Timur
    "bogor",
    "bekasi",
    "grand wisata",  # Bekasi area
    "depok",
    "mekarsari",  # Bekasi/Cileungsi area
    "kemayoran",
    "podomoro",  # Jakarta
    "galaxy",  # Bekasi
    "ciputat",  # Tangerang Selatan
    "ciledug",  # Tangerang
    "cikarang",
    "bintaro",  # Tangerang Selatan
    "serpong",  # Tangerang Selatan
]

# Branches seen during recon that must NOT match Jabodetabek hints (kept
# here so a future hint edit doesn't accidentally widen the match).
_KNOWN_NON_JABODETABEK_BRANCHES = [
    "arcamanik",  # Bandung
    "pekalongan",
    "soreang",  # Bandung area
    "salatiga",
    "madiun",
    "medan",
    "makassar",
    "tasikmalaya",
    "purwokerto",
]


def _is_jabodetabek_branch(branch_name: str) -> bool:
    name_lower = branch_name.lower()
    return any(hint in name_lower for hint in _JABODETABEK_BRANCH_HINTS)


class HerminaScraper(BaseScraper):
    group_name = "hermina"
    base_urls = [SITE_BASE, API_BASE]
    requires_js = False  # both listing and schedule are plain JSON APIs; JS only needed during recon to discover them

    def discover_hospitals(self) -> list[HospitalRef]:
        raise NotImplementedError(
            "Hermina discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN dokter "
            "sekaligus dari listing API yang sudah ter-filter speciality_id."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "Hermina fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors()."
        )

    def _fetch_dermatology_listing(self) -> list[dict]:
        all_entries: list[dict] = []
        page = 1
        while True:
            payload = self._get_json(
                f"{API_BASE}/api/v1/public/doctors",
                hospital_slug="_group",
                cache_key=f"doctors_page_{page}",
                params={
                    "page": page,
                    "per_page": LISTING_PER_PAGE,
                    "lang": "id",
                    "speciality_id": DERMATOLOGY_SPECIALITY_ID,
                },
            )
            entries = payload.get("data", [])
            all_entries.extend(entries)

            pagination = payload.get("pagination", {})
            last_page = pagination.get("last", 1)
            total_count = pagination.get("count")
            log.info(
                "hermina_page_fetched",
                page=page,
                last_page=last_page,
                total_count=total_count,
                n_entries=len(entries),
            )
            if page >= last_page or not entries:
                break
            page += 1

        return all_entries

    def fetch_doctor_schedule(self, doctor_slug: str) -> dict:
        """Fetch the per-doctor schedule payload (recurring weekly pattern,
        grouped by hospital then clinic name).
        """
        return self._get_json(
            f"{API_BASE}/api/v1/public/doctors/{doctor_slug}/schedules",
            hospital_slug=doctor_slug,
            cache_key=f"schedule_{doctor_slug}",
            params={"schedule_type": "executive", "type": "table", "lang": "id"},
        )

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        """Single entrypoint: list all dermatologists via the paginated
        speciality_id-filtered API (confirmed 146 nationwide / 8 pages as
        of recon), then fetch each Jabodetabek-matching doctor's schedule.
        """
        doctors = self._fetch_dermatology_listing()
        log.info("hermina_dermatology_doctors_found_network_wide", count=len(doctors))

        records: list[RawDoctorRecord] = []
        n_skipped_no_slug = 0
        for entry in doctors:
            attrs = entry.get("attributes", {})
            slug = attrs.get("slug")
            practic_locations = attrs.get("practic_locations", [])

            if not slug:
                n_skipped_no_slug += 1
                continue

            if jabodetabek_only and not any(_is_jabodetabek_branch(loc) for loc in practic_locations):
                continue

            schedule_payload = self.fetch_doctor_schedule(slug)
            schedule_by_hospital = schedule_payload.get("data", {})

            records.append(
                RawDoctorRecord(
                    raw_name=attrs.get("full_name", ""),
                    raw_credentials_text=attrs.get("full_name", ""),  # credentials embedded in name string
                    raw_schedule_entries=[schedule_by_hospital],  # nested dict, not flat list — see docstring
                    source_url=f"{SITE_BASE}/id/doctors/{slug}",
                    raw_payload={"listing_entry": entry, "schedule": schedule_by_hospital},
                )
            )

        if n_skipped_no_slug:
            log.warning("hermina_entries_missing_slug", count=n_skipped_no_slug)
        log.info(
            "hermina_dermatology_doctors_final",
            total_network_wide=len(doctors),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
