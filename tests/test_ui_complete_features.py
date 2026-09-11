"""Tests for new UI-complete features added in the Kuro Atelier v8 enhancement pass.

Covers:
- enable_watch storage function (complement to disable_watch)
- POST /api/watchlist/{id}/enable and /disable endpoints
- _watchlist_all includes the id column so the frontend can identify tasks
- POST /api/login-state/{xianyu,wameiji} accepts the `snapshot` field
  (in addition to the legacy `content` field) for the browser-extension paste flow
"""
from __future__ import annotations

import json
import threading

import pytest

import urllib.error
import urllib.request

from cd_monitor.core.models import WatchItem
from cd_monitor.storage.sqlite import (
    add_watch,
    disable_watch,
    enable_watch,
    init_db,
    list_watch_all,
)
from cd_monitor.web_server import create_server


def _get_json(url: str) -> dict:
    import urllib.request
    with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url: str, body: dict) -> tuple[int, dict]:
    import urllib.request
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # noqa: S310
        return exc.code, json.loads(exc.read().decode("utf-8") or "{}")


@pytest.fixture
def running_server(tmp_path):
    db_path = tmp_path / "ui_complete.db"
    init_db(db_path)
    add_watch(db_path, WatchItem(catalog_no="TEST-PAUSE-001", priority=3))
    add_watch(db_path, WatchItem(catalog_no="TEST-PAUSE-002", priority=3))
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    yield base_url, db_path
    server.shutdown()
    server.server_close()


def test_enable_watch_storage_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "storage.db"
    init_db(db_path)
    wid = add_watch(db_path, WatchItem(catalog_no="STORAGE-PAUSE-001", priority=3))
    assert disable_watch(db_path, wid) is True
    items = list_watch_all(db_path)
    assert items[0].catalog_no == "STORAGE-PAUSE-001"
    assert items[0].enabled is False
    assert enable_watch(db_path, wid) is True
    items = list_watch_all(db_path)
    assert items[0].enabled is True
    # Idempotent: enabling again still returns True (row still updated_at-bumped).
    assert enable_watch(db_path, wid) is True


def test_watchlist_all_endpoint_includes_id(running_server) -> None:
    base_url, _ = running_server
    payload = _get_json(f"{base_url}/api/watchlist/all")
    items = payload["items"]
    assert len(items) == 2
    # id field must be exposed so the frontend can render data-task-id and
    # call /api/watchlist/{id}/{enable,disable,hard-delete}.
    assert all(isinstance(it["id"], int) for it in items)
    assert all(it["enabled"] is not None for it in items)


def test_watchlist_disable_enable_endpoints(running_server) -> None:
    base_url, db_path = running_server
    payload = _get_json(f"{base_url}/api/watchlist/all")
    target = next(it for it in payload["items"] if it["catalog_no"] == "TEST-PAUSE-001")
    wid = target["id"]
    # Disable via API
    status, body = _post_json(f"{base_url}/api/watchlist/{wid}/disable", {})
    assert status == 200
    assert body == {"disabled": True, "id": wid}
    # Verify state
    payload = _get_json(f"{base_url}/api/watchlist/all")
    target = next(it for it in payload["items"] if it["id"] == wid)
    assert target["enabled"] in (False, 0)
    # Resume via API
    status, body = _post_json(f"{base_url}/api/watchlist/{wid}/enable", {})
    assert status == 200
    assert body == {"enabled": True, "id": wid}
    payload = _get_json(f"{base_url}/api/watchlist/all")
    target = next(it for it in payload["items"] if it["id"] == wid)
    assert target["enabled"] in (True, 1)


def test_login_state_accepts_snapshot_field(running_server, tmp_path) -> None:
    base_url, _ = running_server
    snapshot = {
        "capturedAt": "2026-06-30T00:00:00Z",
        "pageUrl": "https://www.goofish.com/",
        "cookies": [
            {
                "name": "tracknick",
                "value": "PASTE-TEST",
                "domain": ".goofish.com",
                "path": "/",
                "expires": 9999999999,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ],
        "storage": {"local": {"token": "X"}, "session": {}},
    }
    out_xianyu = tmp_path / "xianyu_state.json"
    out_wameiji = tmp_path / "wameiji_state.json"
    status, body = _post_json(
        f"{base_url}/api/login-state/xianyu",
        {"snapshot": json.dumps(snapshot), "output_path": str(out_xianyu)},
    )
    assert status == 200, body
    assert body["cookie_count"] == 1
    assert body["login_state_ready"] is True
    assert out_xianyu.exists()
    parsed = json.loads(out_xianyu.read_text(encoding="utf-8"))
    assert parsed["format"] == "xianyu-storage-state-v1"
    cookies = parsed["playwright_storage_state"]["cookies"]
    assert cookies[0]["name"] == "tracknick"
    assert cookies[0]["value"] == "PASTE-TEST"

    snapshot_w = dict(snapshot, pageUrl="https://meruki.cn/")
    snapshot_w["cookies"][0]["domain"] = ".meruki.cn"
    status, body = _post_json(
        f"{base_url}/api/login-state/wameiji",
        {"snapshot": json.dumps(snapshot_w), "output_path": str(out_wameiji)},
    )
    assert status == 200, body
    assert body["cookie_count"] == 1
    parsed = json.loads(out_wameiji.read_text(encoding="utf-8"))
    assert parsed["format"] == "wameiji-storage-state-v1"


def test_login_state_rejects_empty_snapshot(running_server) -> None:
    base_url, _ = running_server
    status, body = _post_json(f"{base_url}/api/login-state/xianyu", {"snapshot": "  "})
    assert status == 400
    assert body["error"] == "content_required"
