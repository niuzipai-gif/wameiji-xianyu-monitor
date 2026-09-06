"""Repository facade for the watchlist = tasks table.

A thin convenience over the existing sqlite functions in
cd_monitor.storage.sqlite. Centralizes the row->dict mapping used by the
scheduler, the API, and the failure guard, so the columns stay consistent
across the codebase.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional, Union


def _coerce_keywords(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        # Stored as JSON list or comma-separated. Try JSON first.
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


def _row_to_task(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["required_keywords"] = _coerce_keywords(d.get("required_keywords"))
    d["excluded_keywords"] = _coerce_keywords(d.get("excluded_keywords"))
    d["is_running"] = bool(d.get("is_running", 0))
    # P5.5 filter columns: keep the SQLite truth values exposed via JSON.
    for col in ("personal_only", "analyze_images", "enabled"):
        v = d.get(col)
        if v is not None:
            d[col] = bool(int(v))
    return d


class TaskRepository:
    def __init__(self, db_path: Union[str, Path] = "data/cd_monitor.db") -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()

    def list_all(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist ORDER BY priority DESC, id ASC"
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def list_enabled(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist WHERE enabled = 1 ORDER BY priority DESC, id ASC"
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def list_enabled_with_cron(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM watchlist"
                " WHERE enabled = 1 AND cron IS NOT NULL AND TRIM(cron) != ''"
                " ORDER BY priority DESC, id ASC"
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def get_task(self, task_id: int) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM watchlist WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return _row_to_task(row)

    def find_by_name(self, catalog_no: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM watchlist WHERE catalog_no = ? ORDER BY id DESC LIMIT 1",
                (catalog_no,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_task(row)

    def update_runtime(
        self,
        task_id: int,
        *,
        is_running: Optional[bool] = None,
        last_status: Optional[str] = None,
        last_run_at: Optional[datetime] = None,
    ) -> None:
        sets, args = [], []
        if is_running is not None:
            sets.append("is_running = ?")
            args.append(1 if is_running else 0)
        if last_status is not None:
            sets.append("last_status = ?")
            args.append(last_status)
        if last_run_at is not None:
            sets.append("last_run_at = ?")
            args.append(last_run_at)
        if not sets:
            return
        args.append(task_id)
        with self._conn() as conn:
            conn.execute(
                "UPDATE watchlist SET " + ", ".join(sets) + " WHERE id = ?",
                args,
            )

    def update_next_run(self, task_id: int, next_run_at: Optional[datetime]) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE watchlist SET next_run_at = ? WHERE id = ?",
                (next_run_at, task_id),
            )

    def set_cron(self, task_id: int, cron: Optional[str]) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE watchlist SET cron = ? WHERE id = ?",
                (cron, task_id),
            )

    def set_prompt_files(
        self,
        task_id: int,
        base_file: Optional[str],
        criteria_file: Optional[str],
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE watchlist SET ai_prompt_base_file = ?, ai_prompt_criteria_file = ? WHERE id = ?",
                (base_file, criteria_file, task_id),
            )
