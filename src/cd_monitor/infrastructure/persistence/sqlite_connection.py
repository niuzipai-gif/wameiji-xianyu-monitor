"""SQLite 连接与 schema 初始化（适配 cd_monitor）。

设计要点：
- 与现有 cd_monitor.storage.sqlite.init_db 协同：新 schema 既包含老的业务表
  （watchlist / opportunities / ...），也叠加参考项目的新表
  （tasks / result_items / price_snapshots / result_blacklist_rules / app_metadata）。
- 所有 CREATE 全部 IF NOT EXISTS，老库升级安全。
- 数据库路径默认从 cd_monitor.infrastructure.config.settings 读取。
- WAL 在 Windows 某些 NTFS / 网络盘上会报 disk I/O error；失败自动回退普通日志。
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from cd_monitor.infrastructure.config.settings import business_settings
from cd_monitor.infrastructure.persistence.storage_names import DEFAULT_DATABASE_PATH


BUSY_TIMEOUT_MS = 5000


REFERENCE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS app_metadata (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY,
        task_name TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        keyword TEXT NOT NULL,
        description TEXT,
        analyze_images INTEGER NOT NULL,
        max_pages INTEGER NOT NULL,
        personal_only INTEGER NOT NULL,
        min_price TEXT,
        max_price TEXT,
        cron TEXT,
        ai_prompt_base_file TEXT NOT NULL,
        ai_prompt_criteria_file TEXT NOT NULL,
        account_state_file TEXT,
        account_strategy TEXT NOT NULL,
        free_shipping INTEGER NOT NULL,
        new_publish_option TEXT,
        region TEXT,
        decision_mode TEXT NOT NULL,
        keyword_rules_json TEXT NOT NULL,
        is_running INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS result_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        result_filename TEXT NOT NULL,
        keyword TEXT NOT NULL,
        task_name TEXT NOT NULL,
        crawl_time TEXT NOT NULL,
        publish_time TEXT,
        price REAL,
        price_display TEXT,
        item_id TEXT,
        title TEXT,
        link TEXT,
        link_unique_key TEXT NOT NULL,
        seller_nickname TEXT,
        is_recommended INTEGER NOT NULL,
        analysis_source TEXT,
        keyword_hit_count INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        raw_json TEXT NOT NULL,
        UNIQUE(result_filename, link_unique_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword_slug TEXT NOT NULL,
        keyword TEXT NOT NULL,
        task_name TEXT NOT NULL,
        snapshot_time TEXT NOT NULL,
        snapshot_day TEXT NOT NULL,
        run_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        title TEXT,
        price REAL NOT NULL,
        price_display TEXT,
        tags_json TEXT NOT NULL,
        region TEXT,
        seller TEXT,
        publish_time TEXT,
        link TEXT,
        UNIQUE(keyword_slug, run_id, item_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS result_blacklist_rules (
        result_filename TEXT PRIMARY KEY,
        blacklist_keywords_json TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_name ON tasks(task_name)",
    "CREATE INDEX IF NOT EXISTS idx_results_filename_crawl ON result_items(result_filename, crawl_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_results_filename_publish ON result_items(result_filename, publish_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_results_filename_price ON result_items(result_filename, price DESC)",
    "CREATE INDEX IF NOT EXISTS idx_results_filename_recommended ON result_items(result_filename, is_recommended, analysis_source, crawl_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_keyword_time ON price_snapshots(keyword_slug, snapshot_time DESC)",
    "CREATE INDEX IF NOT EXISTS idx_snapshots_keyword_item_time ON price_snapshots(keyword_slug, item_id, snapshot_time DESC)",
)


def get_database_path() -> str:
    """优先用 CD_DB_PATH，否则回落到默认。"""
    env = os.getenv("CD_DB_PATH")
    if env:
        return env
    try:
        return business_settings.db_path
    except Exception:
        return DEFAULT_DATABASE_PATH


def _prepare_database_file(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # WAL 在 Windows 某些 NTFS / 受限目录会报 disk I/O error；失败回退普通模式。
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.OperationalError:
        conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")


def _ensure_business_tables(conn: sqlite3.Connection) -> None:
    """调用现有 storage.sqlite.init_db 业务表 schema。失败也不致命：
    storage 模块可能未安装某些可选依赖，所以捕获异常。
    """
    try:
        from cd_monitor.storage.sqlite import init_db
        init_db(get_database_path())
    except Exception as exc:  # pragma: no cover
        import logging
        logging.getLogger("cd_monitor.persistence").debug(
            "init_db skipped: %s", exc,
        )


def init_schema(conn: sqlite3.Connection) -> None:
    """叠加参考项目表 + 触发业务表 schema。"""
    _ensure_business_tables(conn)
    for statement in REFERENCE_SCHEMA_STATEMENTS:
        conn.execute(statement)
    _migrate_result_items_status(conn)
    conn.commit()


def _migrate_result_items_status(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT value FROM app_metadata WHERE key = 'migration:result_items_status'"
    ).fetchone()
    if row is not None:
        return
    cols = [r[1] for r in conn.execute("PRAGMA table_info(result_items)").fetchall()]
    if "status" not in cols:
        conn.execute(
            "ALTER TABLE result_items ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
    conn.execute(
        "INSERT OR REPLACE INTO app_metadata(key, value) "
        "VALUES ('migration:result_items_status', 'done')"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_results_filename_status_crawl "
        "ON result_items(result_filename, status, crawl_time DESC)"
    )


@contextmanager
def sqlite_connection(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    path = db_path or get_database_path()
    _prepare_database_file(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        _apply_pragmas(conn)
        yield conn
    finally:
        conn.close()
