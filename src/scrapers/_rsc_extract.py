"""Helper for extracting embedded JSON data from Next.js App Router RSC
(React Server Components) streaming payloads.

Some sources (e.g. Hermina) render doctor listings entirely server-side
with no separate client-side API call — the data ships as a JSON blob
inside `<script>self.__next_f.push([1,"N:...")</script>` tags in the raw
HTML. This is still "structured data" in the spirit of spec §3.7 (we are
not scraping via CSS selectors / text patterns), just embedded rather than
served from a dedicated endpoint.

This is intentionally generic (find *any* array under a given key) rather
than Hermina-specific, so it can be reused if another Next.js App Router
source turns out to need the same approach.
"""

from __future__ import annotations

import json
import re
from typing import Any

_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.DOTALL)
_INDEX_PREFIX_RE = re.compile(r"^(\d+):")


def extract_rsc_json_blocks(html: str) -> list[Any]:
    """Return every RSC push block that parses as JSON (after stripping
    the `N:` index prefix Next.js adds). Blocks that are plain strings
    (not JSON) are skipped rather than raising — RSC streams mix JSON
    blocks with plain text/HTML fragments.
    """
    blocks: list[Any] = []
    for raw in _PUSH_RE.findall(html):
        try:
            unescaped = raw.encode().decode("unicode_escape")
        except UnicodeDecodeError:
            continue

        prefix_match = _INDEX_PREFIX_RE.match(unescaped)
        json_str = unescaped[prefix_match.end() :] if prefix_match else unescaped
        try:
            blocks.append(json.loads(json_str))
        except (json.JSONDecodeError, ValueError):
            continue
    return blocks


def find_first_array_under_key(obj: Any, key: str) -> list[Any] | None:
    """Depth-first search for the first list found under a dict key
    named `key`, anywhere in a nested structure of dicts/lists.
    """
    if isinstance(obj, dict):
        if key in obj and isinstance(obj[key], list):
            return obj[key]
        for v in obj.values():
            found = find_first_array_under_key(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_first_array_under_key(v, key)
            if found is not None:
                return found
    return None


def find_array_under_key_in_blocks(blocks: list[Any], key: str) -> list[Any] | None:
    for block in blocks:
        found = find_first_array_under_key(block, key)
        if found is not None:
            return found
    return None
