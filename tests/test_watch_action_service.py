"""Tests for Round-3 P0 #3 + #9 manual watchlist actions.

Covers WatchActionService:
  * submit_run completes for enabled watches (real TaskRunner)
  * submit_run on disabled watch captures status=failed
  * submit_regenerate_criteria happy path writes file + updates DB
  * submit_regenerate_criteria rejects keyword/non-ai mode
  * submit_regenerate_criteria rejects empty description
  * submit_regenerate_criteria captures AI failure gracefully
  * cancel_job flips a running job to cancelled
  * HTTP smoke: do_POST hits the new endpoints
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
from cd_monitor.services.watch_action_service import (
    ACTION_REGENERATE_CRITERIA,
    ACTION_RUN,
    WatchActionService,
    WatchActionJob,
)
from cd_monitor.storage.sqlite import add_watch, init_db
from cd_monitor.web_server import _build_handler, _get_watch_action_service


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "monitor.db"
    init_db(str(db))
    return db


def _seed_watch(
    db_path: Path,
    *,
    catalog_no: str = "ACT-1",
    description: str = "old description",
    decision_mode: str = "ai",
    enabled: bool = True,
    reference_text: str | None = "# reference",
) -> int:
    if reference_text is not None:
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


def _wait_for_status(svc, job_id, targets, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = svc.get_job(job_id)
        if job and job.status in targets:
            return job
        time.sleep(0.05)
    return svc.get_job(job_id)


def test_run_action_completes_for_enabled_watch(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = _seed_watch(db, catalog_no="RUN-1", description="d")
    svc = WatchActionService(db_path=str(db))
    job = svc.submit_run(wid)
    assert job.action == ACTION_RUN
    terminal = _wait_for_status(svc, job.job_id, {"completed", "failed"})
    assert terminal is not None
    assert terminal.status == "completed"
    assert terminal.result.get("ok") is True
    assert terminal.result.get("task_id") == wid


def test_run_action_marks_disabled_watch_as_failed(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = _seed_watch(db, catalog_no="RUN-2", description="d")
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE watchlist SET enabled = 0 WHERE id = ?", (wid,))
        conn.commit()
    finally:
        conn.close()
    svc = WatchActionService(db_path=str(db))
    job = svc.submit_run(wid)
    terminal = _wait_for_status(svc, job.job_id, {"completed", "failed"})
    assert terminal is not None
    assert terminal.status == "failed"
    assert terminal.error == "task_disabled"


def test_regenerate_criteria_happy_path_writes_file(tmp_path: Path, monkeypatch) -> None:
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)
    wid = _seed_watch(db, catalog_no="REG-OK", description="search for anime cds")
    async def _fake_criteria(user_description, reference_file_path):
        return f"# criteria: {user_description} (ref {reference_file_path})"
    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _fake_criteria)
    svc = WatchActionService(db_path=str(db), prompts_dir="prompts")
    job = svc.submit_regenerate_criteria(wid)
    terminal = _wait_for_status(svc, job.job_id, {"completed", "failed"}, timeout=4.0)
    assert terminal is not None
    assert terminal.status == "completed", terminal.error
    crit_path = Path(terminal.result["criteria_file"])
    assert crit_path.exists()
    body = crit_path.read_text(encoding="utf-8")
    assert "search for anime cds" in body
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT ai_prompt_criteria_file FROM watchlist WHERE id = ?",
            (wid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0]
    assert "reg-ok" in row[0]


def test_regenerate_criteria_skips_keyword_mode(tmp_path: Path, monkeypatch) -> None:
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)
    wid = _seed_watch(db, catalog_no="REG-KW", description="d", decision_mode="keyword")
    called = {"count": 0}
    async def _should_not_run(*_args, **_kwargs):
        called["count"] += 1
        return "x"
    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _should_not_run)
    svc = WatchActionService(db_path=str(db))
    job = svc.submit_regenerate_criteria(wid)
    terminal = _wait_for_status(svc, job.job_id, {"completed", "failed"})
    assert terminal.status == "failed"
    assert terminal.error == "decision_mode_not_ai"
    assert called["count"] == 0


def test_regenerate_criteria_skips_empty_description(tmp_path: Path, monkeypatch) -> None:
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)
    wid = _seed_watch(db, catalog_no="REG-EMPTY", description="")
    svc = WatchActionService(db_path=str(db))
    job = svc.submit_regenerate_criteria(wid)
    terminal = _wait_for_status(svc, job.job_id, {"completed", "failed"})
    assert terminal.status == "failed"
    assert terminal.error == "description_required"


def test_regenerate_criteria_surfaces_ai_failure(tmp_path: Path, monkeypatch) -> None:
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)
    wid = _seed_watch(db, catalog_no="REG-FAIL", description="d")
    async def _broken(*_args, **_kwargs):
        raise RuntimeError("upstream 503")
    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _broken)
    svc = WatchActionService(db_path=str(db))
    job = svc.submit_regenerate_criteria(wid)
    terminal = _wait_for_status(svc, job.job_id, {"completed", "failed"}, timeout=4.0)
    assert terminal.status == "failed"
    assert "ai_generate_failed" in terminal.error


def test_cancel_job_flips_status(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    wid = _seed_watch(db, catalog_no="CANCEL-1", description="d")
    release = threading.Event()
    class _SlowRunner:
        def __init__(self, db_path):
            self._db = db_path
        async def run_now(self, task_id):
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if release.is_set():
                    break
                time.sleep(0.05)
            return {"ok": True, "task_id": task_id, "task_name": "x",
                    "started_at": "now", "snapshot_id": 0}
    svc = WatchActionService(
        db_path=str(db),
        runner_factory=lambda p: _SlowRunner(p),
    )
    job = svc.submit_run(wid)
    time.sleep(0.2)
    assert svc.cancel_job(job.job_id) is True
    release.set()
    terminal = _wait_for_status(svc, job.job_id, {"cancelled", "completed"})
    assert terminal is not None
    assert terminal.status == "cancelled"


@pytest.fixture
def http_server(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    monkeypatch.chdir(tmp_path)
    wid = _seed_watch(db, catalog_no="HTTP-1", description="d")
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


def _http_post(base, path, payload=None, timeout=5.0):
    data = json.dumps(payload or {}).encode("utf-8") if payload else b"{}"
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return exc.code, body


def _http_get(base, path, timeout=5.0):
    req = urllib.request.Request(f"{base}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return exc.code, body


def test_http_start_returns_202_with_job(http_server) -> None:
    server, db, wid = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_post(base, f"/api/watchlist/{wid}/start")
    assert code == 202, body
    assert body.get("accepted") is True
    assert body.get("action") == "run"
    job_id = body.get("job", {}).get("job_id")
    assert job_id
    code, body = _http_get(base, f"/api/watchlist/jobs/{job_id}")
    assert code == 200, body
    assert body.get("action") == "run"
    assert body.get("watch_id") == wid


def test_http_regenerate_criteria_returns_202(http_server, monkeypatch) -> None:
    server, db, wid = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    monkeypatch.chdir(db.parent)
    async def _fake_criteria(user_description, reference_file_path):
        return f"# criteria: {user_description}"
    import cd_monitor.services.scraper._prompt_utils as pu
    monkeypatch.setattr(pu, "generate_criteria", _fake_criteria)
    code, body = _http_post(base, f"/api/watchlist/{wid}/regenerate-criteria")
    assert code == 202, body
    job_id = body["job"]["job_id"]
    deadline = time.monotonic() + 4.0
    while time.monotonic() < deadline:
        c, b = _http_get(base, f"/api/watchlist/jobs/{job_id}")
        if b.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.1)
    assert b.get("status") == "completed", b
    assert b.get("result", {}).get("criteria_file")


def test_http_regenerate_rejects_keyword_mode(http_server) -> None:
    server, db, wid = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "UPDATE watchlist SET decision_mode = ? WHERE id = ?",
            ("keyword", wid),
        )
        conn.commit()
    finally:
        conn.close()
    code, body = _http_post(base, f"/api/watchlist/{wid}/regenerate-criteria")
    assert code == 400, body
    assert body.get("error") == "decision_mode_not_ai"


def test_http_stop_returns_list(http_server) -> None:
    server, db, wid = http_server
    base = f"http://{server.server_address[0]}:{server.server_address[1]}"
    code, body = _http_post(base, f"/api/watchlist/{wid}/stop")
    assert code == 200, body
    assert body.get("stopped") is True
    assert "cancelled_jobs" in body
