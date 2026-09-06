"""Tests for cd_monitor.services.scheduler_service."""
from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from cd_monitor.services.scheduler_service import SchedulerService, TaskRunner
from cd_monitor.storage.sqlite import init_db


def _setup_db(tmp_path: Path) -> Path:
    db = tmp_path / "sched.db"
    init_db(db)
    return db


def _seed_task(db, catalog_no="T-1", *, enabled=1, cron=None):
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO watchlist (catalog_no, enabled, platform, priority, cron)"
            " VALUES (?, ?, ?, 1, ?)",
            (catalog_no, enabled, "both", cron),
        )
        return int(cur.lastrowid)


def _run(coro):
    # Use asyncio.run for clean event-loop lifecycle (Python 3.13 safe).
    return asyncio.run(coro)


class TestTaskRunner:
    def test_run_now_unknown_task(self, tmp_path):
        db = _setup_db(tmp_path)
        runner = TaskRunner(db)
        result = _run(runner.run_now(99999))
        assert result["ok"] is False
        assert result["error"] == "task_not_found"

    def test_run_now_disabled_task(self, tmp_path):
        db = _setup_db(tmp_path)
        tid = _seed_task(db, "DIS-1", enabled=0)
        runner = TaskRunner(db)
        result = _run(runner.run_now(tid))
        assert result["ok"] is False
        assert result["error"] == "task_disabled"

    def test_run_now_executes(self, tmp_path):
        db = _setup_db(tmp_path)
        tid = _seed_task(db, "OK-1")
        runner = TaskRunner(db)
        result = _run(runner.run_now(tid))
        assert result["ok"] is True
        assert result["task_id"] == tid
        assert "started_at" in result
        assert "prompt_chars" in result

    def test_run_by_name(self, tmp_path):
        db = _setup_db(tmp_path)
        _seed_task(db, "NAME-1")
        runner = TaskRunner(db)
        result = _run(runner.run_by_name("NAME-1"))
        assert result["ok"] is True
        assert result["task_name"] == "NAME-1"

    def test_run_by_name_unknown(self, tmp_path):
        db = _setup_db(tmp_path)
        runner = TaskRunner(db)
        result = _run(runner.run_by_name("NOPE"))
        assert result["ok"] is False
        assert result["error"] == "task_not_found"

    def test_run_now_paused_task_rejected(self, tmp_path):
        db = _setup_db(tmp_path)
        tid = _seed_task(db, "PAUSE-1")
        runner = TaskRunner(db)
        for _ in range(3):
            runner.guard.record_failure(tid, "PAUSE-1", "x", "m")
        result = _run(runner.run_now(tid))
        assert result["ok"] is False
        assert result["error"] == "task_paused"
        assert "paused_until" in result

    def test_successful_run_clears_failure_count(self, tmp_path):
        db = _setup_db(tmp_path)
        tid = _seed_task(db, "OK-2")
        runner = TaskRunner(db)
        runner.guard.record_failure(tid, "OK-2", "x", "m")
        _run(runner.run_now(tid))
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT failure_count, last_status FROM watchlist WHERE id = ?",
                (tid,),
            ).fetchone()
        assert row[0] == 0
        assert row[1] == "ok"


class TestSchedulerService:
    def test_loads_jobs_for_enabled_cron_tasks(self, tmp_path):
        db = _setup_db(tmp_path)
        _seed_task(db, "C-1", cron="0 8 * * *")
        _seed_task(db, "C-2", cron="*/15 * * * *")
        _seed_task(db, "C-3")  # no cron
        _seed_task(db, "C-4", enabled=0, cron="0 9 * * *")  # disabled

        svc = SchedulerService(db)
        loaded = _run(svc.reload_jobs())
        assert loaded == 2

    def test_invalid_cron_skipped(self, tmp_path):
        db = _setup_db(tmp_path)
        _seed_task(db, "BAD-1", cron="not-a-cron")
        svc = SchedulerService(db)
        loaded = _run(svc.reload_jobs())
        assert loaded == 0

    def test_reload_clears_existing_jobs(self, tmp_path):
        db = _setup_db(tmp_path)
        _seed_task(db, "R-1", cron="0 8 * * *")
        svc = SchedulerService(db)
        _run(svc.reload_jobs())
        assert len(svc.scheduler.get_jobs()) == 1
        _run(svc.reload_jobs())
        assert len(svc.scheduler.get_jobs()) == 1

    def test_get_next_run_time_unknown_task(self, tmp_path):
        db = _setup_db(tmp_path)
        svc = SchedulerService(db)
        assert svc.get_next_run_time(99999) is None

    def test_lifecycle_hooks_called(self, tmp_path):
        db = _setup_db(tmp_path)
        _seed_task(db, "H-1", cron="0 8 * * *")
        svc = SchedulerService(db)

        started = []
        stopped = []

        async def on_started(tid, name):
            started.append((tid, name))

        async def on_stopped(tid, name):
            stopped.append((tid, name))

        svc.set_lifecycle_hooks(on_started=on_started, on_stopped=on_stopped)
        _run(svc.reload_jobs())
        _run(svc._run_job(1, "H-1"))
        assert started == [(1, "H-1")]
        assert stopped == [(1, "H-1")]

    def test_next_run_time_set_after_reload(self, tmp_path):
        # AsyncIOScheduler computes next_run_time lazily. We have to start
        # it (within an event loop) for get_next_run_time() to return non-None.
        db = _setup_db(tmp_path)
        _seed_task(db, "NR-1", cron="0 8 * * *")
        svc = SchedulerService(db)
        _run(svc.reload_jobs())

        async def _drive():
            svc.start()
            try:
                # Force the scheduler to compute next-run-time by triggering
                # an immediate wakeup on its event loop.
                await asyncio.sleep(0.05)
                nrt = svc.get_next_run_time(1)
                return nrt
            finally:
                svc.stop()

        nrt = _run(_drive())
        assert nrt is not None

    def test_start_and_stop_within_event_loop(self, tmp_path):
        db = _setup_db(tmp_path)
        svc = SchedulerService(db)

        async def _drive():
            svc.start()
            try:
                assert svc.scheduler.running is True
            finally:
                svc.stop()
            # Note: scheduler.running may briefly remain True until the
            # shutdown callback runs; we only assert the start path.

        _run(_drive())

    def test_start_is_idempotent(self, tmp_path):
        db = _setup_db(tmp_path)
        svc = SchedulerService(db)

        async def _drive():
            svc.start()
            try:
                svc.start()  # second call should not raise
                assert svc.scheduler.running is True
            finally:
                svc.stop()

        _run(_drive())