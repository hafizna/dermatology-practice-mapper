"""Sentra Medika Hospital Group adapter.

Reconnaissance (2026-08-31, via httpx + Playwright network capture per
spec §3.7 — a Next.js App Router site; checked /robots.txt on the
main domain first, which Disallows /api/, but the actual API host is a
SEPARATE subdomain with its own permissive robots.txt, see below):

- Public site: sentramedikahospitals.com (Next.js). Its own /robots.txt
  Disallows /api/, but that path prefix does not exist on this domain at
  all — the doctor-search page (/doctors) renders an empty client shell
  and fetches everything from a different host at runtime (confirmed via
  Playwright network capture, and by checking the RSC-embedded JSON in
  the page HTML directly with src/scrapers/_rsc_extract.py's helpers:
  only page metadata — breadcrumbs, <title>, meta tags — ships server-
  side, no doctor data).
- Actual API host: admin.sentramedikahospitals.com — its own
  /robots.txt is `User-agent: *` / `Disallow:` (empty), i.e. fully
  permissive. This adapter only ever calls this host, never anything
  under sentramedikahospitals.com/api/ (spec §3.6 respect_robots_txt).
- Hospitals: GET /api/v1/public/hospitals?per_page=100&fields=...
  Returns every branch with name/address/lat/lon inline — no separate
  geocoding needed for hospital identity (though the project's own OSM
  registry rows remain the canonical Hospital table entries; this is
  just used to confirm which branches are Jabodetabek, see
  _JABODETABEK_HOSPITAL_NAME_HINTS below).
- Specialities: GET /api/v1/public/specializations?per_page=100&fields=...
  TWO distinct specialization_id values both cover dermatology/
  venereology (confirmed by name, not assumed) — see
  discover_dermatology_specialization_ids().
- Doctor listing, filtered by specialization:
    GET /api/v1/public/doctors?page=&per_page=&specialization_ids[]=<id>
        &fields=id,slug,full_name,photo,specializations,hospitals,status
  ⚠️ CONFIRMED: `specialization_id` (singular) and `specializations`/
  `filter[specialization_id]`/`specialization_slug` are all silently
  IGNORED by the server (return the full unfiltered ~494-doctor list).
  Only `specialization_ids[]` (array-bracket form) actually filters —
  found by trying several plausible param name variants against the
  known total count. `meta.pagination` carries `total`/`last_page` for
  standard page-number pagination (not Primaya's broken not_in style).
- Per-doctor schedule (structured JSON, day name + concrete upcoming
  shift dates/times — no HTML parsing needed at all, unlike Primaya):
    GET /api/v1/public/doctors/{slug}/schedules
  Response shape: {"data": [{"polyclinic_id", "polyclinic_name",
  "hospitals": [{"hospital_id", "hospital_name", "hospital_address",
  "schedules": [{"day": "friday", "shifts": [{"start_time", "end_time",
  "date", ...}, ...]}, ...]}]}, ...]}. `hospitals` can list more than
  one branch — see parse_schedule_entries_by_hospital's
  "sentra_medika" entry in src/parsing/schedule.py for how each
  branch's slots are kept separate rather than pooled.

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from src.logging_setup import get_logger
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

API_BASE = "https://admin.sentramedikahospitals.com"

# Jabodetabek branch name fragments, matched case-insensitively against
# a doctor record's `hospitals[].name`. Confirmed against the live
# /api/v1/public/hospitals listing (2026-08-31): Sentra Medika Hospital
# Group has 6 branches total, of which 4 are Jabodetabek (Cibinong,
# Cisalak, Harapan Bunda Hospital — Jakarta Timur/Ciracas, Cikarang) and
# 2 are not (Gempol — Cirebon; Minahasa Utara — Sulawesi Utara). Listed
# here (not hardcoded deep in filtering logic) so it's easy to audit/
# extend as branches open or names change, same convention as
# src/scrapers/siloam.py's _JABODETABEK_NAME_HINTS.
_JABODETABEK_HOSPITAL_NAME_HINTS = [
    "cibinong",
    "cisalak",
    "harapan bunda",
    "cikarang",
]


def _is_jabodetabek_hospital(hospital_name: str) -> bool:
    name_lower = hospital_name.lower()
    return any(hint in name_lower for hint in _JABODETABEK_HOSPITAL_NAME_HINTS)


class SentraMedikaScraper(BaseScraper):
    group_name = "sentra_medika"
    base_urls = [API_BASE]
    requires_js = False  # underlying API is plain JSON; JS only needed during recon to discover it (done, not needed at runtime)

    def discover_dermatology_specialization_ids(self) -> list[str]:
        """Find every specialization_id whose name is a dermatology/
        venereology specialty, rather than hardcoding IDs blindly (spec
        §14 source-drift spirit — same pattern as
        src/scrapers/siloam.py's discover_dermatology_speciality_id()).
        Confirmed 2026-08-31: TWO ids currently qualify (60 and 66, both
        named "Spesialis Dermatologi, Venereologi, dan Estetika", one
        with a "(Kulit dan Kelamin)" suffix) — a doctor can appear under
        either or both, so callers must query all returned ids and
        de-duplicate by doctor id.
        """
        payload = self._get_json(
            f"{API_BASE}/api/v1/public/specializations",
            hospital_slug="_group",
            cache_key="specializations",
            params={"per_page": 100, "fields": "id,name,slug"},
        )
        ids = [
            str(sp["id"])
            for sp in payload.get("data", [])
            if "dermat" in sp.get("name", "").lower() or "venereo" in sp.get("name", "").lower()
        ]
        if not ids:
            raise RuntimeError(
                "Sentra Medika: tidak menemukan specialization dermatologi di "
                "/api/v1/public/specializations — struktur API kemungkinan berubah."
            )
        log.info("sentra_medika_dermatology_specialization_ids_found", ids=ids)
        return ids

    def discover_hospitals(self) -> list[HospitalRef]:
        raise NotImplementedError(
            "Sentra Medika discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN dokter "
            "sekaligus dari listing dokter yang ter-filter specialization_ids[]."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "Sentra Medika fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors()."
        )

    def _fetch_dermatology_listing(self, specialization_ids: list[str]) -> list[dict]:
        """Page through /api/v1/public/doctors for every dermatology
        specialization_id, de-duplicating by doctor id (a doctor can
        match both ids — confirmed 2026-08-31, e.g. dr. Evy Aryanti
        appears under both 60 and 66).
        """
        by_id: dict[int, dict] = {}
        for spec_id in specialization_ids:
            page = 1
            while True:
                payload = self._get_json(
                    f"{API_BASE}/api/v1/public/doctors",
                    hospital_slug="_group",
                    cache_key=f"doctors_spec_{spec_id}_page_{page}",
                    params={
                        "page": page,
                        "per_page": 50,
                        "specialization_ids[]": spec_id,
                        "fields": "id,slug,full_name,photo,specializations,hospitals,status",
                    },
                )
                entries = payload.get("data", [])
                for entry in entries:
                    doctor_id = entry.get("id")
                    if doctor_id is not None:
                        by_id[doctor_id] = entry

                pagination = payload.get("meta", {}).get("pagination", {})
                if not pagination.get("has_more_pages"):
                    break
                page += 1

        return list(by_id.values())

    def fetch_doctor_schedule(self, slug: str) -> dict:
        """Fetch the per-doctor schedule payload (day/time/hospital
        breakdown). Kept as its own method because it's a distinct,
        cacheable API call per doctor.
        """
        return self._get_json(
            f"{API_BASE}/api/v1/public/doctors/{slug}/schedules",
            hospital_slug=slug,
            cache_key=f"schedule_{slug}",
        )

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        specialization_ids = self.discover_dermatology_specialization_ids()

        entries = self._fetch_dermatology_listing(specialization_ids)
        log.info("sentra_medika_dermatology_doctors_found_network_wide", count=len(entries))

        records: list[RawDoctorRecord] = []
        n_skipped_no_slug = 0
        for entry in entries:
            slug = entry.get("slug")
            if not slug:
                n_skipped_no_slug += 1
                continue

            hospitals = entry.get("hospitals", [])
            if jabodetabek_only:
                is_jabodetabek = any(
                    _is_jabodetabek_hospital(h.get("name", "")) for h in hospitals if isinstance(h, dict)
                )
                if not is_jabodetabek:
                    continue

            schedule_payload = self.fetch_doctor_schedule(slug)
            credentials_text = ", ".join(
                sp.get("name", "") for sp in entry.get("specializations", []) if isinstance(sp, dict)
            )

            records.append(
                RawDoctorRecord(
                    raw_name=entry.get("full_name", ""),
                    raw_credentials_text=f"{entry.get('full_name', '')} {credentials_text}".strip(),
                    raw_schedule_entries=[{"raw_schedule": schedule_payload}],
                    source_url=f"https://sentramedikahospitals.com/doctors/detail/{slug}",
                    raw_payload={"listing_entry": entry, "schedule": schedule_payload},
                )
            )

        if n_skipped_no_slug:
            log.warning("sentra_medika_entries_missing_slug", count=n_skipped_no_slug)
        log.info(
            "sentra_medika_dermatology_doctors_final",
            total_network_wide=len(entries),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
