"""Unit tests for the Wameiji login state service (extension snapshot converter)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cd_monitor.services.wameiji_login_state import (
    DEFAULT_OUTPUT,
    STATE_FILE_FORMAT,
    WameijiLoginStateError,
    extension_snapshot_to_playwright_state,
    inspect_wameiji_login_state,
    load_wameiji_login_state,
    parse_extension_snapshot,
    save_wameiji_login_state,
)


def _sample_snapshot() -> dict:
    return {
        "capturedAt": "2026-06-25T00:00:00Z",
        "pageUrl": "https://meruki.cn/dashboard",
        "page": {"pageUrl": "https://meruki.cn/dashboard", "referrer": None, "visibilityState": "visible"},
        "env": {"navigator": {"userAgent": "Mozilla/5.0"}},
        "storage": {
            "local": {"token": "SECRET-LOCAL", "theme": "dark"},
            "session": {"csrf": "CSRF-1"},
        },
        "meta": {"droppedStorageKeys": {"local": [], "session": []}},
        "headers": {"user-agent": "Mozilla/5.0"},
        "cookies": [
            {
                "name": "session",
                "value": "SECRET-COOKIE",
                "domain": ".meruki.cn",
                "path": "/",
                "expires": 9999999999,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            },
            {
                "name": "tracking",
                "value": "ABC",
                "domain": ".meruki.cn",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": False,
                "sameSite": "None",
            },
        ],
    }


def test_extension_snapshot_to_playwright_state_cookies_and_origins() -> None:
    snapshot = _sample_snapshot()
    state = extension_snapshot_to_playwright_state(snapshot)
    assert len(state["cookies"]) == 2
    first = state["cookies"][0]
    assert first["name"] == "session"
    assert first["value"] == "SECRET-COOKIE"
    assert first["domain"] == ".meruki.cn"
    assert first["httpOnly"] is True
    assert first["expires"] == 9999999999  # converted to int
    second = state["cookies"][1]
    assert second["expires"] == -1  # session cookie
    assert len(state["origins"]) >= 1
    origin = state["origins"][0]
    assert origin["origin"] == "https://meruki.cn"
    local = origin.get("localStorage") or []
    assert {"name": "token", "value": "SECRET-LOCAL"} in local
    assert {"name": "theme", "value": "dark"} in local
    session = origin.get("sessionStorage") or []
    assert {"name": "csrf", "value": "CSRF-1"} in session


def test_extension_snapshot_rejects_empty_cookies() -> None:
    with pytest.raises(WameijiLoginStateError, match="cookie"):
        extension_snapshot_to_playwright_state({"cookies": []})


def test_extension_snapshot_drops_invalid_cookies() -> None:
    snapshot = {
        "pageUrl": "https://meruki.cn/",
        "cookies": [
            {"name": "", "value": "x", "domain": ".meruki.cn", "path": "/"},  # empty name
            {"name": "ok", "value": "v", "domain": ".meruki.cn", "path": "/"},
            {"value": "missing-name", "domain": ".meruki.cn", "path": "/"},
            "not-a-dict",
        ],
    }
    state = extension_snapshot_to_playwright_state(snapshot)
    assert len(state["cookies"]) == 1
    assert state["cookies"][0]["name"] == "ok"


def test_parse_extension_snapshot_rejects_invalid_json() -> None:
    with pytest.raises(WameijiLoginStateError, match="not valid JSON"):
        parse_extension_snapshot("{not valid")


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    snapshot = _sample_snapshot()
    output = tmp_path / "wameiji_state.json"
    summary = save_wameiji_login_state(snapshot, output, keep_raw_snapshot=True)
    assert summary["output"] == str(output)
    assert summary["cookie_count"] == 2
    assert summary["origin_count"] >= 1
    assert summary["login_state_ready"] is True
    assert ".meruki.cn" in summary["wameiji_cookie_domains"]

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["format"] == STATE_FILE_FORMAT
    assert "playwright_storage_state" in data
    assert "raw_extension_snapshot" in data

    loaded = load_wameiji_login_state(output)
    assert loaded["cookies"][0]["name"] == "session"
    assert loaded["cookies"][0]["value"] == "SECRET-COOKIE"


def test_save_wameiji_login_state_accepts_json_text(tmp_path: Path) -> None:
    snapshot = _sample_snapshot()
    output = tmp_path / "wameiji_state.json"
    summary = save_wameiji_login_state(json.dumps(snapshot), output, keep_raw_snapshot=False)
    assert summary["cookie_count"] == 2
    data = json.loads(output.read_text(encoding="utf-8"))
    assert "raw_extension_snapshot" not in data


def test_load_wameiji_login_state_missing_file(tmp_path: Path) -> None:
    with pytest.raises(WameijiLoginStateError, match="does not exist"):
        load_wameiji_login_state(tmp_path / "nope.json")


def test_load_wameiji_login_state_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(WameijiLoginStateError, match="not valid JSON"):
        load_wameiji_login_state(bad)


def test_load_wameiji_login_state_missing_playwright_state(tmp_path: Path) -> None:
    p = tmp_path / "wrong.json"
    p.write_text(json.dumps({"format": "x", "cookies": []}), encoding="utf-8")
    with pytest.raises(WameijiLoginStateError, match="playwright_storage_state"):
        load_wameiji_login_state(p)


def test_inspect_wameiji_login_state_ready(tmp_path: Path) -> None:
    snapshot = _sample_snapshot()
    output = tmp_path / "wameiji_state.json"
    save_wameiji_login_state(snapshot, output)
    insp = inspect_wameiji_login_state(output)
    assert insp["status"] == "ready"
    assert ".meruki.cn" in insp["cookie_domains"]


def test_inspect_wameiji_login_state_missing(tmp_path: Path) -> None:
    insp = inspect_wameiji_login_state(tmp_path / "absent.json")
    assert insp["status"] == "missing"
    assert insp["error_type"] == "state_file_missing"


def test_inspect_wameiji_login_state_invalid_domain(tmp_path: Path) -> None:
    p = tmp_path / "wrong_domain.json"
    bad = {"format": STATE_FILE_FORMAT, "playwright_storage_state": {
        "cookies": [{"name": "x", "value": "y", "domain": ".example.com", "path": "/",
                     "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Lax"}],
        "origins": [],
    }}
    p.write_text(json.dumps(bad), encoding="utf-8")
    insp = inspect_wameiji_login_state(p)
    assert insp["status"] == "invalid"
    assert "meruki.cn" in insp["error_message"]


def test_inspect_wameiji_login_state_not_configured() -> None:
    insp = inspect_wameiji_login_state("")
    assert insp["status"] == "not_configured"


def test_default_output_path_constant() -> None:
    assert DEFAULT_OUTPUT == "data/wameiji_state.json"
