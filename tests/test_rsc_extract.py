"""Tests for the generic Next.js RSC extraction helper."""

from __future__ import annotations

from src.scrapers._rsc_extract import extract_rsc_json_blocks, find_array_under_key_in_blocks, find_first_array_under_key


def test_extract_rsc_json_blocks_parses_valid_json_push():
    html = '<script>self.__next_f.push([1,"18:{\\"items\\":[1,2,3]}"])</script>'
    blocks = extract_rsc_json_blocks(html)
    assert blocks == [{"items": [1, 2, 3]}]


def test_extract_rsc_json_blocks_skips_non_json_push():
    html = '<script>self.__next_f.push([1,"9:I[95751,[],\\"\\"]\\n"])</script>'
    # This IS valid JSON actually (a list) -- use a genuinely non-JSON one:
    html2 = '<script>self.__next_f.push([1,"27:T5c6,plain text not json"])</script>'
    blocks = extract_rsc_json_blocks(html2)
    assert blocks == []


def test_extract_rsc_json_blocks_handles_multiple_pushes():
    html = (
        '<script>self.__next_f.push([1,"1:{\\"a\\":1}"])</script>'
        '<script>self.__next_f.push([1,"2:{\\"b\\":2}"])</script>'
    )
    blocks = extract_rsc_json_blocks(html)
    assert {"a": 1} in blocks
    assert {"b": 2} in blocks


def test_find_first_array_under_key_nested():
    obj = {"outer": [{"inner": {"doctors": [{"name": "x"}, {"name": "y"}]}}]}
    result = find_first_array_under_key(obj, "doctors")
    assert result == [{"name": "x"}, {"name": "y"}]


def test_find_first_array_under_key_not_found_returns_none():
    obj = {"outer": {"nothing_here": True}}
    assert find_first_array_under_key(obj, "doctors") is None


def test_find_array_under_key_in_blocks_searches_all_blocks():
    blocks = [{"unrelated": 1}, {"nested": {"doctors": [{"name": "z"}]}}]
    result = find_array_under_key_in_blocks(blocks, "doctors")
    assert result == [{"name": "z"}]
