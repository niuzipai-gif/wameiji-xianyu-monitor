"""基于 SQLite 的任务仓储（适配 cd_monitor）。

设计要点：
- 用 dataclass 而非 pydantic，避免引入 cron_utils / account_strategy_service
  等参考项目内部依赖；后续阶段可平滑升级到 pydantic 模型。
- 写同一个 cd_monitor.db，bootstrap 时通过 sqlite_bootstrap 自动创建表。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from cd_monitor.infrastructure.persistence.sqlite_bootstrap import bootstrap_sqlite_storage
from cd_monitor.infrastructure.persistence.sqlite_connection import sqlite_connection


@dataclass
class Task:
    id: Optional[int] = None
    task_name: str = ""
    enabled: bool = True
    keyword: str = ""
    description: str = ""
    analyze_images: bool = True
    max_pages: int = 1
    personal_only: bool = False
    min_price: Optional[str] = None
    max_price: Optional[str] = None
    cron: Optional[str] = None
    ai_prompt_base_file: str = "prompts/base_prompt.txt"
    ai_prompt_criteria_file: str = ""
    account_state_file: Optional[str] = None
    account_strategy: str = "auto"
    free_shipping: bool = True
    new_publish_option: Optional[str] = None
    region: Optional[str] = None
    decision_mode: str = "ai"
    keyword_rules: List[str] = field(default_factory=list)
    is_running: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["keyword_rules_json"] = json.dumps(d.pop("keyword_rules") or [], ensure_ascii=False)
        return d


def _row_to_task(row) -> Task:
    payload = dict(row)
    payload["enabled"] = bool(payload["enabled"])
    payload["analyze_images"] = bool(payload["analyze_images"])
    payload["personal_only"] = bool(payload["personal_only"])
    payload["free_shipping"] = bool(payload["free_shipping"])
    payload["is_running"] = bool(payload["is_running"])
    payload["keyword_rules"] = json.loads(payload.pop("keyword_rules_json") or "[]")
    return Task(**payload)


def find_task_by_name_sync(task_name: str) -> Optional[Task]:
    bootstrap_sqlite_storage()
    with sqlite_connection() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_name = ? ORDER BY id ASC LIMIT 1",
            (task_name,),
        ).fetchone()
    return _row_to_task(row) if row else None


class SqliteTaskRepository:
    """基于 SQLite 的任务仓储（同步实现，async 包装到 to_thread）。"""

    def __init__(
        self,
        db_path: str | None = None,
        legacy_config_file: str | None = "config.json",
    ):
        self.db_path = db_path
        self.legacy_config_file = legacy_config_file

    # ---- async 接口（供 FastAPI router 使用） ----
    async def find_all(self) -> List[Task]:
        return await asyncio.to_thread(self._find_all_sync)

    async def find_by_id(self, task_id: int) -> Optional[Task]:
        return await asyncio.to_thread(self._find_by_id_sync, task_id)

    async def save(self, task: Task) -> Task:
        return await asyncio.to_thread(self._save_sync, task)

    async def delete(self, task_id: int) -> bool:
        return await asyncio.to_thread(self._delete_sync, task_id)

    # ---- sync 实现 ----
    def _find_all_sync(self) -> List[Task]:
        bootstrap_sqlite_storage(
            self.db_path,
            legacy_config_file=self.legacy_config_file,
        )
        with sqlite_connection(self.db_path) as conn:
            rows = conn.execute("SELECT * FROM tasks ORDER BY id ASC").fetchall()
        return [_row_to_task(row) for row in rows]

    def _find_by_id_sync(self, task_id: int) -> Optional[Task]:
        bootstrap_sqlite_storage(
            self.db_path,
            legacy_config_file=self.legacy_config_file,
        )
        with sqlite_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,),
            ).fetchone()
        return _row_to_task(row) if row else None

    def _save_sync(self, task: Task) -> Task:
        bootstrap_sqlite_storage(
            self.db_path,
            legacy_config_file=self.legacy_config_file,
        )
        with sqlite_connection(self.db_path) as conn:
            task_id = task.id
            if task_id is None:
                task_id = self._next_task_id(conn)
            values = task.to_dict()
            values["id"] = task_id
            values["enabled"] = 1 if task.enabled else 0
            values["analyze_images"] = 1 if task.analyze_images else 0
            values["personal_only"] = 1 if task.personal_only else 0
            values["free_shipping"] = 1 if task.free_shipping else 0
            values["is_running"] = 1 if task.is_running else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks (
                    id, task_name, enabled, keyword, description, analyze_images,
                    max_pages, personal_only, min_price, max_price, cron,
                    ai_prompt_base_file, ai_prompt_criteria_file, account_state_file,
                    account_strategy, free_shipping, new_publish_option, region,
                    decision_mode, keyword_rules_json, is_running
                ) VALUES (
                    :id, :task_name, :enabled, :keyword, :description, :analyze_images,
                    :max_pages, :personal_only, :min_price, :max_price, :cron,
                    :ai_prompt_base_file, :ai_prompt_criteria_file, :account_state_file,
                    :account_strategy, :free_shipping, :new_publish_option, :region,
                    :decision_mode, :keyword_rules_json, :is_running
                )
                """,
                values,
            )
            conn.commit()
            task.id = task_id
            return task

    def _delete_sync(self, task_id: int) -> bool:
        bootstrap_sqlite_storage(
            self.db_path,
            legacy_config_file=self.legacy_config_file,
        )
        with sqlite_connection(self.db_path) as conn:
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def _next_task_id(conn) -> int:
        row = conn.execute("SELECT COALESCE(MAX(id), -1) + 1 AS next_id FROM tasks").fetchone()
        return int(row["next_id"])
