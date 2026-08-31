"""Primaya Hospital adapter — Fase 3.

⚠️ KNOWN INCOMPLETE COVERAGE (confirmed 2026-08-08, accepted per user
decision): the search endpoint's `not_in`-based pagination reliably
returns the first ~28 dermatologists (in 3 calls of 10/10/8) then starts
returning "No results!" with results_count=0 while full_results_count
stays nonzero — i.e. the server-side query breaks rather than continuing
or erroring cleanly. This happened consistently across multiple runs even
after capping/windowing the not_in list (tried both an unbounded
cumulative list and a 20-entry sliding window — see _NOT_IN_WINDOW and
_fetch_dermatology_listing). Result ordering also appears non-
deterministic between runs (different post_ids appear in call 2 across
separate runs), which rules out a simple "resume from where we left off"
fix. This is treated as a genuine limitation of Primaya's WordPress Ajax
Search Pro setup, not a bug in this scraper — Primaya doctors/hospitals
sourced from this adapter should be treated as PARTIAL coverage
(confirmed ~28 of an unknown larger total, last seen full_results_count
before breakage was 48 nationwide). Fase 4/6 must not assume this list is
exhaustive; hospital-level data_status for Primaya branches should
reflect that dermatologist counts here are a lower bound, not confirmed
complete, until this is revisited (e.g. by querying per-city instead of
"semua lokasi", which was not attempted here).

Reconnaissance (2026-08-08, via Playwright network capture per spec §3.7 —
WordPress site, checked /wp-json/ first per §3.7 but the site's own custom
REST endpoint `primayahospital/v1/doctors/search` requires an API key
(401) so it is not usable; the real mechanism is the Ajax Search Pro
plugin's admin-ajax.php action, found by the user):

- Doctor search (Ajax Search Pro plugin), works via plain POST + honest
  UA, no session/cookies/Playwright needed at runtime:
    POST https://primayahospital.com/wp-admin/admin-ajax.php
      action=ajaxsearchpro_search
      aspp=
      asid=1
      asp_inst_id=1_1
      options=termset[lokasi][]=-1&termset[spesialisasi][]=649&aspf[list_rs__3]=__any__
              &device=1&filters_initial=1&filters_changed=0&wpml_lang=id
              &qtranslate_lang=0&woo_currency=IDR&current_page_id=33194
      autop=1
      version=4.29.1
  `termset[spesialisasi][]=649` is the taxonomy term ID for "Kulit dan
  Kelamin" (dermatology) — found by the user, not something this scraper
  discovers dynamically (no endpoint found yet that lists term IDs by
  name; if this ID drifts, re-verify by finding the current
  `cari-jadwal-dokter` filter for kulit dan kelamin manually).

- PAGINATION IS NOT page-number-based. Confirmed 2026-08-08 (48 total
  dermatologists, only 10 returned per call): the front-end's "Lihat
  lebih banyak (N)" button re-POSTs the SAME options string plus a
  `not_in[pagepost][i]=<post_id>` entry for every post_id already shown,
  and `not_in_count=<count>`. The server excludes those IDs and returns
  the next batch. This scraper reproduces that by accumulating seen
  post_ids across calls (see _fetch_dermatology_listing).
  ⚠️ `full_results_count` in the response is NOT a fixed grand total —
  it is the REMAINING count after the not_in exclusion (confirmed
  empirically: 48 -> 38 -> 28 -> ... as batches are consumed). An earlier
  version of this scraper wrongly treated it as a fixed total and stopped
  after only 28/48 doctors (`len(all_cards) >= full_count` triggers early
  once full_count starts shrinking). The correct stop condition is
  "this call returned 0 new cards" or "full_count reached 0", not a
  cumulative comparison against a shrinking number.
- Each result item's `html` field is an HTML fragment (doctor-card);
  `id` field is the WP post_id used both for the doctor's URL slug lookup
  and for the schedule AJAX call below.
- Per-doctor schedule:
    POST https://primayahospital.com/wp-admin/admin-ajax.php
      action=ph_doctor_get_doctor
      post_id=<id>
      _wpnonce=<nonce>
      selected_rs=
  Response is an HTML fragment (schedule-item blocks per hospital/clinic/
  day), not JSON schedule data — Fase 4 parsing will need an HTML/text
  parser for this, not just JSON field access. `_wpnonce` was captured
  once during recon and reused successfully across many calls minutes
  apart; WordPress nonces are typically valid ~24h but this should be
  re-fetched (from the search page's inline script) rather than hardcoded
  if the scraper is run on a different day — see NONCE caveat below.

⚠️ NONCE CAVEAT: DERMATOLOGY_WPNONCE below is a value captured during
recon and is NOT guaranteed to stay valid indefinitely. If schedule
fetches start failing (HTTP 403 on ph_doctor_get_doctor), re-extract a
fresh nonce from https://primayahospital.com/cari-jadwal-dokter/
rather than hardcoding a new one blindly — ideally this should become
a small "refresh nonce via one GET request" step in a future revision.
Re-verified 2026-08-31: the site's inline config script id is now
`module-doctor-js-extra` (`var ph_module_doctor = {...}`, previously
assumed to be `ph_doctor`), and it exposes THREE separate nonces
(`_wpnonce`, `doctor_nonce`, `profile_nonce`) — only `doctor_nonce`
works for the `ph_doctor_get_doctor` ajax action (confirmed by testing
all three against a real post_id; the other two 403). Look for
`doctor_nonce` specifically, not the first `_wpnonce`-like value found.

Re-verify this structure before relying on it long-term (Appendix A
caveat — site behavior can change without notice).
"""

