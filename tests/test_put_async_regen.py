"""Tests for P0 #7: POST /api/watchlist/{id} no longer blocks on AI regen.

This server does not implement do_PUT; watch updates are POSTs (the
route /api/watchlist/{id} is dispatched by _handle_post). The legacy
inline asyncio.run path was a UX hazard: the request blocked on the
LLM for up to 20s. The fix routes the regen through
WatchActionService.submit_regenerate_criteria so the POST returns
immediately with a job_id.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from cd_monitor.core.models import WatchItem
from cd_monitor.services.watch_action_service import WatchActionService
from cd_monitor.storage.sqlite import add_watch, init_db
from cd_monitor.web_server import _build_handler, _get_watch_action_service


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    init_db(str(db))
    return db


def _seed_watch(
    db_path: Path,
    *,
    catalog_no: str = "POST-1",
    description: str = "old description",
    decision_mode: str = "ai",
    enabled: bool = True,
    reference_text: str = "# reference",
) -> int:
    ref_dir = db_path.parent / "prompts"
    ref_dir.mkdir(parents=True, exist_ok=True)
    (ref_dir / "macbook_criteria.txt").write_text(reference_text, encoding="utf-8")
    watch = WatchItem(
        catalog_no=catalog_no,
        description=description,
        decision_mode=decision_mode,
        enabled=enabled,
        required_keywords=[],
        excluded_keywords=[],
    )
    return add_watch(str(db_path), watch)


def _http_post(base, path, payload=None):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
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
    wid = _seed_watch(db, catalog_no="POST-1", description="old description")
    if hasattr(_get_watch_action_service, "_cache"):
        delattr(_get_watch_action_service, "_cache")
    handler = _build_handler(db_path=db, static_dir=tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, db, wid
    server.shutdown()
    server.server_close()
    if hasattr(_get_watch_action_service, "_cache"):
        delattr(_get_watch_action_service, "_cache")


def test_post_with_changed_description_queues_criteria_job(http_server, monkeypatch) -> None:
    """POST /api/watchlist/{id} with a new description should queue (not block) regen."""
    server, db, wid = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    monkeypatch.chdir(db.parent)
    async def _fake_criteria(user_description, reference_file_path):
        return f"# criteria: {user_description}"
    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _fake_criteria)

    t0 = time.monotonic()
    code, body = _http_post(
        base, f"/api/watchlist/{wid}",
        payload={"description": "new description for POST test"},
    )
    elapsed = time.monotonic() - t0
    assert code == 200, body
    assert body.get("updated") is True
    # The POST should return promptly (< 1s), not wait for the LLM.
    assert elapsed < 1.0, f"POST took {elapsed:.2f}s, expected non-blocking"
    criteria = body.get("criteria", {})
    assert criteria.get("queued") is True
    assert criteria.get("job_id")
    assert criteria.get("error") is None

    # The job should eventually complete via the registered service
    job_id = criteria["job_id"]
    svc = _get_watch_action_service(str(db))
    deadline = time.monotonic() + 4
    final = None
    while time.monotonic() < deadline:
        j = svc.get_job(job_id)
        if j and j.status in {"completed", "failed"}:
            final = j
            break
        time.sleep(0.1)
    assert final is not None and final.status == "completed", getattr(final, "error", "no job")


def test_post_unchanged_description_skips_criteria_queue(http_server) -> None:
    """When description is unchanged, criteria should NOT be queued."""
    server, db, wid = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"

    code, body = _http_post(
        base, f"/api/watchlist/{wid}",
        payload={"description": "old description"},  # same as seeded
    )
    assert code == 200, body
    assert body.get("updated") is True
    criteria = body.get("criteria", {})
    assert criteria.get("queued") in (None, False)
    assert criteria.get("job_id") is None


def test_post_keyword_mode_skips_criteria_queue(http_server) -> None:
    """Keyword-mode watches do not need AI criteria; nothing queued."""
    server, db, wid = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE watchlist SET decision_mode = ? WHERE id = ?", ("keyword", wid),
        )
        conn.commit()
    finally:
        conn.close()

    code, body = _http_post(
        base, f"/api/watchlist/{wid}",
        payload={"description": "anything goes here"},
    )
    assert code == 200, body
    criteria = body.get("criteria", {})
    assert criteria.get("queued") in (None, False)
    assert criteria.get("job_id") is None
