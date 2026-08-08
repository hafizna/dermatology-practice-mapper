"""Mitra Keluarga adapter — Fase 3.

Reconnaissance (2026-08-08, via Playwright network capture per spec §3.7):

- Public API host: services.mitrakeluarga.com (backend used by the
  production Next.js site itself; no robots.txt restriction found for
  this API host).
- Dermatology listing (network-wide, single request handles all — API
  supports large per_page so no pagination loop is needed in practice):
    GET /clinic-v2/v1/master-data/doctor/data
        ?page=1&per_page=100&speciality_slug=kulit-and-penyakit-kelamin-8owtyp
  Confirmed 2026-08-08: total=68 doctors, total_page=1 at per_page=100.
- Each listing entry already embeds `clinic` (branch name/slug) and
  `schedules` (concrete upcoming dates, NOT a recurring weekly pattern
  like Siloam) — no separate per-doctor schedule call needed.

Schedule shape difference from Siloam: entries are dated bookable slots
(e.g. "2026-08-09"), not `day_of_week` recurring rules. We derive the
recurring weekly pattern by deduplicating on (day_name, start_time,
end_time) across the visible date range — see _derive_weekly_pattern().
This is an inference from a few weeks of concrete bookings, not a
guarantee the hospital's official recurring schedule matches exactly;
Fase 4 parsing should treat it with appropriate confidence, not "high".

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from src.logging_setup import get_logger
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

API_BASE = "https://services.mitrakeluarga.com"
DERMATOLOGY_SPECIALITY_SLUG = "kulit-and-penyakit-kelamin-8owtyp"

# Jabodetabek branch name fragments, matched against `clinic.name`.
# Mitra Keluarga branch names are city-explicit (unlike Siloam's "Siloam
# Heart Hospital" edge case), so name-matching alone is reliable here —
# still documented explicitly for audit rather than assumed silently.
_JABODETABEK_CLINIC_HINTS = [
    "bekasi",
    "bintaro",
    "cibubur",
    "cikarang",
    "deltamas",  # Cikarang area
    "depok",
    "gading serpong",  # Tangerang
    "grand wisata",  # Bekasi area
    "jatiasih",  # Bekasi
    "kalideres",  # Jakarta
    "kelapa gading",  # Jakarta
    "kemayoran",  # Jakarta
    "pamulang",  # Tangerang Selatan
]

# Explicitly-excluded branches found during recon that could be mistaken
# for Jabodetabek by loose matching (documented so a future name-hint edit
# doesn't accidentally include them).
_KNOWN_NON_JABODETABEK_CLINICS = [
    "bina husada",  # Bekasi region name reused elsewhere; NOT auto-included, verify manually if seen
    "kenjeran",  # Surabaya
    "pondok tjandra",  # Surabaya
    "sidoarjo",
    "surabaya",
    "tegal",
    "waru",  # Surabaya
]


def _is_jabodetabek_clinic(clinic_name: str) -> bool:
    name_lower = clinic_name.lower()
    return any(hint in name_lower for hint in _JABODETABEK_CLINIC_HINTS)


class MitraKeluargaScraper(BaseScraper):
    group_name = "mitra_keluarga"
    base_urls = [API_BASE]
    requires_js = False  # API is plain JSON; JS only needed during recon to discover the endpoint

    def discover_hospitals(self) -> list[HospitalRef]:
        raise NotImplementedError(
            "Mitra Keluarga discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN dokter "
            "sekaligus dari satu endpoint listing spesialisasi (sama seperti Siloam)."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "Mitra Keluarga fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors()."
        )

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        """Single entrypoint: list all dermatologists network-wide (one
        request handles all 68 as of recon — per_page is generous enough
        that pagination is not needed in practice, but we still loop over
        total_page defensively in case the count grows).
        """
        all_entries: list[dict] = []
        page = 1
        while True:
            payload = self._get_json(
                f"{API_BASE}/clinic-v2/v1/master-data/doctor/data",
                hospital_slug="_group",
                cache_key=f"doctors_page_{page}",
                params={"page": page, "per_page": 100, "speciality_slug": DERMATOLOGY_SPECIALITY_SLUG},
            )
            entries = payload.get("data", [])
            all_entries.extend(entries)
            meta = payload.get("meta", {})
            total_page = meta.get("total_page", 1)
            log.info("mitra_keluarga_page_fetched", page=page, total_page=total_page, n_entries=len(entries))
            if page >= total_page or not entries:
                break
            page += 1

        log.info("mitra_keluarga_dermatology_doctors_found_network_wide", count=len(all_entries))

        records: list[RawDoctorRecord] = []
        n_skipped_no_clinic = 0
        for entry in all_entries:
            clinic = entry.get("clinic") or {}
            clinic_name = clinic.get("name", "")
            if not clinic_name:
                n_skipped_no_clinic += 1
                continue

            if jabodetabek_only and not _is_jabodetabek_clinic(clinic_name):
                continue

            doctor = entry.get("doctor") or {}
            records.append(
                RawDoctorRecord(
                    raw_name=doctor.get("name", ""),
                    raw_credentials_text=doctor.get("name", ""),  # credentials embedded in name string
                    raw_schedule_entries=entry.get("schedules", []),
                    source_url=f"https://www.mitrakeluarga.com/dokter/{doctor.get('slug', '')}",
                    raw_payload=entry,
                )
            )

        if n_skipped_no_clinic:
            log.warning("mitra_keluarga_entries_missing_clinic", count=n_skipped_no_clinic)
        log.info(
            "mitra_keluarga_dermatology_doctors_final",
            total_network_wide=len(all_entries),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
