"""Fase 2: BaseScraper mechanics tests — rate limiter and error
classification. Offline / no real network calls.
"""

from __future__ import annotations

import time

import httpx
import pytest

from src.scrapers.base import BlockedError, NetworkError, RateLimiter, StructureChangedError


def test_rate_limiter_delays_second_request_to_same_domain():
    limiter = RateLimiter(seconds=0.2)
    limiter.wait("https://example.com/a")
    start = time.monotonic()
    limiter.wait("https://example.com/b")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.15  # allow small scheduling slack


def test_rate_limiter_does_not_delay_different_domains():
    limiter = RateLimiter(seconds=1.0)
    limiter.wait("https://a.example.com/x")
    start = time.monotonic()
    limiter.wait("https://b.example.com/x")
    elapsed = time.monotonic() - start
    assert elapsed < 0.5


@pytest.fixture()
def impl(tmp_path, monkeypatch):
    from src.scrapers.base import BaseScraper

    monkeypatch.setattr("src.scrapers.base.DATA_DIR", tmp_path)

    class Impl(BaseScraper):
        group_name = "test"
        base_urls = ["https://example.com"]

        def discover_hospitals(self):
            return []

        def fetch_doctors(self, hospital):
            return []

    return Impl(use_cache=False)


def test_blocked_status_raises_blocked_error(impl, monkeypatch):
    def fake_get(self, url, params=None):
        request = httpx.Request("GET", url)
        return httpx.Response(403, request=request, json={})

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    with pytest.raises(BlockedError):
        impl._get_json("https://example.com/api", hospital_slug="x", cache_key="x")


def test_server_error_raises_network_error_and_is_retried(impl, monkeypatch):
    calls = {"n": 0}

    def fake_get(self, url, params=None):
        calls["n"] += 1
        request = httpx.Request("GET", url)
        return httpx.Response(503, request=request, json={})

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    with pytest.raises(NetworkError):
        impl._get_json("https://example.com/api", hospital_slug="x", cache_key="x")
    # tenacity retries up to 3 attempts total
    assert calls["n"] == 3


def test_non_json_response_raises_structure_changed_error(impl, monkeypatch):
    def fake_get(self, url, params=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, text="<html>not json</html>")

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    with pytest.raises(StructureChangedError):
        impl._get_json("https://example.com/api", hospital_slug="x", cache_key="x")


def test_successful_response_is_cached_to_disk(impl, monkeypatch, tmp_path):
    def fake_get(self, url, params=None):
        request = httpx.Request("GET", url)
        return httpx.Response(200, request=request, json={"hello": "world"})

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    result = impl._get_json("https://example.com/api", hospital_slug="x", cache_key="mykey")
    assert result == {"hello": "world"}

    cached_files = list(tmp_path.rglob("mykey.json"))
    assert len(cached_files) == 1