from __future__ import annotations

from selectolax.parser import HTMLParser

from src.logging_setup import get_logger
from src.scrapers.base import BaseScraper, HospitalRef, RawDoctorRecord

log = get_logger(__name__)

AJAX_URL = "https://primayahospital.com/wp-admin/admin-ajax.php"
DERMATOLOGY_TERM_ID = "649"  # "Kulit dan Kelamin" taxonomy term id, found by user via cari-jadwal-dokter URL

# Re-captured 2026-08-31 (the 2026-08-08 value started 403'ing — WordPress
# nonces expire after ~24h, this needed a fresh extraction) — see NONCE
# CAVEAT in module docstring for where/how to re-extract this.
DERMATOLOGY_WPNONCE = "e996cca032"

# Max not_in[pagepost][] entries sent per listing call. Empirically, 25
# entries worked and 28 broke the query (see _fetch_dermatology_listing
# docstring) — kept comfortably under that observed threshold rather than
# at the edge of it, since the exact limit was not exhaustively bisected.
_NOT_IN_WINDOW = 20

_BASE_OPTIONS = (
    "termset[lokasi][]=-1"
    f"&termset[spesialisasi][]={DERMATOLOGY_TERM_ID}"
    "&aspf[list_rs__3]=__any__"
    "&device=1&filters_initial=1&filters_changed=0"
    "&wpml_lang=id&qtranslate_lang=0&woo_currency=IDR"
    "&current_page_id=33194"
)

# Jabodetabek location fragments matched against the doctor-card's
# location text (e.g. "Bekasi", "Tangerang", "Jakarta Barat"). Primaya's
# location field is a short city/area name, not a full branch name.
# "karawang" included per user decision 2026-08-31 (dashboard review) —
# previously excluded as "historically debated", now treated as in-scope.
_JABODETABEK_LOCATION_HINTS = [
    "jakarta",
    "bekasi",
    "tangerang",
    "bogor",
    "depok",
    "cikarang",
    "karawang",
]

_KNOWN_NON_JABODETABEK_LOCATIONS = [
    "makassar",
    "palangkaraya",
    "sorowako",
    "sukabumi",
    "pangkalpinang",
    "semarang",
    "bandung",
    "hertasning",  # Makassar
]


def _is_jabodetabek_location(location_text: str) -> bool:
    text_lower = location_text.lower()
    if any(hint in text_lower for hint in _KNOWN_NON_JABODETABEK_LOCATIONS):
        return False
    return any(hint in text_lower for hint in _JABODETABEK_LOCATION_HINTS)


def _parse_doctor_cards(html: str) -> list[dict]:
    """Parse the search result HTML fragment into per-doctor dicts. This
    IS html-selector parsing (spec §3.7 allows it when no structured
    source exists) — the AJAX response only gives us `results` (name/id/
    link, no location) and `html` (has location, no clean structured
    field for it), so both are combined by post_id.
    """
    tree = HTMLParser(html)
    cards: list[dict] = []
    for card in tree.css("div.item.asp_r_pagepost"):
        class_attr = card.attributes.get("class", "") or ""
        post_id = None
        for cls in class_attr.split():
            if cls.startswith("asp_r_pagepost_"):
                post_id = cls.removeprefix("asp_r_pagepost_")
                break
        if not post_id:
            continue

        name_node = card.css_first(".doctor-card-name")
        location_node = card.css_first(".doctor-card-location")
        link_node = card.css_first(".doctor-card-name a")

        cards.append(
            {
                "post_id": post_id,
                "name": (name_node.text(strip=True) if name_node else "").strip(),
                "location": (location_node.text(strip=True) if location_node else "").strip(),
                "url": link_node.attributes.get("href") if link_node else None,
            }
        )
    return cards


