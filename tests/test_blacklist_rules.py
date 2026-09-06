"""Tests for P0 #10: per-watchlist result blacklist keyword rules.

Covers the storage migration, the ``ResultBlacklistService``, and the
HTTP API exposed at ``/api/watchlist/{id}/blacklist-rules`` (GET + PUT).
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from cd_monitor.core.models import WatchItem
from cd_monitor.services.result_blacklist_service import (
    ResultBlacklistRule,
    ResultBlacklistService,
)
from cd_monitor.storage.sqlite import add_watch, init_db
from cd_monitor.web_server import _build_handler


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    init_db(str(db))
    return db


def _add_watch(db: Path) -> int:
    return add_watch(
        str(db),
        WatchItem(catalog_no="B-TEST-1", title_jp="黑名单测试", title_cn="黑名单测试"),
    )


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


# -------- storage migration --------


def test_migration_creates_table(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    import sqlite3
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='result_blacklist_rules'"
        ).fetchall()
    assert rows and rows[0][0] == "result_blacklist_rules"


# -------- service-level roundtrip --------


def test_service_roundtrip_normalizes_keywords(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = _add_watch(db)
    svc = ResultBlacklistService(db)
    saved = svc.set_keywords(wid, ["  关键词1 ", "re:foo.*bar", "关键词1"])
    # duplicates and blanks are dropped, regex form is preserved
    assert any("re:foo.*bar" in k for k in saved)
    assert len([k for k in saved if "re:" not in k]) == 1

    rule = svc.get_rule(wid)
    assert isinstance(rule, ResultBlacklistRule)
    assert rule.watch_id == wid
    assert rule.updated_at is not None
    # roundtrip via the list accessor
    assert svc.get_keywords(wid) == saved


def test_service_get_unknown_watch_returns_empty(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    svc = ResultBlacklistService(db)
    assert svc.get_keywords(999) == []
    rule = svc.get_rule(999)
    assert rule.watch_id == 999
    assert rule.keywords == []
    assert rule.updated_at is None


def test_service_set_updates_existing_row(tmp_path: Path) -> None:
    import time
    db = _make_db(tmp_path)
    wid = _add_watch(db)
    svc = ResultBlacklistService(db)
    svc.set_keywords(wid, ["first"])
    rule_v1 = svc.get_rule(wid)
    assert rule_v1.updated_at is not None
    time.sleep(1.1)  # SQLite CURRENT_TIMESTAMP is second-resolution.
    svc.set_keywords(wid, ["second", "third"])
    rule_v2 = svc.get_rule(wid)
    assert rule_v1.updated_at != rule_v2.updated_at
    assert set(svc.get_keywords(wid)) == {"second", "third"}


def test_service_clear_removes_row(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = _add_watch(db)
    svc = ResultBlacklistService(db)
    svc.set_keywords(wid, ["foo", "bar"])
    assert svc.get_keywords(wid)
    svc.clear(wid)
    assert svc.get_keywords(wid) == []


# -------- HTTP API --------


def test_http_get_returns_empty_for_new_watch(http_server) -> None:
    server, db = http_server
    wid = _add_watch(db)
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base, f"/api/watchlist/{wid}/blacklist-rules", method="GET"
    )
    assert code == 200, body
    assert body == {"watch_id": wid, "keywords": [], "updated_at": None}


def test_http_put_persists_and_roundtrips(http_server) -> None:
    server, db = http_server
    wid = _add_watch(db)
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base,
        f"/api/watchlist/{wid}/blacklist-rules",
        payload={"keywords": ["关键词A", "re:foo.*bar"]},
    )
    assert code == 200, body
    assert body["watch_id"] == wid
    assert body["saved_count"] == 2
    assert any("re:foo.*bar" in k for k in body["keywords"])
    assert body["updated_at"] is not None

    code, body = _http_request(
        base, f"/api/watchlist/{wid}/blacklist-rules", method="GET"
    )
    assert code == 200
    assert set(body["keywords"]) == set(["关键词a", "re:foo.*bar"])


def test_http_put_accepts_string_payload(http_server) -> None:
    server, db = http_server
    wid = _add_watch(db)
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base,
        f"/api/watchlist/{wid}/blacklist-rules",
        payload={"keywords": "关键词A,关键词B,re:sku.*pro"},
    )
    assert code == 200, body
    assert body["saved_count"] == 3


def test_http_put_rejects_non_list_payload(http_server) -> None:
    server, db = http_server
    wid = _add_watch(db)
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_request(
        base,
        f"/api/watchlist/{wid}/blacklist-rules",
        payload={"keywords": {"oops": 1}},
    )
    assert code == 400
    assert "keywords" in body.get("error", "")


def test_http_get_404_for_unknown_watch(http_server) -> None:
    server, _ = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, _ = _http_request(base, "/api/watchlist/9999/blacklist-rules", method="GET")
    assert code == 404
