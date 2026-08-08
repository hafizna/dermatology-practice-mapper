"""Eka Hospital adapter — Fase 3. MANUAL SNAPSHOT, not a live network scraper.

booking.ekahospital.com is protected by a CloudFront "Human Verification"
challenge (confirmed 2026-08-08 — see config/sources.yaml doctor_schedule_
sources.eka.notes for the full investigation: honest User-Agent gets HTTP
403, real headless Chromium via Playwright also gets 403, and real
headful Chromium gets HTTP 202 with page title "Human Verification").
Automating past a CAPTCHA/human-verification challenge is explicitly
forbidden by spec §3.6. Eka is therefore NOT scraped live.

Per user decision, Eka data instead comes from a manually-saved browser
snapshot ("Web Page, Complete") of the dermatology specialist listing,
placed under data/manual_uploads/eka/{YYYY-MM-DD}/dermatology_listing.html
by the user. This module only PARSES that file — it never makes a network
request. Every record's provenance reflects this: source_tier is manual
(Tier 3 per spec's source-tier scheme, since it did not come from an
automated Tier 1 fetch even though the underlying site IS Tier 1/official),
and scraped_at is the snapshot date embedded in the directory name, not
"now" — the data is only as fresh as the last manual upload.

Known limitation (confirmed 2026-08-08): the listing page gives doctor
name, credentials-in-name, and hospital branch, but NOT practice
schedule (day/time). Fase 4 parsing will need to leave ScheduleSlot rows
empty/absent for Eka doctors and mark hospital.data_status='partial'
rather than 'complete' — schedule data would require also manually
saving each doctor's individual profile page, which the user has not
done (and may not, given the volume — 35 doctors nationwide as of the
2026-08-08 snapshot).

To refresh: user re-saves the page as "Web Page, Complete" from a normal
browser (this bypasses the bot challenge because it's a real human
browsing session, not automation) into a new dated subfolder under
data/manual_uploads/eka/, and re-runs the loader — see
load_latest_snapshot().
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from selectolax.parser import HTMLParser

from src.config import DATA_DIR
from src.logging_setup import get_logger
from src.scrapers.base import RawDoctorRecord

log = get_logger(__name__)

MANUAL_UPLOADS_ROOT = DATA_DIR / "manual_uploads" / "eka"
LISTING_FILENAME = "dermatology_listing.html"

# Jabodetabek branch name fragments, matched against the doctor-card's
# location text (e.g. "EKA Hospital MT Haryono", "RSIA Eka Hospital PIK").
# Confirmed 2026-08-08 snapshot also includes Eka Hospital Pekanbaru,
# which must NOT match.
_JABODETABEK_LOCATION_HINTS = [
    "mt haryono",
    "permata hijau",
    "pluit",
    "bsd",
    "cibubur",
    "depok",
    "bekasi",
    "pik",  # RSIA Eka Hospital PIK
]

_KNOWN_NON_JABODETABEK_LOCATIONS = [
    "pekanbaru",
]


def _is_jabodetabek_location(location_text: str) -> bool:
    text_lower = location_text.lower()
    if any(hint in text_lower for hint in _KNOWN_NON_JABODETABEK_LOCATIONS):
        return False
    return any(hint in text_lower for hint in _JABODETABEK_LOCATION_HINTS)


@dataclass
class EkaSnapshotInfo:
    snapshot_date: dt.date
    path: Path


def find_latest_snapshot() -> EkaSnapshotInfo | None:
    """Find the most recently dated subfolder under
    data/manual_uploads/eka/ that contains the expected listing file.
    Returns None if no manual upload exists yet — callers must treat
    that as "no data", not as an error to guess around (spec §3.1).
    """
    if not MANUAL_UPLOADS_ROOT.exists():
        return None

    candidates: list[EkaSnapshotInfo] = []
    for child in MANUAL_UPLOADS_ROOT.iterdir():
        if not child.is_dir():
            continue
        listing_path = child / LISTING_FILENAME
        if not listing_path.exists():
            continue
        try:
            snapshot_date = dt.date.fromisoformat(child.name)
        except ValueError:
            log.warning("eka_snapshot_folder_not_dated", folder=str(child))
            continue
        candidates.append(EkaSnapshotInfo(snapshot_date=snapshot_date, path=listing_path))

    if not candidates:
        return None
    return max(candidates, key=lambda c: c.snapshot_date)


def _parse_doctor_cards(html: str) -> list[dict]:
    tree = HTMLParser(html)
    cards: list[dict] = []
    for card in tree.css('[data-testid="link-profile-doctor"]'):
        name_node = card.css_first('[data-testid="doctor-name"]')
        location_node = card.css_first('[data-testid="doctor-location"]')
        cards.append(
            {
                "name": (name_node.text(strip=True) if name_node else "").strip(),
                "location": (location_node.text(strip=True) if location_node else "").strip(),
                "url": card.attributes.get("href"),
            }
        )
    return cards


def fetch_all_dermatology_doctors(*, jabodetabek_only: bool = True) -> list[RawDoctorRecord]:
    """Load and parse the latest manual snapshot. Named to match the same
    entrypoint shape as the network-based adapters (Siloam/Mitra Keluarga/
    Hermina/Primaya) even though this one reads a local file, so
    src/scrapers/registry.py can treat it uniformly.
    """
    snapshot = find_latest_snapshot()
    if snapshot is None:
        log.warning(
            "eka_no_manual_snapshot_found",
            expected_root=str(MANUAL_UPLOADS_ROOT),
            hint="Simpan halaman dermatologi Eka sebagai 'Web Page, Complete' ke "
            f"{MANUAL_UPLOADS_ROOT}/{{YYYY-MM-DD}}/{LISTING_FILENAME}",
        )
        return []

    html = snapshot.path.read_text(encoding="utf-8", errors="ignore")
    cards = _parse_doctor_cards(html)
    log.info(
        "eka_manual_snapshot_loaded",
        snapshot_date=snapshot.snapshot_date.isoformat(),
        n_doctors_found=len(cards),
    )

    records: list[RawDoctorRecord] = []
    for card in cards:
        if jabodetabek_only and not _is_jabodetabek_location(card["location"]):
            continue

        records.append(
            RawDoctorRecord(
                raw_name=card["name"],
                raw_credentials_text=card["name"],  # credentials embedded in name string
                raw_schedule_entries=[],  # NOT AVAILABLE from this source — see module docstring
                source_url=card["url"] or "",
                raw_payload={
                    "card": card,
                    "manual_snapshot_date": snapshot.snapshot_date.isoformat(),
                    "manual_snapshot_path": str(snapshot.path),
                },
            )
        )

    log.info(
        "eka_dermatology_doctors_final",
        total_in_snapshot=len(cards),
        jabodetabek_only=jabodetabek_only,
        kept=len(records),
        snapshot_date=snapshot.snapshot_date.isoformat(),
    )
    return records
