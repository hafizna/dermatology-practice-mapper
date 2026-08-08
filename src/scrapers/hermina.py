"""Hermina Hospitals adapter — Fase 3.

Reconnaissance (2026-08-08, via Playwright network capture + RSC payload
inspection per spec §3.7):

- Doctor listing page (server-rendered, Next.js App Router):
    GET https://herminahospitals.com/id/doctors/specialist/kulit-dan-kelamin-dermatologi-dan-venereologi
  No separate client-side API call for this listing (0 xhr/fetch requests
  observed) — the doctor array ships embedded in a React Server Components
  streaming payload inside `<script>self.__next_f.push(...)</script>` tags.
  Extracted via src/scrapers/_rsc_extract.py (find the `doctors` array),
  not CSS-selector scraping. Confirmed 2026-08-08: 12 dermatologists
  found, no pagination UI/parameter detected — if this count looks wrong
  for a hospital group Hermina's size, re-verify rather than trust it
  blindly (Appendix A caveat).
- api.herminahospitals.com/api/v1/public/doctors (generic listing
  endpoint) does NOT actually filter by any `speciality`/`specialty`/
  `speciality_slug` query parameter tried — it silently ignores the filter
  and returns doctors of ALL specialities. Do not use this endpoint for
  discovery; the RSC-embedded listing above is correctly pre-filtered.
- Per-doctor schedule (recurring weekly pattern, day_integer 0=? — see
  note below — NOT concrete dates like Mitra Keluarga):
    GET https://api.herminahospitals.com/api/v1/public/doctors/{slug}/schedules
        ?schedule_type=executive&type=table&lang=id
  Response: {"data": {"<hospital name>": {"<clinic name>": [{"day":
  "monday", "day_integer": 1, "from_time": "10:00", "to_time": "12:00",
  ...}]}}}. day_integer appears to be 1=Monday..6=Saturday based on the
  "monday"->1 mapping observed; Sunday (0 or 7?) was not observed in the
  sample and should not be assumed — Fase 4 parsing should derive
  day_of_week from the `day` string (spec's canonical 0=Senin mapping),
  not blindly trust day_integer.

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from src.logging_setup import get_logger
from src.scrapers._rsc_extract import extract_rsc_json_blocks, find_array_under_key_in_blocks
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

SITE_BASE = "https://herminahospitals.com"
API_BASE = "https://api.herminahospitals.com"
DERMATOLOGY_LISTING_PATH = "/id/doctors/specialist/kulit-dan-kelamin-dermatologi-dan-venereologi"

# Jabodetabek branch name fragments, matched against `practic_locations`
# entries (e.g. "Hermina Depok", "Hermina Daan Mogot"). Hermina branch
# names are city/area-explicit, but some area names are ambiguous with
# other regions (documented below) so this list is deliberately specific.
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
    "cikarang",
    "bintaro",  # Tangerang Selatan
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
]


def _is_jabodetabek_branch(branch_name: str) -> bool:
    name_lower = branch_name.lower()
    return any(hint in name_lower for hint in _JABODETABEK_BRANCH_HINTS)


class HerminaScraper(BaseScraper):
    group_name = "hermina"
    base_urls = [SITE_BASE, API_BASE]
    requires_js = False  # listing is server-rendered HTML (no JS needed at runtime); schedule is plain JSON API

    def discover_hospitals(self) -> list[HospitalRef]:
        raise NotImplementedError(
            "Hermina discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN dokter "
            "sekaligus dari satu halaman listing spesialisasi."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "Hermina fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors()."
        )

    def _fetch_dermatology_listing(self) -> list[dict]:
        html = self._get_html(
            f"{SITE_BASE}{DERMATOLOGY_LISTING_PATH}",
            hospital_slug="_group",
            cache_key="dermatology_listing",
        )
        blocks = extract_rsc_json_blocks(html)
        doctors = find_array_under_key_in_blocks(blocks, "doctors")
        if doctors is None:
            raise RuntimeError(
                "Hermina: tidak menemukan array 'doctors' di RSC payload halaman listing "
                "dermatologi — struktur situs kemungkinan berubah. Lihat PROJECT_SPEC.md §14."
            )
        return doctors

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
        """Single entrypoint: list all dermatologists (RSC-embedded, one
        HTML fetch covers all — no pagination observed during recon), then
        fetch each one's recurring weekly schedule.
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
