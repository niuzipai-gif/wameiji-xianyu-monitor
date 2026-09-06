"""Tests for P0 #6: AI settings CRUD + /test endpoint.

Covers:
  - GET  /api/settings/ai: returns the merged view (env + override).
  - POST /api/settings/ai: persists the editable fields into user_settings.
  - POST /api/settings/ai/test: cheap probe (no API call) reports configured
    state; the configured state depends on the test env (.env is loaded).
  - api_key is never exposed in plain text (only its presence / source).
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from cd_monitor.storage.sqlite import init_db
from cd_monitor.web_server import _build_handler


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    init_db(str(db))
    return db


def _http_request(base, path, payload=None, method="POST"):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return exc.code, body


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)
    handler = _build_handler(db_path=db, static_dir=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, db
    server.shutdown()
    server.server_close()


def test_get_settings_returns_expected_shape(http_server) -> None:
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(base, "/api/settings/ai", method="GET")
    assert code == 200, body
    # Required keys
    for k in ("base_url", "model_name", "is_configured",
              "api_key_configured", "api_key_source", "fallback_enabled",
              "fallback_configured", "supported_providers"):
        assert k in body, f"missing {k} in {body}"
    # The api_key field is masked; we only see api_key_configured.
    assert "api_key" not in body
    # supported_providers is the canonical list
    assert "openai" in body["supported_providers"]


def test_post_settings_persists_ai_config(http_server) -> None:
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base, "/api/settings/ai",
        payload={
            "ai_base_url": "https://api.openai.com/v1",
            "ai_model_name": "gpt-4o-mini",
            "ai_api_key": "sk-test-1234",
        },
    )
    assert code == 200, body
    assert body["saved"] >= 1
    view = body["view"]
    assert view["base_url"] == "https://api.openai.com/v1"
    assert view["model_name"] == "gpt-4o-mini"
    # The override sets api_key_source to user_settings.
    assert view["api_key_source"] == "user_settings"
    assert view["api_key_configured"] is True


def test_post_settings_reports_saved_count(http_server) -> None:
    """POST returns a `saved` count of how many keys were written."""
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base, "/api/settings/ai",
        payload={"ai_base_url": "https://api.openai.com/v1"},
    )
    assert code == 200, body
    assert body["saved"] == 1


def test_post_settings_unknown_field_is_ignored(http_server) -> None:
    """Unknown fields in the payload should not be persisted."""
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base, "/api/settings/ai",
        payload={"ai_base_url": "https://api.openai.com/v1",
                 "unknown_field": "x", "another": "y"},
    )
    assert code == 200, body
    # Only ai_base_url is saved; the rest are ignored.
    assert body["saved"] == 1


def test_test_endpoint_returns_view_and_reason(http_server) -> None:
    """The /test endpoint returns the view and a reason string."""
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(base, "/api/settings/ai/test", payload={})
    assert code == 200, body
    assert "view" in body
    assert "reason" in body
    # The configured state depends on env vars; just check we got a valid reason.
    assert body["reason"] in ("configured", "not_configured", "client_available", "client_unavailable")
    if body["reason"] in ("configured", "client_available"):
        assert body["ok"] is True
    else:
        assert body["ok"] is False


def test_test_endpoint_live_probes_client(http_server) -> None:
    """live=True does an extra availability check on the AI client module."""
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base, "/api/settings/ai/test",
        payload={"live": True},
    )
    assert code == 200, body
    assert "view" in body
    assert "reason" in body
