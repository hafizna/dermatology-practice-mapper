"""Siloam Hospitals adapter — Fase 2 pilot adapter.

Reconnaissance (2026-08-08, via Playwright network capture per spec §3.7 —
inspect DevTools Network before writing an HTML parser):

- Public API host: mysiloam-api.siloamhospitals.com (no robots.txt; this is
  a JSON API backend, not crawlable web content — the same endpoints the
  production site itself calls).
- Dermatology speciality_id (stable across the whole Siloam network):
    44d54965-b740-451c-9fec-a21b6e7544bf  ("Dermatologi (Kulit)")
  with 4 known sub-specializations (SpDVE/SpKK family) — see
  discover_dermatology_speciality_id() for how this is confirmed at
  runtime rather than hardcoded blindly.
- Doctor listing (network-wide, must be filtered to Jabodetabek hospitals
  after fetch — Siloam has no per-branch listing endpoint we found):
    GET /api/v2/doctors/withavailability?show=&page=&specialityId=...
- Per-doctor schedule (this is where day/time/hospital breakdown lives):
    GET /api/v2/schedules/withavailability/doctor/{doctor_id}?consultationTypeId=1:4:5:6:11:12

IMPORTANT: Eka Hospital (booking.ekahospital.com) was the spec's original
pilot candidate but returns HTTP 403 from CloudFront for both an honest
User-Agent and a real headless Chromium (Playwright) — i.e. bot protection
that cannot be bypassed through normal means (spec §16). Per user decision,
Siloam replaces Eka as the Fase 2 pilot adapter.

Re-verify this structure before relying on it long-term — site behavior
can change without notice (spec Appendix A caveat).
"""

from __future__ import annotations

from src.logging_setup import get_logger
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

API_BASE = "https://mysiloam-api.siloamhospitals.com"
DERMATOLOGY_SPECIALITY_ID = "44d54965-b740-451c-9fec-a21b6e7544bf"

# Jabodetabek branch name fragments, matched case-insensitively against
# `hospital_names` in the doctor listing API. Kept here (not hardcoded
# deep in parsing logic) so it's easy to audit/extend as branches open or
# names change. Cross-checked against config/sources.yaml notes and the
# hospital's own city/address field, not just name pattern-matching alone
# (see _is_jabodetabek_hospital).
_JABODETABEK_NAME_HINTS = [
    "kebon jeruk",
    "asri",
    "tb simatupang",
    "mampang",
    "kelapa dua",
    "lippo village",
    "lippo cikarang",
    "bekasi",
    "bogor",
    "sentosa",
    "cempaka putih",
    "semanggi",  # MRCCC
    "senayan",  # Siloam Specialist Center Senayan
]

_JABODETABEK_ADDRESS_HINTS = [
    "jakarta",
    "bogor",
    "depok",
    "tangerang",
    "bekasi",
    "banten",  # Tangerang addresses are often tagged province=Banten
]


def _is_jabodetabek_hospital(hospital_name: str, hospital_address: str | None) -> bool:
    name_lower = hospital_name.lower()
    if any(hint in name_lower for hint in _JABODETABEK_NAME_HINTS):
        return True
    if hospital_address:
        addr_lower = hospital_address.lower()
        if any(hint in addr_lower for hint in _JABODETABEK_ADDRESS_HINTS):
            return True
    return False


