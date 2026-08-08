"""Fetch data/processed/derm_mapper.sqlite from a GitHub Release asset
when running on a host without the file already present (e.g. Streamlit
Community Cloud).

Why this exists: .gitignore deliberately keeps the database (real doctor
names/schedules) out of the git history — that rule predates this
deploy setup and is not being relaxed just to make hosting easier. A
GitHub Release asset on the SAME private repo is a separate mechanism
from the commit history: it's uploaded via `gh release upload` (see
scripts/publish_database_release.py), never touches a git commit, and
is only reachable with a token that has access to the private repo —
so this keeps the "no doctor data in the repo history" rule intact
while still letting a hosted dashboard have real data to show.

Local development is unaffected: if the database file already exists
(the normal case when you've run the CLI pipeline locally), this module
does nothing. It only activates when the file is missing AND the
required secrets are configured — the two conditions that are true on
a fresh Streamlit Cloud deploy and false on a dev machine.
"""

from __future__ import annotations

import os

import httpx

from src.config import DATA_DIR
from src.logging_setup import get_logger

log = get_logger(__name__)

DB_PATH = DATA_DIR / "processed" / "derm_mapper.sqlite"


def _get_secret(name: str) -> str | None:
    """Read from Streamlit secrets when running under Streamlit Cloud,
    falling back to an environment variable for local/CI use. Streamlit
    is imported lazily so this module has no hard dependency on it for
    callers (e.g. the CLI) that never need the fallback path.
    """
    value = os.environ.get(name)
    if value:
        return value
    try:
        import streamlit as st

        return st.secrets.get(name)
    except Exception:  # pragma: no cover - no secrets.toml / not under streamlit
        return None


def ensure_database_present() -> bool:
    """Download the database from a GitHub Release asset if it's not
    already on disk. Returns True if the database is present after this
    call (whether it was already there or just downloaded), False if it
    could not be obtained (missing config, network failure, etc.) — the
    caller (src/app.py) is responsible for showing a clear message
    rather than crashing on a missing-table error.
    """
    if DB_PATH.exists():
        return True

    repo = _get_secret("GITHUB_REPO")  # e.g. "hafizna/dermatology-practice-mapper"
    tag = _get_secret("GITHUB_RELEASE_TAG") or "latest"
    token = _get_secret("GITHUB_TOKEN")
    asset_name = _get_secret("GITHUB_RELEASE_ASSET") or "derm_mapper.sqlite"

    if not repo or not token:
        log.warning("deploy_data_missing_config", db_exists=False, has_repo=bool(repo), has_token=bool(token))
        return False

    api_base = f"https://api.github.com/repos/{repo}/releases"
    release_url = f"{api_base}/latest" if tag == "latest" else f"{api_base}/tags/{tag}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "DermPracticeMapper-Deploy/0.1",
    }

    try:
        with httpx.Client(timeout=30.0) as client:
            release_resp = client.get(release_url, headers=headers)
            release_resp.raise_for_status()
            release = release_resp.json()

            asset = next((a for a in release.get("assets", []) if a["name"] == asset_name), None)
            if asset is None:
                log.error("deploy_data_asset_not_found", asset_name=asset_name, release_tag=tag)
                return False

            # Private-repo release assets must be fetched via the API
            # asset endpoint with an Accept: application/octet-stream
            # header — the browser-facing browser_download_url requires
            # a signed session cookie, not a bearer token.
            asset_headers = dict(headers, Accept="application/octet-stream")
            download_resp = client.get(asset["url"], headers=asset_headers, follow_redirects=True)
            download_resp.raise_for_status()

            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            DB_PATH.write_bytes(download_resp.content)
            log.info("deploy_data_downloaded", size_bytes=len(download_resp.content), release_tag=tag)
            return True
    except httpx.HTTPError as exc:
        log.error("deploy_data_download_failed", error=str(exc))
        return False
