"""Tests for cd_monitor.web_p51.P51Router."""
from __future__ import annotations

import asyncio
import sqlite3
from http import HTTPStatus
from pathlib import Path

from cd_monitor.web_p51 import P51Router
from cd_monitor.services.scheduler_service import SchedulerService
from cd_monitor.services.price_history_service import PriceSnapshot
from cd_monitor.storage.sqlite import init_db


def _setup(tmp_path):
    db = tmp_path / "p51.db"
    init_db(db)
    router = P51Router(db)
    return db, router


def _seed(db, catalog_no, *, cron=None, enabled=1, paused_until=None, failure_count=0):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO watchlist (catalog_no, enabled, platform, priority, cron,"
            " failure_count, paused_until, last_status)"
            " VALUES (?, ?, ?, 1, ?, ?, ?, ?)",
            (catalog_no, enabled, "both", cron, failure_count, paused_until, "ok"),
        )
        return int(cur.lastrowid)


class _Recorder:
    """Captures (body, status) tuples passed to respond()."""
    def __init__(self):
        self.calls = []

    def __call__(self, body, status=None):
        self.calls.append((body, status))


def _run(coro):
    # Use asyncio.run for clean event-loop lifecycle (Python 3.13 safe).
    return asyncio.run(coro)


class TestGetRoutes:
    def test_get_paused_returns_items(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        a = _seed(db, "PA-A")
        b = _seed(db, "PA-B")
        for tid in (a, b):
            for _ in range(3):
                router.guard.record_failure(tid, str(tid), "x", "m")
        handled = router.handle_get("/api/tasks/paused", rec)
        assert handled is True
        assert len(rec.calls) == 1
        body, status = rec.calls[0]
        assert status is None
        catalogs = {row["catalog_no"] for row in body["items"]}
        assert catalogs == {"PA-A", "PA-B"}

    def test_get_price_history_lists_trends(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        router.history.record_snapshot(PriceSnapshot(
            catalog_no="PH-1", source="x", platform="wameiji", price=100.0, currency="JPY",
        ))
        handled = router.handle_get("/api/price-history", rec)
        assert handled is True
        body, _ = rec.calls[0]
        catalogs = {row["catalog_no"] for row in body["items"]}
        assert "PH-1" in catalogs

    def test_get_price_history_per_catalog(self, tmp_path):
        db, router = _setup(tmp_path)
        router.history.record_snapshot(PriceSnapshot(
            catalog_no="PH-2", source="x", platform="wameiji", price=50.0, currency="JPY",
        ))
        rec = _Recorder()
        handled = router.handle_get("/api/price-history/PH-2", rec)
        assert handled is True
        body, _ = rec.calls[0]
        assert body["trend"] is not None
        assert body["trend"]["catalog_no"] == "PH-2"
        assert len(body["recent"]) == 1

    def test_get_price_history_unknown_catalog_returns_empty(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        router.handle_get("/api/price-history/NOPE", rec)
        body, status = rec.calls[0]
        assert status is None or status == HTTPStatus.OK
        assert body["trend"] is None
        assert body["recent"] == []

    def test_get_failures_for_task(self, tmp_path):
        db, router = _setup(tmp_path)
        tid = _seed(db, "F-1")
        for i in range(2):
            router.guard.record_failure(tid, "F-1", "err" + str(i), "m")
        rec = _Recorder()
        handled = router.handle_get("/api/tasks/" + str(tid) + "/failures", rec)
        assert handled is True
        body, _ = rec.calls[0]
        assert len(body["items"]) == 2

    def test_get_failures_bad_id_returns_not_found(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        router.handle_get("/api/tasks/abc/failures", rec)
        body, status = rec.calls[0]
        assert status == HTTPStatus.NOT_FOUND

    def test_get_prompt_preview(self, tmp_path):
        db, router = _setup(tmp_path)
        tid = _seed(db, "P-1")
        rec = _Recorder()
        handled = router.handle_get("/api/tasks/" + str(tid) + "/prompt-preview", rec)
        assert handled is True
        body, _ = rec.calls[0]
        assert "text" in body
        assert "warnings" in body

    def test_get_prompt_preview_unknown_task_404(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        router.handle_get("/api/tasks/99999/prompt-preview", rec)
        body, status = rec.calls[0]
        assert status == HTTPStatus.NOT_FOUND

    def test_unhandled_route_returns_false(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        handled = router.handle_get("/api/other", rec)
        assert handled is False
        assert rec.calls == []


class TestPostRoutes:
    def test_reload_without_scheduler_returns_zero(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        handled = router.handle_post("/api/scheduler/reload", {}, rec)
        assert handled is True
        body, _ = rec.calls[0]
        assert body == {"loaded": 0}

    def test_reload_with_scheduler_loads_jobs(self, tmp_path):
        db, router = _setup(tmp_path)
        _seed(db, "S-1", cron="0 8 * * *")
        router.scheduler = SchedulerService(db)
        rec = _Recorder()
        router.handle_post("/api/scheduler/reload", {}, rec)
        body, _ = rec.calls[0]
        assert body["loaded"] == 1

    def test_set_cron_valid(self, tmp_path):
        db, router = _setup(tmp_path)
        tid = _seed(db, "CR-1")
        rec = _Recorder()
        router.handle_post("/api/tasks/" + str(tid) + "/schedule", {"cron": "@hourly"}, rec)
        body, _ = rec.calls[0]
        assert body["cron"] == "0 * * * *"

    def test_set_cron_clear(self, tmp_path):
        db, router = _setup(tmp_path)
        tid = _seed(db, "CR-2", cron="@daily")
        rec = _Recorder()
        router.handle_post("/api/tasks/" + str(tid) + "/schedule", {"cron": ""}, rec)
        body, _ = rec.calls[0]
        assert body["cron"] is None

    def test_set_cron_invalid_returns_400(self, tmp_path):
        db, router = _setup(tmp_path)
        tid = _seed(db, "CR-3")
        rec = _Recorder()
        router.handle_post("/api/tasks/" + str(tid) + "/schedule", {"cron": "totally-bad"}, rec)
        body, status = rec.calls[0]
        assert status == HTTPStatus.BAD_REQUEST
        assert body["error"] == "invalid_cron"

    def test_set_cron_bad_id_returns_404(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        router.handle_post("/api/tasks/abc/schedule", {"cron": "@daily"}, rec)
        body, status = rec.calls[0]
        assert status == HTTPStatus.NOT_FOUND

    def test_run_now_success(self, tmp_path):
        db, router = _setup(tmp_path)
        tid = _seed(db, "RN-1")
        rec = _Recorder()
        router.handle_post("/api/tasks/" + str(tid) + "/run-now", {}, rec)
        body, status = rec.calls[0]
        assert status == HTTPStatus.OK
        assert body["ok"] is True

    def test_run_now_paused_returns_423(self, tmp_path):
        db, router = _setup(tmp_path)
        tid = _seed(db, "RN-2")
        for _ in range(3):
            router.guard.record_failure(tid, "RN-2", "x", "m")
        rec = _Recorder()
        router.handle_post("/api/tasks/" + str(tid) + "/run-now", {}, rec)
        body, status = rec.calls[0]
        assert status == HTTPStatus.LOCKED
        assert body["error"] == "task_paused"

    def test_clear_pause(self, tmp_path):
        db, router = _setup(tmp_path)
        tid = _seed(db, "CP-1")
        for _ in range(3):
            router.guard.record_failure(tid, "CP-1", "x", "m")
        rec = _Recorder()
        router.handle_post("/api/tasks/" + str(tid) + "/clear-pause", {}, rec)
        body, _ = rec.calls[0]
        assert body["cleared"] is True
        assert router.guard.is_paused(tid)[0] is False

    def test_set_prompt_files(self, tmp_path):
        db, router = _setup(tmp_path)
        tid = _seed(db, "PF-1")
        rec = _Recorder()
        router.handle_post("/api/tasks/" + str(tid) + "/prompt-files", {
            "ai_prompt_base_file": "base.txt",
            "ai_prompt_criteria_file": "crit.txt",
        }, rec)
        body, _ = rec.calls[0]
        assert body["ai_prompt_base_file"] == "base.txt"
        assert body["ai_prompt_criteria_file"] == "crit.txt"

    def test_post_snapshot(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        router.handle_post("/api/price-history/snapshot", {
            "catalog_no": "SNAP-1",
            "source": "manual",
            "platform": "wameiji",
            "price": 1234.5,
            "currency": "JPY",
            "price_cny": 60.0,
            "sample_count": 5,
            "decision": "buy",
        }, rec)
        body, status = rec.calls[0]
        assert status is None or status == HTTPStatus.OK
        assert body["catalog_no"] == "SNAP-1"
        assert body["id"] > 0

    def test_post_snapshot_invalid_payload(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        # Price must be numeric; non-numeric triggers ValueError -> 400.
        router.handle_post("/api/price-history/snapshot", {
            "catalog_no": "X",
            "price": "not-a-number",
        }, rec)
        body, status = rec.calls[0]
        assert status == HTTPStatus.BAD_REQUEST
        assert body["error"] == "invalid_payload"

    def test_unhandled_post_returns_false(self, tmp_path):
        db, router = _setup(tmp_path)
        rec = _Recorder()
        handled = router.handle_post("/api/other", {}, rec)
        assert handled is False
        assert rec.calls == []