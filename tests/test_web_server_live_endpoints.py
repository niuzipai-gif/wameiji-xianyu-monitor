"""Tests for /api/scan/live-html and /api/scan/live-watchlist endpoints.

Uses the same ThreadingHTTPServer pattern as tests/test_web_server.py.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

import pytest

from cd_monitor.core.models import WatchItem
from cd_monitor.services.scan import scan_once_mock
from cd_monitor.storage.sqlite import add_watch, init_db
from cd_monitor.web_server import create_server


@pytest.fixture(autouse=True)
def enable_live_collection_for_active_path_tests(monkeypatch):
    """These tests exercise the live-path behavior after an explicit opt-in."""
    monkeypatch.setenv("DUAL_MARKET_COLLECTION_PAUSED", "0")


def _post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - depends on test
        body = exc.read().decode("utf-8", errors="replace")
        return {"_status": exc.code, "_body": body}


def _start_server(db_path, monkeypatch_stub):
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    return server, thread, base_url


@pytest.fixture
def live_html_stub(monkeypatch):
    """Replace capture_and_evaluate_live_html at the import location used by web_server."""
    calls: list[dict[str, Any]] = []

    def make_stub(return_value):
        async def _stub(*args, **kwargs):
            calls.append({"args": args, "kwargs": kwargs})
            return return_value

        return _stub, calls

    return make_stub


@pytest.fixture
def notify_stub(monkeypatch):
    calls: list[dict[str, Any]] = []

    def _stub(db_path, ids, opps, **kwargs):
        calls.append({"ids": ids, "channel": kwargs.get("channel_spec"), "dry_run": kwargs.get("dry_run")})
        return [
            {"opportunity_id": oid, "channel": kwargs.get("channel_spec"), "status": "no_channel_configured"}
            for oid in ids
        ]

    monkeypatch.setattr("cd_monitor.web_server.notify_opportunities", _stub)
    return calls


# === /api/scan/live-html ==========================================
def test_scan_live_html_returns_400_when_catalog_missing(tmp_path):
    db_path = tmp_path / "api.db"
    init_db(db_path)
    server, thread, base_url = _start_server(db_path, None)
    try:
        result = _post_json(f"{base_url}/api/scan/live-html", {})
        assert result.get("_status") == 400
        assert "catalog_no_required" in result.get("_body", "")
    finally:
        server.shutdown()
        server.server_close()


def test_scan_live_html_returns_human_required_when_capture_blocks(tmp_path, monkeypatch, live_html_stub):
    db_path = tmp_path / "api.db"
    init_db(db_path)
    stub, calls = live_html_stub(
        {
            "catalog_no": "SRCL-3520",
            "status": "human_required",
            "opportunity_count": 0,
            "opportunity_ids": [],
            "wameiji_capture": {
                "status": "human_required",
                "error_type": "async_capture_required",
            },
            "xianyu_capture": None,
        }
    )
    monkeypatch.setattr("cd_monitor.web_server.capture_and_evaluate_live_html", stub)

    server, thread, base_url = _start_server(db_path, None)
    try:
        result = _post_json(f"{base_url}/api/scan/live-html", {"catalog_no": "SRCL-3520"})
        assert result.get("status") == "human_required"
        assert result.get("catalog_no") == "SRCL-3520"
        assert "notifications" not in result
        assert len(calls) == 1
    finally:
        server.shutdown()
        server.server_close()


def test_scan_live_html_dispatches_notifications(tmp_path, monkeypatch, live_html_stub, notify_stub):
    db_path = tmp_path / "api.db"
    init_db(db_path)
    stub, _ = live_html_stub(
        {
            "catalog_no": "SRCL-3520",
            "status": "ok",
            "opportunity_count": 1,
            "opportunity_ids": [42],
            "wameiji_capture": {"status": "ok"},
            "xianyu_capture": {"status": "ok"},
        }
    )
    monkeypatch.setattr("cd_monitor.web_server.capture_and_evaluate_live_html", stub)

    class _Opp:
        catalog_no = "SRCL-3520"
        decision = "strong_alert"
    monkeypatch.setattr("cd_monitor.web_server.get_opportunity", lambda db_path, oid: _Opp())

    server, thread, base_url = _start_server(db_path, None)
    try:
        result = _post_json(
            f"{base_url}/api/scan/live-html",
            {"catalog_no": "SRCL-3520", "notify": True, "notify_channel": "feishu", "notify_dry_run": True},
        )
        assert result.get("status") == "ok"
        assert "notifications" in result
        assert len(result["notifications"]) == 1
        assert result["notifications"][0]["channel"] == "feishu"
        assert result["notifications"][0]["opportunity_id"] == 42
    finally:
        server.shutdown()
        server.server_close()


def test_scan_live_html_threads_profile_dir_from_config(tmp_path, monkeypatch, live_html_stub):
    from cd_monitor.config import BrowserConfig, ProjectConfig

    db_path = tmp_path / "api.db"
    init_db(db_path)
    cfg = ProjectConfig()
    cfg.browser = BrowserConfig(wameiji_profile_dir="C:/test/profile", xianyu_state_file="")
    monkeypatch.setattr("cd_monitor.web_server.load_config", lambda _x: cfg)

    stub, calls = live_html_stub(
        {
            "catalog_no": "SRCL-3520",
            "status": "human_required",
            "opportunity_count": 0,
            "opportunity_ids": [],
            "wameiji_capture": {"status": "human_required"},
            "xianyu_capture": None,
        }
    )
    monkeypatch.setattr("cd_monitor.web_server.capture_and_evaluate_live_html", stub)

    server, thread, base_url = _start_server(db_path, None)
    try:
        _post_json(f"{base_url}/api/scan/live-html", {"catalog_no": "SRCL-3520"})
        assert len(calls) == 1
        assert calls[0]["kwargs"]["profile_dir"] == "C:/test/profile"
    finally:
        server.shutdown()
        server.server_close()


def test_scan_live_html_returns_500_when_capture_raises(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    init_db(db_path)

    async def boom(*args, **kwargs):
        raise RuntimeError("playwright not installed")

    monkeypatch.setattr("cd_monitor.web_server.capture_and_evaluate_live_html", boom)

    server, thread, base_url = _start_server(db_path, None)
    try:
        result = _post_json(f"{base_url}/api/scan/live-html", {"catalog_no": "SRCL-3520"})
        assert result.get("_status") == 500
        assert "live_capture_failed" in result.get("_body", "")
    finally:
        server.shutdown()
        server.server_close()


# === /api/scan/live-watchlist =====================================
def test_scan_live_watchlist_returns_zero_when_empty(tmp_path, monkeypatch, live_html_stub):
    db_path = tmp_path / "api.db"
    init_db(db_path)
    stub, _ = live_html_stub({"status": "ok", "opportunity_count": 0, "opportunity_ids": []})
    monkeypatch.setattr("cd_monitor.web_server.capture_and_evaluate_live_html", stub)

    server, thread, base_url = _start_server(db_path, None)
    try:
        result = _post_json(f"{base_url}/api/scan/live-watchlist", {})
        assert result["scanned_count"] == 0
        assert result["ok_count"] == 0
        assert result["blocked_count"] == 0
        assert result["opportunity_count"] == 0
        assert result["notifications"] == []
    finally:
        server.shutdown()
        server.server_close()


def test_scan_live_watchlist_iterates_each_watch(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    init_db(db_path)
    add_watch(db_path, WatchItem(catalog_no="SRCL-3520"))
    add_watch(db_path, WatchItem(catalog_no="SRCL-9999"))

    call_count = {"n": 0}

    async def fake_capture(catalog_no, *args, **kwargs):
        call_count["n"] += 1
        if catalog_no == "SRCL-3520":
            return {
                "catalog_no": catalog_no,
                "status": "ok",
                "opportunity_count": 1,
                "opportunity_ids": [],
                "wameiji_capture": {"status": "ok"},
                "xianyu_capture": {"status": "ok"},
            }
        return {
            "catalog_no": catalog_no,
            "status": "human_required",
            "opportunity_count": 0,
            "opportunity_ids": [],
            "wameiji_capture": {"status": "human_required", "error_type": "security_check"},
            "xianyu_capture": None,
        }

    monkeypatch.setattr("cd_monitor.web_server.capture_and_evaluate_live_html", fake_capture)

    server, thread, base_url = _start_server(db_path, None)
    try:
        result = _post_json(f"{base_url}/api/scan/live-watchlist", {})
        assert result["scanned_count"] == 2
        assert result["ok_count"] == 1
        assert result["blocked_count"] == 1
        assert call_count["n"] == 2
        blocked_catalogs = {b["catalog_no"] for b in result["blocked"]}
        assert "SRCL-9999" in blocked_catalogs
    finally:
        server.shutdown()
        server.server_close()


def test_scan_live_watchlist_handles_exception_per_watch(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    init_db(db_path)
    add_watch(db_path, WatchItem(catalog_no="SRCL-A"))
    add_watch(db_path, WatchItem(catalog_no="SRCL-B"))

    async def fake_capture(catalog_no, *args, **kwargs):
        if catalog_no == "SRCL-A":
            raise RuntimeError("boom")
        return {
            "catalog_no": catalog_no,
            "status": "ok",
            "opportunity_count": 0,
            "opportunity_ids": [],
            "wameiji_capture": {"status": "ok"},
            "xianyu_capture": {"status": "ok"},
        }

    monkeypatch.setattr("cd_monitor.web_server.capture_and_evaluate_live_html", fake_capture)

    server, thread, base_url = _start_server(db_path, None)
    try:
        result = _post_json(f"{base_url}/api/scan/live-watchlist", {})
        assert result["scanned_count"] == 2
        assert result["ok_count"] == 1
        assert result["blocked_count"] == 1
        err_block = next(b for b in result["blocked"] if b["catalog_no"] == "SRCL-A")
        assert err_block["status"] == "error"
        assert "boom" in err_block["error"]
    finally:
        server.shutdown()
        server.server_close()


def test_scan_live_watchlist_dispatches_notifications(tmp_path, monkeypatch, notify_stub):
    db_path = tmp_path / "api.db"
    init_db(db_path)
    add_watch(db_path, WatchItem(catalog_no="SRCL-3520"))

    async def fake_capture(catalog_no, *args, **kwargs):
        return {
            "catalog_no": catalog_no,
            "status": "ok",
            "opportunity_count": 1,
            "opportunity_ids": [42],
            "wameiji_capture": {"status": "ok"},
            "xianyu_capture": {"status": "ok"},
        }

    monkeypatch.setattr("cd_monitor.web_server.capture_and_evaluate_live_html", fake_capture)

    class _Opp:
        catalog_no = "SRCL-3520"
        decision = "strong_alert"
    monkeypatch.setattr("cd_monitor.web_server.get_opportunity", lambda db_path, oid: _Opp())

    server, thread, base_url = _start_server(db_path, None)
    try:
        result = _post_json(
            f"{base_url}/api/scan/live-watchlist",
            {"notify": True, "notify_channel": "dingtalk", "notify_dry_run": True},
        )
        assert result["notifications"][0]["opportunity_id"] == 42
        assert result["notifications"][0]["channel"] == "dingtalk"
        assert len(notify_stub) == 1
        assert notify_stub[0]["channel"] == "dingtalk"
        assert notify_stub[0]["dry_run"] is True
    finally:
        server.shutdown()
        server.server_close()
