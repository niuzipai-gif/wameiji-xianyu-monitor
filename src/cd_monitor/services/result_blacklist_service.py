"""Per-watchlist result blacklist keyword rules (P5.5+).

Port of Usagi's `result_blacklist_rules` table to Kuro's per-watch model:
each watchlist (watch_id) owns a list of blacklist keywords that are matched
against scanned records before they reach the AI/keyword decision engine.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional, Union

from cd_monitor.services.scraper._result_blacklist_service import (
    normalize_blacklist_keywords,
)


@dataclass(slots=True)
class ResultBlacklistRule:
    watch_id: int
    keywords: list[str] = field(default_factory=list)
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "watch_id": int(self.watch_id),
            "keywords": list(self.keywords),
            "updated_at": self.updated_at,
        }


class ResultBlacklistService:
    """Persistence + lookup for `result_blacklist_rules` rows.

    All writes go through `set_keywords` which normalizes input via
    `normalize_blacklist_keywords` (so plain text and `re:...` regex
    keywords are stored in canonical form, matching the matcher in
    `cd_monitor.services.scraper._result_blacklist_service`).
    """

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
                CREATE TABLE IF NOT EXISTS result_blacklist_rules (
                  watch_id INTEGER PRIMARY KEY,
                  blacklist_keywords_json TEXT NOT NULL DEFAULT '"[]"',
                  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(watch_id) REFERENCES watchlist(id) ON DELETE CASCADE
                )
                """
            )

    def _decode_keywords(self, raw: Optional[str]) -> list[str]:
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if isinstance(parsed, list):
            return [str(x) for x in parsed if str(x).strip()]
        return []

    def get_keywords(self, watch_id: int) -> list[str]:
        self._ensure_table()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT blacklist_keywords_json FROM result_blacklist_rules"
                " WHERE watch_id = ?",
                (watch_id,),
            ).fetchone()
        if row is None:
            return []
        return self._decode_keywords(row[0])

    def get_rule(self, watch_id: int) -> ResultBlacklistRule:
        self._ensure_table()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT watch_id, blacklist_keywords_json, updated_at"
                " FROM result_blacklist_rules WHERE watch_id = ?",
                (watch_id,),
            ).fetchone()
        if row is None:
            return ResultBlacklistRule(watch_id=watch_id, keywords=[])
        return ResultBlacklistRule(
            watch_id=int(row["watch_id"]),
            keywords=self._decode_keywords(row["blacklist_keywords_json"]),
            updated_at=str(row["updated_at"]) if row["updated_at"] is not None else None,
        )

    def set_keywords(self, watch_id: int, keywords: Iterable[str] | str) -> list[str]:
        normalized = normalize_blacklist_keywords(keywords)
        payload = json.dumps(normalized, ensure_ascii=False)
        self._ensure_table()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO result_blacklist_rules (
                    watch_id, blacklist_keywords_json, updated_at
                ) VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(watch_id) DO UPDATE SET
                    blacklist_keywords_json = excluded.blacklist_keywords_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (watch_id, payload),
            )
        return normalized

    def clear(self, watch_id: int) -> None:
        self._ensure_table()
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM result_blacklist_rules WHERE watch_id = ?",
                (watch_id,),
            )
