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


# --- _curl_get_json: used only by adapters where httpx is blocked but the
# system curl binary is not (Brawijaya) — spec §3.6, not evasion, see
# src/scrapers/base.py docstring on that method. ---


class _FakeCompletedProcess:
    def __init__(self, stdout: str, returncode: int = 0, stderr: str = ""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_curl_get_json_parses_body_and_status(impl, monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, timeout, check):
        return _FakeCompletedProcess(stdout='{"hello": "curl"}\n200')

    monkeypatch.setattr("src.scrapers.base.subprocess.run", fake_run)

    result = impl._curl_get_json("https://example.com/api", hospital_slug="x", cache_key="curlkey")
    assert result == {"hello": "curl"}
    cached_files = list(tmp_path.rglob("curlkey.json"))
    assert len(cached_files) == 1


def test_curl_get_json_blocked_status_raises_blocked_error(impl, monkeypatch):
    def fake_run(cmd, capture_output, text, timeout, check):
        return _FakeCompletedProcess(stdout="\n403")

    monkeypatch.setattr("src.scrapers.base.subprocess.run", fake_run)

    with pytest.raises(BlockedError):
        impl._curl_get_json("https://example.com/api", hospital_slug="x", cache_key="x")


def test_curl_get_json_server_error_retried_then_raises(impl, monkeypatch):
    calls = {"n": 0}

    def fake_run(cmd, capture_output, text, timeout, check):
        calls["n"] += 1
        return _FakeCompletedProcess(stdout="\n500")

    monkeypatch.setattr("src.scrapers.base.subprocess.run", fake_run)

    with pytest.raises(NetworkError):
        impl._curl_get_json("https://example.com/api", hospital_slug="x", cache_key="x")
    assert calls["n"] == 3


def test_curl_get_json_nonzero_exit_raises_network_error(impl, monkeypatch):
    def fake_run(cmd, capture_output, text, timeout, check):
        return _FakeCompletedProcess(stdout="", returncode=1, stderr="curl: (6) Could not resolve host")

    monkeypatch.setattr("src.scrapers.base.subprocess.run", fake_run)

    with pytest.raises(NetworkError):
        impl._curl_get_json("https://example.com/api", hospital_slug="x", cache_key="x")
