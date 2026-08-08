#!/usr/bin/env python
"""Source-drift detection — spec §14 "Source drift".

Checks selectors / expected fields for each configured adapter, tells
network failure apart from structural change, and reports which adapters
are likely broken. Meaningful only once adapters exist (Fase 2+); until
then this script has nothing to check and says so explicitly rather than
reporting a fake "all OK".
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as `python scripts/check_sources.py` directly (not just
# `python -m`), by ensuring the repo root is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scrapers.registry import ADAPTERS  # noqa: E402


def main() -> int:
    if not ADAPTERS:
        print(
            "check_sources: tidak ada adapter terdaftar di src/scrapers/registry.py. "
            "Belum ada yang bisa dicek drift-nya (adapter pertama dibangun di Fase 2)."
        )
        return 0

    # Real drift-checking logic lands alongside the first adapters (Fase 2/3).
    print(f"check_sources: {len(ADAPTERS)} adapter(s) terdaftar — pengecekan drift belum diimplementasikan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
