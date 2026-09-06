"""P5.4 contract alignment with Usagi ai-goofish-monitor.

Ports the second half of Usagi TaskGenerateRequest.validate_decision_mode_payload
(src/domain/models/task.py:350-358) that the prior P5.4 round skipped:

  - decision_mode == "ai"     requires a non-empty description.
  - decision_mode == "keyword" requires at least one required_keywords entry.

Plus the Usagi TaskUpdate.validate_partial_keyword_payload partial-update
semantics so PUTs only re-check the invariant for the field actually
touched (rather than always merging + validating everything).

Also covers the storage migration idempotency and round-trip persistence
for the four P5.4 columns (decision_mode, description, ai_prompt_base_file,
ai_prompt_criteria_file).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request


def _post(url, payload):
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


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _start_server(tmp_path, filename="p54.db"):
    from cd_monitor.storage.sqlite import init_db
    from cd_monitor.web_server import create_server
    db_path = tmp_path / filename
    init_db(db_path)
    server = create_server("127.0.0.1", 0, db_path, static_dir="web")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    return server, db_path, base


# ---------------------------------------------------------------------------
# Storage migration + round-trip
# ---------------------------------------------------------------------------


def test_migration_idempotent_double_init(tmp_path) -> None:
    """Running init_db twice must not duplicate any column."""
    from cd_monitor.storage.sqlite import init_db
    db_path = tmp_path / "idem.db"
    init_db(db_path)
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()]
    for col in ("decision_mode", "description",
                "ai_prompt_base_file", "ai_prompt_criteria_file"):
        assert cols.count(col) == 1, f"column {col} duplicated"


def test_storage_round_trip_all_four_columns(tmp_path) -> None:
    """Storage layer preserves all four P5.4 columns."""
    from cd_monitor.storage.sqlite import (
        init_db, add_watch, list_watch_all,
    )
    from cd_monitor.core.models import WatchItem
    db_path = tmp_path / "round.db"
    init_db(db_path)
    add_watch(
        db_path,
        WatchItem(
            catalog_no="P54-RT",
            decision_mode="ai",
            description="Xun Zhao chu ji xian ding pan; bu yao zu lin ban",
            ai_prompt_base_file="prompts/anime_base.txt",
            ai_prompt_criteria_file="prompts/anime_criteria.txt",
        ),
    )
    items = list_watch_all(db_path)
    assert len(items) == 1
    w = items[0]
    assert w.decision_mode == "ai"
    assert w.description.startswith("Xun Zhao")
    assert w.ai_prompt_base_file == "prompts/anime_base.txt"
    assert w.ai_prompt_criteria_file == "prompts/anime_criteria.txt"


# ---------------------------------------------------------------------------
# POST /api/watchlist invariants
# ---------------------------------------------------------------------------


def test_post_ai_without_description_rejected(tmp_path) -> None:
    server, _, base = _start_server(tmp_path)
    try:
        code, body = _post(
            f"{base}/api/watchlist",
            {"catalog_no": "P54-A-NODESC", "decision_mode": "ai"},
        )
        assert code == 400, body
        assert "AI 判断模式下" in body["error"], body
    finally:
        server.shutdown(); server.server_close()


def test_post_keyword_without_required_keywords_rejected(tmp_path) -> None:
    server, _, base = _start_server(tmp_path)
    try:
        code, body = _post(
            f"{base}/api/watchlist",
            {"catalog_no": "P54-K-NOKW", "decision_mode": "keyword"},
        )
        assert code == 400, body
        assert "关键词判断模式下" in body["error"], body
    finally:
        server.shutdown(); server.server_close()


def test_post_keyword_with_required_keywords_ok(tmp_path) -> None:
    server, _, base = _start_server(tmp_path)
    try:
        code, body = _post(
            f"{base}/api/watchlist",
            {
                "catalog_no": "P54-K-OK",
                "decision_mode": "keyword",
                "required_keywords": ["chun ji xian ding"],
            },
        )
        assert code == 200, body
        assert body["catalog_no"] == "P54-K-OK"
    finally:
        server.shutdown(); server.server_close()


def test_post_ai_with_description_and_prompt_files_ok(tmp_path) -> None:
    server, _, base = _start_server(tmp_path)
    try:
        code, body = _post(
            f"{base}/api/watchlist",
            {
                "catalog_no": "P54-A-OK",
                "decision_mode": "ai",
                "description": "Xun Zhao chu ji xian ding pan",
                "ai_prompt_base_file": "prompts/anime_base.txt",
                "ai_prompt_criteria_file": "prompts/anime_criteria.txt",
            },
        )
        assert code == 200, body
        wid = body["id"]
        listed = _get(f"{base}/api/watchlist")
        item = next(i for i in listed["items"] if i["id"] == wid)
        assert item["decision_mode"] == "ai"
        assert item["description"].startswith("Xun Zhao")
        assert item["ai_prompt_base_file"] == "prompts/anime_base.txt"
        assert item["ai_prompt_criteria_file"] == "prompts/anime_criteria.txt"
    finally:
        server.shutdown(); server.server_close()


# ---------------------------------------------------------------------------
# PUT /api/watchlist/{id} — switch-mode + partial-update semantics
# ---------------------------------------------------------------------------


def test_put_switch_to_ai_against_empty_existing_description_rejected(tmp_path) -> None:
    """PUT {decision_mode: ai} against a watch that never had a description must 400."""
    server, _, base = _start_server(tmp_path)
    try:
        code, body = _post(
            f"{base}/api/watchlist",
            {
                "catalog_no": "P54-SWITCH-AI",
                "decision_mode": "keyword",
                "required_keywords": ["foo"],
            },
        )
        assert code == 200, body
        wid = body["id"]
        code, body = _post(
            f"{base}/api/watchlist/{wid}",
            {"decision_mode": "ai"},
        )
        assert code == 400, body
        assert "AI 判断模式下" in body["error"]
    finally:
        server.shutdown(); server.server_close()


def test_put_partial_description_empty_on_ai_watch_rejected(tmp_path) -> None:
    """PUT {description: ""} on an ai watch must 400 (clearing is not allowed)."""
    server, _, base = _start_server(tmp_path)
    try:
        code, body = _post(
            f"{base}/api/watchlist",
            {
                "catalog_no": "P54-CLEAR-DESC",
                "decision_mode": "ai",
                "description": "have desc",
            },
        )
        assert code == 200, body
        wid = body["id"]
        code, body = _post(
            f"{base}/api/watchlist/{wid}",
            {"description": ""},
        )
        assert code == 400, body
        assert "AI 判断模式下" in body["error"]
    finally:
        server.shutdown(); server.server_close()


def test_put_partial_keywords_empty_on_keyword_watch_rejected(tmp_path) -> None:
    """PUT {required_keywords: []} on a keyword watch must 400."""
    server, _, base = _start_server(tmp_path)
    try:
        code, body = _post(
            f"{base}/api/watchlist",
            {
                "catalog_no": "P54-CLEAR-KW",
                "decision_mode": "keyword",
                "required_keywords": ["foo"],
            },
        )
        assert code == 200, body
        wid = body["id"]
        code, body = _post(
            f"{base}/api/watchlist/{wid}",
            {"required_keywords": []},
        )
        assert code == 400, body
        assert "关键词判断模式下" in body["error"]
    finally:
        server.shutdown(); server.server_close()


def test_put_switch_keyword_to_ai_with_description_ok(tmp_path) -> None:
    """PUT {decision_mode: ai, description: ...} on a keyword watch must succeed."""
    server, _, base = _start_server(tmp_path)
    try:
        code, body = _post(
            f"{base}/api/watchlist",
            {
                "catalog_no": "P54-SWITCH-OK",
                "decision_mode": "keyword",
                "required_keywords": ["foo"],
            },
        )
        assert code == 200, body
        wid = body["id"]
        code, body = _post(
            f"{base}/api/watchlist/{wid}",
            {"decision_mode": "ai", "description": "now in ai mode"},
        )
        assert code == 200, body
        assert body["updated"] is True
    finally:
        server.shutdown(); server.server_close()
