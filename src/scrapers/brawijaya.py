"""Brawijaya Hospital adapter — Fase 3.

Reconnaissance (2026-08-08):

- brawijayahospital.com's MAIN pages (e.g. /find-doctor) are behind
  Cloudflare bot-protection: honest-UA curl gets HTTP 200 for static
  pages but the page itself is client-rendered (0 doctors in raw HTML);
  Playwright HEADLESS gets HTTP 403 with a `cdn-cgi/challenge-platform`
  response (same pattern as Eka's CloudFront block); Playwright HEADFUL
  gets a normal 200. This is the same class of headless-detection issue
  documented for Eka Hospital — NOT something this scraper bypasses
  automatically (spec §3.6).
- HOWEVER: the site's own `/api/*` Next.js API routes are NOT behind the
  same protection — confirmed 2026-08-08 that plain `curl` with an honest
  User-Agent (no Playwright, no session) gets clean 200 JSON responses
  from them. This is analogous to finding a public API for a
  JS-rendered page (spec §3.7), not evasion — the API routes are simply
  less aggressively protected than the HTML pages, and are the same
  routes the site's own frontend calls.
- Doctor discovery + schedule come from TWO endpoints:
    1. GET /api/proxy?service=data&path=items/branch
           &filter[status][_eq]=published&fields=rsid,name_hospital,address_hospital
       Lists all hospital branches with numeric `rsid`. Confirmed
       2026-08-08: all 7 branches (Antasari, Saharjo, Taman Mini, Duren
       Tiga, Depok, Tangerang, Kemang) are Jabodetabek — consistent with
       the Fase 1 registry, which also found no non-Jabodetabek Brawijaya
       branch.
    2. GET /api/appointment/getSpecialistDoctorsSchedule?group=true&rsid={rsid}
       Per-branch, ALL specialities grouped together with fully
       structured schedules (weekday int, start_hour/minute,
       end_hour/minute, plus `cuti` leave-of-absence dates). This
       scraper filters the returned groups to specialist names
       containing "Dermat" or "Kulit" (case-insensitive) — confirmed
       2026-08-08 that BOTH phrasings are used simultaneously across
       branches ("Spesialis Dermatovenereologi" vs "Spesialis Penyakit
       Kulit dan Kelamin") with NO overlap between the two doctor sets,
       so filtering on only one phrase would silently miss doctors.
- No cross-branch doctor listing endpoint was found/used — this adapter
  queries the schedule endpoint once per branch (7 calls) rather than a
  single global "all dermatologists" call, unlike most other adapters in
  this project.

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from src.logging_setup import get_logger
from src.scrapers.base import (
    BaseScraper,
    BlockedError,
    HospitalRef,
    NetworkError,
    RawDoctorRecord,
    StructureChangedError,
)

log = get_logger(__name__)

SITE_BASE = "https://brawijayahospital.com"
PROXY_PATH = "/api/proxy"
SCHEDULE_PATH = "/api/appointment/getSpecialistDoctorsSchedule"

# Case-insensitive substrings matching the specialist group names that
# cover dermatology at this source. Confirmed 2026-08-08 both are used
# with disjoint doctor sets (see module docstring) — filtering on only
# one would silently under-count.
_DERMATOLOGY_SPECIALIST_HINTS = ["dermat", "kulit"]


def _is_dermatology_specialist(specialist_name: str) -> bool:
    name_lower = specialist_name.lower()
    return any(hint in name_lower for hint in _DERMATOLOGY_SPECIALIST_HINTS)


class BrawijayaScraper(BaseScraper):
    group_name = "brawijaya"
    base_urls = [SITE_BASE]
    requires_js = False  # /api/* routes are plain JSON, confirmed accessible via curl despite the main site's Cloudflare protection

    def discover_hospitals(self) -> list[HospitalRef]:
        """Fetch the branch list. All 7 branches found are Jabodetabek as
        of the 2026-08-08 snapshot (see module docstring) — no location
        filtering is applied here, but callers should not assume this
        will always be true if Brawijaya opens branches elsewhere.
        """
        payload = self._curl_get_json(
            f"{SITE_BASE}{PROXY_PATH}",
            hospital_slug="_group",
            cache_key="branches",
            params={
                "service": "data",
                "path": "items/branch",
                "filter[status][_eq]": "published",
                "fields": "rsid,name_hospital,address_hospital",
            },
        )
        branches = payload.get("data", [])
        return [
            HospitalRef(
                name=b.get("name_hospital", ""),
                url=f"{SITE_BASE}",
                slug=str(b.get("rsid")),
            )
            for b in branches
            if b.get("rsid") is not None
        ]

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "Brawijaya fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors(), yang loop semua cabang otomatis."
        )

    def fetch_branch_schedule(self, rsid: str) -> dict:
        return self._curl_get_json(
            f"{SITE_BASE}{SCHEDULE_PATH}",
            hospital_slug=f"rsid_{rsid}",
            cache_key=f"schedule_rsid_{rsid}",
            params={"group": "true", "rsid": rsid},
        )

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        """`jabodetabek_only` is accepted for interface consistency with
        other adapters but has no effect here — every branch this
        adapter discovers is Jabodetabek as of the 2026-08-08 snapshot
        (see module docstring), so there is nothing to filter out.

        Per-branch failures (e.g. rsid=66 "Taman Mini" returned a genuine
        HTTP 500 from Brawijaya's own server during recon 2026-08-08, not
        a block) are logged and skipped rather than aborting the whole
        run — spec §3.1: a branch we couldn't fetch must show up as
        missing/unknown data, not silently zero doctors, and must not
        take down data from branches that DID succeed.
        """
        branches = self.discover_hospitals()
        log.info("brawijaya_branches_found", count=len(branches))

        records: list[RawDoctorRecord] = []
        seen_pids: set[int] = set()
        failed_branches: list[str] = []
        for branch in branches:
            try:
                payload = self.fetch_branch_schedule(branch.slug)
            except (NetworkError, BlockedError, StructureChangedError) as exc:
                log.warning(
                    "brawijaya_branch_schedule_fetch_failed",
                    branch_name=branch.name,
                    branch_rsid=branch.slug,
                    error=str(exc),
                )
                failed_branches.append(branch.name)
                continue

            groups = payload.get("data", [])

            for group in groups:
                specialist_name = group.get("specialist", "")
                if not _is_dermatology_specialist(specialist_name):
                    continue

                for doc in group.get("doctors", []):
                    pid = doc.get("pid")
                    key = (pid, branch.slug)
                    if pid is not None and key in seen_pids:
                        continue
                    if pid is not None:
                        seen_pids.add(key)

                    records.append(
                        RawDoctorRecord(
                            raw_name=doc.get("dokter", ""),
                            raw_credentials_text=doc.get("dokter", ""),  # credentials embedded in name string
                            raw_schedule_entries=doc.get("schedules", []),
                            source_url=f"{SITE_BASE}/find-doctor/{pid}" if pid else "",
                            raw_payload={
                                "doctor": doc,
                                "specialist_group": specialist_name,
                                "branch_name": branch.name,
                                "branch_rsid": branch.slug,
                            },
                        )
                    )

        if failed_branches:
            log.warning("brawijaya_branches_incomplete", failed_branches=failed_branches)
        log.info(
            "brawijaya_dermatology_doctors_final",
            n_branches=len(branches),
            n_branches_failed=len(failed_branches),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
