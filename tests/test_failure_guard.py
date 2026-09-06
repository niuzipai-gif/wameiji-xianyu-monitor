"""Tests for cd_monitor.services.failure_guard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import sqlite3

from cd_monitor.services.failure_guard import (
    DEFAULT_THRESHOLD,
    FailureGuard,
    GuardDecision,
)
from cd_monitor.storage.sqlite import init_db


def _setup(tmp_path: Path):
    db = tmp_path / "guard.db"
    init_db(db)
    return db, FailureGuard(db, threshold=3, cooldown_hours=6, daily_notify_cap=1)


def _seed_task(tmp_path: Path, catalog_no: str = "T-001") -> int:
    db, _ = _setup(tmp_path)
    with sqlite3.connect(db) as conn:
        cur = conn.execute(
            "INSERT INTO watchlist (catalog_no, enabled, platform, priority) VALUES (?, 1, ?, 1)",
            (catalog_no, "both"),
        )
        return int(cur.lastrowid)


class TestGuardDecision:
    def test_to_dict(self):
        d = GuardDecision(
            should_pause=True,
            should_notify=False,
            consecutive_failures=3,
            threshold=3,
            paused_until=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            reason="auto-paused",
        ).to_dict()
        assert d["should_pause"] is True
        assert d["consecutive_failures"] == 3
        assert d["threshold"] == 3
        assert d["paused_until"].startswith("2026-01-01")
        assert d["reason"] == "auto-paused"

    def test_to_dict_with_no_pause(self):
        d = GuardDecision(
            should_pause=False,
            should_notify=False,
            consecutive_failures=1,
            threshold=3,
            paused_until=None,
        ).to_dict()
        assert d["paused_until"] is None


class TestRecordFailure:
    def test_first_failure_does_not_pause(self, tmp_path):
        _, guard = _setup(tmp_path)
        decision = guard.record_failure(
            task_id=999, task_name="ghost",
            error_type="network", error_message="boom",
        )
        assert decision.consecutive_failures == 1
        assert decision.should_pause is False
        assert decision.threshold == DEFAULT_THRESHOLD

    def test_threshold_failures_pauses(self, tmp_path):
        db, guard = _setup(tmp_path)
        tid = _seed_task(tmp_path, "PAUSE-1")
        last = None
        for _ in range(3):
            last = guard.record_failure(
                task_id=tid, task_name="PAUSE-1",
                error_type="login", error_message="bad cookie",
            )
        assert last is not None
        assert last.consecutive_failures == 3
        assert last.should_pause is True
        assert last.paused_until is not None
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT failure_count, last_status FROM watchlist WHERE id = ?",
                (tid,),
            ).fetchone()
        assert row[0] == 3
        assert row[1] == "paused"

    def test_threshold_failures_notifies_once(self, tmp_path):
        _, guard = _setup(tmp_path)
        tid = _seed_task(tmp_path, "NOTIFY-1")
        decisions = [
            guard.record_failure(
                task_id=tid, task_name="NOTIFY-1",
                error_type="rate_limit", error_message="429",
            )
            for _ in range(3)
        ]
        notified_count = sum(1 for d in decisions if d.should_notify)
        assert notified_count == 1
        # Only the 3rd (threshold-reaching) failure should pause.
        assert decisions[-1].should_pause is True
        assert not decisions[0].should_pause
        assert not decisions[1].should_pause

    def test_custom_threshold(self, tmp_path):
        db = tmp_path / "guard2.db"
        init_db(db)
        guard = FailureGuard(db, threshold=5, cooldown_hours=2, daily_notify_cap=1)
        tid = _seed_task(tmp_path, "TH-5")
        decisions = [
            guard.record_failure(
                task_id=tid, task_name="TH-5",
                error_type="x", error_message="m",
            )
            for _ in range(5)
        ]
        assert decisions[-1].should_pause is True
        assert decisions[-1].threshold == 5


class TestRecordSuccess:
    def test_resets_failure_count(self, tmp_path):
        db, guard = _setup(tmp_path)
        tid = _seed_task(tmp_path, "RESET-1")
        for _ in range(3):
            guard.record_failure(
                task_id=tid, task_name="RESET-1",
                error_type="x", error_message="m",
            )
        guard.record_success(tid)
        with sqlite3.connect(db) as conn:
            row = conn.execute(
                "SELECT failure_count, paused_until FROM watchlist WHERE id = ?",
                (tid,),
            ).fetchone()
        assert row[0] == 0
        assert row[1] is None

    def test_record_success_unknown_task_does_not_raise(self, tmp_path):
        _, guard = _setup(tmp_path)
        guard.record_success(999999)


class TestIsPaused:
    def test_returns_false_when_no_pause(self, tmp_path):
        _, guard = _setup(tmp_path)
        tid = _seed_task(tmp_path, "P-OK")
        paused, until = guard.is_paused(tid)
        assert paused is False
        assert until is None

    def test_returns_true_after_threshold(self, tmp_path):
        _, guard = _setup(tmp_path)
        tid = _seed_task(tmp_path, "P-AUTO")
        for _ in range(3):
            guard.record_failure(
                task_id=tid, task_name="P-AUTO",
                error_type="x", error_message="m",
            )
        paused, until = guard.is_paused(tid)
        assert paused is True
        assert until is not None

    def test_returns_false_after_pause_expires(self, tmp_path):
        db, guard = _setup(tmp_path)
        tid = _seed_task(tmp_path, "P-EXP")
        for _ in range(3):
            guard.record_failure(
                task_id=tid, task_name="P-EXP",
                error_type="x", error_message="m",
            )
        # Backdate paused_until to 1 hour ago, written in tz-aware form so
        # the parsed datetime matches the tz-aware internal clock.
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with sqlite3.connect(db) as conn:
            conn.execute(
                "UPDATE watchlist SET paused_until = ? WHERE id = ?",
                (past, tid),
            )
        paused, _ = guard.is_paused(tid)
        assert paused is False

    def test_unknown_task_returns_false(self, tmp_path):
        _, guard = _setup(tmp_path)
        paused, until = guard.is_paused(9999999)
        assert paused is False
        assert until is None


class TestClearPause:
    def test_clear_returns_true_when_paused(self, tmp_path):
        _, guard = _setup(tmp_path)
        tid = _seed_task(tmp_path, "CLEAR-1")
        for _ in range(3):
            guard.record_failure(
                task_id=tid, task_name="CLEAR-1",
                error_type="x", error_message="m",
            )
        assert guard.is_paused(tid)[0] is True
        cleared = guard.clear_pause(tid)
        assert cleared is True
        assert guard.is_paused(tid)[0] is False


class TestQueries:
    def test_get_recent_failures(self, tmp_path):
        _, guard = _setup(tmp_path)
        tid = _seed_task(tmp_path, "REC-1")
        for i in range(2):
            guard.record_failure(
                task_id=tid, task_name="REC-1",
                error_type="err" + str(i), error_message="msg" + str(i),
            )
        rows = guard.get_recent_failures(tid)
        assert len(rows) == 2
        assert rows[0]["created_at"] >= rows[1]["created_at"]

    def test_get_all_paused(self, tmp_path):
        _, guard = _setup(tmp_path)
        a = _seed_task(tmp_path, "P-A")
        b = _seed_task(tmp_path, "P-B")
        for _ in range(3):
            guard.record_failure(task_id=a, task_name="P-A", error_type="x", error_message="m")
            guard.record_failure(task_id=b, task_name="P-B", error_type="x", error_message="m")
        paused = guard.get_all_paused()
        catalogs = {r["catalog_no"] for r in paused}
        assert catalogs >= {"P-A", "P-B"}

    def test_get_recent_failures_respects_limit(self, tmp_path):
        _, guard = _setup(tmp_path)
        tid = _seed_task(tmp_path, "LIM-1")
        for i in range(5):
            guard.record_failure(
                task_id=tid, task_name="LIM-1",
                error_type="err" + str(i), error_message="m",
            )
        rows = guard.get_recent_failures(tid, limit=2)
        assert len(rows) == 2
