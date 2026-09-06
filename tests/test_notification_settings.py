"""Tests for P0 #5: notification settings CRUD + Bark channel + /test endpoint.

Covers:
  - GET  /api/settings/notifications: returns the channel view.
  - POST /api/settings/notifications: persists bark_url into user_settings.
  - POST /api/settings/notifications/test: dry_run path is safe + returns
    the resolved notifier output without hitting the network.
  - POST /api/settings/notifications/test: rejects unsupported channels.
  - POST /api/settings/notifications/test: live send errors surface as
    a JSON failure rather than a 500.
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


def test_get_settings_returns_default_view(http_server) -> None:
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(base, "/api/settings/notifications", method="GET")
    assert code == 200, body
    assert body["channel"] == "feishu"
    assert "bark_url" in body
    assert "feishu_webhook" in body
    assert "dingtalk_webhook" in body
    assert "bark" in body["supported_channels"]


def test_post_settings_persists_bark_url(http_server) -> None:
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base, "/api/settings/notifications",
        payload={"bark_url": "https://api.day.app/TESTKEY123"},
    )
    assert code == 200, body
    assert body["bark_url"] == "https://api.day.app/TESTKEY123"
    # Re-GET to confirm persistence
    code, body = _http_request(base, "/api/settings/notifications", method="GET")
    assert code == 200
    assert body["bark_url"] == "https://api.day.app/TESTKEY123"


def test_test_endpoint_dry_run_does_not_hit_network(http_server) -> None:
    """dry_run=True must return a structured preview without HTTP call."""
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    # First persist a bark URL so the factory can build a real client
    _http_request(
        base, "/api/settings/notifications",
        payload={"bark_url": "https://api.day.app/DRYRUN"},
    )
    code, body = _http_request(
        base, "/api/settings/notifications/test",
        payload={"channel": "bark", "dry_run": True, "title": "hello", "body": "world"},
    )
    assert code == 200, body
    assert body["ok"] is True
    assert body["channel"] == "bark"
    assert body["dry_run"] is True
    result = body["result"]
    assert result["status"] == "dry_run"
    assert result["title"] == "hello"
    assert result["body"] == "world"


def test_test_endpoint_unknown_channel_returns_400(http_server) -> None:
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base, "/api/settings/notifications/test",
        payload={"channel": "carrier-pigeon", "dry_run": True},
    )
    assert code == 400, body
    assert body["ok"] is False
    assert "carrier-pigeon" in body.get("error", "") or "Unsupported" in body.get("error", "")


def test_test_endpoint_unsupported_live_send(http_server) -> None:
    """Channels without send_text (e.g. feishu) return 400 on live test."""
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base, "/api/settings/notifications/test",
        payload={"channel": "feishu", "dry_run": False},
    )
    # feishu notifier does not have send_text
    assert code == 400, body
    assert body["ok"] is False
    assert "live_send_not_supported" in body.get("error", "")


def test_test_endpoint_bark_live_send_failure_surfaces(http_server, monkeypatch) -> None:
    """A network failure on live Bark send returns a structured error, not 500."""
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    _http_request(
        base, "/api/settings/notifications",
        payload={"bark_url": "https://api.day.app/LIVEFAIL"},
    )
    # Monkey-patch ONLY the bark client's urlopen (not the test's own).
    import urllib.error
    from cd_monitor.notify import bark as _bark_mod
    def _boom(*_args, **_kwargs):
        raise urllib.error.URLError("name resolution failed")
    monkeypatch.setattr(_bark_mod, "_urlopen", _boom)
    code, body = _http_request(
        base, "/api/settings/notifications/test",
        payload={"channel": "bark", "dry_run": False},
    )
    assert code == 200, body
    assert body["ok"] is True  # outer ok; the notifier captures the inner failure
    result = body["result"]
    assert result["status"] == "failed"
    assert "url_error" in result.get("error", "")
