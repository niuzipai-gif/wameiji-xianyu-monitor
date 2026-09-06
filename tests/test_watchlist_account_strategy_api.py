"""Regression tests for /api/watchlist accepting/validating
account_strategy + account_state_file (P5.3 task-level binding).

Previously the POST handler built WatchItem(...) without forwarding the two
new fields, so a `fixed` strategy without a file silently succeeded and
update_watch was driven by a dict that dropped the new fields too. Both
paths are now covered.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

from cd_monitor.storage.sqlite import init_db
from cd_monitor.web_server import create_server


def _get_json(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_json(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def _start_server(db_path):
    init_db(db_path)
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://{server.server_address[0]}:{server.server_address[1]}"
    return server, base_url


def test_post_watchlist_with_fixed_strategy_requires_state_file(tmp_path) -> None:
    db_path = tmp_path / "p5_3_fixed_requires_file.db"
    server, base_url = _start_server(db_path)
    try:
        code, body = _post_json(
            f"{base_url}/api/watchlist",
            {                "decision_mode": "keyword",
                "required_keywords": ["__p54_seed__"],
                "catalog_no": "FIXED-NOFILE", "account_strategy": "fixed"},
        )
        assert code == 400, body
        assert "固定账号" in body["error"]
        # Nothing should have been written.
        listing = _get_json(f"{base_url}/api/watchlist/all")["items"]
        assert all(item["catalog_no"] != "FIXED-NOFILE" for item in listing)
    finally:
        server.shutdown()
        server.server_close()


def test_post_watchlist_persists_account_strategy_and_state_file(tmp_path) -> None:
    db_path = tmp_path / "p5_3_persist.db"
    server, base_url = _start_server(db_path)
    try:
        code, body = _post_json(
            f"{base_url}/api/watchlist",
            {
                "decision_mode": "keyword",
                "required_keywords": ["__p54_seed__"],
                "catalog_no": "FIXED-WITH-FILE",
                "account_strategy": "fixed",
                "account_state_file": "xianyu/main.json",
            },
        )
        assert code == 200, body
        assert body["catalog_no"] == "FIXED-WITH-FILE"
        watch_id = body["id"]

        listing = _get_json(f"{base_url}/api/watchlist/all")["items"]
        row = next(item for item in listing if item["catalog_no"] == "FIXED-WITH-FILE")
        assert row["account_strategy"] == "fixed"
        assert row["account_state_file"] == "xianyu/main.json"
        assert watch_id > 0
    finally:
        server.shutdown()
        server.server_close()


def test_post_watchlist_auto_strategy_defaults_to_auto(tmp_path) -> None:
    db_path = tmp_path / "p5_3_auto_default.db"
    server, base_url = _start_server(db_path)
    try:
        code, body = _post_json(
            f"{base_url}/api/watchlist",
            {                "decision_mode": "keyword",
                "required_keywords": ["__p54_seed__"],
                "catalog_no": "AUTO-DEFAULT"},
        )
        assert code == 200, body
        listing = _get_json(f"{base_url}/api/watchlist/all")["items"]
        row = next(item for item in listing if item["catalog_no"] == "AUTO-DEFAULT")
        assert row["account_strategy"] == "auto"
        assert row["account_state_file"] in (None, "")
    finally:
        server.shutdown()
        server.server_close()


def test_post_watchlist_normalises_unknown_strategy_with_file(tmp_path) -> None:
    db_path = tmp_path / "p5_3_normalise.db"
    server, base_url = _start_server(db_path)
    try:
        code, body = _post_json(
            f"{base_url}/api/watchlist",
            {
                "decision_mode": "keyword",
                "required_keywords": ["__p54_seed__"],
                "catalog_no": "WEIRD-STRATEGY",
                "account_strategy": "WeirdStrategy",
                "account_state_file": "xianyu/weird.json",
            },
        )
        assert code == 200, body
        listing = _get_json(f"{base_url}/api/watchlist/all")["items"]
        row = next(item for item in listing if item["catalog_no"] == "WEIRD-STRATEGY")
        # Unknown strategy + non-empty file collapses to "fixed".
        assert row["account_strategy"] == "fixed"
        assert row["account_state_file"] == "xianyu/weird.json"
    finally:
        server.shutdown()
        server.server_close()


def test_put_watchlist_rejects_fixed_without_file(tmp_path) -> None:
    db_path = tmp_path / "p5_3_put_fixed_no_file.db"
    server, base_url = _start_server(db_path)
    try:
        code, body = _post_json(
            f"{base_url}/api/watchlist",
            {                "decision_mode": "keyword",
                "required_keywords": ["__p54_seed__"],
                "catalog_no": "PUT-CASE", "account_strategy": "auto"},
        )
        assert code == 200, body
        watch_id = body["id"]

        code, body = _post_json(
            f"{base_url}/api/watchlist/{watch_id}",
            {"account_strategy": "fixed"},  # no file supplied
        )
        assert code == 400, body
        assert "固定账号" in body["error"]

        # The DB row must NOT have switched to fixed with a NULL file.
        listing = _get_json(f"{base_url}/api/watchlist/all")["items"]
        row = next(item for item in listing if item["id"] == watch_id)
        assert row["account_strategy"] != "fixed"
    finally:
        server.shutdown()
        server.server_close()


def test_put_watchlist_persists_account_strategy_change(tmp_path) -> None:
    db_path = tmp_path / "p5_3_put_change.db"
    server, base_url = _start_server(db_path)
    try:
        code, body = _post_json(
            f"{base_url}/api/watchlist",
            {                "decision_mode": "keyword",
                "required_keywords": ["__p54_seed__"],
                "catalog_no": "PUT-CHANGE"},
        )
        assert code == 200, body
        watch_id = body["id"]

        code, body = _post_json(
            f"{base_url}/api/watchlist/{watch_id}",
            {
                "account_strategy": "rotate",
                "account_state_file": "xianyu/pool.json",
            },
        )
        assert code == 200, body
        assert body["updated"] is True

        listing = _get_json(f"{base_url}/api/watchlist/all")["items"]
        row = next(item for item in listing if item["id"] == watch_id)
        assert row["account_strategy"] == "rotate"
        assert row["account_state_file"] == "xianyu/pool.json"
    finally:
        server.shutdown()
        server.server_close()


def test_get_watchlist_includes_account_fields(tmp_path) -> None:
    db_path = tmp_path / "p5_3_get_includes.db"
    server, base_url = _start_server(db_path)
    try:
        _post_json(
            f"{base_url}/api/watchlist",
            {
                "decision_mode": "keyword",
                "required_keywords": ["__p54_seed__"],
                "catalog_no": "GET-CHECK",
                "account_strategy": "fixed",
                "account_state_file": "xianyu/alpha.json",
            },
        )
        # /api/watchlist (enabled-only) and /api/watchlist/all must both
        # include the P5.3 fields.
        enabled = _get_json(f"{base_url}/api/watchlist")["items"]
        all_rows = _get_json(f"{base_url}/api/watchlist/all")["items"]
        for label, items in (("enabled", enabled), ("all", all_rows)):
            row = next((it for it in items if it["catalog_no"] == "GET-CHECK"), None)
            assert row is not None, label
            assert row["account_strategy"] == "fixed", label
            assert row["account_state_file"] == "xianyu/alpha.json", label
    finally:
        server.shutdown()
        server.server_close()