class PrimayaScraper(BaseScraper):
    group_name = "primaya"
    base_urls = [AJAX_URL]
    requires_js = False  # plain POST endpoint; JS/Playwright only needed during recon to discover the not_in pagination mechanism

    def discover_hospitals(self) -> list[HospitalRef]:
        raise NotImplementedError(
            "Primaya discover_hospitals() tidak berdiri sendiri — gunakan "
            "fetch_all_dermatology_doctors(), yang menemukan hospital DAN dokter "
            "sekaligus dari listing yang ter-filter termset[spesialisasi]."
        )

    def fetch_doctors(self, hospital: HospitalRef) -> list[RawDoctorRecord]:
        raise NotImplementedError(
            "Primaya fetch_doctors(hospital) tidak dipakai langsung — gunakan "
            "fetch_all_dermatology_doctors()."
        )

    def _fetch_dermatology_listing(self) -> list[dict]:
        """Walk the not_in-based cumulative pagination until the server
        stops returning new doctors (spec §9 Fase 2/3 caching principle
        applies per-call, but each call's `options` string differs by
        accumulated not_in list, so each is cached under its own key).

        ⚠️ SERVER-SIDE LIMIT ON not_in SIZE (confirmed 2026-08-08): sending
        more than ~25 `not_in[pagepost][i]` entries causes the endpoint to
        return results_count=0 / "No results!" while still reporting a
        nonzero full_results_count — i.e. the query silently breaks rather
        than erroring cleanly. This is a genuine quirk of Primaya's Ajax
        Search Pro setup, not something this scraper can fix by itself.
        Mitigation: only the most recently seen post_ids are kept in the
        not_in list (_NOT_IN_WINDOW), on the assumption that the server's
        underlying ordering is stable enough that once a post_id has
        scrolled out of the last _NOT_IN_WINDOW results it won't resurface
        within the same run. This is a heuristic, not a guarantee — if a
        future scrape yields fewer doctors than a previous one for no
        registry/config reason, re-check this window logic first.
        """
        all_cards: list[dict] = []
        seen_post_ids: list[str] = []
        call_num = 0

        while True:
            not_in_window = seen_post_ids[-_NOT_IN_WINDOW:]
            options = _BASE_OPTIONS
            if not_in_window:
                not_in_parts = "&".join(
                    f"not_in[pagepost][{i}]={pid}" for i, pid in enumerate(not_in_window)
                )
                options = f"{options}&{not_in_parts}&not_in_count={len(not_in_window)}"

            payload = self._post_json(
                AJAX_URL,
                hospital_slug="_group",
                cache_key=f"listing_call_{call_num}",
                data={
                    "action": "ajaxsearchpro_search",
                    "aspp": "",
                    "asid": "1",
                    "asp_inst_id": "1_1",
                    "options": options,
                    "autop": "1",
                    "version": "4.29.1",
                    **({"asp_call_num": str(call_num)} if call_num > 0 else {}),
                },
            )

            batch_count = payload.get("results_count", 0)
            full_count = payload.get("full_results_count", 0)
            cards = _parse_doctor_cards(payload.get("html", ""))
            new_cards = [c for c in cards if c["post_id"] not in seen_post_ids]

            log.info(
                "primaya_listing_call",
                call_num=call_num,
                batch_count=batch_count,
                full_count=full_count,
                cards_parsed=len(cards),
                new_cards=len(new_cards),
                not_in_window_size=len(not_in_window),
            )

            if not new_cards:
                # Either genuinely done (full_count <= 0) or the server
                # returned only already-seen doctors (window drifted) —
                # either way there's nothing new to extract from this call.
                break

            all_cards.extend(new_cards)
            seen_post_ids.extend(c["post_id"] for c in new_cards)
            call_num += 1

            # full_results_count is NOT a fixed grand total — it is the
            # REMAINING count after excluding not_in (confirmed
            # empirically 2026-08-08: it decreases call over call, e.g.
            # 48 -> 38 -> 28 as batches of 10/10/8 are consumed). The
            # correct stop condition is "no new results", not "cumulative
            # >= some fixed number" (that bug caused an earlier run to
            # stop at 28/48 doctors).
            if full_count <= 0:
                break
            if call_num > 20:  # safety cap — should never trigger at full_count~48
                log.warning("primaya_listing_pagination_safety_cap_hit", n_cards=len(all_cards))
                break

        return all_cards

    def fetch_doctor_schedule(self, post_id: str) -> str:
        """Fetch the per-doctor schedule HTML fragment. Returns raw HTML
        (not parsed) — Fase 4 parsing is responsible for extracting
        structured day/time/hospital data from it.
        """
        payload = self._post_json(
            AJAX_URL,
            hospital_slug=post_id,
            cache_key=f"schedule_{post_id}",
            data={
                "action": "ph_doctor_get_doctor",
                "post_id": post_id,
                "_wpnonce": DERMATOLOGY_WPNONCE,
                "selected_rs": "",
            },
        )
        return payload.get("data", "") or ""

    def fetch_all_dermatology_doctors(self, *, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
        cards = self._fetch_dermatology_listing()
        log.info("primaya_dermatology_doctors_found_network_wide", count=len(cards))

        records: list[RawDoctorRecord] = []
        for card in cards:
            if jabodetabek_only and not _is_jabodetabek_location(card["location"]):
                continue

            schedule_html = self.fetch_doctor_schedule(card["post_id"])

            records.append(
                RawDoctorRecord(
                    raw_name=card["name"],
                    raw_credentials_text=card["name"],  # credentials embedded in name string
                    raw_schedule_entries=[{"raw_html": schedule_html}],  # HTML fragment, parsed in Fase 4
                    source_url=card["url"] or "",
                    raw_payload={"card": card, "schedule_html": schedule_html},
                )
            )

        log.info(
            "primaya_dermatology_doctors_final",
            total_network_wide=len(cards),
            jabodetabek_only=jabodetabek_only,
            kept=len(records),
        )
        return records
