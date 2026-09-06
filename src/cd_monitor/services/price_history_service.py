"""Price history snapshots + trend queries.

Each scan inserts a row into `price_snapshots`. The service exposes:
  - record_snapshot(): append a single scan result
  - record_snapshots(): bulk insert
  - get_recent(): last N snapshots for a catalog
  - get_trend(): aggregate stats (count, min, max, avg, latest)
  - get_all_trends(): per-catalog summary for the dashboard

A "snapshot" is one row per (catalog, source, platform, captured_at).
The dedup key is (catalog_no, source, platform, captured_at) so re-running
the same scan within the same minute does not double-count.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator, Optional, Union


@dataclass(slots=True)
class PriceSnapshot:
    catalog_no: str
    source: str           # e.g. "wameiji", "xianyu", "scan"
    platform: str         # e.g. "wameiji", "xianyu", "both"
    price: float
    currency: str         # "JPY" / "CNY" / "USD"
    price_cny: Optional[float] = None
    sample_count: int = 0
    decision: Optional[str] = None
    opportunity_id: Optional[int] = None
    task_id: Optional[int] = None
    run_id: Optional[int] = None
    source_kind: str = "mock"        # mock | live | manual
    scan_run_id: Optional[str] = None  # shared id for one scan pass
    notes: Optional[str] = None
    captured_at: Optional[datetime] = None

    def to_row(self) -> tuple:
        return (
            self.catalog_no,
            self.source,
            self.platform,
            float(self.price),
            self.currency,
            float(self.price_cny) if self.price_cny is not None else None,
            int(self.sample_count),
            self.decision,
            self.opportunity_id,
            self.task_id,
            self.run_id,
            (self.source_kind or "mock"),
            self.scan_run_id,
            self.notes,
            self.captured_at or datetime.now(),
        )


@dataclass(slots=True)
class PriceTrend:
    catalog_no: str
    sample_count: int
    min_price: Optional[float]
    max_price: Optional[float]
    avg_price: Optional[float]
    latest_price: Optional[float]
    latest_decision: Optional[str]
    first_seen_at: Optional[str]
    last_seen_at: Optional[str]

    def to_dict(self) -> dict:
        return {
            "catalog_no": self.catalog_no,
            "sample_count": self.sample_count,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "avg_price": self.avg_price,
            "latest_price": self.latest_price,
            "latest_decision": self.latest_decision,
            "first_seen_at": self.first_seen_at,
            "last_seen_at": self.last_seen_at,
        }


class PriceHistoryService:
    def __init__(self, db_path: Union[str, Path] = "data/cd_monitor.db") -> None:
        self.db_path = Path(db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()

    def _ensure_table(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS price_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  catalog_no TEXT NOT NULL,
                  source TEXT NOT NULL,
                  platform TEXT NOT NULL,
                  price REAL NOT NULL,
                  currency TEXT NOT NULL,
                  price_cny REAL,
                  sample_count INTEGER DEFAULT 0,
                  decision TEXT,
                  opportunity_id INTEGER,
                  task_id INTEGER,
                  run_id INTEGER,
                  source_kind TEXT DEFAULT 'mock',
                  scan_run_id TEXT,
                  notes TEXT,
                  captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(price_snapshots)").fetchall()
            }
            for col, decl in (
                ("source_kind", "TEXT DEFAULT 'mock'"),
                ("scan_run_id", "TEXT"),
                ("notes", "TEXT"),
            ):
                if col not in existing:
                    conn.execute(f"ALTER TABLE price_snapshots ADD COLUMN {col} {decl}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_price_snapshots_catalog_time"
                " ON price_snapshots(catalog_no, captured_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_price_snapshots_scan_run"
                " ON price_snapshots(scan_run_id)"
            )

    def record_snapshot(self, snap: PriceSnapshot) -> int:
        self._ensure_table()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO price_snapshots (
                  catalog_no, source, platform, price, currency,
                  price_cny, sample_count, decision, opportunity_id,
                  task_id, run_id, source_kind, scan_run_id, notes,
                  captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                snap.to_row(),
            )
            return int(cur.lastrowid or 0)

    def record_snapshots(self, snaps: Iterable[PriceSnapshot]) -> int:
        self._ensure_table()
        rows = [s.to_row() for s in snaps]
        if not rows:
            return 0
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT INTO price_snapshots (
                  catalog_no, source, platform, price, currency,
                  price_cny, sample_count, decision, opportunity_id,
                  task_id, run_id, source_kind, scan_run_id, notes,
                  captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def get_recent(self, catalog_no: str, limit: int = 50) -> list[dict]:
        self._ensure_table()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, catalog_no, source, platform, price, currency,
                       price_cny, sample_count, decision, opportunity_id,
                       task_id, run_id, source_kind, scan_run_id, notes,
                       captured_at
                FROM price_snapshots
                WHERE catalog_no = ?
                ORDER BY captured_at DESC
                LIMIT ?
                """,
                (catalog_no, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_by_scan_run(self, scan_run_id: str) -> list[dict]:
        """Return all snapshots that share ``scan_run_id``.

        Used by the Web UI to draw the timeline of a single scan pass
        (multiple platforms + decisions, one UUID)."""
        self._ensure_table()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, catalog_no, source, platform, price, currency,
                       price_cny, sample_count, decision, opportunity_id,
                       task_id, run_id, source_kind, scan_run_id, notes,
                       captured_at
                FROM price_snapshots
                WHERE scan_run_id = ?
                ORDER BY captured_at ASC, id ASC
                """,
                (scan_run_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_trend(self, catalog_no: str, *, since: Optional[datetime] = None) -> Optional[PriceTrend]:
        self._ensure_table()
        with self._conn() as conn:
            if since is None:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS c,
                           MIN(price) AS mn, MAX(price) AS mx, AVG(price) AS av,
                           (SELECT price FROM price_snapshots
                             WHERE catalog_no = ? ORDER BY captured_at DESC LIMIT 1) AS latest,
                           (SELECT decision FROM price_snapshots
                             WHERE catalog_no = ? ORDER BY captured_at DESC LIMIT 1) AS latest_decision,
                           MIN(captured_at) AS first_at,
                           MAX(captured_at) AS last_at
                    FROM price_snapshots WHERE catalog_no = ?
                    """,
                    (catalog_no, catalog_no, catalog_no),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS c,
                           MIN(price) AS mn, MAX(price) AS mx, AVG(price) AS av,
                           (SELECT price FROM price_snapshots
                             WHERE catalog_no = ? AND captured_at >= ?
                             ORDER BY captured_at DESC LIMIT 1) AS latest,
                           (SELECT decision FROM price_snapshots
                             WHERE catalog_no = ? AND captured_at >= ?
                             ORDER BY captured_at DESC LIMIT 1) AS latest_decision,
                           MIN(captured_at) AS first_at,
                           MAX(captured_at) AS last_at
                    FROM price_snapshots WHERE catalog_no = ? AND captured_at >= ?
                    """,
                    (catalog_no, since, catalog_no, since, catalog_no, since),
                ).fetchone()
        if row is None or int(row["c"] or 0) == 0:
            return None
        return PriceTrend(
            catalog_no=catalog_no,
            sample_count=int(row["c"] or 0),
            min_price=float(row["mn"]) if row["mn"] is not None else None,
            max_price=float(row["mx"]) if row["mx"] is not None else None,
            avg_price=float(row["av"]) if row["av"] is not None else None,
            latest_price=float(row["latest"]) if row["latest"] is not None else None,
            latest_decision=row["latest_decision"],
            first_seen_at=row["first_at"],
            last_seen_at=row["last_at"],
        )

    def get_all_trends(self, *, limit: int = 200) -> list[dict]:
        self._ensure_table()
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT catalog_no,
                       COUNT(*) AS sample_count,
                       MIN(price) AS min_price,
                       MAX(price) AS max_price,
                       AVG(price) AS avg_price,
                       MAX(captured_at) AS last_seen_at
                FROM price_snapshots
                GROUP BY catalog_no
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
