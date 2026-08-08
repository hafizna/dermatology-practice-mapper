"""Upload data/processed/derm_mapper.sqlite as a GitHub Release asset on
the current repo, for src/deploy_data.py to download at dashboard
startup on a host (e.g. Streamlit Community Cloud) that doesn't have the
file locally.

Run this manually after refreshing data (fetch-registry -> scrape --all
-> compute-core) and whenever you want the hosted dashboard to pick up
new data — per the user's own description of the workflow (2026-08-09):
"refreshnya via GitHub or code aja... dashboard emang cuma display."

Each run replaces the SAME release tag ("data") so the hosted app always
downloads the latest snapshot from one stable tag/asset name, rather
than accumulating a new release per refresh.

Requires the `gh` CLI, already authenticated (gh auth status) with
access to the repo.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = REPO_ROOT / "data" / "processed" / "derm_mapper.sqlite"
RELEASE_TAG = "data"
ASSET_NAME = "derm_mapper.sqlite"


def main() -> None:
    if not DB_PATH.exists():
        print(f"Database tidak ditemukan di {DB_PATH}. Jalankan pipeline dulu (fetch-registry, scrape, compute-core).")
        sys.exit(1)

    size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"Database: {DB_PATH} ({size_mb:.2f} MB)")

    # Delete the existing release (if any) so re-uploading the same
    # asset name doesn't fail with "asset already exists". --cleanup-tag
    # also removes the underlying git tag so the next create starts clean.
    subprocess.run(
        ["gh", "release", "delete", RELEASE_TAG, "--yes", "--cleanup-tag"],
        cwd=REPO_ROOT,
        capture_output=True,
    )  # ignore failure: fine if it didn't exist yet

    result = subprocess.run(
        [
            "gh", "release", "create", RELEASE_TAG,
            str(DB_PATH),
            "--title", "Dashboard data snapshot",
            "--notes", "Auto-published by scripts/publish_database_release.py. "
                       "Downloaded by the hosted dashboard at startup — see src/deploy_data.py.",
        ],
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        print("Gagal membuat release. Cek `gh auth status` dan koneksi ke GitHub.")
        sys.exit(1)

    print(f"\nBerhasil. Release tag '{RELEASE_TAG}' dengan asset '{ASSET_NAME}' sudah ter-upload.")
    print("Dashboard yang di-hosting akan mengunduh ini otomatis saat start berikutnya.")


if __name__ == "__main__":
    main()
