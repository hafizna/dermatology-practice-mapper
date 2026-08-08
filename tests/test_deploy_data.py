"""Fase 8 deploy helper: ensure_database_present() tests.

Offline (spec §14) — GitHub API calls are mocked via monkeypatching
httpx.Client, never a real network request.
"""

from __future__ import annotations

import httpx
import pytest

from src import deploy_data


class _FakeResponse:
    def __init__(self, json_body=None, content=b"", status_code=200):
        self._json_body = json_body
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_body


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self._responses.pop(0)


def test_ensure_database_present_noop_when_file_already_exists(tmp_path, monkeypatch):
    db_path = tmp_path / "derm_mapper.sqlite"
    db_path.write_bytes(b"existing content")
    monkeypatch.setattr(deploy_data, "DB_PATH", db_path)

    result = deploy_data.ensure_database_present()

    assert result is True
    assert db_path.read_bytes() == b"existing content"  # untouched, no download attempted


def test_ensure_database_present_returns_false_without_config(tmp_path, monkeypatch):
    db_path = tmp_path / "derm_mapper.sqlite"
    monkeypatch.setattr(deploy_data, "DB_PATH", db_path)
    monkeypatch.delenv("GITHUB_REPO", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    result = deploy_data.ensure_database_present()

    assert result is False
    assert not db_path.exists()


def test_ensure_database_present_downloads_when_configured(tmp_path, monkeypatch):
    db_path = tmp_path / "derm_mapper.sqlite"
    monkeypatch.setattr(deploy_data, "DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_REPO", "someuser/somerepo")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    release_json = {
        "assets": [
            {"name": "derm_mapper.sqlite", "url": "https://api.github.com/repos/someuser/somerepo/releases/assets/1"}
        ]
    }
    fake_responses = [
        _FakeResponse(json_body=release_json),
        _FakeResponse(content=b"downloaded sqlite bytes"),
    ]
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(fake_responses))

    result = deploy_data.ensure_database_present()

    assert result is True
    assert db_path.read_bytes() == b"downloaded sqlite bytes"


def test_ensure_database_present_returns_false_when_asset_missing(tmp_path, monkeypatch):
    db_path = tmp_path / "derm_mapper.sqlite"
    monkeypatch.setattr(deploy_data, "DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_REPO", "someuser/somerepo")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    release_json = {"assets": [{"name": "some_other_file.db", "url": "https://example.com/wrong"}]}
    fake_responses = [_FakeResponse(json_body=release_json)]
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(fake_responses))

    result = deploy_data.ensure_database_present()

    assert result is False
    assert not db_path.exists()


def test_ensure_database_present_returns_false_on_http_error(tmp_path, monkeypatch):
    db_path = tmp_path / "derm_mapper.sqlite"
    monkeypatch.setattr(deploy_data, "DB_PATH", db_path)
    monkeypatch.setenv("GITHUB_REPO", "someuser/somerepo")
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    fake_responses = [_FakeResponse(status_code=401)]
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(fake_responses))

    result = deploy_data.ensure_database_present()

    assert result is False
    assert not db_path.exists()


def test_env_var_takes_precedence_over_missing_streamlit_secrets(monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", "env-value")
    assert deploy_data._get_secret("GITHUB_REPO") == "env-value"


def test_get_secret_returns_none_when_neither_source_available(monkeypatch):
    monkeypatch.delenv("SOME_UNSET_KEY", raising=False)
    assert deploy_data._get_secret("SOME_UNSET_KEY") is None