class SiloamScraper(BaseScraper):
    group_name = "siloam"
    base_urls = [API_BASE]
    requires_js = False  # underlying API is plain JSON; JS only needed to discover it (recon done, not needed at runtime)

    def discover_dermatology_speciality_id(self) -> str:
        """Confirm DERMATOLOGY_SPECIALITY_ID still matches "Dermatologi
        (Kulit)" in the live specialities list, rather than trusting the
        hardcoded constant blindly (spec §14 source-drift spirit). Falls
        back to searching by name if the ID has changed.
        """
        payload = self._get_json(
            f"{API_BASE}/api/v2/generals/specialities/sub-specialities",
            hospital_slug="_group",
            cache_key="specialities",
            params={"lang": "id"},
        )
        for sp in payload.get("data", []):
            if sp.get("speciality_id") == DERMATOLOGY_SPECIALITY_ID:
                name = sp.get("speciality_name", "")
                if "dermat" not in name.lower() and "kulit" not in name.lower():
                    log.warning(
                        "siloam_speciality_id_mismatch",
                        expected_id=DERMATOLOGY_SPECIALITY_ID,
                        found_name=name,
                    )
                return DERMATOLOGY_SPECIALITY_ID

        # ID drifted — search by name as a fallback, and report loudly.
        for sp in payload.get("data", []):
            name = sp.get("speciality_name", "")
            if "dermat" in name.lower() or ("kulit" in name.lower() and "kelamin" not in name.lower()):
                log.warning(
                    "siloam_speciality_id_drifted",
                    old_id=DERMATOLOGY_SPECIALITY_ID,
                    new_id=sp.get("speciality_id"),
                    name=name,
                )
                return sp["speciality_id"]

        raise RuntimeError(
            "Siloam: tidak menemukan speciality dermatologi di /generals/specialities/sub-specialities "
            "— struktur API kemungkinan berubah. Lihat PROJECT_SPEC.md §14 (source drift)."
        )

    def discover_hospitals(self) -> list[HospitalRef]:
        """Siloam has no dedicated "list hospitals in group" endpoint we
        found; hospital identity is derived from the dermatologist listing
        itself (fetch_doctors fetches network-wide once and this method
        reuses that to avoid a second full fetch). For the pilot adapter,
        discovery and doctor-fetch are combined in fetch_all_derm_doctors().
        """
        raise NotImplementedError(
            "Siloam discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN "
            "dokter sekaligus dari satu endpoint listing spesialisasi."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "Siloam fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors(), yang sudah mengembalikan "
            "RawDoctorRecord per dokter beserta hospital_names asal."
        )

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        """Single entrypoint for the pilot adapter: list all dermatologists
        network-wide (Siloam's API is not per-hospital), fetch each one's
        schedule (which carries `hospital_address`), and only THEN decide
        Jabodetabek membership from the real address.

        Name-based matching alone is insufficient: e.g. "Siloam Heart
        Hospital" has no city in its name but is actually in Cinere, Depok
        (confirmed via its schedule's hospital_address during
        reconnaissance 2026-08-08). Filtering on the listing name field
        before fetching schedules would have silently dropped it — this
        method fetches schedules for every network-wide match first, then
        filters on real address data, so no Jabodetabek branch is lost to
        a naming quirk.
        """
        speciality_id = self.discover_dermatology_speciality_id()

        listing = self._get_json(
            f"{API_BASE}/api/v2/doctors/withavailability",
            hospital_slug="_group",
            cache_key=f"doctors_speciality_{speciality_id}",
            params={"show": 200, "page": 1, "specialityId": speciality_id},
        )
        entries = listing.get("data", [])
        log.info("siloam_dermatology_doctors_found_network_wide", count=len(entries))

        records: list[RawDoctorRecord] = []
        n_skipped_no_id = 0
        for entry in entries:
            doctor_id = entry.get("doctor_id")
            doctor_seo_key = entry.get("doctor_seo_key", "")
            if not doctor_id:
                n_skipped_no_id += 1
                continue

            schedule_payload = self.fetch_doctor_schedule(doctor_id, doctor_seo_key)
            availability = schedule_payload.get("data", {}).get("availability", [])
            schedule_entries = schedule_payload.get("data", {}).get("schedule", [])

            if jabodetabek_only:
                is_jabodetabek = any(
                    _is_jabodetabek_hospital(a.get("hospital_name", ""), a.get("hospital_address"))
                    for a in availability
                )
                if not is_jabodetabek:
                    continue

            records.append(
                RawDoctorRecord(
                    raw_name=entry.get("name", ""),
                    raw_credentials_text=entry.get("name", ""),  # credentials are embedded in the name string
                    raw_schedule_entries=schedule_entries,
                    source_url=f"https://www.siloamhospitals.com/dokter/{doctor_seo_key}",
                    raw_payload={"listing": entry, "availability": availability},
                )
            )

        if n_skipped_no_id:
            log.warning("siloam_entries_missing_doctor_id", count=n_skipped_no_id)
        log.info(
            "siloam_dermatology_doctors_final",
            total_network_wide=len(entries),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records

    def fetch_doctor_schedule(self, doctor_id: str, doctor_seo_key: str) -> dict:
        """Fetch the per-doctor schedule payload (day/time/hospital
        breakdown, plus `availability[].hospital_address` used for
        Jabodetabek filtering). Kept as its own method because it's a
        distinct, cacheable API call per doctor.
        """
        return self._get_json(
            f"{API_BASE}/api/v2/schedules/withavailability/doctor/{doctor_id}",
            hospital_slug=doctor_seo_key or doctor_id,
            cache_key=f"schedule_{doctor_id}",
            params={"consultationTypeId": "1:4:5:6:11:12"},
        )
