"""Task-level failure circuit breaker.

Goals:
- When the login state is invalid / a task is rate-limited / the platform
  is down, avoid infinite retries and high-frequency request storms.
- After N consecutive failures, auto-pause the task for a cooldown window.
- During the pause, notify the user at most once per day until they update
  the login state file (or run `clear_pause()`), at which point the task
  auto-resumes.

Uses only the stdlib + sqlite3 (the project already has sqlite for the DB).
Cross-process safe: every read/write goes through the shared SQLite file.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional, Union

try:
    from zoneinfo import ZoneInfo  # py3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None


DEFAULT_THRESHOLD = 3
DEFAULT_COOLDOWN_HOURS = 6
DEFAULT_DAILY_NOTIFY_CAP = 1
DEFAULT_TZ = "Asia/Shanghai"


def _load_tz(name: str):
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(name)
    except Exception:
        return None


def _now(tz_name: str, now: Optional[datetime] = None) -> datetime:
    if now is not None:
        return now
    tz = _load_tz(tz_name)
    if tz is None:
        return datetime.now()
    return datetime.now(tz)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat()


def _iso_to_dt(value: Union[str, datetime, None]) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


@dataclass(slots=True)
class GuardDecision:
    should_pause: bool
    should_notify: bool
    consecutive_failures: int
    threshold: int
    paused_until: Optional[datetime]
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "should_pause": self.should_pause,
            "should_notify": self.should_notify,
            "consecutive_failures": self.consecutive_failures,
            "threshold": self.threshold,
            "paused_until": _dt_to_iso(self.paused_until),
            "reason": self.reason,
        }


class FailureGuard:
    """Circuit breaker backed by the failure_records table.

    Use a single shared FailureGuard instance per process. The class itself
    is stateless; state lives in SQLite so multiple processes (web server +
    a separately-spawned scanner) see the same view.
    """

    def __init__(
        self,
        db_path: Union[str, Path] = "data/cd_monitor.db",
        *,
        threshold: int = DEFAULT_THRESHOLD,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
        daily_notify_cap: int = DEFAULT_DAILY_NOTIFY_CAP,
        tz_name: str = DEFAULT_TZ,
    ) -> None:
        self.db_path = Path(db_path)
        self.threshold = max(1, _as_int(threshold, DEFAULT_THRESHOLD))
        self.cooldown_hours = max(1, _as_int(cooldown_hours, DEFAULT_COOLDOWN_HOURS))
        self.daily_notify_cap = max(1, _as_int(daily_notify_cap, DEFAULT_DAILY_NOTIFY_CAP))
        self.tz_name = tz_name
        self._ensure_table()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS failure_records (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id INTEGER,
                  task_name TEXT,
                  source TEXT,
                  keyword TEXT,
                  error_type TEXT,
                  error_message TEXT,
                  paused INTEGER DEFAULT 0,
                  notified_at TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_failure_records_task_time"
                " ON failure_records(task_id, created_at DESC)"
            )

    # ---------- core API ----------

    def record_failure(
        self,
        task_id: int,
        task_name: str,
        error_type: str,
        error_message: str,
        *,
        source: Optional[str] = None,
        keyword: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> GuardDecision:
        """Record a failure and decide whether to pause + notify."""
        ts = _now(self.tz_name, now=now)
        consecutive = self._consecutive_failures(task_id, before_ts=ts)
        consecutive += 1  # include the one we are about to record
        paused = consecutive >= self.threshold
        paused_until = ts + timedelta(hours=self.cooldown_hours) if paused else None
        notify = paused and self._should_notify(task_id, ts)

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO failure_records
                  (task_id, task_name, source, keyword, error_type, error_message, paused, notified_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    task_name,
                    source,
                    keyword,
                    error_type,
                    error_message[:500] if error_message else None,
                    1 if paused else 0,
                    ts if notify else None,
                    ts,
                ),
            )
            if paused:
                # Mirror to watchlist for fast UI lookup.
                conn.execute(
                    "UPDATE watchlist SET failure_count = ?, paused_until = ?, last_status = ?"
                    " WHERE id = ?",
                    (consecutive, paused_until, "paused", task_id),
                )
            return GuardDecision(
                should_pause=paused,
                should_notify=notify,
                consecutive_failures=consecutive,
                threshold=self.threshold,
                paused_until=paused_until,
                reason=("auto-paused after %d consecutive failures" % consecutive) if paused else "",
            )

    def record_success(self, task_id: int) -> None:
        """Reset failure counter for a task after a successful run."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE watchlist SET failure_count = 0, paused_until = NULL, last_status = ?"
                " WHERE id = ?",
                ("ok", task_id),
            )

    def is_paused(self, task_id: int, now: Optional[datetime] = None) -> tuple[bool, Optional[datetime]]:
        ts = _now(self.tz_name, now=now)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT paused_until, last_status FROM watchlist WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return False, None
        paused_until = _iso_to_dt(row["paused_until"])
        if paused_until is None:
            return False, None
        if ts >= paused_until:
            return False, paused_until
        return True, paused_until

    def clear_pause(self, task_id: int) -> bool:
        """Manually clear the pause flag (e.g. after updating login state)."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE watchlist SET failure_count = 0, paused_until = NULL WHERE id = ?",
                (task_id,),
            )
            return cur.rowcount > 0

    def get_recent_failures(
        self,
        task_id: int,
        limit: int = 20,
    ) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, task_id, task_name, source, keyword, error_type, error_message,"
                " paused, notified_at, created_at"
                " FROM failure_records WHERE task_id = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_paused(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, catalog_no, cron, failure_count, paused_until, last_status"
                " FROM watchlist WHERE paused_until IS NOT NULL"
                " ORDER BY paused_until DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- internals ----------

    def _consecutive_failures(self, task_id: int, before_ts: datetime) -> int:
        """Count consecutive failures for the task ending just before `before_ts`.

        A success row (in watchlist.last_status == 'ok' with a recent timestamp)
        or the absence of failure rows since a manual reset terminates the streak.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT created_at FROM failure_records WHERE task_id = ?"
                " ORDER BY created_at DESC LIMIT 50",
                (task_id,),
            ).fetchall()
        count = 0
        for row in rows:
            ts = _iso_to_dt(row["created_at"])
            if ts is None or ts > before_ts:
                continue
            count += 1
        return count

    def _should_notify(self, task_id: int, now: datetime) -> bool:
        """Return True if we should send a notification for this pause now.

        Caps to at most `daily_notify_cap` notifications per task per day.
        """
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM failure_records"
                " WHERE task_id = ? AND paused = 1"
                " AND notified_at IS NOT NULL AND notified_at >= ?",
                (task_id, start_of_day),
            ).fetchone()
        return int(row["c"] or 0) < self.daily_notify_cap
